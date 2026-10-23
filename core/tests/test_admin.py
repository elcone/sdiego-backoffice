from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import ChargeConcept, Contact, FeeConfiguration, House, HouseContact, PayerAccount


class AdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin", password="test-password"
        )
        cls.house = House.objects.create(number="001")
        cls.contact = Contact.objects.create(name="Alex Smith")

    def setUp(self):
        self.client.force_login(self.user)

    def test_all_models_registered_and_list_views_available(self):
        for model in [
            House,
            Contact,
            HouseContact,
            PayerAccount,
            ChargeConcept,
            FeeConfiguration,
        ]:
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)
                self.assertEqual(
                    self.client.get(
                        reverse(f"admin:core_{model._meta.model_name}_changelist")
                    ).status_code,
                    200,
                )

    def test_relationship_inlines(self):
        house_page = self.client.get(
            reverse("admin:core_house_change", args=[self.house.pk])
        )
        self.assertContains(house_page, "house_contacts-TOTAL_FORMS")
        self.assertContains(house_page, "Is responsible")
        contact_page = self.client.get(
            reverse("admin:core_contact_change", args=[self.contact.pk])
        )
        self.assertContains(contact_page, "payer_accounts-TOTAL_FORMS")
        self.assertContains(contact_page, "house_contacts-TOTAL_FORMS")
