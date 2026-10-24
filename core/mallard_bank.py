"""Adapter for Mallard's sdiego-history-v1.1 bank transaction CSV."""

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db.models import Q

from .bank_import import MATCH_FIELDS, parse_amount, parse_date, read_csv_rows, source_differs, validate_transactions
from .models import BankTransaction


VERIFIED_ACCOUNT = "0392369300201"
COLUMNS = (
    "transaction_code", "source_namespace", "bank_account", "bank_receipt", "line_type",
    "transaction_date", "transaction_time", "description", "debit", "credit", "bank_balance",
    "amount", "direction", "payer_name", "payer_account", "payer_rfc", "payer_bank",
    "tracking_key", "suggested_house", "assigned_house", "classification", "reconciliation_status",
    "reviewed_payment_id", "source_reference", "bronze_date_text",
)
REQUIRED_VALUES = (
    "transaction_code", "source_namespace", "bank_account", "bank_receipt", "line_type",
    "transaction_date", "transaction_time", "description", "bank_balance", "amount",
    "direction", "classification", "reconciliation_status", "source_reference", "bronze_date_text",
)


def _money(value, field):
    if not re.fullmatch(r"-?[0-9]+\.[0-9]{2}", value):
        raise ValidationError(f"{field} must be an exact two-decimal amount.")
    return parse_amount(value, decimal_separator=".", thousands_separator=None)


def parse_mallard_row(row, *, filename, digest, line):
    for field in REQUIRED_VALUES:
        if not row[field].strip():
            raise ValidationError(f"{field} is required.")
    if row["bank_account"] != VERIFIED_ACCOUNT:
        raise ValidationError("bank_account does not match the verified historical account.")
    if row["source_namespace"] != "sdiego-banbajio-history":
        raise ValidationError("source_namespace does not match the Mallard historical contract.")
    if not re.fullmatch(r"BT-[0-9a-f]{64}", row["transaction_code"]):
        raise ValidationError("transaction_code must preserve a Mallard legacy reference.")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", row["transaction_date"]):
        raise ValidationError("transaction_date must use YYYY-MM-DD.")
    transaction_date = parse_date(row["transaction_date"], date_format="%Y-%m-%d")
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}", row["transaction_time"]):
        raise ValidationError("transaction_time must use local HH:MM:SS.")
    try:
        transaction_time = datetime.strptime(row["transaction_time"], "%H:%M:%S").time()
    except ValueError as error:
        raise ValidationError("Invalid local transaction_time.") from error
    if row["line_type"] not in {"CREDIT", "DEBIT", "COMMISSION", "VAT"}:
        raise ValidationError("Unknown line_type.")
    is_credit = row["line_type"] == "CREDIT"
    if row["direction"] != ("CREDIT" if is_credit else "DEBIT"):
        raise ValidationError("direction contradicts line_type.")
    active, inactive = ("credit", "debit") if is_credit else ("debit", "credit")
    magnitude = _money(row["amount"], "amount")
    if magnitude <= 0 or _money(row[active], active) != magnitude:
        raise ValidationError("amount must be positive and equal the active credit/debit value.")
    if row[inactive] and _money(row[inactive], inactive) != 0:
        raise ValidationError("The inactive credit/debit value must be empty or zero.")
    _money(row["bank_balance"], "bank_balance")
    record = BankTransaction(
        bank_account=row["bank_account"], bank_receipt=row["bank_receipt"], line_type=row["line_type"],
        transaction_code=row["transaction_code"], transaction_date=transaction_date,
        transaction_time=transaction_time, description=row["description"],
        amount=magnitude if is_credit else -magnitude,
        source_identifiers={field: row[field] for field in (
            "transaction_code", "source_namespace", "bank_account", "bank_receipt", "line_type",
            "tracking_key", "reviewed_payment_id", "source_reference",
        ) if row[field]},
        source_values=dict(row), source_file=filename, source_file_sha256=digest,
        source_row_number=line,
    )
    validate_transactions([record])
    return record


