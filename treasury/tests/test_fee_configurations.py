from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from treasury.models import ChargeConcept, FeeConfiguration


class FeeConfigurationTests(TestCase):
    def setUp(self):
        self.concept = ChargeConcept.objects.create(name="Cuota", kind="ORDINARY_FEE")

    def test_monthly_inclusive_ranges_and_open_end(self):
        FeeConfiguration.objects.create(
            concept=self.concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 2, 28),
        )
        FeeConfiguration.objects.create(
            concept=self.concept,
            amount="120.00",
            frequency="MONTHLY",
            start_date=date(2026, 3, 1),
        )
        for start, end in (
            (date(2026, 2, 1), date(2026, 2, 28)),
            (date(2027, 1, 1), None),
        ):
            with self.subTest(start=start), self.assertRaises(ValidationError):
                FeeConfiguration.objects.create(
                    concept=self.concept,
                    amount="150.00",
                    frequency="MONTHLY",
                    start_date=start,
                    end_date=end,
                )

    def test_overlap_cannot_bypass_database(self):
        FeeConfiguration.objects.create(
            concept=self.concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            FeeConfiguration.objects.bulk_create(
                [
                    FeeConfiguration(
                        concept=self.concept,
                        amount="200.00",
                        frequency="MONTHLY",
                        start_date=date(2026, 2, 1),
                    )
                ]
            )

    def test_months_must_be_complete_including_leap_year(self):
        invalid_dates = [
            (date(2028, 1, 2), None),
            (date(2028, 1, 1), date(2028, 2, 28)),
            (date(2028, 3, 1), date(2028, 2, 29)),
        ]
        for start, end in invalid_dates:
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                FeeConfiguration.objects.create(
                    concept=self.concept,
                    amount="100.00",
                    frequency="MONTHLY",
                    start_date=start,
                    end_date=end,
                )
        FeeConfiguration.objects.create(
            concept=self.concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2028, 2, 1),
            end_date=date(2028, 2, 29),
        )

    def test_amount_must_be_positive(self):
        for amount in ("0.00", "-1.00"):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                FeeConfiguration.objects.create(
                    concept=self.concept,
                    amount=amount,
                    frequency="MONTHLY",
                    start_date=date(2026, 1, 1),
                )
            with self.assertRaises(IntegrityError), transaction.atomic():
                FeeConfiguration.objects.bulk_create(
                    [
                        FeeConfiguration(
                            concept=self.concept,
                            amount=amount,
                            frequency="MONTHLY",
                            start_date=date(2026, 1, 1),
                        )
                    ]
                )

    def test_frequency_fields_are_mutually_exclusive(self):
        for data in (
            {"frequency": "MONTHLY"},
            {
                "frequency": "MONTHLY",
                "start_date": date(2026, 1, 1),
                "generation_date": date(2026, 1, 1),
            },
            {"frequency": "ONE_TIME", "generation_date": date(2026, 1, 1)},
            {
                "frequency": "ONE_TIME",
                "generation_date": date(2026, 1, 10),
                "due_date": date(2026, 1, 9),
            },
            {
                "frequency": "ONE_TIME",
                "generation_date": date(2026, 1, 10),
                "due_date": date(2026, 1, 15),
                "start_date": date(2026, 1, 1),
            },
        ):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                FeeConfiguration.objects.create(
                    concept=self.concept, amount="100.00", **data
                )

    def test_database_rejects_invalid_frequency_dates(self):
        for data in (
            {"frequency": "MONTHLY", "start_date": date(2026, 1, 2)},
            {
                "frequency": "MONTHLY",
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 1, 30),
            },
            {
                "frequency": "ONE_TIME",
                "generation_date": date(2026, 1, 10),
                "due_date": date(2026, 1, 9),
            },
            {"frequency": "OTHER"},
        ):
            with (
                self.subTest(data=data),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                FeeConfiguration.objects.bulk_create(
                    [FeeConfiguration(concept=self.concept, amount="100.00", **data)]
                )

    def test_simultaneous_extraordinary_concepts_and_historical_surcharge(self):
        for name in ("Obra A", "Obra B"):
            concept = ChargeConcept.objects.create(name=name, kind="EXTRAORDINARY_FEE")
            FeeConfiguration.objects.create(
                concept=concept,
                amount="100.00",
                frequency="MONTHLY",
                start_date=date(2026, 1, 1),
            )
        surcharge = ChargeConcept.objects.create(name="Recargo", kind="SURCHARGE")
        for year in (2025, 2026):
            FeeConfiguration.objects.create(
                concept=surcharge,
                amount="50.00",
                frequency="MONTHLY",
                start_date=date(year, 1, 1),
                end_date=date(year, 12, 31),
            )

    def test_edit_checks_other_configurations_only(self):
        configuration = FeeConfiguration.objects.create(
            concept=self.concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        configuration.amount = "150.00"
        configuration.save()
