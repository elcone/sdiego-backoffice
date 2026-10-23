from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from core.models import FeeConfiguration


class FeeConfigurationTests(TestCase):
    def setUp(self):
        FeeConfiguration.objects.all().delete()

    def configuration(self, start=date(2026, 1, 1), end=date(2026, 1, 31), **overrides):
        values = dict(
            monthly_fee=Decimal("1000.00"),
            late_fee=Decimal("150.00"),
            payment_due_day=5,
            valid_from=start,
            valid_until=end,
        )
        values.update(overrides)
        return FeeConfiguration(**values)

    def test_valid_periods(self):
        for start, end in [
            (date(2026, 1, 1), date(2026, 1, 1)),
            (date(2026, 1, 2), date(2026, 1, 31)),
            (date(2026, 2, 1), None),
        ]:
            config = self.configuration(start, end)
            config.full_clean()
            config.save()
            config.full_clean()  # Updating itself must remain valid.

    def test_invalid_values(self):
        for values in [
            dict(monthly_fee=Decimal("-0.01")),
            dict(late_fee=Decimal("-0.01")),
            dict(payment_due_day=0),
            dict(payment_due_day=29),
            dict(valid_until=date(2025, 12, 31)),
            dict(monthly_fee=Decimal("1.001")),
        ]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.configuration(**values).full_clean()

    def test_database_checks(self):
        self.assertEqual(connection.vendor, "postgresql")
        for values in [
            dict(monthly_fee=Decimal("-0.01")),
            dict(late_fee=Decimal("-0.01")),
            dict(payment_due_day=0),
            dict(payment_due_day=29),
        ]:
            with (
                self.subTest(values=values),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.configuration(**values).save()

    def test_reversed_period_database_rejection(self):
        # PostgreSQL may reject the range expression before evaluating the check.
        from django.db import DatabaseError

        with self.assertRaises(DatabaseError), transaction.atomic():
            self.configuration(end=date(2025, 12, 31)).save()

    def test_overlaps_rejected_by_validation_and_database(self):
        self.configuration().save()
        for start, end in [
            (date(2026, 1, 1), date(2026, 1, 31)),
            (date(2025, 12, 1), date(2026, 2, 1)),
            (date(2026, 1, 10), date(2026, 1, 20)),
            (date(2025, 12, 1), date(2026, 1, 1)),
            (date(2026, 1, 31), date(2026, 2, 10)),
            (date(2025, 12, 1), None),
            (date(2026, 1, 15), None),
        ]:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValidationError):
                    self.configuration(start, end).full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.configuration(start, end).save()

    def test_existing_open_ended_period_rejects_future_periods(self):
        self.configuration(end=None).save()
        for end in [date(2027, 1, 31), None]:
            with self.subTest(end=end):
                with self.assertRaises(ValidationError):
                    self.configuration(date(2027, 1, 1), end).full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.configuration(date(2027, 1, 1), end).save()
        before = self.configuration(date(2025, 12, 1), date(2025, 12, 31))
        before.full_clean()
        before.save()

    def test_zero_amounts_and_due_day_boundaries(self):
        for day in [1, 28]:
            self.configuration(
                monthly_fee=Decimal("0.00"),
                late_fee=Decimal("0.00"),
                payment_due_day=day,
            ).full_clean()

    def test_decimal_precision(self):
        config = self.configuration(
            monthly_fee=Decimal("1234.56"), late_fee=Decimal("0.01")
        )
        config.full_clean()
        config.save()
        config.refresh_from_db()
        self.assertEqual(config.monthly_fee, Decimal("1234.56"))
        self.assertEqual(config.late_fee, Decimal("0.01"))
        self.assertIsInstance(config.monthly_fee, Decimal)
