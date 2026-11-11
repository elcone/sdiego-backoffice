from datetime import UTC, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from treasury.models import BankTransaction, Charge, ChargeType, ExpenseType, House


class ChargeCatalogAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = get_user_model().objects.create_user(username="catalog-reader")
        cls.editor = get_user_model().objects.create_user(username="catalog-editor")
        cls.editor.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="treasury",
                codename__in=[
                    "add_charge", "change_charge", "delete_charge", "add_chargetype", "change_chargetype",
                    "delete_chargetype", "add_expensetype", "change_expensetype",
                    "delete_expensetype",
                ],
            )
        )
        cls.house = House.objects.create(number=6101)
        cls.kind = ChargeType.objects.create(name="Cuota")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.editor)
        self.payload = {
            "house": self.house.pk, "charge_type": self.kind.pk,
            "period": "2026-11-01", "due_date": "2026-11-15",
            "amount": "-12.35", "description": "Ajuste",
        }

    def test_charge_type_api_defaults_and_explicit_state(self):
        self.assertTrue(ChargeType.objects.get(pk=self.kind.pk).is_active)
        for data, expected in [
            ({"name": "Activo por defecto"}, True),
            ({"name": "Inactivo", "is_active": False}, False),
        ]:
            response = self.client.post("/api/charge-type/", data, format="json")
            self.assertEqual(response.status_code, 201)
            self.assertIs(response.json()["is_active"], expected)
            self.assertIs(
                ChargeType.objects.get(pk=response.json()["id"]).is_active, expected
            )

    def test_inactive_charge_type_preserves_history_and_blocks_new_charges(self):
        charge = self.client.post("/api/charge/", self.payload, format="json")
        self.assertEqual(charge.status_code, 201)
        before = list(Charge.objects.values())
        detail = f"/api/charge-type/{self.kind.pk}/"
        response = self.client.patch(detail, {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["is_active"], False)
        self.assertFalse(ChargeType.objects.get(pk=self.kind.pk).is_active)
        catalog = self.client.get("/api/charge-type/").json()["results"]
        self.assertIn(
            {"id": self.kind.pk, "name": self.kind.name, "is_active": False}, catalog
        )
        bulk = {key: value for key, value in self.payload.items() if key != "house"}
        for url, payload in [
            ("/api/charge/", self.payload),
            ("/api/charge/generate-all/", bulk),
        ]:
            with self.subTest(url=url):
                response = self.client.post(url, payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("charge_type", response.json())
        self.assertEqual(list(Charge.objects.values()), before)
        self.assertEqual(
            self.client.get(f"/api/charge/{charge.json()['id']}/").json()["charge_type"],
            self.kind.pk,
        )
        self.assertEqual(
            self.client.get("/api/charge/").json()["results"][0]["charge_type"],
            self.kind.pk,
        )
        self.assertEqual(self.client.delete(detail).status_code, 400)

        response = self.client.patch(detail, {"is_active": True}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["is_active"], True)
        self.assertEqual(
            self.client.post("/api/charge/", self.payload, format="json").status_code,
            201,
        )
        House.objects.create(number=6102)
        response = self.client.post("/api/charge/generate-all/", bulk, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created_count"], 1)

    def test_catalog_crud_and_permissions(self):
        for endpoint, model in [
            ("charge-type", ChargeType), ("expense-type", ExpenseType),
        ]:
            with self.subTest(endpoint=endpoint):
                url = f"/api/{endpoint}/"
                self.assertEqual(self.client.get(url).status_code, 200)
                response = self.client.post(url, {"name": "Nuevo"}, format="json")
                self.assertEqual(response.status_code, 201)
                pk = response.json()["id"]
                detail = f"{url}{pk}/"
                self.assertEqual(
                    self.client.patch(detail, {"name": "Editado"}, format="json").status_code, 200
                )
                self.assertEqual(model.objects.get(pk=pk).name, "Editado")
                for data in [{"name": ""}, {"name": "a" * 201}]:
                    self.assertEqual(self.client.post(url, data, format="json").status_code, 400)
                self.client.force_authenticate(self.reader)
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.post(url, {"name": "Otro"}, format="json").status_code, 403)
                self.assertEqual(self.client.patch(detail, {"name": "Otro"}, format="json").status_code, 403)
                self.assertEqual(self.client.delete(detail).status_code, 403)
                self.client.force_authenticate(self.editor)
                self.assertEqual(self.client.delete(detail).status_code, 204)
                self.assertFalse(model.objects.filter(pk=pk).exists())
                self.client.force_authenticate(None)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.client.force_authenticate(self.editor)

    def test_catalog_uniqueness_matches_models(self):
        ExpenseType.objects.create(name="Jardín")
        self.assertEqual(self.client.post("/api/expense-type/", {"name": "Jardín"}).status_code, 400)
        self.assertEqual(self.client.post("/api/charge-type/", {"name": self.kind.name}).status_code, 201)

    def test_referenced_types_preserve_financial_records(self):
        charge = self.client.post("/api/charge/", self.payload).json()
        expense = ExpenseType.objects.create(name="Servicios")
        BankTransaction.objects.create(
            bank_account="001", bank_receipt="test", line_type="DEBIT",
            transaction_timestamp=datetime(2026, 11, 1, tzinfo=UTC),
            amount="-10.00", balance="100.00", concept="Gasto", expense_type=expense,
        )
        before_charges = list(Charge.objects.values())
        before_banks = list(BankTransaction.objects.values())
        for endpoint, pk in [("charge-type", self.kind.pk), ("expense-type", expense.pk)]:
            response = self.client.delete(f"/api/{endpoint}/{pk}/")
            self.assertEqual(response.status_code, 400)
            self.assertIn("registros asociados", response.json()["detail"])
        self.assertEqual(list(Charge.objects.values()), before_charges)
        self.assertEqual(list(BankTransaction.objects.values()), before_banks)
        self.assertEqual(Charge.objects.get(pk=charge["id"]).amount, Decimal("-12.35"))

    def test_individual_creation_dates_amounts_relations_and_no_updates(self):
        for amount in ["-0.01", "0.00", "9999999999999999.99"]:
            response = self.client.post("/api/charge/", {**self.payload, "amount": amount}, format="json")
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["amount"], amount)
            detail = f'/api/charge/{response.json()["id"]}/'
            self.assertEqual(self.client.patch(detail, {"amount": "1.00"}).status_code, 405)
            self.assertEqual(self.client.delete(detail).status_code, 405)
        before = list(Charge.objects.values())
        for field, value in [
            ("period", "invalid"),
            ("due_date", "2026-02-30"), ("amount", "1.001"),
            ("amount", "10000000000000000.00"), ("amount", None),
            ("house", 999999), ("charge_type", 999999),
        ]:
            with self.subTest(field=field, value=value):
                response = self.client.post("/api/charge/", {**self.payload, field: value}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        self.assertEqual(list(Charge.objects.values()), before)
        self.assertEqual(self.client.get("/api/charge/").json()["count"], 3)
        self.client.force_authenticate(self.reader)
        self.assertEqual(
            self.client.post("/api/charge/", self.payload).status_code, 403
        )

    def test_individual_charge_accepts_any_date_and_optional_due_date(self):
        for due_date in [{"due_date": None}, {}]:
            payload = {
                key: value for key, value in self.payload.items() if key != "due_date"
            }
            payload.update(period="2026-11-19", **due_date)
            response = self.client.post("/api/charge/", payload, format="json")
            self.assertEqual(response.status_code, 201, response.data)
            charge = Charge.objects.get(pk=response.data["id"])
            charge.full_clean()
            self.assertEqual(charge.period.isoformat(), "2026-11-19")
            self.assertIsNone(charge.due_date)
            detail = self.client.get(f"/api/charge/{charge.pk}/")
            self.assertIsNone(detail.data["due_date"])
        response = self.client.get("/api/charge/")
        self.assertTrue(all(row["due_date"] is None for row in response.data["results"]))

    def test_session_csrf_for_catalog_and_charge_writes(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.editor)
        self.assertEqual(client.post("/api/charge-type/", {"name": "CSRF"}).status_code, 403)
        client.get("/api/charge-type/")
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post("/api/charge-type/", {"name": "CSRF"}, HTTP_X_CSRFTOKEN=token).status_code, 201)
        client.get("/api/charge/")
        self.assertEqual(client.post("/api/charge/", self.payload, HTTP_X_CSRFTOKEN=token).status_code, 201)

    def test_charge_search_description_existing_fields_filters_and_pagination(self):
        first = self.client.post("/api/charge/", {
            **self.payload, "description": "Mantenimiento extraordinario",
        }, format="json").json()
        other_house = House.objects.create(number=7202)
        other_kind = ChargeType.objects.create(name="Especial")
        second = self.client.post("/api/charge/", {
            **self.payload, "house": other_house.pk,
            "charge_type": other_kind.pk, "period": "2026-12-01",
            "description": "",
        }, format="json").json()
        before = list(Charge.objects.order_by("pk").values())
        for query, expected in [
            ("TENIM", [first["id"]]),
            ("EXTRAORD", [first["id"]]),
            ("6101", [first["id"]]),
            ("cuot", [first["id"]]),
            ("2026-11", [first["id"]]),
            ("7202", [second["id"]]),
            ("ESPEC", [second["id"]]),
            ("2026-12", [second["id"]]),
            ("inexistente", []),
        ]:
            with self.subTest(query=query):
                response = self.client.get("/api/charge/", {"search": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [row["id"] for row in response.json()["results"]], expected
                )
        response = self.client.get("/api/charge/", {
            "search": "mantenimiento", "house": other_house.pk,
        })
        self.assertEqual(response.json()["results"], [])
        response = self.client.get("/api/charge/", {
            "search": "mantenimiento", "house": self.house.pk,
            "charge_type": self.kind.pk, "period": "2026-11-01",
        })
        self.assertEqual(
            response.json()["results"][0]["description"],
            "Mantenimiento extraordinario",
        )
        self.assertEqual(second["description"], "")
        self.assertEqual(list(Charge.objects.order_by("pk").values()), before)
        for _ in range(11):
            self.client.post("/api/charge/", {
                **self.payload, "description": "Mantenimiento",
            }, format="json")
        page_one = self.client.get("/api/charge/", {
            "search": "MANTEN", "page_size": 10,
        }).json()
        page_two = self.client.get("/api/charge/", {
            "search": "MANTEN", "page_size": 10, "page": 2,
        }).json()
        self.assertEqual(page_one["count"], 12)
        self.assertEqual(len(page_one["results"]), 10)
        self.assertEqual(len(page_two["results"]), 2)
        self.assertIsNotNone(page_one["next"])
        self.assertIsNotNone(page_two["previous"])
        self.assertFalse(
            {row["id"] for row in page_one["results"]}
            & {row["id"] for row in page_two["results"]}
        )

    def test_charge_api_pages_of_fifty_preserve_order_without_overlap(self):
        Charge.objects.bulk_create([
            Charge(
                house=self.house, charge_type=self.kind,
                period="2026-11-01", due_date="2026-11-15",
                amount="100.00", description="Paginación",
            )
            for _ in range(105)
        ])
        expected = list(
            Charge.objects.order_by("-period", "id").values_list("pk", flat=True)
        )
        received = []
        for page, size in [(1, 50), (2, 50), (3, 5)]:
            response = self.client.get(
                "/api/charge/", {"page_size": 50, "page": page}
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["count"], 105)
            self.assertEqual(len(data["results"]), size)
            self.assertEqual(data["previous"] is not None, page > 1)
            self.assertEqual(data["next"] is not None, page < 3)
            received.extend(row["id"] for row in data["results"])
        self.assertEqual(received, expected)
        self.assertEqual(len(set(received)), 105)
