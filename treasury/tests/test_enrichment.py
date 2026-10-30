from datetime import UTC, date, datetime
from io import StringIO

from django.core.management import call_command
from django.test import TransactionTestCase

from core.models import BankTransaction as CoreBankTransaction
from core.models import House as CoreHouse
from core.models import Payment
from treasury.models import BankTransaction, House


class EnrichmentCommandTests(TransactionTestCase):
    def setUp(self):
        self.house = House.objects.create(number=6164)
        self.other_house = House.objects.create(number=6165)

    def bank(self, receipt, concept="", **kwargs):
        return BankTransaction.objects.create(
            bank_account="001",
            bank_receipt=receipt,
            line_type="CREDIT",
            transaction_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            amount="100.00",
            balance="100.00",
            concept=concept,
            **kwargs,
        )

    def run_command(self, dry_run=False):
        output = StringIO()
        call_command("enrich_bank_transactions", dry_run=dry_run, stdout=output)
        return output.getvalue()

    def snapshot(self):
        return list(BankTransaction.objects.order_by("id").values())

    def test_dry_run_has_no_changes(self):
        self.bank("001", "Concepto del Pago: 6164 |", description="Manual note")
        before = self.snapshot()
        output = self.run_command(dry_run=True)
        self.assertEqual(before, self.snapshot())
        self.assertIn("'assigned': 1", output)

    def test_existing_assignment_and_original_banking_values_are_preserved(self):
        bank = self.bank(
            "001",
            "Concepto del Pago: 6165 |",
            house=self.house,
            description="Manual note",
        )
        before = self.snapshot()[0]
        self.run_command()
        bank.refresh_from_db()
        self.assertEqual(bank.house_id, self.house.pk)
        self.assertFalse(bank.is_reviewed)
        self.assertNotIn("assignment_source", bank.metadata)
        for field in [
            "bank_account",
            "bank_receipt",
            "line_type",
            "transaction_timestamp",
            "amount",
            "balance",
            "concept",
        ]:
            self.assertEqual(getattr(bank, field), before[field])
        self.assertTrue(bank.description.startswith("Manual note"))

    def test_verified_historical_provenance_and_account_assignment(self):
        core_house = CoreHouse.objects.create(number="6164")
        core_bank = CoreBankTransaction.objects.create(
            bank_account="0001",
            bank_receipt="001",
            line_type="CREDIT",
            transaction_date=date(2026, 1, 1),
            transaction_time="12:00:00",
            description="Original",
            amount="100.00",
            transaction_code="bank-001",
            source_identifiers={},
            source_values={},
            source_file="bank.csv",
            source_file_sha256="0" * 64,
            source_row_number=1,
        )
        Payment.objects.create(
            source_id="payment-001",
            house=core_house,
            bank_transaction=core_bank,
            origin="BANCO_DIRECTO",
            real_date=date(2026, 1, 1),
            effective_date=date(2026, 1, 1),
            amount="100.00",
            effective_amount="100.00",
            assignment_approved=True,
            source_status="APROBADO",
        )
        historical = self.bank("001", "Cuenta Ordenante: 000123 |", house=self.house)
        pending = self.bank("002", "Cuenta Ordenante: 000123 |")
        self.run_command()
        historical.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(historical.metadata["assignment_source"], "historical_core")
        self.assertFalse(historical.is_reviewed)
        self.assertEqual(pending.house_id, self.house.pk)
        self.assertFalse(pending.is_reviewed)
        before = self.snapshot()
        output = self.run_command()
        self.assertEqual(before, self.snapshot())
        self.assertIn("'metadata_updated': 0", output)
        self.assertIn("'notes_updated': 0", output)

    def test_name_only_suggests_and_automatic_assignment_is_not_evidence(self):
        self.bank(
            "001",
            "Ordenante: Juan Perez Cuenta Ordenante: 001 | Concepto del Pago: 6164 |",
        )
        inferred = self.bank("002", "Cuenta Ordenante: 001 |")
        self.run_command()
        inferred.refresh_from_db()
        self.assertIsNone(inferred.house_id)
        trusted = self.bank(
            "003", "Ordenante: Juan Perez |", house=self.house, is_reviewed=True
        )
        suggested = self.bank("004", "Ordenante: JUAN  PEREZ |")
        self.run_command()
        suggested.refresh_from_db()
        self.assertIsNone(suggested.house_id)
        self.assertEqual(
            suggested.metadata["matching"]["candidate_houses"], [trusted.house.number]
        )
        self.assertIn("revisión humana", suggested.description)

    def test_noncredits_are_not_assigned(self):
        bank = self.bank("001", "Concepto del Pago: 6164 |")
        BankTransaction.objects.filter(pk=bank.pk).update(line_type="DEBIT")
        self.run_command()
        bank.refresh_from_db()
        self.assertIsNone(bank.house_id)
        self.assertNotIn("matching", bank.metadata)

    def test_ambiguous_account_keeps_pending_and_preserves_manual_notes(self):
        self.bank(
            "001", "Cuenta Ordenante: 000123 |", house=self.house, is_reviewed=True
        )
        self.bank(
            "002",
            "Cuenta Ordenante: 000123 |",
            house=self.other_house,
            is_reviewed=True,
        )
        pending = self.bank(
            "003", "Cuenta Ordenante: 000123 |", description="Manual note"
        )
        self.run_command()
        pending.refresh_from_db()
        self.assertIsNone(pending.house_id)
        self.assertTrue(pending.metadata["matching"]["ambiguous"])
        self.assertEqual(pending.metadata["matching"]["candidate_houses"], [6164, 6165])
        self.assertTrue(pending.description.startswith("Manual note\n"))
        before = self.snapshot()
        self.run_command()
        self.assertEqual(before, self.snapshot())

    def test_new_automatic_assignment_remains_unreviewed(self):
        pending = self.bank("001", "Concepto del Pago: 6164 |", is_reviewed=True)
        self.run_command()
        pending.refresh_from_db()
        self.assertEqual(pending.house_id, self.house.pk)
        self.assertFalse(pending.is_reviewed)
