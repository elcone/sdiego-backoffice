from datetime import date
from decimal import Decimal
from importlib import import_module

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from core.models import ChargeConcept, Contact, FeeConfiguration, House


class ReferenceDataTests(TestCase):
    def run_seed(self):
        migration = import_module("core.migrations.0003_initial_fee_configuration")
        state = MigrationLoader(connection).project_state(
            ("core", "0003_initial_fee_configuration")
        )
        with connection.schema_editor() as editor:
            migration.seed_initial_fee_configuration(state.apps, editor)

    def assert_initial_schedule(self):
        self.assertEqual(FeeConfiguration.objects.count(), 2)
        january = FeeConfiguration.objects.get(valid_from=date(2026, 1, 1))
        self.assertEqual(january.valid_until, date(2026, 1, 31))
        self.assertEqual(january.monthly_fee, Decimal("0.00"))
        february = FeeConfiguration.objects.get(valid_from=date(2026, 2, 1))
        self.assertIsNone(february.valid_until)
        self.assertEqual(february.monthly_fee, Decimal("1000.00"))
        for config in [january, february]:
            self.assertEqual(config.late_fee, Decimal("150.00"))
            self.assertEqual(config.payment_due_day, 5)
            config.full_clean()
        self.assertFalse(
            FeeConfiguration.objects.filter(valid_from=date(2026, 10, 23)).exists()
        )

    def test_initial_reference_data(self):
        # The test database is built by applying migrations from scratch.
        self.assert_initial_schedule()
        self.assertEqual(House.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)
        self.assertEqual(ChargeConcept.objects.count(), 0)

    def test_seed_is_repeatable(self):
        original = list(FeeConfiguration.objects.values())
        self.run_seed()
        self.run_seed()
        self.assert_initial_schedule()
        self.assertEqual(list(FeeConfiguration.objects.values()), original)

    def test_seed_from_empty_configuration_preserves_unrelated_data(self):
        FeeConfiguration.objects.all().delete()
        unrelated = FeeConfiguration.objects.create(
            valid_from=date(2025, 1, 1),
            valid_until=date(2025, 12, 31),
            monthly_fee=Decimal("25.00"),
            late_fee=Decimal("2.00"),
            payment_due_day=10,
        )
        snapshot = FeeConfiguration.objects.filter(pk=unrelated.pk).values().get()
        self.run_seed()
        self.run_seed()
        self.assertEqual(FeeConfiguration.objects.count(), 3)
        self.assertEqual(
            FeeConfiguration.objects.filter(pk=unrelated.pk).values().get(), snapshot
        )
        unrelated.delete()
        self.assert_initial_schedule()

    def test_conflicting_configuration_is_preserved_without_partial_seed(self):
        FeeConfiguration.objects.all().delete()
        FeeConfiguration.objects.create(
            valid_from=date(2026, 10, 23),
            monthly_fee=Decimal("500.00"),
            late_fee=Decimal("10.00"),
            payment_due_day=10,
        )
        snapshot = list(FeeConfiguration.objects.values())
        with self.assertRaisesMessage(RuntimeError, "Manual review is required"):
            self.run_seed()
        self.assertEqual(list(FeeConfiguration.objects.values()), snapshot)
