from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from treasury.models import BankTransaction, Charge, ChargeType, ExpenseType, House


@override_settings(TIME_ZONE="America/Chihuahua")
class SummaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="summary-reader")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.sequence = 0
        self.kind = ChargeType.objects.create(name="Cuota")
        self.house = House.objects.create(number=6101)

    def bank(self, amount, line_type="CREDIT", timestamp="2026-11-03T12:00:00+00:00", **values):
        self.sequence += 1
        return BankTransaction.objects.create(
            bank_account=values.pop("bank_account", "001"),
            bank_receipt=str(self.sequence),
            line_type=line_type,
            transaction_timestamp=datetime.fromisoformat(timestamp),
            amount=amount,
            balance=values.pop("balance", "100.00"),
            concept="Movimiento",
            **values,
        )

    def charge(self, house, amount, period=date(2026, 11, 1)):
        return Charge.objects.create(
            house=house, charge_type=self.kind, amount=amount,
            period=period, due_date=date(2026, 11, 15),
        )

    def summary(self, now=datetime(2026, 11, 3, 12, tzinfo=UTC)):
        with patch("treasury.api.views.timezone.now", return_value=now):
            response = self.client.get("/api/summary/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_empty_data_and_authentication_read_only(self):
        data = self.summary()
        self.assertEqual(data, {
            "income": "0.00", "expense": "0.00", "receivable": "0.00",
            "pending_expenses": "0.00", "top_houses": [], "top_expenses": [],
            "unrecognized_income": "0.00",
        })
        self.assertEqual(self.client.post("/api/summary/", {}).status_code, 405)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/summary/").status_code, 403)

    def test_bank_balance_reuses_latest_per_account_and_primary_key_tie_break(self):
        self.bank("10.00", timestamp="2026-10-01T00:00:00+00:00", balance="9999.00")
        self.bank("-5.00", "DEBIT", balance="100.25")
        self.bank("-1.00", "COMMISSION", balance="99.25")
        self.bank("10.00", bank_account="002", balance="50.50")
        self.bank("1.00", timestamp="2026-10-01T00:00:00+00:00",
                  bank_account="002", balance="999.00")
        data = self.client.get("/api/bank-transaction/summary/").json()
        self.assertEqual(Decimal(data["balance"]), Decimal("149.75"))

    def test_unrecognized_income_sums_all_history_regardless_of_review(self):
        self.bank("10.25", timestamp="2025-01-01T12:00:00+00:00", is_reviewed=True)
        self.bank("20.50", is_reviewed=False)
        self.bank("30.75", timestamp="2099-01-01T12:00:00+00:00", is_reviewed=True)
        self.bank("999.00", house=self.house)
        for line_type in ("DEBIT", "COMMISSION", "COMMISSION_VAT"):
            self.bank("100.00", line_type)
        before = list(BankTransaction.objects.order_by("pk").values())
        self.assertEqual(self.summary()["unrecognized_income"], "61.50")
        self.assertEqual(list(BankTransaction.objects.order_by("pk").values()), before)

    def test_unrecognized_income_is_zero_without_matching_transactions(self):
        self.bank("999.00", house=self.house, is_reviewed=False)
        self.bank("888.00", house=self.house, is_reviewed=True)
        for line_type in ("DEBIT", "COMMISSION", "COMMISSION_VAT"):
            self.bank("100.00", line_type)
        self.assertEqual(self.summary()["unrecognized_income"], "0.00")

    def test_month_boundaries_use_configured_timezone_and_include_all_egress_types(self):
        self.bank("999.00", timestamp="2026-11-01T05:59:59+00:00")
        self.bank("10.25", timestamp="2026-11-01T06:00:00+00:00")
        self.bank("20.50", timestamp="2026-12-01T05:59:59+00:00")
        self.bank("888.00", timestamp="2026-12-01T06:00:00+00:00")
        self.bank("-3.00", "DEBIT")
        self.bank("-1.00", "COMMISSION")
        self.bank("-0.16", "COMMISSION_VAT")
        self.bank("-999.00", "DEBIT", timestamp="2026-11-01T05:59:59+00:00")
        self.bank("-888.00", "DEBIT", timestamp="2026-12-01T06:00:00+00:00")
        self.charge(self.house, "1000.00")
        with timezone.override("Asia/Tokyo"):
            data = self.summary()
        self.assertEqual(data["income"], "30.75")
        self.assertEqual(data["expense"], "-4.16")

    def test_december_and_leap_february_calendar_boundaries(self):
        for now, start, end in [
            (datetime(2026, 12, 15, tzinfo=UTC), "2026-12-01T06:00:00+00:00", "2027-01-01T06:00:00+00:00"),
            (datetime(2028, 2, 15, tzinfo=UTC), "2028-02-01T06:00:00+00:00", "2028-03-01T06:00:00+00:00"),
        ]:
            with self.subTest(now=now):
                self.bank("10.00", timestamp=start)
                self.bank("999.00", timestamp=end)
                self.assertEqual(self.summary(now)["income"], "10.00")

    def test_pending_expenses_is_all_time_unclassified_or_unreviewed_without_duplicates(self):
        kind = ExpenseType.objects.create(name="Mantenimiento")
        self.bank("-10.00", "DEBIT", expense_type=kind, is_reviewed=False)
        self.bank("-20.00", "DEBIT", expense_type=None, is_reviewed=True)
        self.bank("-30.00", "DEBIT", expense_type=None, is_reviewed=False)
        self.bank("-40.00", "DEBIT", expense_type=kind, is_reviewed=True)
        self.bank("-2.00", "COMMISSION", timestamp="2025-01-01T12:00:00+00:00")
        self.bank("-0.32", "COMMISSION_VAT", timestamp="2025-01-01T12:00:00+00:00")
        self.bank("1000.00", expense_type=kind, is_reviewed=False)
        self.assertEqual(self.summary()["pending_expenses"], "-62.32")

    def test_debt_matches_houses_current_balances_and_top_ten(self):
        self.charge(self.house, "100.00")
        self.charge(self.house, "-10.00")
        self.bank("30.00", house=self.house, is_reviewed=False)
        self.bank("20.00", house=self.house, is_reviewed=True)
        # Future charges remain in current balances, matching Casas.
        self.charge(self.house, "5.00", period=date(2099, 1, 1))
        self.bank("-999.00", "DEBIT", house=self.house)
        for number, amount in [(6102, "0.00"), (6103, "-25.00")]:
            self.charge(House.objects.create(number=number), amount)
        for index in range(12):
            self.charge(House.objects.create(number=6200 + index), str(100 + index))
        before_charges = list(Charge.objects.order_by("pk").values())
        before_banks = list(BankTransaction.objects.order_by("pk").values())
        data = self.summary()
        houses = self.client.get("/api/house/", {"page_size": 100}).json()["results"]
        pending = [row for row in houses if Decimal(row["balance"]) > 0]
        self.assertEqual(
            Decimal(data["receivable"]), sum(Decimal(row["balance"]) for row in pending)
        )
        expected = sorted(pending, key=lambda row: (-Decimal(row["balance"]), row["number"]))[:10]
        self.assertEqual(data["top_houses"], [
            {"number": row["number"], "balance": row["balance"]} for row in expected
        ])
        self.assertEqual(len(data["top_houses"]), 10)
        self.assertEqual(list(Charge.objects.order_by("pk").values()), before_charges)
        self.assertEqual(list(BankTransaction.objects.order_by("pk").values()), before_banks)

    def test_query_count_does_not_grow_with_houses_or_transactions(self):
        with self.assertNumQueries(6):
            self.summary()
        for index in range(15):
            self.charge(House.objects.create(number=6200 + index), "100.00")
            self.bank("-1.00", "DEBIT")
        with self.assertNumQueries(6):
            self.summary()

    def test_expense_ranking_groups_monthly_egresses_by_accumulated_magnitude(self):
        maintenance = ExpenseType.objects.create(name="Mantenimiento")
        services = ExpenseType.objects.create(name="Servicios")
        self.bank("-100.00", "DEBIT", expense_type=maintenance)
        self.bank("-50.00", "DEBIT", expense_type=maintenance, is_reviewed=True)
        self.bank("-5.00", "COMMISSION", expense_type=maintenance)
        self.bank("-0.80", "COMMISSION_VAT", expense_type=maintenance)
        self.bank("-200.00", "DEBIT", expense_type=services)
        self.bank("-20.00", "DEBIT")
        self.bank("-1.00", "COMMISSION")
        self.bank("-0.16", "COMMISSION_VAT")
        self.bank("9000.00", expense_type=maintenance)
        self.bank("-9999.00", "DEBIT", expense_type=maintenance,
                  timestamp="2026-11-01T05:59:59+00:00")
        self.bank("-9999.00", "DEBIT", expense_type=maintenance,
                  timestamp="2026-12-01T06:00:00+00:00")
        before = list(BankTransaction.objects.order_by("pk").values())
        data = self.summary()
        self.assertEqual(data["top_expenses"], [
            {"name": "Servicios", "total": "200.00"},
            {"name": "Mantenimiento", "total": "155.80"},
            {"name": "Sin clasificar", "total": "21.16"},
        ])
        self.assertEqual(data["expense"], "-376.96")
        self.assertEqual(
            sum(Decimal(row["total"]) for row in data["top_expenses"]),
            -Decimal(data["expense"]),
        )
        self.assertEqual(list(BankTransaction.objects.order_by("pk").values()), before)

    def test_ranking_uses_magnitude_of_each_accumulated_total(self):
        kind = ExpenseType.objects.create(name="Ajustes")
        self.bank("-100.00", "DEBIT", expense_type=kind)
        self.bank("40.00", "DEBIT", expense_type=kind)
        self.bank("-80.00", "DEBIT")
        data = self.summary()
        self.assertEqual(data["top_expenses"], [
            {"name": "Sin clasificar", "total": "80.00"},
            {"name": "Ajustes", "total": "60.00"},
        ])
        self.assertEqual(data["expense"], "-140.00")

    def test_expense_ranking_limits_to_ten_after_grouping_and_keeps_others_unchanged(self):
        self.charge(self.house, "123.45")
        self.bank("10.00")
        for index in range(12):
            kind = ExpenseType.objects.create(name=f"Gasto {index}")
            self.bank(str(-(index + 1)), "DEBIT", expense_type=kind)
        data = self.summary()
        self.assertEqual(data["top_expenses"], [
            {"name": f"Gasto {index}", "total": f"{index + 1}.00"}
            for index in range(11, 1, -1)
        ])
        self.assertEqual(data["income"], "10.00")
        self.assertEqual(data["expense"], "-78.00")
        self.assertEqual(data["pending_expenses"], "-78.00")
        self.assertEqual(data["receivable"], "123.45")
        self.assertEqual(data["top_houses"], [{"number": 6101, "balance": "123.45"}])

    def test_income_only_does_not_create_expense_groups(self):
        self.bank("100.00", expense_type=ExpenseType.objects.create(name="No es gasto"))
        self.assertEqual(self.summary()["top_expenses"], [])

    def test_expense_ranking_includes_start_and_excludes_end_of_local_month(self):
        self.bank("-10.00", "DEBIT", timestamp="2026-11-01T06:00:00+00:00")
        self.bank("-20.00", "DEBIT", timestamp="2026-12-01T05:59:59+00:00")
        self.bank("-999.00", "DEBIT", timestamp="2026-11-01T05:59:59+00:00")
        self.bank("-999.00", "DEBIT", timestamp="2026-12-01T06:00:00+00:00")
        self.assertEqual(
            self.summary()["top_expenses"], [{"name": "Sin clasificar", "total": "30.00"}]
        )
