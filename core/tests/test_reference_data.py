from datetime import date
from decimal import Decimal
from importlib import import_module
from unittest import skip

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from core.models import ChargeConcept, Contact, FeeConfiguration, House


@skip("Reference-data migrations are deferred pending domain model approval.")
class ReferenceDataTests(TestCase):
    def run_correction(self):
        migration = import_module("core.migrations.0003_correct_initial_fee_schedule")
        state = MigrationLoader(connection).project_state(
            ("core", "0003_correct_initial_fee_schedule")
        )
        with connection.schema_editor() as editor:
            migration.correct_initial_fee_schedule(state.apps, editor)

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
        migration = import_module("core.migrations.0002_initial_reference_data")
        self.assertEqual(ChargeConcept.objects.count(), 7)
        self.assertSetEqual(
            set(ChargeConcept.objects.values_list("code", flat=True)),
            {code for code, _, _ in migration.CONCEPTS},
        )
        self.assertEqual(ChargeConcept.objects.filter(is_adjustment=True).count(), 3)
        self.assertEqual(ChargeConcept.objects.filter(is_active=True).count(), 7)
        self.assert_initial_schedule()
        self.assertEqual(House.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)

    def test_correction_is_repeatable(self):
        original_ids = set(FeeConfiguration.objects.values_list("pk", flat=True))
        self.run_correction()
        self.run_correction()
        self.assert_initial_schedule()
        self.assertSetEqual(
            set(FeeConfiguration.objects.values_list("pk", flat=True)), original_ids
        )

    def test_repeatable_original_seed_and_correction_of_previous_schedule(self):
        FeeConfiguration.objects.all().delete()
        migration = import_module("core.migrations.0002_initial_reference_data")
        state = MigrationLoader(connection).project_state(
            ("core", "0002_initial_reference_data")
        )
        with connection.schema_editor() as editor:
            migration.seed_reference_data(state.apps, editor)
            migration.seed_reference_data(state.apps, editor)
        old_config = FeeConfiguration.objects.get()
        self.assertEqual(old_config.valid_from, date(2026, 10, 23))
        self.run_correction()
        self.run_correction()
        self.assert_initial_schedule()
        self.assertEqual(ChargeConcept.objects.count(), 7)
