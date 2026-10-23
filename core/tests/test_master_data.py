from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import ChargeConcept, Contact, House, HouseContact, PayerAccount


class MasterDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.contact = Contact.objects.create(name="Alex Smith")
        cls.house = House.objects.create(number="001")

    def assert_database_rejects(self, model, **values):
        with self.assertRaises(IntegrityError), transaction.atomic():
            model.objects.create(**values)

    def test_unique_house_numbers(self):
        self.assert_database_rejects(House, number="001")
        self.house.refresh_from_db()
        self.assertEqual(self.house.number, "001")

    def test_house_without_contacts(self):
        self.assertFalse(self.house.house_contacts.exists())

    def test_required_fields(self):
        for instance in [
            House(),
            Contact(),
            PayerAccount(contact=self.contact),
            ChargeConcept(),
        ]:
            with (
                self.subTest(model=type(instance).__name__),
                self.assertRaises(ValidationError),
            ):
                instance.full_clean()

    def test_optional_contact_attributes_and_duplicate_names(self):
        self.contact.full_clean()
        other = Contact.objects.create(name=self.contact.name)
        self.assertEqual((other.rfc, other.phone, other.email), ("", "", ""))

    def test_unique_house_contact(self):
        HouseContact.objects.create(house=self.house, contact=self.contact)
        self.assert_database_rejects(
            HouseContact, house=self.house, contact=self.contact, is_responsible=True
        )

    def test_multiple_responsible_contacts_and_multiple_houses(self):
        other = Contact.objects.create(name="Sam Smith")
        second_house = House.objects.create(number="002")
        for house, contact in [
            (self.house, self.contact),
            (self.house, other),
            (second_house, self.contact),
        ]:
            HouseContact.objects.create(
                house=house, contact=contact, is_responsible=True
            )
        self.assertEqual(
            self.house.house_contacts.filter(is_responsible=True).count(), 2
        )
        self.assertEqual(self.contact.house_contacts.count(), 2)

    def test_multiple_accounts_preserve_zeros_and_allow_other_banks(self):
        for bank, number in [
            ("Bank A", "000123"),
            ("Bank B", "000123"),
            ("Bank A", "000456"),
        ]:
            PayerAccount.objects.create(
                contact=self.contact, bank_name=bank, account_number=number
            )
        self.assertEqual(self.contact.payer_accounts.count(), 3)
        self.assertEqual(
            PayerAccount.objects.get(bank_name="Bank B").account_number, "000123"
        )

    def test_duplicate_account_prevention(self):
        values = dict(contact=self.contact, bank_name="Bank A", account_number="000123")
        PayerAccount.objects.create(**values)
        self.assert_database_rejects(PayerAccount, **values)

    def test_account_can_be_shared_by_contacts(self):
        other = Contact.objects.create(name="Sam Smith")
        for contact in [self.contact, other]:
            PayerAccount.objects.create(
                contact=contact, bank_name="Bank A", account_number="000123"
            )
        self.assertEqual(PayerAccount.objects.count(), 2)

    def test_unique_concept_codes_and_defaults(self):
        concept = ChargeConcept.objects.create(code="TEST", name="Test")
        self.assertFalse(concept.is_adjustment)
        self.assertTrue(concept.is_active)
        self.assert_database_rejects(ChargeConcept, code="TEST", name="Other")
