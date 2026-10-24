"""Synthetic fixtures exercise infrastructure, not a claimed BanBajío CSV layout."""

from datetime import date, time
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase

from core.bank_import import (
    parse_amount,
    parse_date,
    persist_transactions,
    read_csv_rows,
)
from core.models import BankTransaction


class BankTransactionTests(TestCase):
    def candidate(self, identity="synthetic:account-001:transaction-001", **overrides):
        values = {
            "bank_account": "0392369300201",
            "bank_receipt": identity,
            "line_type": "CREDIT",
            "transaction_time": time(12, 30, 0),
            "transaction_date": date(2026, 3, 1),
            "description": "Synthetic credit",
            "amount": Decimal("1234.56"),
            "transaction_code": identity,
            "source_identifiers": {"account": "0001", "test_id": identity},
            "source_values": {
                "test_id": identity,
                "test_amount": "1234.56",
                "test_date": "2026-03-01",
            },
            "source_file": "synthetic.csv",
            "source_file_sha256": "a" * 64,
            "source_row_number": 2,
        }
        values.update(overrides)
        return BankTransaction(**values)

    def test_valid_import_and_decimal_precision(self):
        self.assertEqual(connection.vendor, "postgresql")
        result = persist_transactions([self.candidate()])
        self.assertEqual(result, {"imported": 1, "unchanged": 0})
        stored = BankTransaction.objects.get()
        self.assertEqual(stored.amount, Decimal("1234.56"))
        self.assertIsInstance(stored.amount, Decimal)
        self.assertEqual(stored.source_identifiers["account"], "0001")
        self.assertEqual(stored.source_values["test_amount"], "1234.56")
        self.assertEqual(stored.source_file_sha256, "a" * 64)

    def test_signed_credits_and_debits(self):
        persist_transactions(
            [
                self.candidate(),
                self.candidate(
                    "synthetic:debit",
                    amount=Decimal("-25.01"),
                    line_type="DEBIT",
                    description="Synthetic debit",
                ),
            ]
        )
        amounts = list(
            BankTransaction.objects.order_by("amount").values_list("amount", flat=True)
        )
        self.assertEqual(amounts, [Decimal("-25.01"), Decimal("1234.56")])
        self.assertIn("+1234.56", str(BankTransaction.objects.get(amount__gt=0)))
        self.assertIn("-25.01", str(BankTransaction.objects.get(amount__lt=0)))

    def test_duplicate_records_in_batch_reject_entire_import(self):
        with self.assertRaisesMessage(
            ValidationError, "Duplicate transaction identity"
        ):
            persist_transactions(
                [self.candidate(), self.candidate(source_row_number=3)]
            )
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_database_identity_constraint(self):
        persist_transactions([self.candidate()])
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.candidate().save()

    def test_same_date_description_amount_do_not_define_identity(self):
        result = persist_transactions(
            [self.candidate(), self.candidate("synthetic:other-id")]
        )
        self.assertEqual(result["imported"], 2)

    def test_identical_reimport_preserves_original_audit_information(self):
        persist_transactions([self.candidate()])
        snapshot = BankTransaction.objects.values().get()
        result = persist_transactions(
            [
                self.candidate(
                    source_file="renamed.csv",
                    source_row_number=20,
                    source_file_sha256="b" * 64,
                )
            ]
        )
        self.assertEqual(result, {"imported": 0, "unchanged": 1})
        self.assertEqual(BankTransaction.objects.values().get(), snapshot)

    def test_conflicting_source_information_requires_manual_review(self):
        persist_transactions([self.candidate()])
        snapshot = BankTransaction.objects.values().get()
        for overrides in [
            {"description": "Changed"},
            {"amount": Decimal("1234.57")},
            {"transaction_date": date(2026, 3, 2)},
            {"source_identifiers": {"account": "0002"}},
            {"source_values": {"test_amount": "1,234.56"}},
        ]:
            with self.subTest(overrides=overrides):
                with self.assertRaisesMessage(ValidationError, "manual review"):
                    persist_transactions([self.candidate(**overrides)])
                self.assertEqual(BankTransaction.objects.values().get(), snapshot)

    def test_discrepancy_rolls_back_earlier_inserts(self):
        persist_transactions([self.candidate()])
        with self.assertRaisesMessage(ValidationError, "manual review"):
            persist_transactions(
                [
                    self.candidate("synthetic:new"),
                    self.candidate(description="Changed"),
                ]
            )
        self.assertEqual(BankTransaction.objects.count(), 1)
        self.assertFalse(
            BankTransaction.objects.filter(transaction_code="synthetic:new").exists()
        )

    def test_validation_errors_reject_entire_batch(self):
        for overrides in [
            {"transaction_date": "not-a-date"},
            {"amount": Decimal("1.001")},
            {"amount": "NaN"},
            {"amount": "Infinity"},
            {"description": " "},
            {"transaction_code": ""},
            {"source_values": {}},
            {"source_identifiers": {"id": ""}},
            {"source_identifiers": {"id": 123}},
            {"source_file_sha256": "invalid"},
            {"source_row_number": 0},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                persist_transactions(
                    [self.candidate("synthetic:new"), self.candidate(**overrides)]
                )
            self.assertEqual(BankTransaction.objects.count(), 0)

    def test_empty_import_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "no transactions"):
            persist_transactions([])

    def test_existing_instance_cannot_be_used_as_import_candidate(self):
        persist_transactions([self.candidate()])
        with self.assertRaisesMessage(ValidationError, "new BankTransaction"):
            persist_transactions([BankTransaction.objects.get()])

    def test_explicit_date_format(self):
        self.assertEqual(
            parse_date("31/03/2026", date_format="%d/%m/%Y"), date(2026, 3, 31)
        )
        for value in ["31/02/2026", "2026-03-31", "", None]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_date(value, date_format="%d/%m/%Y")

    def test_explicit_amount_formats(self):
        self.assertEqual(
            parse_amount("1,234.56", decimal_separator=".", thousands_separator=","),
            Decimal("1234.56"),
        )
        self.assertEqual(
            parse_amount("-1.234,56", decimal_separator=",", thousands_separator="."),
            Decimal("-1234.56"),
        )
        for value in [
            "",
            "NaN",
            "Infinity",
            "1.001",
            "12,34.56",
            "1e3",
            "$1.00",
            " 1.00",
            "1,00",
            "10000000000000000.00",
        ]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_amount(value, decimal_separator=".", thousands_separator=",")

    def test_csv_parsing_preserves_original_strings_and_quoted_values(self):
        rows = read_csv_rows(
            StringIO('test_id,test_description\n0001,"Original, description"\n'),
            required_columns=["test_id", "test_description"],
            delimiter=",",
        )
        self.assertEqual(
            rows,
            [(2, {"test_id": "0001", "test_description": "Original, description"})],
        )

    def test_invalid_file_structures(self):
        for content in [
            "",
            "test_id,test_amount\n",
            "test_id\n0001\n",
            "test_id,test_id\n1,1\n",
            "test_id,\n1,2\n",
            "test_id,test_amount\n1\n",
            "test_id,test_amount\n1,2,3\n",
            'test_id,test_amount\n1,"unterminated\n',
            "test_id,test_amount\n,1.00\n",
            "test_id,test_amount\n1, \n",
        ]:
            with self.subTest(content=content), self.assertRaises(ValidationError):
                read_csv_rows(
                    StringIO(content),
                    required_columns=["test_id", "test_amount"],
                    delimiter=",",
                )
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_synthetic_parse_validate_persist_pipeline(self):
        content = "test_id,test_date,test_amount,test_description\n0001,2026-03-01,12.34,Test\n"
        rows = read_csv_rows(
            StringIO(content),
            delimiter=",",
            required_columns=[
                "test_id",
                "test_date",
                "test_amount",
                "test_description",
            ],
        )
        candidates = [
            self.candidate(
                identity=f"synthetic:{row['test_id']}",
                transaction_date=parse_date(row["test_date"], date_format="%Y-%m-%d"),
                amount=parse_amount(
                    row["test_amount"], decimal_separator=".", thousands_separator=None
                ),
                description=row["test_description"],
                source_values=row,
                source_row_number=line,
            )
            for line, row in rows
        ]
        self.assertEqual(
            persist_transactions(candidates), {"imported": 1, "unchanged": 0}
        )
        self.assertEqual(BankTransaction.objects.get().source_values, rows[0][1])

    def test_command_rejects_unknown_layout(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "unverified.csv"
            path.write_text("unknown,columns\n1,2\n")
            with self.assertRaisesMessage(CommandError, "25 Mallard"):
                call_command("import_bank_transactions", str(path))
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_command_reports_missing_file(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesMessage(CommandError, "cannot be read"):
                call_command(
                    "import_bank_transactions", str(Path(directory) / "missing.csv")
                )

    def test_admin_keeps_source_and_audit_fields_read_only(self):
        model_admin = admin.site._registry[BankTransaction]
        request = RequestFactory().get("/admin/")
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertSetEqual(
            set(model_admin.readonly_fields),
            {
                field.name
                for field in BankTransaction._meta.fields
                if field.name != "id"
            },
        )
