from datetime import date, datetime, timezone
from decimal import Decimal

from django.contrib import admin
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from treasury.models import BankTransaction, Charge, ChargeType, ExpenseType, House


class TreasuryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.house = House.objects.create(number=1)
        cls.expense_type = ExpenseType.objects.create(name="Maintenance")
        cls.charge_type = ChargeType.objects.create(name="Fee")
        cls.timestamp = datetime(2026, 10, 29, 12, 30, 45, 123456, tzinfo=timezone.utc)

    def bank_values(self, **overrides):
        return {
            "bank_account": "000123",
            "bank_receipt": "000456",
            "line_type": BankTransaction.LineType.CREDIT,
            "transaction_timestamp": self.timestamp,
            "amount": Decimal("-123.45"),
            "balance": Decimal("9876543210.98"),
            "concept": "Original transaction",
            **overrides,
        }

    def charge_values(self, **overrides):
        return {
            "house": self.house,
            "charge_type": self.charge_type,
            "period": date(2026, 10, 1),
            "due_date": date(2026, 10, 15),
            "amount": Decimal("123.45"),
            **overrides,
        }

    def test_creation_and_original_bank_values(self):
        bank = BankTransaction.objects.create(**self.bank_values())
        charge = Charge.objects.create(**self.charge_values())
        bank.refresh_from_db()
        charge.refresh_from_db()
        self.assertEqual(bank.description, "")
        self.assertIsNone(bank.house_id)
        self.assertIsNone(bank.expense_type_id)
        self.assertEqual(bank.amount, Decimal("-123.45"))
        self.assertEqual(bank.balance, Decimal("9876543210.98"))
        self.assertEqual(bank.transaction_timestamp, self.timestamp)
        self.assertEqual(bank.bank_account, "000123")
        self.assertEqual(charge.amount, Decimal("123.45"))
        self.assertEqual(charge.house, self.house)
        self.assertEqual(charge.charge_type, self.charge_type)

    def test_unique_constraints(self):
        BankTransaction.objects.create(**self.bank_values())
        for model, values in [
            (House, {"number": 1}),
            (ExpenseType, {"name": "Maintenance"}),
            (BankTransaction, self.bank_values()),
        ]:
            with self.subTest(model=model.__name__):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    model.objects.create(**values)
        for field in ["bank_account", "bank_receipt", "line_type"]:
            BankTransaction.objects.create(**self.bank_values(**{field: BankTransaction.LineType.DEBIT if field == "line_type" else "DIFFERENT"}))
        ChargeType.objects.create(name=self.charge_type.name)

    def test_foreign_keys_and_required_relations(self):
        for model, values in [
            (Charge, self.charge_values(house=None)),
            (Charge, self.charge_values(charge_type=None)),
        ]:
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    model.objects.create(**values)
        for model, values in [
            (Charge, {**self.charge_values(), "house_id": 999999}),
            (Charge, {**self.charge_values(), "charge_type_id": 999999}),
            (BankTransaction, self.bank_values(house_id=999999)),
            (BankTransaction, self.bank_values(expense_type_id=999999)),
        ]:
            if "house_id" in values:
                values.pop("house", None)
            if "charge_type_id" in values:
                values.pop("charge_type", None)
            with self.subTest(model=model.__name__, values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    model.objects.create(**values)
                    transaction.get_connection().check_constraints()

    def test_protected_bank_relations(self):
        bank = BankTransaction.objects.create(
            **self.bank_values(house=self.house, expense_type=self.expense_type)
        )
        bank.refresh_from_db()
        self.assertEqual(bank.house, self.house)
        self.assertEqual(bank.expense_type, self.expense_type)
        for instance in [self.house, self.expense_type]:
            with self.assertRaises(ProtectedError):
                instance.delete()

    def test_protected_charge_relations(self):
        Charge.objects.create(**self.charge_values())
        for instance in [self.house, self.charge_type]:
            with self.assertRaises(ProtectedError):
                instance.delete()

    def test_long_bank_concept(self):
        concept = "Long bank concept " * 100
        bank = BankTransaction.objects.create(**self.bank_values(concept=concept))
        bank.refresh_from_db()
        self.assertEqual(bank.concept, concept)

    def test_admin_registration(self):
        for model in [House, BankTransaction, Charge, ChargeType, ExpenseType]:
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site.is_registered(model))
                self.assertIsInstance(admin.site.get_model_admin(model), admin.ModelAdmin)

    def test_line_type_choices(self):
        self.assertEqual(
            BankTransaction.LineType.choices,
            [("DEBIT", "Cargo"), ("CREDIT", "Abono"),
             ("COMMISSION", "Comisión"), ("COMMISSION_VAT", "IVA de comisión")],
        )
        for value in BankTransaction.LineType.values:
            bank = BankTransaction(**self.bank_values(line_type=value))
            bank.full_clean()