def read_mallard_csv(path):
    path = Path(path)
    try:
        contents = path.read_bytes()
        text = contents.decode("utf-8")
    except OSError as error:
        raise ValidationError("The CSV file cannot be read.") from error
    except UnicodeDecodeError as error:
        raise ValidationError("The CSV must be UTF-8.") from error
    if text.startswith("\ufeff"):
        raise ValidationError("The Mallard CSV must be UTF-8 without BOM.")
    digest = hashlib.sha256(contents).hexdigest()
    rows = read_csv_rows(StringIO(text, newline=""), required_columns=(), delimiter=",")
    if set(rows[0][1]) != set(COLUMNS):
        raise ValidationError("Expected exactly the 25 Mallard v1.1 bank CSV columns.")
    records, errors = [], []
    for line, row in rows:
        try:
            records.append(parse_mallard_row(row, filename=path.name, digest=digest, line=line))
        except ValidationError as error:
            errors.append({"row": line, "messages": error.messages})
    return records, errors, len(rows), digest


def preflight_mallard_file(path, *, manifest_path=None):
    """Read and compare only; never save candidates or change existing transactions."""
    records, errors, total, digest = read_mallard_csv(path)
    groups, external_groups = defaultdict(list), defaultdict(list)
    for record in records:
        groups[tuple(getattr(record, field) for field in MATCH_FIELDS)].append(record)
        external_groups[record.transaction_code].append(record)
    duplicate_keys = {key for key, group in groups.items() if len(group) > 1}
    existing = list(BankTransaction.objects.filter(
        Q(bank_account__in={r.bank_account for r in records},
          bank_receipt__in={r.bank_receipt for r in records})
        | Q(transaction_code__in=external_groups)
    ))
    by_identity = {tuple(getattr(r, field) for field in MATCH_FIELDS): r for r in existing}
    by_code = {r.transaction_code: r for r in existing}
    rejected_rows = {error["row"] for error in errors}
    conflict_rows, unchanged = set(), 0
    for record in records:
        key = tuple(getattr(record, field) for field in MATCH_FIELDS)
        stored = by_identity.get(key)
        same_code = by_code.get(record.transaction_code)
        conflict = ((stored is not None and source_differs(stored, record))
                    or (same_code is not None and same_code is not stored)
                    or any(source_differs(other, record) for other in groups[key])
                    or any(tuple(getattr(other, f) for f in MATCH_FIELDS) != key
                           for other in external_groups[record.transaction_code]))
        messages = []
        if key in duplicate_keys:
            messages.append("Duplicate matching identity within the CSV.")
        if conflict:
            conflict_rows.add(record.source_row_number)
            messages.append("Conflicting source values or external reference; manual review is required.")
        if messages:
            rejected_rows.add(record.source_row_number)
            errors.append({"row": record.source_row_number, "messages": messages})
        elif stored is not None:
            unchanged += 1
    credit_total = sum((r.amount for r in records if r.amount > 0), Decimal("0.00"))
    debit_total = -sum((r.amount for r in records if r.amount < 0), Decimal("0.00"))
    report = {
        "total": total, "accepted": total - len(rejected_rows), "rejected": len(rejected_rows),
        "duplicate_identities": len(duplicate_keys), "conflicting_values": len(conflict_rows),
        "unchanged": unchanged, "credit_total": credit_total, "debit_total": debit_total,
        "net_total": credit_total - debit_total,
        "line_types": dict(Counter(r.line_type for r in records)), "errors": errors,
    }
    if manifest_path is not None:
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            matches = (
                manifest["contract_version"] == "sdiego-history-v1.1"
                and manifest["source_namespace"] == "sdiego-banbajio-history"
                and manifest["files"]["bank_transactions.csv"] == digest
                and manifest["counts"]["bank_transactions"] == total
                and Decimal(manifest["credit_total"]) == credit_total
                and Decimal(manifest["debit_total"]) == debit_total
            )
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as error:
            raise ValidationError("The Mallard manifest is invalid or cannot be read.") from error
        if not matches:
            raise ValidationError("CSV hash, contract, count or monetary totals do not match the manifest.")
    return records, report
