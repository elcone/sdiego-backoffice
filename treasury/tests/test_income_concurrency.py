from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TransactionTestCase

from treasury.models import (
    BankTransaction,
    Charge,
    ChargeConcept,
    House,
    Payment,
    PaymentApplication,
)


class IncomeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.house = House.objects.create(number="001")
        self.concept = ChargeConcept.objects.create(name="Cuota", kind="ORDINARY_FEE")
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
            amount="100.00",
        )
        self.payment = Payment.objects.create(house=self.house, bank_transaction=bank)

    def run_concurrently(self, operations):
        barrier = Barrier(len(operations))

        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                operation()
                return "saved"
            except ValidationError:
                return "rejected"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(run, operations))

    def test_shared_payment_cannot_be_overapplied(self):
        other = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=self.charge.period,
            due_date=self.charge.due_date,
        )
        results = self.run_concurrently(
            [
                lambda: PaymentApplication.objects.create(
                    payment_id=self.payment.pk, charge_id=self.charge.pk, amount="80.00"
                ),
                lambda: PaymentApplication.objects.create(
                    payment_id=self.payment.pk, charge_id=other.pk, amount="80.00"
                ),
            ]
        )
        self.assertCountEqual(results, ["saved", "rejected"])
        self.assertEqual(self.payment.applied_amount, 80)

    def test_shared_charge_cannot_be_overapplied(self):
        bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="002",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="100.00",
        )
        other = Payment.objects.create(house=self.house, bank_transaction=bank)
        results = self.run_concurrently(
            [
                lambda: PaymentApplication.objects.create(
                    payment_id=self.payment.pk, charge_id=self.charge.pk, amount="80.00"
                ),
                lambda: PaymentApplication.objects.create(
                    payment_id=other.pk, charge_id=self.charge.pk, amount="80.00"
                ),
            ]
        )
        self.assertCountEqual(results, ["saved", "rejected"])
        self.assertEqual(self.charge.applied_amount, 80)

    def test_duplicate_payment_identification_is_rejected(self):
        bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="002",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="100.00",
        )
        operation = lambda: Payment.objects.create(
            house_id=self.house.pk, bank_transaction_id=bank.pk
        )
        self.assertCountEqual(
            self.run_concurrently([operation, operation]), ["saved", "rejected"]
        )
        self.assertEqual(Payment.objects.filter(bank_transaction=bank).count(), 1)

    def test_charge_edit_and_application_are_serialized(self):
        def reduce_charge():
            charge = Charge.objects.get(pk=self.charge.pk)
            charge.amount = "60.00"
            charge.save()

        results = self.run_concurrently(
            [
                reduce_charge,
                lambda: PaymentApplication.objects.create(
                    payment_id=self.payment.pk, charge_id=self.charge.pk, amount="80.00"
                ),
            ]
        )
        self.assertCountEqual(results, ["saved", "rejected"])
        self.charge.refresh_from_db()
        self.assertLessEqual(self.charge.applied_amount, self.charge.exigible_amount)

    def test_negative_adjustment_and_application_are_serialized(self):
        adjustment = ChargeConcept.objects.create(name="Ajuste", kind="ADJUSTMENT")
        results = self.run_concurrently(
            [
                lambda: Charge.objects.create(
                    house_id=self.house.pk,
                    concept_id=adjustment.pk,
                    adjusts_charge_id=self.charge.pk,
                    amount="-50.00",
                    period=self.charge.period,
                    due_date=self.charge.due_date,
                ),
                lambda: PaymentApplication.objects.create(
                    payment_id=self.payment.pk, charge_id=self.charge.pk, amount="80.00"
                ),
            ]
        )
        self.assertCountEqual(results, ["saved", "rejected"])
        self.assertLessEqual(self.charge.applied_amount, self.charge.exigible_amount)

    def test_house_change_and_application_are_serialized(self):
        house = House.objects.create(number="002")

        def move_payment():
            payment = Payment.objects.get(pk=self.payment.pk)
            payment.house_id = house.pk
            payment.save()

        results = self.run_concurrently(
            [
                move_payment,
                lambda: PaymentApplication.objects.create(
                    payment_id=self.payment.pk, charge_id=self.charge.pk, amount="80.00"
                ),
            ]
        )
        self.assertCountEqual(results, ["saved", "rejected"])
