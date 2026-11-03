import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from treasury.models import BankTransaction, ExpenseType, House


class ExpenseImportTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "resources").mkdir()
        self.path = self.root / "resources/gastos.csv"
        self.bank = self.create_bank()
        self.write([["001", "Mantenimiento", "Reparación administrativa"]])

    def create_bank(self, receipt="001", line_type="DEBIT", account="01"):
        return BankTransaction.objects.create(
            bank_account=account,
            bank_receipt=receipt,
            line_type=line_type,
            transaction_timestamp=timezone.now(),
            amount="100.00",
            balance="900.00",
            concept="Concepto bancario",
            description="Anterior",
            house=House.objects.get_or_create(number=1)[0],
            metadata={"original": True},
            is_reviewed=True,
        )

    def write(self, rows, headers=None):
        with self.path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(
                headers
                if headers is not None
                else ["bank_receipt", "expense_type", "description"]
            )
            writer.writerows(rows)

    def run_import(self, **options):
        output = StringIO()
        with override_settings(BASE_DIR=self.root):
            call_command("import_expenses", stdout=output, **options)
        return output.getvalue()

    def test_import_creates_type_and_preserves_other_fields(self):
        before = BankTransaction.objects.values().get(pk=self.bank.pk)
        self.run_import()
        after = BankTransaction.objects.values().get(pk=self.bank.pk)
        self.assertEqual(after.pop("description"), "Reparación administrativa")
        expense_type_id = after.pop("expense_type_id")
        self.assertEqual(
            ExpenseType.objects.get(pk=expense_type_id).name, "Mantenimiento"
        )
        before.pop("description")
        before.pop("expense_type_id")
        self.assertEqual(before, after)
        self.assertEqual(BankTransaction.objects.count(), 1)

    def test_reuses_existing_type(self):
        expense_type = ExpenseType.objects.create(name="Mantenimiento")
        self.run_import()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.expense_type_id, expense_type.pk)
        self.assertEqual(ExpenseType.objects.count(), 1)

    def test_excludes_all_non_debit_lines_with_same_receipt(self):
        others = [
            self.create_bank(line_type=value)
            for value in ["CREDIT", "COMMISSION", "COMMISSION_VAT", "OTHER"]
        ]
        before = list(
            BankTransaction.objects.filter(pk__in=[b.pk for b in others]).values()
        )
        self.run_import()
        self.assertEqual(
            before,
            list(
                BankTransaction.objects.filter(pk__in=[b.pk for b in others]).values()
            ),
        )

    def test_non_debit_alone_is_not_a_match(self):
        for line_type in ["CREDIT", "COMMISSION", "COMMISSION_VAT", "OTHER"]:
            with self.subTest(line_type=line_type):
                BankTransaction.objects.filter(pk=self.bank.pk).update(
                    line_type=line_type
                )
                with self.assertRaisesMessage(CommandError, "no existe"):
                    self.run_import()
        self.assertFalse(ExpenseType.objects.exists())

    def test_missing_receipt_aborts_all_changes(self):
        self.write([["001", "Mantenimiento", "Nueva"], ["missing", "Otro", ""]])
        before = list(BankTransaction.objects.values())
        with self.assertRaisesMessage(CommandError, "no existe"):
            self.run_import()
        self.assertEqual(before, list(BankTransaction.objects.values()))
        self.assertFalse(ExpenseType.objects.exists())

    def test_ambiguous_debits_across_accounts(self):
        self.create_bank(account="02")
        with self.assertRaisesMessage(CommandError, "ambiguo"):
            self.run_import()
        self.assertFalse(ExpenseType.objects.exists())

    def test_duplicate_receipts_even_with_different_values(self):
        for name in ["Mantenimiento", "Otro"]:
            with self.subTest(name=name):
                self.write([["001", "Mantenimiento", ""], ["001", name, "Otra"]])
                with self.assertRaisesMessage(CommandError, "duplicado"):
                    self.run_import()
                self.assertFalse(ExpenseType.objects.exists())

    def test_invalid_csv(self):
        cases = [
            (["receipt", "expense_type", "description"], [["001", "Tipo", ""]]),
            (None, [["001", "Tipo"]]),
            (None, [["001", "Tipo", "", "extra"]]),
            (None, [["", "Tipo", ""]]),
            (None, [["001", " ", ""]]),
            (None, [["001", "x" * 201, ""]]),
        ]
        for headers, rows in cases:
            with self.subTest(rows=rows, headers=headers):
                self.write(rows, headers)
                with self.assertRaises(CommandError):
                    self.run_import()
                self.assertFalse(ExpenseType.objects.exists())

    def test_empty_description_is_allowed(self):
        self.write([["001", "Mantenimiento", ""]])
        self.run_import()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.description, "")

    def test_identical_rerun_does_not_save_bank_or_duplicate_type(self):
        self.run_import()
        before = list(BankTransaction.objects.values())
        with patch.object(BankTransaction, "save") as save:
            output = self.run_import()
        save.assert_not_called()
        self.assertIn(
            "por actualizar: 0; sin cambios: 1; tipos de gasto nuevos: 0", output
        )
        self.assertEqual(before, list(BankTransaction.objects.values()))
        self.assertEqual(ExpenseType.objects.count(), 1)

    def test_preflight_counts_without_writing(self):
        before = list(BankTransaction.objects.values())
        output = self.run_import(preflight=True)
        self.assertIn(
            "por actualizar: 1; sin cambios: 0; tipos de gasto nuevos: 1", output
        )
        self.assertEqual(before, list(BankTransaction.objects.values()))
        self.assertFalse(ExpenseType.objects.exists())

    def test_runtime_validation_failure_rolls_back_writes(self):
        self.create_bank(receipt="002")
        self.write([["001", "Mantenimiento", "Nueva"], ["002", "Otro", "Nueva"]])
        before = list(BankTransaction.objects.order_by("pk").values())
        original_save = BankTransaction.save

        def failing_save(instance, *args, **kwargs):
            if instance.bank_receipt == "002":
                raise ValidationError("Error de validación")
            return original_save(instance, *args, **kwargs)

        with (
            patch.object(BankTransaction, "save", failing_save),
            self.assertRaises(CommandError),
        ):
            self.run_import()
        self.assertEqual(before, list(BankTransaction.objects.order_by("pk").values()))
        self.assertFalse(ExpenseType.objects.exists())

    def test_multiple_expenses_share_one_new_type(self):
        self.create_bank(receipt="002")
        self.write([["001", "Mantenimiento", "Uno"], ["002", "Mantenimiento", "Dos"]])
        output = self.run_import(preflight=True)
        self.assertIn(
            "por actualizar: 2; sin cambios: 0; tipos de gasto nuevos: 1", output
        )
        self.run_import()
        self.assertEqual(ExpenseType.objects.count(), 1)
        self.assertEqual(
            BankTransaction.objects.filter(expense_type__isnull=False).count(), 2
        )

    def test_explicit_relative_path_uses_backend_root(self):
        output = self.run_import(csv_path=Path("resources/gastos.csv"), preflight=True)
        self.assertIn("por actualizar: 1", output)

    def test_preflight_counts_already_classified_as_unchanged(self):
        self.run_import()
        output = self.run_import(preflight=True)
        self.assertIn(
            "por actualizar: 0; sin cambios: 1; tipos de gasto nuevos: 0", output
        )
