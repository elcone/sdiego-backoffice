from datetime import UTC, date, datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from treasury.models import BankTransaction, Charge, ChargeType, ExpenseType, House


class TreasuryAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = get_user_model().objects.create_user(username="reader")
        cls.editor = get_user_model().objects.create_user(username="editor")
        cls.editor.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="treasury",
                codename="change_banktransaction",
            )
        )
        cls.house = House.objects.create(number=6102)
        cls.other_house = House.objects.create(number=6101)
        cls.expense_type = ExpenseType.objects.create(name="Maintenance")
        cls.charge_type = ChargeType.objects.create(name="Monthly fee")
        cls.banks = []
        for index, line_type in enumerate(BankTransaction.LineType.values):
            cls.banks.append(
                BankTransaction.objects.create(
                    bank_account="000123",
                    bank_receipt=f"000{index}",
                    line_type=line_type,
                    transaction_timestamp=datetime(2026, 2, 2, 12, tzinfo=UTC),
                    amount=Decimal("100.25")
                    if line_type == "CREDIT"
                    else Decimal("-5.25"),
                    balance=Decimal("1000.50"),
                    concept=f"Original concept {index}",
                )
            )
        cls.credit = (
            cls.banks[0] if cls.banks[0].line_type == "CREDIT" else cls.banks[1]
        )
        cls.charge = Charge.objects.create(
            house=cls.house,
            charge_type=cls.charge_type,
            period=date(2026, 2, 1),
            due_date=date(2026, 2, 5),
            amount=Decimal("1000.00"),
            description="Monthly fee",
        )
        cls.adjustment = Charge.objects.create(
            house=cls.other_house,
            charge_type=cls.charge_type,
            period=date(2026, 3, 1),
            due_date=date(2026, 3, 1),
            amount=Decimal("-100.00"),
            description="CF adjustment",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.reader)

    def url(self, bank=None):
        return f"/api/bank-transaction/{(bank or self.credit).pk}/"

    def test_lists_details_and_exact_fields(self):
        for endpoint, instance, fields in [
            ("house", self.house, {"id", "number"}),
            (
                "bank-transaction",
                self.credit,
                {
                    "id",
                    "bank_account",
                    "bank_receipt",
                    "line_type",
                    "transaction_timestamp",
                    "amount",
                    "balance",
                    "concept",
                    "house",
                    "expense_type",
                    "description",
                },
            ),
            (
                "charge",
                self.charge,
                {
                    "id",
                    "house",
                    "charge_type",
                    "period",
                    "due_date",
                    "amount",
                    "description",
                },
            ),
            ("charge-type", self.charge_type, {"id", "name"}),
            ("expense-type", self.expense_type, {"id", "name"}),
        ]:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(f"/api/{endpoint}/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    set(response.json()), {"count", "next", "previous", "results"}
                )
                response = self.client.get(f"/api/{endpoint}/{instance.pk}/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.json()), fields)
                self.assertEqual(response.json()["id"], instance.pk)
                self.assertEqual(
                    self.client.get(f"/api/{endpoint}/999999/").status_code, 404
                )
        data = self.client.get(f"/api/charge/{self.adjustment.pk}/").json()
        self.assertEqual(data["amount"], "-100.00")
        self.assertEqual(data["house"], self.other_house.pk)

    def test_pagination_and_maximum_size(self):
        House.objects.bulk_create([House(number=number) for number in range(1, 121)])
        for params, length in [
            ({}, 25),
            ({"page_size": 10}, 10),
            ({"page_size": 500}, 100),
        ]:
            data = self.client.get("/api/house/", params).json()
            self.assertEqual(data["count"], 122)
            self.assertEqual(len(data["results"]), length)
            self.assertIsNotNone(data["next"])
        data = self.client.get("/api/house/", {"page_size": 100, "page": 2}).json()
        self.assertEqual(len(data["results"]), 22)
        self.assertIsNotNone(data["previous"])

    def test_empty_catalogs(self):
        ExpenseType.objects.all().delete()
        self.assertEqual(self.client.get("/api/expense-type/").json()["results"], [])
        Charge.objects.all().delete()
        ChargeType.objects.all().delete()
        self.assertEqual(self.client.get("/api/charge-type/").json()["count"], 0)

    def test_bank_filters_search_and_dates(self):
        self.credit.house = self.house
        self.credit.save()
        debit = self.banks[0]
        debit.expense_type = self.expense_type
        debit.save()
        for params, expected in [
            ({"line_type": "CREDIT"}, {self.credit.pk}),
            ({"house": self.house.pk}, {self.credit.pk}),
            ({"expense_type": self.expense_type.pk}, {debit.pk}),
            ({"search": self.credit.bank_receipt}, {self.credit.pk}),
            ({"search": "concept 1"}, {self.credit.pk}),
            (
                {"date_from": "2026-02-02", "date_to": "2026-02-02"},
                {b.pk for b in self.banks},
            ),
            ({"date_from": "2026-02-03"}, set()),
            ({"date_to": "2026-02-01"}, set()),
            ({"status": "pending"}, {b.pk for b in self.banks[2:]}),
            ({"status": "classified"}, {debit.pk, self.credit.pk}),
            ({"status": "all"}, {b.pk for b in self.banks}),
            ({"status": "pending", "line_type": "CREDIT"}, set()),
        ]:
            with self.subTest(params=params):
                response = self.client.get("/api/bank-transaction/", params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {r["id"] for r in response.json()["results"]}, expected
                )
        for params in [
            {"line_type": "INVALID"},
            {"status": "invalid"},
            {"date_from": "invalid"},
            {"house": "invalid"},
        ]:
            self.assertEqual(
                self.client.get("/api/bank-transaction/", params).status_code, 400
            )

    def test_status_uses_the_relevant_relationship(self):
        self.credit.expense_type = self.expense_type
        self.credit.save()
        for bank in self.banks:
            if bank.line_type != "CREDIT":
                bank.house = self.house
                bank.save()
        data = self.client.get("/api/bank-transaction/", {"status": "pending"}).json()
        self.assertEqual(data["count"], 4)

    def test_charge_filters(self):
        for params, expected in [
            ({"house": self.house.pk}, {self.charge.pk}),
            (
                {"charge_type": self.charge_type.pk},
                {self.charge.pk, self.adjustment.pk},
            ),
            ({"period": "2026-03-01"}, {self.adjustment.pk}),
        ]:
            data = self.client.get("/api/charge/", params).json()
            self.assertEqual({r["id"] for r in data["results"]}, expected)
        self.assertEqual(
            self.client.get("/api/charge/", {"period": "invalid"}).status_code, 400
        )

    def test_ordering(self):
        data = self.client.get("/api/house/").json()["results"]
        self.assertEqual([r["number"] for r in data], [6101, 6102])
        first = self.banks[0]
        first.transaction_timestamp = datetime(2026, 3, 1, tzinfo=UTC)
        first.save()
        data = self.client.get("/api/bank-transaction/").json()["results"]
        self.assertEqual([r["id"] for r in data], [b.pk for b in self.banks])
        data = self.client.get("/api/charge/").json()["results"]
        self.assertEqual([r["id"] for r in data], [self.adjustment.pk, self.charge.pk])
        ExpenseType.objects.create(name="Accounting")
        ChargeType.objects.create(name="Adjustment")
        for endpoint in ["expense-type", "charge-type"]:
            names = [
                r["name"]
                for r in self.client.get(f"/api/{endpoint}/").json()["results"]
            ]
            self.assertEqual(names, sorted(names))

    def test_credit_assignment_change_and_removal(self):
        self.client.force_authenticate(self.editor)
        for value in [self.house.pk, self.other_house.pk, None]:
            response = self.client.patch(self.url(), {"house": value}, format="json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["house"], value)
            self.credit.refresh_from_db()
            self.assertEqual(self.credit.house_id, value)

    def test_expense_classification_and_description(self):
        self.client.force_authenticate(self.editor)
        for bank in self.banks:
            before = BankTransaction.objects.values().get(pk=bank.pk)
            changes = {"description": "Administrative note"}
            if bank.line_type != "CREDIT":
                changes["expense_type"] = self.expense_type.pk
            response = self.client.patch(self.url(bank), changes, format="json")
            self.assertEqual(response.status_code, 200)
            after = BankTransaction.objects.values().get(pk=bank.pk)
            expected = {**before, "description": "Administrative note"}
            if bank.line_type != "CREDIT":
                expected["expense_type_id"] = self.expense_type.pk
            self.assertEqual(after, expected)
            if bank.line_type != "CREDIT":
                response = self.client.patch(
                    self.url(bank), {"expense_type": None}, format="json"
                )
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()["expense_type"])

    def test_reject_unknown_banking_and_incompatible_fields_atomically(self):
        self.client.force_authenticate(self.editor)
        for bank in self.banks:
            forbidden = [
                "id",
                "bank_account",
                "bank_receipt",
                "line_type",
                "transaction_timestamp",
                "amount",
                "balance",
                "concept",
                "unknown",
            ]
            forbidden += ["expense_type"] if bank.line_type == "CREDIT" else ["house"]
            for field in forbidden:
                with self.subTest(line_type=bank.line_type, field=field):
                    before = BankTransaction.objects.values().get(pk=bank.pk)
                    response = self.client.patch(
                        self.url(bank),
                        {field: None, "description": "Must not persist"},
                        format="json",
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(field, response.json())
                    self.assertEqual(
                        before, BankTransaction.objects.values().get(pk=bank.pk)
                    )

    def test_invalid_relationship_ids(self):
        self.client.force_authenticate(self.editor)
        for bank, field in [(self.credit, "house"), (self.banks[0], "expense_type")]:
            for value in [999999, "invalid"]:
                response = self.client.patch(
                    self.url(bank), {field: value}, format="json"
                )
                self.assertEqual(response.status_code, 400)

    def test_authentication_and_change_permission(self):
        self.client.force_authenticate(None)
        for endpoint in [
            "house",
            "bank-transaction",
            "charge",
            "charge-type",
            "expense-type",
        ]:
            self.assertEqual(self.client.get(f"/api/{endpoint}/").status_code, 403)
            self.assertEqual(self.client.get(f"/api/{endpoint}/1/").status_code, 403)
        self.assertEqual(
            self.client.patch(
                self.url(), {"house": self.house.pk}, format="json"
            ).status_code,
            403,
        )
        self.client.force_authenticate(self.reader)
        self.assertEqual(
            self.client.patch(
                self.url(), {"house": self.house.pk}, format="json"
            ).status_code,
            403,
        )
        self.client.force_authenticate(self.editor)
        self.assertEqual(
            self.client.patch(
                self.url(), {"house": self.house.pk}, format="json"
            ).status_code,
            200,
        )

    def test_session_authentication_requires_csrf_for_patch(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.editor)
        self.assertEqual(client.get(self.url()).status_code, 200)
        self.assertEqual(
            client.patch(
                self.url(), {"description": "Note"}, format="json"
            ).status_code,
            403,
        )
        client.get("/admin/login/")
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.patch(
            self.url(), {"description": "Note"}, format="json", HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(response.status_code, 200)

    def test_disallowed_methods(self):
        self.client.force_authenticate(self.editor)
        for endpoint, instance in [
            ("house", self.house),
            ("bank-transaction", self.credit),
            ("charge", self.charge),
            ("charge-type", self.charge_type),
            ("expense-type", self.expense_type),
        ]:
            for method in ["post", "put", "delete"]:
                for url in [f"/api/{endpoint}/", f"/api/{endpoint}/{instance.pk}/"]:
                    with self.subTest(method=method, url=url):
                        self.assertEqual(
                            getattr(self.client, method)(
                                url, {}, format="json"
                            ).status_code,
                            405,
                        )
            if endpoint != "bank-transaction":
                self.assertEqual(
                    self.client.patch(
                        f"/api/{endpoint}/{instance.pk}/", {}, format="json"
                    ).status_code,
                    405,
                )
