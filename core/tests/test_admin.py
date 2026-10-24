from datetime import date, time
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from core.models import (
    BankTransaction,
    ChargeConcept,
    Contact,
    FeeConfiguration,
    House,
    HouseContact,
    PayerAccount,
)


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
            BankTransaction,
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


class BankTransactionAdminReadOnlyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = get_user_model().objects.create_superuser(
            username="bank-admin", password="test-password"
        )
        cls.viewer = get_user_model().objects.create_user(username="bank-viewer", is_staff=True)
        cls.viewer.user_permissions.add(Permission.objects.get(
            content_type__app_label="core", codename="view_banktransaction"
        ))
        cls.change_user = get_user_model().objects.create_user(username="bank-change", is_staff=True)
        cls.change_user.user_permissions.add(Permission.objects.get(
            content_type__app_label="core", codename="change_banktransaction"
        ))
        cls.no_access_user = get_user_model().objects.create_user(username="bank-no-access", is_staff=True)
        cls.bank_transaction = BankTransaction.objects.create(
            bank_account="0392369300201", bank_receipt="000123", line_type="CREDIT",
            transaction_date=date(2026, 3, 1), transaction_time=time(12, 34, 56),
            description="Synthetic bank credit", amount=Decimal("1000.00"),
            transaction_code="BT-" + "a" * 64,
            source_identifiers={"bank_receipt": "000123", "tracking_key": "0000456"},
            source_values={"amount": "1000.00", "bank_balance": "2000.00"},
            source_file="synthetic.csv", source_file_sha256="a" * 64, source_row_number=2,
        )

    def setUp(self):
        self.client.force_login(self.superuser)
        self.snapshot = BankTransaction.objects.values().get(pk=self.bank_transaction.pk)
        self.change_url = reverse("admin:core_banktransaction_change", args=[self.bank_transaction.pk])
        self.list_url = reverse("admin:core_banktransaction_changelist")

    def test_authorized_users_can_view_list_and_detail_without_save_controls(self):
        for user in [self.superuser, self.viewer, self.change_user]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.list_url).status_code, 200)
                detail = self.client.get(self.change_url)
                self.assertContains(detail, "Synthetic bank credit")
                self.assertNotContains(detail, 'name="_save"')
                self.assertEqual(BankTransaction.objects.values().get(), self.snapshot)

    def test_unauthorized_staff_cannot_view_bank_transactions(self):
        self.client.force_login(self.no_access_user)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(self.client.get(self.change_url).status_code, 403)

    def test_change_posts_cannot_modify_bank_metadata_or_audit_fields(self):
        payloads = [
            {"_save": "Save"},
            {
                "_save": "Save", "bank_account": "0000000000000", "bank_receipt": "CHANGED",
                "line_type": "DEBIT", "transaction_date": "2000-01-01", "transaction_time": "00:00:00",
                "description": "Changed", "amount": "-0.01", "transaction_code": "BT-" + "b" * 64,
                "source_identifiers": '{"tracking_key":"CHANGED"}',
                "source_values": '{"amount":"0.01","bank_balance":"0.00"}',
                "source_file": "changed.csv", "source_file_sha256": "b" * 64,
                "source_row_number": "99", "created_at": "2000-01-01T00:00:00Z",
                "updated_at": "2000-01-01T00:00:00Z",
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(self.change_url, payload)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(BankTransaction.objects.values().get(), self.snapshot)
                self.bank_transaction.refresh_from_db()
                self.assertEqual(self.bank_transaction.created_at, self.snapshot["created_at"])
                self.assertEqual(self.bank_transaction.updated_at, self.snapshot["updated_at"])

    def test_add_and_delete_requests_are_forbidden(self):
        for url in [
            reverse("admin:core_banktransaction_add"),
            reverse("admin:core_banktransaction_delete", args=[self.bank_transaction.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
                self.assertEqual(BankTransaction.objects.count(), 1)
                self.assertEqual(BankTransaction.objects.values().get(), self.snapshot)

    def test_bulk_delete_cannot_remove_imported_transactions(self):
        self.client.post(self.list_url, {
            "action": "delete_selected", "_selected_action": [str(self.bank_transaction.pk)], "post": "yes",
        })
        self.assertEqual(BankTransaction.objects.count(), 1)
        self.assertEqual(BankTransaction.objects.values().get(), self.snapshot)
