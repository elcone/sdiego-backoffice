from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from treasury.models import Charge, ChargeConcept, FeeConfiguration, House


class ObligationViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="obligations", password="test-password"
        )
        self.client.force_login(self.user)
        self.house = House.objects.create(number="001")
        self.concept = ChargeConcept.objects.create(
            name="Cuota", kind="ORDINARY_FEE", is_surcharge_eligible=True
        )
        self.configuration = FeeConfiguration.objects.create(
            concept=self.concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
        )

    def url(self, model, action, obj=None):
        return reverse(
            f"treasury:{model._meta.model_name}:{action}",
            args=[obj.pk] if obj else None,
        )

    def test_concept_and_configuration_crud(self):
        cases = (
            (
                ChargeConcept,
                {"name": "Multa", "kind": "FINE"},
                {"name": "Multa actualizada", "kind": "FINE"},
            ),
            (
                FeeConfiguration,
                {
                    "concept": self.concept.pk,
                    "amount": "200.00",
                    "frequency": "ONE_TIME",
                    "generation_date": "2026-02-10",
                    "due_date": "2026-02-20",
                },
                {
                    "concept": self.concept.pk,
                    "amount": "250.00",
                    "frequency": "ONE_TIME",
                    "generation_date": "2026-02-10",
                    "due_date": "2026-02-20",
                },
            ),
        )
        for model, data, changed in cases:
            with self.subTest(model=model.__name__):
                for action in ("list", "add"):
                    self.assertEqual(
                        self.client.get(self.url(model, action)).status_code, 200
                    )
                response = self.client.post(self.url(model, "add"), data)
                self.assertEqual(response.status_code, 302)
                obj = model.objects.latest("pk")
                for action in ("detail", "change", "delete"):
                    self.assertEqual(
                        self.client.get(self.url(model, action, obj)).status_code, 200
                    )
                self.assertEqual(
                    self.client.post(
                        self.url(model, "change", obj), changed
                    ).status_code,
                    302,
                )
                self.assertEqual(
                    self.client.post(self.url(model, "delete", obj)).status_code, 302
                )
                self.assertFalse(model.objects.filter(pk=obj.pk).exists())

    def test_charge_has_complete_crud(self):
        data = {
            "house": self.house.pk,
            "concept": self.concept.pk,
            "amount": "100.00",
            "period": "2026-01-01",
            "due_date": "2026-01-05",
        }
        self.assertEqual(self.client.get(self.url(Charge, "add")).status_code, 200)
        self.assertEqual(
            self.client.post(self.url(Charge, "add"), data).status_code, 302
        )
        charge = Charge.objects.get()
        self.assertTrue(charge.is_surcharge_eligible)
        for action in ("list", "detail"):
            self.assertEqual(
                self.client.get(
                    self.url(Charge, action, charge if action == "detail" else None)
                ).status_code,
                200,
            )
        detail = self.client.get(self.url(Charge, "detail", charge))
        self.assertContains(detail, "Eliminar")
        for action in ("change", "delete"):
            self.assertEqual(
                self.client.get(self.url(Charge, action, charge)).status_code, 200
            )
        self.assertEqual(
            self.client.post(
                self.url(Charge, "change", charge), {**data, "amount": "150.00"}
            ).status_code,
            302,
        )
        charge.refresh_from_db()
        self.assertEqual(str(charge.amount), "150.00")
        self.assertEqual(
            self.client.post(self.url(Charge, "delete", charge)).status_code, 302
        )
        self.assertFalse(Charge.objects.filter(pk=charge.pk).exists())

    def test_concepts_and_configurations_have_no_bulk_deletion(self):
        for model in (ChargeConcept, FeeConfiguration, Charge):
            with self.assertRaises(NoReverseMatch):
                self.url(model, "bulk_delete")

    def test_manual_adjustment_and_spanish_validation(self):
        original = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
        )
        adjustment = ChargeConcept.objects.create(name="Ajuste", kind="ADJUSTMENT")
        data = {
            "house": self.house.pk,
            "concept": adjustment.pk,
            "amount": "-300.00",
            "period": "2026-01-01",
            "due_date": "2026-01-05",
        }
        self.assertContains(
            self.client.post(self.url(Charge, "add"), data),
            "Un ajuste requiere el cargo original",
        )
        self.assertEqual(
            self.client.post(
                self.url(Charge, "add"), {**data, "adjusts_charge": original.pk}
            ).status_code,
            302,
        )
        self.assertTrue(Charge.objects.get(concept=adjustment).is_surcharge_eligible)
        self.assertContains(
            self.client.post(
                self.url(ChargeConcept, "add"),
                {"name": "Recargo", "kind": "SURCHARGE", "is_surcharge_eligible": "on"},
            ),
            "Un recargo no puede generar otros recargos",
        )

    def test_generation_requires_preview_and_confirmation(self):
        url = self.url(FeeConfiguration, "generate")
        data = {"configuration": self.configuration.pk, "period": "2026-01-01"}
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {**data, "_confirm": "1"})
        self.assertContains(response, "Previsualice los cargos antes de confirmar")
        self.assertFalse(Charge.objects.exists())
        preview = self.client.post(url, data)
        self.assertContains(preview, "Previsualización de cargos")
        self.assertContains(preview, "Confirmar generación")
        self.assertFalse(Charge.objects.exists())
        token = preview.context["form"].data["preview_token"]
        confirmed = self.client.post(
            url, {**data, "preview_token": token, "_confirm": "1"}, follow=True
        )
        self.assertContains(confirmed, "Cargos creados: 1. Cargos omitidos: 0.")
        repeated = self.client.post(
            url, {**data, "preview_token": token, "_confirm": "1"}, follow=True
        )
        self.assertContains(repeated, "Cargos creados: 0. Cargos omitidos: 1.")

    def test_generation_page_has_main_heading_before_form(self):
        response = self.client.get(self.url(FeeConfiguration, "generate"))
        self.assertContains(
            response, '<h1 class="vf-card__title">Generar cuotas</h1>', html=True
        )
        self.assertContains(response, "Previsualizar")
        self.assertNotContains(response, "Previsualización de cargos")
        self.assertNotContains(response, "Confirmar generación")

    def test_preview_is_a_separate_card_with_its_own_confirmation(self):
        House.objects.create(number="002")
        Charge.objects.create(
            house=self.house,
            concept=self.concept,
            fee_configuration=self.configuration,
            amount="100.00",
            period=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
        )
        response = self.client.post(
            self.url(FeeConfiguration, "generate"),
            {
                "configuration": self.configuration.pk,
                "period": "2026-01-01",
            },
        )
        self.assertEqual(
            response.context["summary"], {"total": 2, "pending": 1, "skipped": 1}
        )
        content = response.content.decode()
        forms = [
            part.split("</form>", 1)[0]
            for part in content.split('<form class="vf-form"')[1:]
        ]
        self.assertEqual(len(forms), 2)
        self.assertIn("Previsualizar", forms[0])
        self.assertNotIn("Confirmar generación", forms[0])
        self.assertIn("Confirmar generación", forms[1])
        self.assertNotIn("Previsualizar", forms[1])
        self.assertIn(
            'name="configuration" value="' + str(self.configuration.pk) + '"', forms[1]
        )
        self.assertIn('name="period" value="2026-01-01"', forms[1])
        self.assertIn('name="preview_token"', forms[1])
        self.assertIn('name="csrfmiddlewaretoken"', forms[1])
        cards = content.split('class="mdc-card vf-card"')
        self.assertEqual(len(cards), 3)
        self.assertIn("Generar cuotas", cards[1])
        self.assertNotIn("Previsualización de cargos", cards[1])
        self.assertIn("Previsualización de cargos", cards[2])
        for label in (
            "Total evaluados: 2",
            "Por crear: 1",
            "Omitidos: 1",
            "Pendiente de creación",
            "Existente: se omitirá",
        ):
            self.assertContains(response, label)
        self.assertContains(
            response, 'class="vf-list__table-header vf-list__table-header-numeric"'
        )
        self.assertContains(
            response, 'class="vf-list__table-cell vf-list__table-cell-numeric"', count=2
        )
        self.assertContains(response, '<th scope="col"', count=4)

    def test_empty_preview_summary(self):
        self.house.delete()
        response = self.client.post(
            self.url(FeeConfiguration, "generate"),
            {
                "configuration": self.configuration.pk,
                "period": "2026-01-01",
            },
        )
        self.assertEqual(
            response.context["summary"], {"total": 0, "pending": 0, "skipped": 0}
        )
        self.assertContains(response, "No hay casas para generar cargos.")

    def test_invalid_period_or_confirmation_is_legible(self):
        url = self.url(FeeConfiguration, "generate")
        data = {"configuration": self.configuration.pk, "period": "2026-01-02"}
        self.assertContains(self.client.post(url, data), "primer día del mes")
        data["period"] = "2026-01-01"
        self.assertContains(
            self.client.post(
                url, {**data, "preview_token": "invalid", "_confirm": "1"}
            ),
            "confirmación no es válida",
        )

    def test_generation_form_excludes_adjustments_and_surcharges(self):
        from treasury.forms import FeeGenerationForm

        for kind in ("ADJUSTMENT", "SURCHARGE"):
            concept = ChargeConcept.objects.create(name=kind, kind=kind)
            config = FeeConfiguration.objects.create(
                concept=concept,
                amount="10.00",
                frequency="MONTHLY",
                start_date=date(2026, 1, 1),
            )
            self.assertNotIn(
                config, FeeGenerationForm().fields["configuration"].queryset
            )

    def test_permissions_for_crud_and_generation(self):
        user = get_user_model().objects.create_user(username="restricted")
        self.client.force_login(user)
        charge = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
        )
        for obj in (self.concept, self.configuration, charge):
            model = type(obj)
            for action in ("list", "detail", "add"):
                url = self.url(model, action, obj if action == "detail" else None)
                self.assertEqual(self.client.get(url).status_code, 403)
                if action == "add":
                    self.assertEqual(self.client.post(url, {}).status_code, 403)
            for action in ("change", "delete"):
                self.assertEqual(
                    self.client.get(self.url(model, action, obj)).status_code, 403
                )
                self.assertEqual(
                    self.client.post(self.url(model, action, obj), {}).status_code, 403
                )
        generate_url = self.url(FeeConfiguration, "generate")
        self.assertEqual(self.client.get(generate_url).status_code, 403)
        self.assertEqual(self.client.post(generate_url, {}).status_code, 403)
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="treasury", codename__startswith="view_"
            )
        )
        self.client.force_login(user)
        for obj in (self.concept, self.configuration, charge):
            self.assertEqual(
                self.client.get(self.url(type(obj), "list")).status_code, 200
            )
            self.assertEqual(
                self.client.get(self.url(type(obj), "detail", obj)).status_code, 200
            )
            for action in ("change", "delete"):
                self.assertEqual(
                    self.client.post(self.url(type(obj), action, obj), {}).status_code,
                    403,
                )
        self.assertEqual(self.client.post(generate_url, {}).status_code, 403)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="treasury", codename="add_charge"
            )
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(generate_url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(generate_url).status_code, 302)

    def test_searches_and_filters(self):
        charge = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
        )
        self.assertEqual(
            list(
                self.client.get(
                    self.url(ChargeConcept, "list"),
                    {"_search": "Cuota", "kind": "ORDINARY_FEE"},
                ).context["object_list"]
            ),
            [self.concept],
        )
        self.assertEqual(
            list(
                self.client.get(
                    self.url(FeeConfiguration, "list"),
                    {"frequency": "MONTHLY", "concept": self.concept.pk},
                ).context["object_list"]
            ),
            [self.configuration],
        )
        self.assertEqual(
            list(
                self.client.get(
                    self.url(Charge, "list"),
                    {"_search": "001", "house": self.house.pk, "period": "2026-01-01"},
                ).context["object_list"]
            ),
            [charge],
        )
