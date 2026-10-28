from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from treasury.models import (
    BankTransaction,
    Charge,
    ChargeConcept,
    House,
    Payment,
    PaymentApplication,
)


class IncomeViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="income-admin", password="test-password"
        )
        self.client.force_login(self.user)
        self.house = House.objects.create(number="001")
        self.concept = ChargeConcept.objects.create(name="Cuota", kind="ORDINARY_FEE")
        self.charge = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 10, 1),
            due_date=date(2026, 10, 5),
        )
        self.bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="001",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="150.00",
        )
        self.payment = Payment.objects.create(
            house=self.house, bank_transaction=self.bank
        )

    def url(self, model, action, obj=None):
        return reverse(
            f"treasury:{model._meta.model_name}:{action}",
            args=[obj.pk] if obj else None,
        )

    def application_url(self, action, application=None, payment=None):
        kwargs = {"payment_pk": (payment or self.payment).pk}
        if application:
            kwargs["pk"] = application.pk
        return reverse(f"treasury:payment:paymentapplication:{action}", kwargs=kwargs)

    def test_bank_create_list_detail_without_edit_delete(self):
        data = {
            "bank_account": "001",
            "bank_receipt": "002",
            "line_type": "VAT",
            "transaction_date": "2026-10-01",
            "amount": "10.00",
            "description": "IVA",
        }
        self.assertEqual(
            self.client.get(self.url(BankTransaction, "add")).status_code, 200
        )
        self.assertEqual(
            self.client.post(self.url(BankTransaction, "add"), data).status_code, 302
        )
        for action in ("list", "detail"):
            self.assertEqual(
                self.client.get(
                    self.url(
                        BankTransaction,
                        action,
                        self.bank if action == "detail" else None,
                    )
                ).status_code,
                200,
            )
        for action in ("change", "delete", "bulk_delete"):
            with self.assertRaises(NoReverseMatch):
                self.url(BankTransaction, action, self.bank)

    def test_payment_crud_without_applications(self):
        bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="002",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="100.00",
        )
        data = {"bank_transaction": bank.pk, "house": self.house.pk}
        self.assertEqual(self.client.get(self.url(Payment, "add")).status_code, 200)
        self.assertEqual(
            self.client.post(self.url(Payment, "add"), data).status_code, 302
        )
        payment = Payment.objects.get(bank_transaction=bank)
        for action in ("list", "detail", "change", "delete"):
            self.assertEqual(
                self.client.get(
                    self.url(Payment, action, payment if action != "list" else None)
                ).status_code,
                200,
            )
        self.assertEqual(
            self.client.post(self.url(Payment, "change", payment), data).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(self.url(Payment, "delete", payment)).status_code, 302
        )
        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())

    def test_applications_use_native_crud_scoped_to_payment(self):
        self.assertContains(
            self.client.get(self.url(Payment, "detail", self.payment)),
            "Este pago no tiene aplicaciones.",
        )
        self.assertEqual(self.client.get(self.application_url("add")).status_code, 200)
        data = {"charge": self.charge.pk, "amount": "40.00"}
        response = self.client.post(self.application_url("add"), data)
        self.assertRedirects(response, self.url(Payment, "detail", self.payment))
        application = PaymentApplication.objects.get()
        for action in ("list", "detail", "change", "delete"):
            self.assertEqual(
                self.client.get(
                    self.application_url(
                        action, application if action != "list" else None
                    )
                ).status_code,
                200,
            )
        self.assertEqual(
            self.client.post(
                self.application_url("change", application), {**data, "amount": "50.00"}
            ).status_code,
            302,
        )
        application.refresh_from_db()
        self.assertEqual(str(application.amount), "50.00")
        self.assertContains(
            self.client.get(self.url(Payment, "detail", self.payment)),
            "Agregar aplicación",
        )
        self.assertEqual(
            self.client.post(self.application_url("delete", application)).status_code,
            302,
        )
        self.assertFalse(PaymentApplication.objects.exists())

    def test_parent_cannot_be_changed_or_accessed_through_another_payment(self):
        bank = BankTransaction.objects.create(
            bank_account="001",
            bank_receipt="002",
            line_type="CREDIT",
            transaction_date=date(2026, 10, 1),
            amount="100.00",
        )
        other = Payment.objects.create(house=self.house, bank_transaction=bank)
        data = {"charge": self.charge.pk, "amount": "40.00", "payment": other.pk}
        self.assertEqual(
            self.client.post(self.application_url("add"), data).status_code, 302
        )
        application = PaymentApplication.objects.get()
        self.assertEqual(application.payment_id, self.payment.pk)
        self.assertEqual(
            self.client.post(
                self.application_url("change", application), data
            ).status_code,
            302,
        )
        application.refresh_from_db()
        self.assertEqual(application.payment_id, self.payment.pk)
        for action in ("detail", "change", "delete"):
            url = self.application_url(action, application, payment=other)
            self.assertEqual(self.client.get(url).status_code, 404)
            if action != "detail":
                self.assertEqual(self.client.post(url, data).status_code, 404)

    def test_limits_and_payment_changes_are_readable_in_forms(self):
        data = {"charge": self.charge.pk, "amount": "101.00"}
        self.assertContains(
            self.client.post(self.application_url("add"), data), "saldo exigible"
        )
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="40.00"
        )
        other_house = House.objects.create(number="002")
        response = self.client.post(
            self.url(Payment, "change", self.payment),
            {"bank_transaction": self.bank.pk, "house": other_house.pk},
        )
        self.assertContains(response, "antes de cambiar la casa")
        self.assertEqual(
            self.client.get(self.url(Payment, "delete", self.payment)).status_code, 403
        )
        self.assertEqual(
            self.client.post(self.url(Payment, "delete", self.payment)).status_code, 403
        )
        self.assertEqual(
            self.client.post(self.url(Charge, "delete", self.charge)).status_code, 403
        )

    def test_house_detail_shows_dynamic_balances_and_related_links(self):
        response = self.client.get(self.url(House, "detail", self.house))
        self.assertEqual(response.context["balances"]["net"], -50)
        for label in (
            "Obligaciones pendientes",
            "Crédito bancario sin aplicar",
            "Saldo neto",
            "Ver cargos",
            "Ver pagos",
        ):
            self.assertContains(response, label)
        self.assertContains(response, f"?house={self.house.pk}")
        PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="40.00"
        )
        self.assertEqual(
            self.client.get(self.url(House, "detail", self.house)).context["balances"],
            {"obligations": 60, "unapplied_credit": 110, "net": -50},
        )

    def test_permissions_and_no_bulk_delete(self):
        application = PaymentApplication.objects.create(
            payment=self.payment, charge=self.charge, amount="40.00"
        )
        user = get_user_model().objects.create_user(username="income-viewer")
        self.client.force_login(user)
        for model, obj in ((BankTransaction, self.bank), (Payment, self.payment)):
            for action in ("list", "detail", "add"):
                self.assertEqual(
                    self.client.get(
                        self.url(model, action, obj if action == "detail" else None)
                    ).status_code,
                    403,
                )
            self.assertEqual(
                self.client.post(self.url(model, "add"), {}).status_code, 403
            )
            with self.assertRaises(NoReverseMatch):
                self.url(model, "bulk_delete")
        for action in ("list", "detail", "add", "change", "delete"):
            url = self.application_url(
                action,
                application if action in ("detail", "change", "delete") else None,
            )
            self.assertEqual(self.client.get(url).status_code, 403)
            if action in ("add", "change", "delete"):
                self.assertEqual(self.client.post(url, {}).status_code, 403)
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="treasury", codename__startswith="view_"
            )
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.application_url("list")).status_code, 200)
        self.assertEqual(
            self.client.get(self.url(Payment, "detail", self.payment)).status_code, 200
        )
        self.assertEqual(
            self.client.post(self.application_url("add"), {}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                self.application_url("change", application), {}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(self.application_url("delete", application)).status_code,
            403,
        )
        with self.assertRaises(NoReverseMatch):
            self.application_url("bulk_delete")
        self.client.logout()
        self.assertEqual(
            self.client.get(self.url(BankTransaction, "list")).status_code, 302
        )

    def test_application_requires_parent_view_permission(self):
        user = get_user_model().objects.create_user(username="application-only")
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="treasury",
                codename__in=("add_paymentapplication", "view_paymentapplication"),
            )
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.application_url("add")).status_code, 403)
        self.assertEqual(self.client.get(self.application_url("list")).status_code, 403)

    def test_bank_and_payment_searches(self):
        response = self.client.get(
            self.url(BankTransaction, "list"), {"_search": "001", "line_type": "CREDIT"}
        )
        self.assertEqual(list(response.context["object_list"]), [self.bank])
        response = self.client.get(
            self.url(Payment, "list"), {"house": self.house.pk, "_search": "001"}
        )
        self.assertEqual(list(response.context["object_list"]), [self.payment])
        self.assertNotContains(response, ">Amount<")
        self.assertContains(response, "Importe")

    def test_repeated_application_submission_does_not_duplicate(self):
        data = {"charge": self.charge.pk, "amount": "40.00"}
        self.assertEqual(
            self.client.post(self.application_url("add"), data).status_code, 302
        )
        response = self.client.post(self.application_url("add"), data)
        self.assertContains(response, "Ya existe una aplicación")
        self.assertEqual(PaymentApplication.objects.count(), 1)

    def test_late_validation_errors_are_rendered_in_native_form(self):
        with patch.object(
            PaymentApplication,
            "save",
            side_effect=ValidationError("El saldo cambió; revise la aplicación."),
        ):
            response = self.client.post(
                self.application_url("add"),
                {"charge": self.charge.pk, "amount": "40.00"},
            )
        self.assertContains(response, "El saldo cambió; revise la aplicación.")
        self.assertFalse(PaymentApplication.objects.exists())
