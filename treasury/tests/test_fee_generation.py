from datetime import date
from unittest.mock import patch

from django.core import signing
from django.core.exceptions import ValidationError
from django.test import TestCase

from treasury.models import Charge, ChargeConcept, FeeConfiguration, House


class FeeGenerationTests(TestCase):
    def setUp(self):
        self.concept = ChargeConcept.objects.create(
            name="Cuota", kind="ORDINARY_FEE", is_surcharge_eligible=True
        )
        self.configuration = FeeConfiguration.objects.create(
            concept=self.concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.house = House.objects.create(number="001")
        House.objects.create(number="002")
        self.period = date(2026, 2, 1)

    def token(self):
        return signing.dumps(
            self.configuration.preview_signature(
                self.period, self.configuration.preview(self.period)
            ),
            salt="treasury.fee-generation",
        )

    def test_preview_does_not_create_charges_and_generation_is_idempotent(self):
        rows = self.configuration.preview(self.period)
        self.assertEqual(len(rows), 2)
        self.assertFalse(Charge.objects.exists())
        self.assertTrue(all(row["due_date"] == date(2026, 2, 5) for row in rows))
        token = self.token()
        self.assertEqual(
            self.configuration.generate(self.period, preview_token=token), (2, 0)
        )
        self.assertEqual(
            self.configuration.generate(self.period, preview_token=token), (0, 2)
        )
        self.assertTrue(
            all(row["exists"] for row in self.configuration.preview(self.period))
        )
        self.assertTrue(
            all(Charge.objects.values_list("is_surcharge_eligible", flat=True))
        )

    def test_only_missing_houses_are_generated(self):
        Charge.objects.create(
            house=self.house,
            concept=self.concept,
            fee_configuration=self.configuration,
            amount="100.00",
            period=self.period,
            due_date=date(2026, 2, 5),
        )
        self.assertEqual(
            self.configuration.generate(self.period, preview_token=self.token()), (1, 1)
        )

    def test_monthly_period_must_be_valid_and_effective(self):
        for period in (date(2026, 1, 2), date(2025, 12, 1), date(2027, 1, 1)):
            with self.subTest(period=period), self.assertRaises(ValidationError):
                self.configuration.preview(period)
        self.configuration.preview(date(2026, 1, 1))
        self.configuration.preview(date(2026, 12, 1))

    def test_one_time_uses_concrete_dates(self):
        configuration = FeeConfiguration.objects.create(
            concept=self.concept,
            amount="200.00",
            frequency="ONE_TIME",
            generation_date=date(2026, 2, 15),
            due_date=date(2026, 2, 22),
        )
        self.assertEqual(
            configuration.preview(self.period)[0]["due_date"], date(2026, 2, 22)
        )
        with self.assertRaises(ValidationError):
            configuration.preview(date(2026, 3, 1))
        token = signing.dumps(
            configuration.preview_signature(
                self.period, configuration.preview(self.period)
            ),
            salt="treasury.fee-generation",
        )
        self.assertEqual(
            configuration.generate(self.period, preview_token=token), (2, 0)
        )
        self.assertEqual(
            configuration.generate(self.period, preview_token=token), (0, 2)
        )
        self.assertEqual(
            set(Charge.objects.values_list("due_date", flat=True)), {date(2026, 2, 22)}
        )

    def test_adjustment_and_surcharge_are_not_generated(self):
        for kind in ("SURCHARGE", "ADJUSTMENT"):
            concept = ChargeConcept.objects.create(name=kind, kind=kind)
            configuration = FeeConfiguration.objects.create(
                concept=concept,
                amount="10.00",
                frequency="MONTHLY",
                start_date=date(2026, 1, 1),
            )
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                configuration.preview(self.period)

    def test_generation_rolls_back_entire_batch(self):
        original_save = Charge.save

        def fail_second(charge, *args, **kwargs):
            if charge.house_id != self.house.pk:
                raise ValidationError("Fallo de prueba")
            return original_save(charge, *args, **kwargs)

        token = self.token()
        with (
            patch.object(Charge, "save", fail_second),
            self.assertRaises(ValidationError),
        ):
            self.configuration.generate(self.period, preview_token=token)
        self.assertEqual(Charge.objects.count(), 0)

    def test_configuration_changes_do_not_rewrite_generated_charges(self):
        self.configuration.generate(self.period, preview_token=self.token())
        self.configuration.amount = "200.00"
        self.configuration.save()
        self.concept.is_surcharge_eligible = False
        self.concept.save()
        self.assertTrue(
            all(
                str(charge.amount) == "100.00" and charge.is_surcharge_eligible
                for charge in Charge.objects.all()
            )
        )

    def test_stale_or_forged_confirmation_cannot_generate(self):
        token = self.token()
        self.configuration.amount = "200.00"
        self.configuration.save()
        with self.assertRaises(ValidationError):
            self.configuration.generate(self.period, preview_token=token)
        with self.assertRaises(signing.BadSignature):
            self.configuration.generate(self.period, preview_token="invalid")
        self.assertFalse(Charge.objects.exists())

    def test_new_house_requires_new_preview(self):
        token = self.token()
        House.objects.create(number="003")
        with self.assertRaises(ValidationError):
            self.configuration.generate(self.period, preview_token=token)

    def test_changed_concept_requires_new_preview(self):
        token = self.token()
        self.concept.kind = "EXTRAORDINARY_FEE"
        self.concept.save()
        with self.assertRaises(ValidationError):
            self.configuration.generate(self.period, preview_token=token)

    def test_generation_with_no_houses(self):
        House.objects.all().delete()
        self.assertEqual(
            self.configuration.generate(self.period, preview_token=self.token()), (0, 0)
        )
