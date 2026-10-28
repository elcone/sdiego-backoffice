from datetime import date

from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from treasury.models import (
    BankTransaction,
    Charge,
    ChargeConcept,
    Contact,
    House,
    HouseContact,
    PayerAccount,
    Payment,
    PaymentApplication,
)


class PaymentTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(number="001")
        self.other_house = House.objects.create(number="002")
        self.bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="001",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="100.00",
        )
        self.other_bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="002",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="200.00",
        )
        self.payment = Payment.objects.create(
            house=self.house, bank_transaction=self.bank
        )
        self.concept = ChargeConcept.objects.create(name="Cuota", kind="ORDINARY_FEE")
        self.charge = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 10, 1),
            due_date=date(2026, 10, 5),
        )

    def test_unapplied_payment_and_derived_amount(self):
        self.assertEqual(self.payment.amount, self.bank.amount)
        self.assertEqual(self.payment.available_amount, self.bank.amount)
        self.assertEqual(self.payment.applied_amount, 0)
        self.assertNotIn("amount", {f.name for f in Payment._meta.fields})

    def test_bank_transaction_is_unique_and_credit_only(self):
        with self.assertRaises(ValidationError):
            Payment.objects.create(house=self.other_house, bank_transaction=self.bank)
        for kind in ("DEBIT", "COMMISSION", "VAT"):
            bank = BankTransaction.objects.create(
                bank_account="001",
                bank_receipt=kind,
                line_type=kind,
                transaction_date=date(2026, 10, 1),
                amount="50.00",
            )
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                Payment.objects.create(house=self.house, bank_transaction=bank)

    def test_payer_can_have_any_existing_house_contact_relationship(self):
        contact = Contact.objects.create(name="Contacto")
        payer = PayerAccount.objects.create(
            contact=contact, bank="Banco", account_number="001"
        )
        self.payment.payer_account = payer
        with self.assertRaises(ValidationError):
            self.payment.save()
        relation = HouseContact.objects.create(
            house=self.house, contact=contact, is_active=False, is_responsible=False
        )
        self.payment.save()
        relation.is_active = True
        relation.save()
        self.payment.save()
        relation.is_responsible = True
        relation.save()
        self.payment.save()

    def test_house_and_movement_can_change_without_applications(self):
        self.payment.house = self.other_house
        self.payment.bank_transaction = self.other_bank
        self.payment.save()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.house, self.other_house)
        self.assertEqual(self.payment.amount, self.other_bank.amount)

    def test_house_and_movement_cannot_change_with_applications(self):
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="50.00"
        )
        self.payment.house = self.other_house
        with self.assertRaises(ValidationError):
            self.payment.save()
        self.payment.house = self.house
        self.payment.bank_transaction = self.other_bank
        with self.assertRaises(ValidationError):
            self.payment.save()

    def test_payer_can_be_corrected_with_applications(self):
        application = PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="50.00"
        )
        contact = Contact.objects.create(name="Titular")
        payer = PayerAccount.objects.create(
            contact=contact, bank="Banco", account_number="001"
        )
        HouseContact.objects.create(house=self.house, contact=contact, is_active=False)
        self.payment.payer_account = payer
        self.payment.save()
        self.assertEqual(self.payment.payer_account, payer)
        application.refresh_from_db()
        self.assertEqual(str(application.amount), "50.00")
        self.payment.payer_account = None
        self.payment.save()

    def test_delete_requires_no_applications(self):
        application = PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="50.00"
        )
        with self.assertRaises(ProtectedError):
            self.payment.delete()
        application.delete()
        self.payment.delete()
        self.assertTrue(BankTransaction.objects.filter(pk=self.bank.pk).exists())

    def test_bank_mutations_cannot_invalidate_payments(self):
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="70.00"
        )
        self.bank.amount = "60.00"
        with self.assertRaises(ValidationError):
            self.bank.save()
        self.bank.amount = "100.00"
        self.bank.line_type = "DEBIT"
        with self.assertRaises(ValidationError):
            self.bank.save()

    def test_protected_references(self):
        for obj in (self.house, self.bank):
            with self.assertRaises(ProtectedError):
                obj.delete()
        contact = Contact.objects.create(name="Titular")
        payer = PayerAccount.objects.create(
            contact=contact, bank="Banco", account_number="001"
        )
        HouseContact.objects.create(house=self.house, contact=contact)
        self.payment.payer_account = payer
        self.payment.save()
        with self.assertRaises(ProtectedError):
            payer.delete()
