from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from treasury.models import (
    BankTransaction,
    Charge,
    ChargeConcept,
    House,
    Payment,
    PaymentApplication,
)


class PaymentApplicationTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(number="001")
        self.concept = ChargeConcept.objects.create(name="Cuota", kind="ORDINARY_FEE")
        self.adjustment = ChargeConcept.objects.create(name="Ajuste", kind="ADJUSTMENT")
        self.charge = Charge.objects.create(
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
        self.payment = Payment.objects.create(house=self.house, bank_transaction=bank)

    def test_partial_applications_edit_and_delete(self):
        application = PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="40.00"
        )
        self.assertEqual(self.payment.available_amount, Decimal("110.00"))
        application.amount = "60.00"
        application.save()
        self.assertEqual(self.charge.applied_amount, Decimal("60.00"))
        application.delete()
        self.assertEqual(self.payment.available_amount, Decimal("150.00"))

    def test_unique_pair_and_database_positive_amount(self):
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="10.00"
        )
        with self.assertRaises(ValidationError):
            PaymentApplication.objects.create(
                payment=self.payment, charge=self.charge, amount="10.00"
            )
        for amount in ("10.00", "0.00", "-1.00"):
            with (
                self.subTest(amount=amount),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                PaymentApplication.objects.bulk_create(
                    [
                        PaymentApplication(
                            payment=self.payment, charge=self.charge, amount=amount
                        )
                    ]
                )

    def test_other_house_and_nonpositive_charges_are_rejected(self):
        other_house = House.objects.create(number="002")
        other = Charge.objects.create(
            house=other_house,
            concept=self.concept,
            amount="100.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        negative = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.charge,
            amount="-200.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        zero = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="0.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        for charge in (other, negative, zero, self.charge):
            with self.subTest(charge=charge.pk), self.assertRaises(ValidationError):
                PaymentApplication.objects.create(
                    payment=self.payment, charge=charge, amount="1.00"
                )

    def test_payment_funds_across_multiple_charges(self):
        other = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="100.00"
        )
        with self.assertRaises(ValidationError):
            PaymentApplication.objects.create(
                payment=self.payment, charge=other, amount="51.00"
            )
        PaymentApplication.objects.create(
            payment=self.payment, charge=other, amount="50.00"
        )

    def test_charge_capacity_across_multiple_payments(self):
        bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="002",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="100.00",
        )
        payment = Payment.objects.create(house=self.house, bank_transaction=bank)
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="60.00"
        )
        with self.assertRaises(ValidationError):
            PaymentApplication.objects.create(
                payment=payment, charge=self.charge, amount="41.00"
            )
        PaymentApplication.objects.create(
            payment=payment, charge=self.charge, amount="40.00"
        )

    def test_positive_adjustment_has_independent_capacity(self):
        positive = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.charge,
            amount="30.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        self.assertEqual(self.charge.exigible_amount, Decimal("100.00"))
        self.assertEqual(positive.exigible_amount, Decimal("30.00"))
        PaymentApplication.objects.create(
            payment=self.payment, charge=positive, amount="30.00"
        )
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="100.00"
        )

    def test_negative_adjustment_reduces_only_original_exigible(self):
        Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.charge,
            amount="-30.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        self.assertEqual(self.charge.exigible_amount, Decimal("70.00"))
        with self.assertRaises(ValidationError):
            PaymentApplication.objects.create(
                payment=self.payment, charge=self.charge, amount="70.01"
            )
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="70.00"
        )

    def test_charge_mutations_must_preserve_applications(self):
        application = PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="80.00"
        )
        self.charge.amount = Decimal("79.00")
        with self.assertRaises(ValidationError):
            self.charge.save()
        self.charge.amount = Decimal("100.00")
        self.charge.house = House.objects.create(number="002")
        with self.assertRaises(ValidationError):
            self.charge.save()
        self.charge.refresh_from_db()
        with self.assertRaises(ProtectedError):
            self.charge.delete()
        application.amount = "40.00"
        application.save()
        self.charge.amount = "50.00"
        self.charge.save()

    def test_adjustments_reject_overapplication_without_rewriting_applications(self):
        application = PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="80.00"
        )
        negative = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.charge,
            amount="-20.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        negative.amount = "-200.00"
        with self.assertRaises(ValidationError):
            negative.save()
        application.refresh_from_db()
        self.assertEqual(application.amount, Decimal("80.00"))
        application.delete()
        negative.save()
        self.assertEqual(self.charge.exigible_amount, 0)

    def test_moving_a_negative_adjustment_checks_new_original(self):
        other = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        negative = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=other,
            amount="-30.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="80.00"
        )
        negative.adjusts_charge = self.charge
        with self.assertRaises(ValidationError):
            negative.save()

    def test_application_can_move_without_exceeding_limits(self):
        other = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        application = PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="40.00"
        )
        application.charge = other
        application.save()
        self.assertEqual(self.charge.applied_amount, 0)
        self.assertEqual(other.applied_amount, Decimal("40.00"))

    def test_positive_adjustment_cannot_turn_negative_after_application(self):
        positive = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.charge,
            amount="30.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        PaymentApplication.objects.create(
            payment=self.payment, charge=positive, amount="20.00"
        )
        positive.amount = "-30.00"
        with self.assertRaises(ValidationError):
            positive.save()
