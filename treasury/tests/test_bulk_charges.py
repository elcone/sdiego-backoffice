from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from treasury.models import Charge, ChargeType, House


URL = "/api/charge/generate-all/"


class BulkChargeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="bulk-editor")
        cls.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="treasury", codename="add_charge"
            )
        )
        cls.houses = [House.objects.create(number=n) for n in (6101, 6102, 6103)]
        cls.kind = ChargeType.objects.create(name="Cuota")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.payload = {
            "charge_type": self.kind.pk,
            "period": "2026-11-01",
            "due_date": "2026-11-15",
            "amount": "-12.35",
            "description": "Ajuste",
        }

    def generate(self, data=None, created=3, skipped=0, total=3):
        response = self.client.post(URL, data or self.payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "created_count": created,
                "skipped_count": skipped,
                "total_count": total,
            },
        )
        self.assertEqual(created + skipped, total)
        return response

    def test_all_houses_repeated_requests_and_description_is_not_identity(self):
        self.generate()
        before = list(Charge.objects.order_by("pk").values())
        self.assertEqual({row["house_id"] for row in before}, {h.pk for h in self.houses})
        for row in before:
            self.assertEqual(row["amount"], Decimal("-12.35"))
            self.assertEqual(row["period"], date(2026, 11, 1))
            self.assertEqual(row["due_date"], date(2026, 11, 15))
            self.assertEqual(row["description"], "Ajuste")
        self.generate(created=0, skipped=3)
        self.generate({**self.payload, "description": "Otro texto"}, created=0, skipped=3)
        self.assertEqual(list(Charge.objects.order_by("pk").values()), before)

    def test_partial_duplicates_and_multiple_existing_duplicates_are_preserved(self):
        values = {**self.payload, "charge_type": self.kind, "house": self.houses[0]}
        Charge.objects.create(**values)
        Charge.objects.create(**values)
        before = list(Charge.objects.order_by("pk").values())
        self.generate(created=2, skipped=1)
        self.assertEqual(Charge.objects.count(), 4)
        self.assertEqual(list(Charge.objects.order_by("pk").values())[:2], before)

    def test_each_identity_difference_allows_distinct_charges(self):
        self.generate()
        other_kind = ChargeType.objects.create(name="Otra cuota")
        for field, value in [
            ("charge_type", other_kind.pk),
            ("period", "2026-12-01"),
            ("amount", "-12.34"),
            ("due_date", "2026-11-16"),
        ]:
            with self.subTest(field=field):
                self.generate({**self.payload, field: value})
        new_house = House.objects.create(number=6104)
        self.generate(created=1, skipped=3, total=4)
        self.assertEqual(Charge.objects.filter(house=new_house).count(), 1)
        self.assertEqual(Charge.objects.count(), 16)

    def test_no_houses_is_success(self):
        House.objects.all().delete()
        self.generate(created=0, skipped=0, total=0)

    def test_validation_rejects_bad_values_and_arbitrary_houses(self):
        for field, value in [
            ("period", "2026-11-02"), ("period", "not-a-date"),
            ("due_date", "2026-02-30"), ("amount", "1.001"),
            ("due_date", None),
            ("amount", "10000000000000000.00"), ("amount", None),
            ("charge_type", 999999), ("charge_type", None),
            ("houses", [self.houses[0].pk]), ("house", self.houses[0].pk),
        ]:
            with self.subTest(field=field):
                response = self.client.post(
                    URL, {**self.payload, field: value}, format="json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        for field in ("charge_type", "period", "due_date", "amount"):
            payload = {key: value for key, value in self.payload.items() if key != field}
            self.assertEqual(self.client.post(URL, payload).status_code, 400)
        self.assertEqual(Charge.objects.count(), 0)

    def test_rollback_after_an_insert_preserves_existing_charges(self):
        existing = Charge.objects.create(
            house=self.houses[0], charge_type=self.kind,
            period="2026-11-01", due_date="2026-11-15",
            amount="-12.35", description="Conservar",
        )
        before = list(Charge.objects.values())
        original_save = Charge.save
        calls = 0

        def fail_second(instance, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Error durante la generación")
            return original_save(instance, *args, **kwargs)

        with patch.object(Charge, "save", fail_second):
            with self.assertRaisesMessage(RuntimeError, "Error durante la generación"):
                self.client.post(URL, self.payload, format="json")
        self.assertEqual(calls, 2)
        self.assertEqual(list(Charge.objects.values()), before)
        existing.refresh_from_db()
        self.assertEqual(existing.description, "Conservar")

    def test_creation_permissions_and_session_csrf(self):
        reader = get_user_model().objects.create_user(username="bulk-reader")
        self.client.force_authenticate(reader)
        self.assertEqual(self.client.post(URL, self.payload).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(URL, self.payload).status_code, 403)
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(URL, self.payload).status_code, 403)
        client.get("/api/charge/")
        self.assertEqual(
            client.post(
                URL, self.payload, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value
            ).status_code,
            200,
        )

    def test_individual_creation_still_allows_exact_duplicates(self):
        self.generate()
        payload = {**self.payload, "house": self.houses[0].pk}
        for _ in range(2):
            self.assertEqual(
                self.client.post("/api/charge/", payload, format="json").status_code,
                201,
            )
        self.assertEqual(Charge.objects.filter(house=self.houses[0]).count(), 3)
        self.generate(created=0, skipped=3)


class ConcurrentBulkChargeTests(TransactionTestCase):
    def test_simultaneous_requests_wait_and_do_not_duplicate(self):
        user = get_user_model().objects.create_user(username="concurrent-editor")
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="treasury", codename="add_charge"
            )
        )
        kind = ChargeType.objects.create(name="Cuota")
        for number in (6101, 6102):
            House.objects.create(number=number)
        payload = {
            "charge_type": kind.pk, "period": "2026-11-01",
            "due_date": "2026-11-15", "amount": "100.00", "description": "Cuota",
        }
        inserted = Event()
        release = Event()
        second_started = Event()
        second_pid = []
        original_save = Charge.save

        def hold_first_insert(instance, *args, **kwargs):
            result = original_save(instance, *args, **kwargs)
            if not inserted.is_set():
                inserted.set()
                if not release.wait(10):
                    raise RuntimeError("Timed out waiting for concurrent request")
            return result

        def send(second=False):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                if second:
                    second_pid.append(connection.connection.info.backend_pid)
                    second_started.set()
                response = client.post(URL, payload, format="json")
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with patch.object(Charge, "save", hold_first_insert):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(send)
                try:
                    self.assertTrue(inserted.wait(10))
                    second = executor.submit(send, True)
                    self.assertTrue(second_started.wait(10))
                    deadline = monotonic() + 5
                    waiting = False
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                                [second_pid[0]],
                            )
                            row = cursor.fetchone()
                        if row and row[0] == "Lock":
                            waiting = True
                            break
                        sleep(0.01)
                    self.assertTrue(waiting, "Second request must wait for PostgreSQL lock")
                finally:
                    release.set()
                first_status, first_data = first.result(timeout=10)
                second_status, second_data = second.result(timeout=10)
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(first_data, {
            "created_count": 2, "skipped_count": 0, "total_count": 2,
        })
        self.assertEqual(second_data, {
            "created_count": 0, "skipped_count": 2, "total_count": 2,
        })
        self.assertEqual(Charge.objects.count(), 2)
