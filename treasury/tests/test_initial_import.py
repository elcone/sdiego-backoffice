import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from treasury.models import BankTransaction, Charge, ChargeType, House


class InitialImportTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.resources = Path(self.directory.name)
        self.write("casas.csv", ["number"], [[1]])
        self.write("movimientos_bancarios.csv", [
            "bank_account", "bank_receipt", "line_type", "transaction_timestamp",
            "amount", "balance", "concept",
        ], [["001", "002", "CREDIT", "2026-02-01 16:16:02", "1000.00", "1250.50", "Original"]])
        self.write("movimientos_casas.csv", ["bank_receipt", "house_number"], [["002", 1]])
        self.write("cuotas_mensuales.csv", [
            "house_number", "charge_type", "period", "due_date", "amount", "description",
        ], [[1, "Cuota mensual", "2026-02-01", "2026-02-05", "1000.00", "Monthly fee"]])
        self.write("ajustes_cf.csv", ["house_number", "period", "amount", "description"], [
            [1, "2026-02-03", "-100.00", "Original adjustment"],
        ])

    def write(self, name, headers, rows):
        with (self.resources / name).open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(headers)
            writer.writerows(rows)

    def run_import(self):
        call_command("import_initial_data", resources=self.resources, stdout=StringIO())

    def test_import_and_unchanged_rerun(self):
        self.run_import()
        bank = BankTransaction.objects.get()
        self.assertEqual(bank.bank_account, "001")
        self.assertEqual(bank.transaction_timestamp.isoformat(), "2026-02-01T16:16:02+00:00")
        self.assertEqual(bank.house.number, 1)
        adjustment = Charge.objects.get(charge_type__name="Ajuste por pago en Comunidad Feliz")
        self.assertEqual(adjustment.period, adjustment.due_date)
        self.assertEqual(str(adjustment.amount), "-100.00")
        models = [House, BankTransaction, ChargeType, Charge]
        before = [list(model.objects.order_by("pk").values()) for model in models]
        self.run_import()
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in models])

    def test_unmatched_assignment_leaves_database_empty(self):
        self.write("movimientos_casas.csv", ["bank_receipt", "house_number"], [["missing", 1]])
        with self.assertRaises(CommandError):
            self.run_import()
        for model in [House, BankTransaction, ChargeType, Charge]:
            self.assertFalse(model.objects.exists())

    def test_conflicting_existing_bank_aborts_without_changes(self):
        self.run_import()
        BankTransaction.objects.update(balance="999.99")
        before = list(BankTransaction.objects.values())
        with self.assertRaises(CommandError):
            self.run_import()
        self.assertEqual(before, list(BankTransaction.objects.values()))
