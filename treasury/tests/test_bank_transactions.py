from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from treasury.models import BankTransaction


class BankTransactionTests(TestCase):
    def test_identity_preserves_text_and_separates_types_and_accounts(self):
        for kind in BankTransaction.LineType:
            bank = BankTransaction.objects.create(
                bank_account="000123",
                bank_receipt="000045",
                line_type=kind,
                transaction_date=date(2026, 10, 1),
                amount="100.00",
            )
            bank.refresh_from_db()
            self.assertEqual(bank.bank_account, "000123")
            self.assertEqual(bank.bank_receipt, "000045")
            self.assertEqual(bank.amount, Decimal("100.00"))
        BankTransaction.objects.create(
            bank_account="000124",
            bank_receipt="000045",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="100.00",
        )
        with self.assertRaises(ValidationError):
            BankTransaction.objects.create(
                bank_account="000123",
                bank_receipt="000045",
                line_type="CREDIT",
                transaction_date=date(2026, 10, 1),
                amount="100.00",
            )

    def test_required_fields_amount_and_type(self):
        base = {
            "bank_account": "001",
            "bank_receipt": "002",
            "line_type": "CREDIT",
            "transaction_date": date(2026, 10, 1),
            "amount": "10.00",
        }
        for change in (
            {"bank_account": ""},
            {"bank_receipt": "  "},
            {"transaction_date": None},
            {"amount": "0.00"},
            {"amount": "-1.00"},
            {"line_type": "OTHER"},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                BankTransaction.objects.create(**{**base, **change})

    def test_database_constraints(self):
        base = {
            "bank_account": "001",
            "bank_receipt": "002",
            "line_type": "CREDIT",
            "transaction_date": date(2026, 10, 1),
            "amount": "10.00",
        }
        BankTransaction.objects.create(**base)
        for change in (
            {},
            {"line_type": "OTHER"},
            {"amount": "0.00"},
            {"amount": "-1.00"},
            {"bank_account": "  "},
            {"bank_receipt": "\t"},
        ):
            with (
                self.subTest(change=change),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                BankTransaction.objects.bulk_create(
                    [BankTransaction(**{**base, **change})]
                )

    def test_no_reference_or_redundant_fields(self):
        self.assertEqual(
            {f.name for f in BankTransaction._meta.fields},
            {
                "id",
                "created_at",
                "updated_at",
                "bank_account",
                "bank_receipt",
                "line_type",
                "transaction_date",
                "description",
                "amount",
            },
        )
