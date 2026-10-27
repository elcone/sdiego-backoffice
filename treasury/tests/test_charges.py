from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from treasury.models import Charge, ChargeConcept, FeeConfiguration, House


class ChargeTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(number="001")
        self.concept = ChargeConcept.objects.create(
            name="Cuota", kind="ORDINARY_FEE", is_surcharge_eligible=True
        )
        self.adjustment = ChargeConcept.objects.create(name="Ajuste", kind="ADJUSTMENT")
        self.original = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
        )

    def test_adjustments_can_exceed_original_and_inherit_eligibility(self):
        for amount in ("-200.00", "300.00"):
            charge = Charge.objects.create(
                house=self.house,
                concept=self.adjustment,
                adjusts_charge=self.original,
                amount=amount,
                period=date(2026, 1, 1),
                due_date=date(2026, 1, 5),
            )
            self.assertTrue(charge.is_surcharge_eligible)
            charge.refresh_from_db()
            self.assertTrue(charge.is_surcharge_eligible)
        other = Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="100.00",
            period=date(2026, 2, 1),
            due_date=date(2026, 2, 5),
        )
        self.concept.is_surcharge_eligible = False
        self.concept.save()
        charge = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=other,
            amount="-1.00",
            period=date(2026, 2, 1),
            due_date=date(2026, 2, 5),
        )
        self.assertTrue(charge.is_surcharge_eligible)

    def test_adjustment_requires_original_of_same_house(self):
        other_house = House.objects.create(number="002")
        for house, original in ((self.house, None), (other_house, self.original)):
            with self.subTest(house=house), self.assertRaises(ValidationError):
                Charge.objects.create(
                    house=house,
                    concept=self.adjustment,
                    adjusts_charge=original,
                    amount="-50.00",
                    period=date(2026, 1, 1),
                    due_date=date(2026, 1, 5),
                )

    def test_only_adjustments_allow_negative_amounts(self):
        with self.assertRaises(ValidationError):
            Charge.objects.create(
                house=self.house,
                concept=self.concept,
                amount="-1.00",
                period=date(2026, 1, 1),
                due_date=date(2026, 1, 5),
            )

    def test_period_and_self_reference(self):
        self.original.period = date(2026, 1, 2)
        with self.assertRaises(ValidationError):
            self.original.full_clean()
        self.original.period = date(2026, 1, 1)
        self.original.adjusts_charge = self.original
        with self.assertRaises(ValidationError):
            self.original.full_clean()

    def test_manual_charges_can_repeat_but_generated_charges_cannot(self):
        Charge.objects.create(
            house=self.house,
            concept=self.concept,
            amount="200.00",
            period=self.original.period,
            due_date=self.original.due_date,
        )
        configuration = FeeConfiguration.objects.create(
            concept=self.concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
        )
        Charge.objects.create(
            house=self.house,
            concept=self.concept,
            fee_configuration=configuration,
            amount="100.00",
            period=self.original.period,
            due_date=self.original.due_date,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Charge.objects.bulk_create(
                [
                    Charge(
                        house=self.house,
                        concept=self.concept,
                        fee_configuration=configuration,
                        amount="100.00",
                        period=self.original.period,
                        due_date=self.original.due_date,
                    )
                ]
            )
        with self.assertRaises(ProtectedError):
            configuration.delete()

    def test_model_validates_charge_relationships(self):
        for values in (
            {"concept": self.adjustment},
            {"concept": self.adjustment, "adjusts_charge_id": 999999},
            {"concept": self.concept, "adjusts_charge": self.original},
            {"concept": self.concept, "period": date(2026, 1, 2)},
        ):
            data = {
                "house": self.house,
                "amount": "10.00",
                "period": date(2026, 1, 1),
                "due_date": date(2026, 1, 5),
                **values,
            }
            with (
                self.subTest(values=values),
                self.assertRaises(ValidationError),
            ):
                Charge(**data).full_clean()

    def test_model_allows_editing_and_deleting_charges(self):
        self.original.amount = Decimal("200.00")
        self.original.save()
        self.original.refresh_from_db()
        self.assertEqual(self.original.amount, Decimal("200.00"))
        original_pk = self.original.pk
        self.original.delete()
        self.assertFalse(Charge.objects.filter(pk=original_pk).exists())

    def test_database_allows_updates_and_deletes_without_trigger(self):
        Charge.objects.filter(pk=self.original.pk).update(amount="200.00")
        self.original.refresh_from_db()
        self.assertEqual(self.original.amount, Decimal("200.00"))
        Charge.objects.filter(pk=self.original.pk).delete()
        self.assertFalse(Charge.objects.filter(pk=self.original.pk).exists())

    def test_cycles_cannot_be_introduced(self):
        adjustment = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.original,
            amount="-1.00",
            period=self.original.period,
            due_date=self.original.due_date,
        )
        self.original.adjusts_charge = adjustment
        self.original.concept = self.adjustment
        with self.assertRaises(ValidationError):
            self.original.save()

    def test_longer_adjustment_cycles_are_rejected(self):
        first = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.original,
            amount="-1.00",
            period=self.original.period,
            due_date=self.original.due_date,
        )
        second = Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=first,
            amount="-1.00",
            period=self.original.period,
            due_date=self.original.due_date,
        )
        first.adjusts_charge = second
        with self.assertRaises(ValidationError):
            first.save()

    def test_ordinary_edit_preserves_eligibility_snapshot(self):
        self.concept.is_surcharge_eligible = False
        self.concept.save()
        self.original.amount = Decimal("150.00")
        self.original.save()
        self.original.refresh_from_db()
        self.assertTrue(self.original.is_surcharge_eligible)

    def test_original_with_adjustments_cannot_be_deleted_or_move_houses(self):
        Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.original,
            amount="-1.00",
            period=self.original.period,
            due_date=self.original.due_date,
        )
        with self.assertRaises(ProtectedError):
            self.original.delete()
        self.original.house = House.objects.create(number="002")
        with self.assertRaises(ValidationError):
            self.original.save()

    def test_house_and_concept_are_protected(self):
        for obj in (self.house, self.concept):
            with self.assertRaises(ProtectedError):
                obj.delete()

    def test_model_copies_eligibility_for_adjustments(self):
        Charge.objects.create(
            house=self.house,
            concept=self.adjustment,
            adjusts_charge=self.original,
            amount="-300.00",
            period=date(2026, 1, 1),
            due_date=date(2026, 1, 5),
            is_surcharge_eligible=False,
        )
        self.assertTrue(
            Charge.objects.get(concept=self.adjustment).is_surcharge_eligible
        )

    def test_configuration_concept_must_match_charge(self):
        concept = ChargeConcept.objects.create(
            name="Otra cuota", kind="EXTRAORDINARY_FEE"
        )
        configuration = FeeConfiguration.objects.create(
            concept=concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
        )
        data = {
            "house": self.house,
            "concept": self.concept,
            "fee_configuration": configuration,
            "amount": "100.00",
            "period": date(2026, 1, 1),
            "due_date": date(2026, 1, 5),
        }
        with self.assertRaises(ValidationError):
            Charge.objects.create(**data)

    def test_adjustments_cannot_be_associated_with_generation_configuration(self):
        configuration = FeeConfiguration.objects.create(
            concept=self.adjustment,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
        )
        data = {
            "house": self.house,
            "concept": self.adjustment,
            "adjusts_charge": self.original,
            "fee_configuration": configuration,
            "amount": "100.00",
            "period": date(2026, 1, 1),
            "due_date": date(2026, 1, 5),
        }
        with self.assertRaises(ValidationError):
            Charge.objects.create(**data)
