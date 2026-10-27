from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from treasury.models import ChargeConcept, FeeConfiguration


class ChargeConceptTests(TestCase):
    def test_kinds_and_eligibility(self):
        for kind in ChargeConcept.Kind:
            concept = ChargeConcept.objects.create(name=kind.label, kind=kind)
            self.assertFalse(concept.is_surcharge_eligible)
            if kind != ChargeConcept.Kind.SURCHARGE:
                concept.is_surcharge_eligible = True
                concept.save()

    def test_surcharge_cannot_generate_surcharges(self):
        concept = ChargeConcept(
            name="Recargo",
            kind=ChargeConcept.Kind.SURCHARGE,
            is_surcharge_eligible=True,
        )
        with self.assertRaises(ValidationError):
            concept.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChargeConcept.objects.bulk_create([concept])

    def test_name_is_required_and_unique(self):
        with self.assertRaises(ValidationError):
            ChargeConcept.objects.create(name="", kind=ChargeConcept.Kind.FINE)
        ChargeConcept.objects.create(name="Multa", kind=ChargeConcept.Kind.FINE)
        with self.assertRaises(ValidationError):
            ChargeConcept.objects.create(
                name="Multa", kind=ChargeConcept.Kind.ORDINARY_FEE
            )

    def test_invalid_kind_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChargeConcept.objects.bulk_create(
                [ChargeConcept(name="Inválido", kind="OTHER")]
            )

    def test_configuration_protects_concept(self):
        from datetime import date

        from django.db.models.deletion import ProtectedError

        concept = ChargeConcept.objects.create(
            name="Cuota", kind=ChargeConcept.Kind.ORDINARY_FEE
        )
        FeeConfiguration.objects.create(
            concept=concept,
            amount="100.00",
            frequency="MONTHLY",
            start_date=date(2026, 1, 1),
        )
        with self.assertRaises(ProtectedError):
            concept.delete()
