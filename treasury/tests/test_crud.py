from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from treasury.forms import ContactForm, HouseContactForm, HouseForm, PayerAccountForm
from treasury.models import Contact, House, HouseContact, PayerAccount


class CatalogTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.user = get_user_model().objects.create_superuser(
            username="catalog-admin", password="test-password"
        )
        self.client.force_login(self.user)
        self.house = House.objects.create(number="001")
        self.contact = Contact.objects.create(name="Ana")

    def url(self, model, action, obj=None):
        return reverse(
            f"treasury:{model._meta.model_name}:{action}",
            args=[obj.pk] if obj else None,
        )

    def test_crud_for_all_models(self):
        cases = [
            (House, {"number": "002"}, {"number": "003"}),
            (
                Contact,
                {"name": "Luis"},
                {"name": "Lucía", "email": "lucia@example.com"},
            ),
            (
                HouseContact,
                {"house": self.house.pk, "contact": self.contact.pk, "is_active": "on"},
                {
                    "house": self.house.pk,
                    "contact": self.contact.pk,
                    "is_responsible": "on",
                },
            ),
            (
                PayerAccount,
                {"contact": self.contact.pk, "bank": "Banco", "account_number": "0001"},
                {
                    "contact": self.contact.pk,
                    "bank": "Banco",
                    "account_number": "00012",
                },
            ),
        ]
        for model, create_data, update_data in cases:
            with self.subTest(model=model.__name__):
                for action in ("list", "add"):
                    self.assertEqual(
                        self.client.get(self.url(model, action)).status_code, 200
                    )
                self.assertEqual(
                    self.client.post(self.url(model, "add"), create_data).status_code,
                    302,
                )
                obj = model.objects.latest("pk")
                for action in ("detail", "change", "delete"):
                    self.assertEqual(
                        self.client.get(self.url(model, action, obj)).status_code, 200
                    )
                self.assertEqual(
                    self.client.post(
                        self.url(model, "change", obj), update_data
                    ).status_code,
                    302,
                )
                obj.refresh_from_db()
                for field, value in update_data.items():
                    if field not in ("house", "contact", "is_responsible"):
                        self.assertEqual(getattr(obj, field), value)
                if model is HouseContact:
                    self.assertFalse(obj.is_active)
                    self.assertTrue(obj.is_responsible)
                self.assertEqual(
                    self.client.post(self.url(model, "delete", obj)).status_code, 302
                )
                self.assertFalse(model.objects.filter(pk=obj.pk).exists())

    def test_search_and_relationship_filters(self):
        other = House.objects.create(number="099")
        relation = HouseContact.objects.create(
            house=self.house, contact=self.contact, is_active=False, is_responsible=True
        )
        response = self.client.get(self.url(House, "list"), {"_search": "001"})
        self.assertEqual(list(response.context["object_list"]), [self.house])
        self.assertNotContains(response, str(other))
        response = self.client.get(self.url(HouseContact, "list"))
        self.assertIn(relation, response.context["object_list"])
        response = self.client.get(
            self.url(HouseContact, "list"), {"is_active": "true"}
        )
        self.assertEqual(list(response.context["object_list"]), [])
        response = self.client.get(
            self.url(HouseContact, "list"),
            {"is_active": "false", "is_responsible": "true"},
        )
        self.assertEqual(list(response.context["object_list"]), [relation])

    def test_detail_is_complete_and_actions_are_in_spanish(self):
        relation = HouseContact.objects.create(
            house=self.house, contact=self.contact, is_responsible=True, is_active=False
        )
        response = self.client.get(self.url(HouseContact, "detail", relation))
        fields = {
            field.name: value
            for field, label, value in response.context["view"].get_object_data()
        }
        self.assertEqual(
            set(fields), {field.name for field in HouseContact._meta.fields}
        )
        self.assertEqual(fields["is_responsible"], "Sí")
        self.assertEqual(fields["is_active"], "No")
        self.assertContains(response, "Eliminar")
        self.assertContains(response, "Contactos de casas")

    def test_protected_delete_get_and_post(self):
        HouseContact.objects.create(
            house=self.house, contact=self.contact, is_active=False
        )
        for obj in (self.house, self.contact):
            for method in (self.client.get, self.client.post):
                with self.assertRaises(ProtectedError):
                    method(self.url(type(obj), "delete", obj))
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())

    def test_permissions_for_every_catalog(self):
        relation = HouseContact.objects.create(house=self.house, contact=self.contact)
        account = PayerAccount.objects.create(
            contact=self.contact, bank="Banco", account_number="01"
        )
        viewer = get_user_model().objects.create_user(username="viewer")
        objects = (self.house, self.contact, relation, account)
        for obj in objects:
            self.client.logout()
            self.assertEqual(
                self.client.get(self.url(type(obj), "list")).status_code, 302
            )
            self.client.force_login(viewer)
            for action in ("list", "add", "detail", "change", "delete"):
                url = self.url(
                    type(obj), action, obj if action not in ("list", "add") else None
                )
                self.assertEqual(self.client.get(url).status_code, 403)
                if action in ("add", "change", "delete"):
                    self.assertEqual(self.client.post(url, {}).status_code, 403)
        viewer.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="treasury", codename__startswith="view_"
            )
        )
        self.client.force_login(viewer)
        for obj in objects:
            self.assertEqual(
                self.client.get(self.url(type(obj), "list")).status_code, 200
            )
            self.assertEqual(
                self.client.get(self.url(type(obj), "detail", obj)).status_code, 200
            )
            self.assertEqual(
                self.client.post(self.url(type(obj), "delete", obj)).status_code, 403
            )

    def test_spanish_validation_and_reactivation(self):
        for form_class in (HouseForm, ContactForm, HouseContactForm, PayerAccountForm):
            form = form_class(data={})
            self.assertFalse(form.is_valid())
            self.assertIn("Este campo es obligatorio", str(form.errors))
        self.assertFalse(
            ContactForm(data={"name": "Ana", "email": "invalid"}).is_valid()
        )
        relation = HouseContact.objects.create(
            house=self.house, contact=self.contact, is_active=False
        )
        data = {"house": self.house.pk, "contact": self.contact.pk, "is_active": True}
        duplicate = HouseContactForm(data=data)
        self.assertFalse(duplicate.is_valid())
        self.assertIn("Ya existe una relación", str(duplicate.errors))
        form = HouseContactForm(data=data, instance=relation)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().pk, relation.pk)
        self.assertEqual(HouseContact.objects.count(), 1)
