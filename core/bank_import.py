"""Source-independent validation and atomic persistence; no bank layout is assumed."""

import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.db import IntegrityError, transaction

from .models import BankTransaction

MATCH_FIELDS = ("bank_account", "bank_receipt", "line_type")
SOURCE_FIELDS = (
    "transaction_code",
    "transaction_time",
    "transaction_date",
    "description",
    "amount",
    "source_identifiers",
    "source_values",
)
PROVENANCE_FIELDS = ("source_file", "source_file_sha256", "source_row_number")


def read_csv_rows(stream, *, required_columns, delimiter):
    """Read a layout with explicitly supplied headers and delimiter, preserving values."""
    required_columns = tuple(required_columns)
    reader = csv.reader(stream, delimiter=delimiter, strict=True)
    try:
        headers = next(reader, None)
        if not headers or any(not column.strip() for column in headers):
            raise ValidationError("The CSV must have a nonempty header row.")
        if len(headers) != len(set(headers)):
            raise ValidationError("The CSV contains duplicate column names.")
        if not set(required_columns).issubset(headers):
            raise ValidationError("The CSV is missing required columns.")
        rows = []
        for values in reader:
            if len(values) != len(headers):
                raise ValidationError(
                    f"CSV line {reader.line_num}: incorrect number of columns."
                )
            row = dict(zip(headers, values))
            if any(not row[column].strip() for column in required_columns):
                raise ValidationError(
                    f"CSV line {reader.line_num}: required values are missing."
                )
            rows.append((reader.line_num, row))
        if not rows:
            raise ValidationError("The CSV contains no transactions.")
        return rows
    except csv.Error as error:
        raise ValidationError(
            f"Invalid CSV structure near line {reader.line_num}."
        ) from error


def parse_date(value, *, date_format):
    try:
        return datetime.strptime(value, date_format).date()
    except (ValueError, TypeError) as error:
        raise ValidationError("Invalid transaction date.") from error


def parse_amount(value, *, decimal_separator, thousands_separator):
    """Parse only a specified numeric format; never infer locale or round cents."""
    if decimal_separator not in (".", ",") or thousands_separator not in (
        None,
        ".",
        ",",
        " ",
    ):
        raise ValueError("Unsupported monetary separators.")
    if thousands_separator == decimal_separator:
        raise ValueError("Monetary separators must differ.")
    if not isinstance(value, str):
        raise ValidationError("Invalid transaction amount.")
    integer = r"\d+"
    if thousands_separator:
        separator = re.escape(thousands_separator)
        integer = rf"(?:\d+|\d{{1,3}}(?:{separator}\d{{3}})+)"
    pattern = rf"[+-]?{integer}(?:{re.escape(decimal_separator)}\d{{1,2}})?"
    if not re.fullmatch(pattern, value):
        raise ValidationError("Invalid transaction amount or monetary precision.")
    normalized = (
        value.replace(thousands_separator, "") if thousands_separator else value
    )
    try:
        amount = Decimal(normalized.replace(decimal_separator, "."))
        DecimalValidator(18, 2)(amount)
        return amount
    except (InvalidOperation, ValidationError) as error:
        raise ValidationError(
            "Invalid transaction amount or monetary precision."
        ) from error


def validate_transactions(records):
    """Validate normalized candidates produced by a verified bank adapter."""
    records = list(records)
    if not records:
        raise ValidationError("The file contains no transactions.")
    errors = []
    identities = set()
    for index, record in enumerate(records, start=1):
        if (
            not isinstance(record, BankTransaction)
            or not record._state.adding
            or record.pk is not None
        ):
            raise ValidationError(
                "Import candidates must be new BankTransaction instances."
            )
        try:
            record.full_clean(validate_unique=False, validate_constraints=False)
            if (record.line_type == "CREDIT" and record.amount <= 0) or (
                record.line_type != "CREDIT" and record.amount >= 0
            ):
                raise ValidationError("The signed amount contradicts line_type.")
            if (
                any(not getattr(record, field).strip() for field in MATCH_FIELDS)
                or not record.description.strip()
                or not record.transaction_code.strip()
                or not record.source_file.strip()
            ):
                raise ValidationError(
                    "Transaction description, identity and source filename are required."
                )
            for field in ["source_values", "source_identifiers"]:
                values = getattr(record, field)
                if (
                    not isinstance(values, dict)
                    or not values
                    or any(
                        not isinstance(key, str)
                        or not key.strip()
                        or not isinstance(value, str)
                        for key, value in values.items()
                    )
                ):
                    raise ValidationError(
                        f"{field} must preserve source strings in a nonempty object."
                    )
            if any(not value.strip() for value in record.source_identifiers.values()):
                raise ValidationError("Source identifiers cannot be empty.")
            if not re.fullmatch(r"[0-9a-f]{64}", record.source_file_sha256):
                raise ValidationError(
                    "The source file must have a valid SHA-256 digest."
                )
            identity = tuple(getattr(record, field) for field in MATCH_FIELDS)
            if identity in identities:
                raise ValidationError("Duplicate transaction identity within the file.")
            identities.add(identity)
        except ValidationError as error:
            errors.extend(f"Record {index}: {message}" for message in error.messages)
    if errors:
        raise ValidationError(errors)
    return records


def source_differs(existing, candidate):
    return any(getattr(existing, field) != getattr(candidate, field) for field in SOURCE_FIELDS)


def _check_source(existing, candidate):
    if source_differs(existing, candidate):
        raise ValidationError(
            f"Source discrepancy at row {candidate.source_row_number}; manual review is required. "
            "No transactions were imported."
        )


def persist_transactions(records):
    """All-or-nothing persistence, including concurrent reimports of a verified identity."""
    records = validate_transactions(records)
    imported = 0
    unchanged = 0
    try:
        with transaction.atomic():
            for record in records:
                defaults = {
                    field: getattr(record, field)
                    for field in SOURCE_FIELDS + PROVENANCE_FIELDS
                }
                existing, created = BankTransaction.objects.get_or_create(
                    **{field: getattr(record, field) for field in MATCH_FIELDS},
                    defaults=defaults,
                )
                if created:
                    imported += 1
                else:
                    _check_source(existing, record)
                    unchanged += 1
    except IntegrityError as error:
        raise ValidationError(
            "A bank identity or external reference conflicts with stored data; manual review is required. "
            "No transactions were imported."
        ) from error
    return {"imported": imported, "unchanged": unchanged}
