from datetime import UTC, date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from treasury.models import BankTransaction, Charge, ChargeType, House


class HouseAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="house-reader")
        cls.house = House.objects.create(number=6101)
        cls.empty = House.objects.create(number=6102)
        cls.kind = ChargeType.objects.create(name="Cuota mensual")
        cls.cf = ChargeType.objects.create(name="Ajuste por pago en Comunidad Feliz")
        for amount, kind in [
            ("1000.25", cls.kind),
            ("200.00", cls.kind),
            ("-100.00", cls.cf),
        ]:
            Charge.objects.create(
                house=cls.house,
                charge_type=kind,
                period=date(2026, 2, 1),
                due_date=date(2026, 3, 1),
                amount=amount,
                description=kind.name,
            )
        for index, (amount, reviewed) in enumerate(
            [("800.15", False), ("600.10", True)]
        ):
            BankTransaction.objects.create(
                house=cls.house,
                line_type="CREDIT",
                bank_account="001",
                bank_receipt=str(index),
                transaction_timestamp=datetime(2026, 2, 2, 2, tzinfo=UTC),
                amount=amount,
                balance="9999.00",
                concept="Abono",
                is_reviewed=reviewed,
            )
        BankTransaction.objects.create(
            house=cls.house,
            line_type="DEBIT",
            bank_account="001",
            bank_receipt="debit",
            transaction_timestamp=datetime(2026, 2, 1, tzinfo=UTC),
            amount="-20.00",
            balance="9999.00",
            concept="Gasto",
        )
        BankTransaction.objects.create(
            line_type="CREDIT",
            bank_account="001",
            bank_receipt="unassigned",
            transaction_timestamp=datetime(2026, 2, 1, tzinfo=UTC),
            amount="900.00",
            balance="9999.00",
            concept="Sin asignar",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_list_exact_search_totals_and_empty_house(self):
        data = self.client.get("/api/house/").json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(
            data["results"],
            [
                {
                    "id": self.house.pk,
                    "number": 6101,
                    "total_charges": "1100.25",
                    "total_payments": "1400.25",
                    "balance": "-300.00",
                },
                {
                    "id": self.empty.pk,
                    "number": 6102,
                    "total_charges": "0.00",
                    "total_payments": "0.00",
                    "balance": "0.00",
                },
            ],
        )
        for number, count in [("6101", 1), ("610", 0), ("9999", 0)]:
            self.assertEqual(
                self.client.get("/api/house/", {"number": number}).json()["count"],
                count,
            )
        self.assertEqual(
            self.client.get("/api/house/", {"number": "abc"}).status_code, 400
        )
        self.assertEqual(
            self.client.get("/api/house/", {"number": "6101.1"}).status_code, 400
        )

    def test_chronological_order_includes_future_charges_without_cutoff(self):
        Charge.objects.create(
            house=self.house,
            charge_type=self.kind,
            period=date(2099, 1, 1),
            due_date=date(2099, 2, 1),
            amount="50.00",
            description="Cuota futura",
        )
        data = self.client.get(f"/api/house/{self.house.pk}/statement/").json()
        self.assertEqual(data["results"][-1]["date"], "2099-01-01")
        self.assertEqual(data["results"][-1]["description"], "Cuota futura")
        self.assertEqual(data["results"][-1]["running_balance"], "-250.00")
        dates = [row["date"] for row in data["results"]]
        self.assertEqual(dates, sorted(dates))
        house = self.client.get("/api/house/", {"number": 6101}).json()["results"][0]
        self.assertEqual(house["balance"], data["balance"])

    @override_settings(TIME_ZONE="America/Chihuahua")
    def test_statement_timezone_order_and_running_balance(self):
        response = self.client.get(f"/api/house/{self.house.pk}/statement/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["house"], {"id": self.house.pk, "number": 6101})
        self.assertEqual(
            [r["transaction_type"] for r in data["results"]],
            ["charge"] * 3 + ["payment"] * 2,
        )
        self.assertEqual([r["date"] for r in data["results"]], ["2026-02-01"] * 5)
        self.assertEqual(
            [r["running_balance"] for r in data["results"]],
            ["1000.25", "1200.25", "1100.25", "300.10", "-300.00"],
        )
        for kind in ["charge", "payment"]:
            ids = [r["id"] for r in data["results"] if r["transaction_type"] == kind]
            self.assertEqual(ids, sorted(ids))
        self.assertEqual(data["total_charges"], "1100.25")
        self.assertEqual(data["total_payments"], "1400.25")
        self.assertEqual(data["balance"], data["results"][-1]["running_balance"])
        self.assertEqual(
            sum(Decimal(r["charge_amount"]) for r in data["results"]),
            Decimal(data["total_charges"]),
        )

    def test_empty_statement_authentication_and_missing_house(self):
        data = self.client.get(f"/api/house/{self.empty.pk}/statement/").json()
        self.assertEqual(data["results"], [])
        self.assertEqual(data["balance"], "0.00")
        self.assertEqual(
            self.client.get("/api/house/999999/statement/").status_code, 404
        )
        self.assertEqual(
            self.client.post(f"/api/house/{self.house.pk}/statement/", {}).status_code,
            405,
        )
        self.client.force_authenticate(None)
        for url in ["/api/house/", f"/api/house/{self.house.pk}/statement/"]:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_pagination_and_no_n_plus_one(self):
        with self.assertNumQueries(2):
            self.client.get("/api/house/", {"page_size": 100})
        House.objects.bulk_create([House(number=i) for i in range(1, 80)])
        with self.assertNumQueries(2):
            response = self.client.get("/api/house/", {"page_size": 100})
        self.assertEqual(len(response.json()["results"]), 81)
        data = self.client.get("/api/house/", {"page_size": 10, "page": 2}).json()
        self.assertEqual(data["count"], 81)
        self.assertEqual(len(data["results"]), 10)
        self.assertIsNotNone(data["previous"])
