from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from treasury.models import Contact, House, HouseContact, PayerAccount, TimestampedModel
from treasury.querysets import HouseContactQuerySet, PayerAccountQuerySet


class TreasuryModelsTests(TestCase):
    def test_timestamp_inheritance_and_form_fields(self):
        from treasury.forms import (
            ContactForm,
            HouseContactForm,
            HouseForm,
            PayerAccountForm,
        )

        self.assertTrue(TimestampedModel._meta.abstract)
        for model, form_class in (
            (House, HouseForm),
            (Contact, ContactForm),
            (HouseContact, HouseContactForm),
            (PayerAccount, PayerAccountForm),
        ):
            self.assertTrue(issubclass(model, TimestampedModel))
            self.assertTrue(model._meta.get_field("created_at").auto_now_add)
            self.assertTrue(model._meta.get_field("updated_at").auto_now)
            self.assertNotIn("created_at", form_class.base_fields)
            self.assertNotIn("updated_at", form_class.base_fields)
            self.assertTrue(
                all(not field.help_text for field in form_class.base_fields.values())
            )
        self.assertIsInstance(HouseContact.objects.all(), HouseContactQuerySet)

    def setUp(self):
        self.house = House.objects.create(number="0101")
        self.contact = Contact.objects.create(
            name="Ana López", email="ana@example.com", phone="5550101"
        )

    def test_house_contact_queryset_keeps_inactive_rows_visible(self):
        relation = HouseContact.objects.create(
            house=self.house, contact=self.contact, is_responsible=True
        )
        self.assertEqual(HouseContact.objects.count(), 1)
        self.assertEqual(HouseContact.objects.active().count(), 1)
        self.assertEqual(HouseContact.objects.responsible().count(), 1)
        relation.is_active = False
        relation.save()
        self.assertEqual(HouseContact.objects.count(), 1)
        self.assertEqual(HouseContact.objects.active().count(), 0)
        self.assertEqual(HouseContact.objects.responsible().count(), 0)
        self.assertEqual(HouseContact.objects.for_house(self.house).count(), 1)
        self.assertEqual(
            HouseContact.objects.with_related().get(pk=relation.pk).house,
            self.house,
        )

    def test_house_contact_cannot_be_duplicated(self):
        HouseContact.objects.create(house=self.house, contact=self.contact)
        with self.assertRaises(IntegrityError):
            HouseContact.objects.create(house=self.house, contact=self.contact)

    def test_payer_account_preserves_leading_zeroes_and_is_unique(self):
        account = PayerAccount.objects.create(
            contact=self.contact, bank="Banco", account_number="001234"
        )
        self.assertEqual(account.account_number, "001234")
        with self.assertRaises(IntegrityError):
            PayerAccount.objects.create(
                contact=self.contact, bank="Banco", account_number="001234"
            )

    def test_payer_account_with_related_loads_contact_in_one_query(self):
        account = PayerAccount.objects.create(
            contact=self.contact, bank="Banco", account_number="00123"
        )
        self.assertIsInstance(PayerAccount.objects.all(), PayerAccountQuerySet)
        with self.assertNumQueries(1):
            loaded = PayerAccount.objects.with_related().get(pk=account.pk)
            self.assertEqual(loaded.contact.name, self.contact.name)

    def test_related_records_protect_house_and_contact_deletion(self):
        relation = HouseContact.objects.create(house=self.house, contact=self.contact)
        with self.assertRaises(ProtectedError):
            self.house.delete()
        with self.assertRaises(ProtectedError):
            self.contact.delete()
        relation.delete()
        PayerAccount.objects.create(
            contact=self.contact, bank="Banco", account_number="123"
        )
        with self.assertRaises(ProtectedError):
            self.contact.delete()
