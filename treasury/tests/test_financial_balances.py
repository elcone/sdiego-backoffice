from datetime import date
from decimal import Decimal

from django.test import TestCase

from treasury.models import (
    BankTransaction,
    Charge,
    ChargeConcept,
    House,
    Payment,
    PaymentApplication,
)


class FinancialBalanceTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(number="001")
        self.concept = ChargeConcept.objects.create(name="Cuota", kind="ORDINARY_FEE")
        self.adjustment = ChargeConcept.objects.create(name="Ajuste", kind="ADJUSTMENT")

    def test_empty_house(self):
        self.assertEqual(
            self.house.financial_balances(),
            {"obligations": 0, "unapplied_credit": 0, "net": 0},
        )

    def test_applications_do_not_double_discount_global_balance(self):
        charge = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 10, 1),
            due_date=date(2026, 10, 5),
        )
        bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="001",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="150.00",
        )
        payment = Payment.objects.create(house=self.house, bank_transaction=bank)
        self.assertEqual(
            self.house.financial_balances(),
            {
                "obligations": Decimal("100.00"),
                "unapplied_credit": Decimal("150.00"),
                "net": Decimal("-50.00"),
            },
        )
        application = PaymentApplication.objects.create(
            payment=payment, charge=charge, amount="40.00"
        )
        self.assertEqual(
            self.house.financial_balances(),
            {
                "obligations": Decimal("60.00"),
                "unapplied_credit": Decimal("110.00"),
                "net": Decimal("-50.00"),
            },
        )
        application.delete()
        self.assertEqual(self.house.financial_balances()["net"], Decimal("-50.00"))

    def test_excess_negative_adjustments_remain_signed_and_positive_adjustments_independent(
        self,
    ):
        charge = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 10, 1),
            due_date=date(2026, 10, 5),
        )
        positive = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=charge,
            amount="20.00",
            period=charge.period,
            due_date=charge.due_date,
        )
        Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=charge,
            amount="-150.00",
            period=charge.period,
            due_date=charge.due_date,
        )
        bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="001",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="300.00",
        )
        payment = Payment.objects.create(house=self.house, bank_transaction=bank)
        self.assertEqual(
            self.house.financial_balances(),
            {
                "obligations": Decimal("20.00"),
                "unapplied_credit": Decimal("300.00"),
                "net": Decimal("-330.00"),
            },
        )
        PaymentApplication.objects.create(
            payment=payment, charge=positive, amount="20.00"
        )
        self.assertEqual(
            self.house.financial_balances(),
            {
                "obligations": 0,
                "unapplied_credit": Decimal("280.00"),
                "net": Decimal("-330.00"),
            },
        )

    def test_multiple_applications_do_not_multiply_aggregates(self):
        first = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 10, 1),
            due_date=date(2026, 10, 5),
        )
        second = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="200.00",
            period=first.period,
            due_date=first.due_date,
        )
        for receipt in ("001", "002"):
            bank = BankTransaction.objects.create(
                bank_account="001",
                bank_receipt=receipt,
                line_type="CREDIT",
                transaction_date=date(2026, 10, 1),
                amount="100.00",
            )
            payment = Payment.objects.create(house=self.house, bank_transaction=bank)
            PaymentApplication.objects.create(
                payment=payment, charge=first, amount="25.00"
            )
            PaymentApplication.objects.create(
                payment=payment, charge=second, amount="25.00"
            )
        self.assertEqual(
            self.house.financial_balances(),
            {
                "obligations": Decimal("200.00"),
                "unapplied_credit": Decimal("100.00"),
                "net": Decimal("100.00"),
            },
        )
        other = House.objects.create(number="002")
        self.assertEqual(
            other.financial_balances(),
            {"obligations": 0, "unapplied_credit": 0, "net": 0},
        )
