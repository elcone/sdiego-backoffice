from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from django.core import signing
from django.db import (
    IntegrityError,
    OperationalError,
    close_old_connections,
    connections,
)
from django.test import TransactionTestCase

from treasury.models import Charge, ChargeConcept, FeeConfiguration, House


class ObligationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.concept = ChargeConcept.objects.create(name="Cuota", kind="ORDINARY_FEE")
        self.house = House.objects.create(number="001")

    def test_concurrent_generation_is_idempotent(self):
        configuration = FeeConfiguration.objects.create(
            concept=self.concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
        )
        period = date(2026, 1, 1)
        token = signing.dumps(
            configuration.preview_signature(period, configuration.preview(period)),
            salt="treasury.fee-generation",
        )
        barrier = Barrier(2)

        def generate():
            close_old_connections()
            try:
                current = FeeConfiguration.objects.get(pk=configuration.pk)
                barrier.wait(timeout=10)
                return current.generate(period, preview_token=token)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: generate(), range(2)))
        self.assertCountEqual(results, [(1, 0), (0, 1)])
        self.assertEqual(Charge.objects.count(), 1)

    def test_database_excludes_concurrent_overlapping_ranges(self):
        barrier = Barrier(2)

        def insert():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                FeeConfiguration.objects.bulk_create(
                    [
                        FeeConfiguration(
                            concept_id=self.concept.pk,
                            amount="100.00",
                            frequency="MONTHLY",
                            start_date=date(2026, 1, 1),
                        )
                    ]
                )
                return "created"
            except (IntegrityError, OperationalError) as exc:
                self.assertIn(exc.__cause__.sqlstate, ("23P01", "40P01"))
                return "excluded"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: insert(), range(2)))
        self.assertCountEqual(results, ["created", "excluded"])
