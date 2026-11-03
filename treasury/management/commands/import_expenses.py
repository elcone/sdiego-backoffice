import csv
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from treasury.models import BankTransaction, ExpenseType


class Command(BaseCommand):
    help = "Clasifica gastos de movimientos DEBIT existentes de forma atómica."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path", nargs="?", type=Path, default=Path("resources/gastos.csv")
        )
        parser.add_argument(
            "--preflight",
            action="store_true",
            help="Valida y cuenta sin escribir datos.",
        )

    def handle(self, *args, **options):
        path = options["csv_path"]
        if not path.is_absolute():
            path = settings.BASE_DIR / path
        try:
            with transaction.atomic():
                updated, unchanged, created = self.import_expenses(
                    path, options["preflight"]
                )
        except (OSError, UnicodeError, csv.Error, ValidationError) as exc:
            raise CommandError(f"Importación cancelada: {exc}") from exc
        prefix = (
            "Simulación sin cambios. "
            if options["preflight"]
            else "Importación completada. "
        )
        self.stdout.write(
            f"{prefix}Movimientos por actualizar: {updated}; "
            f"sin cambios: {unchanged}; tipos de gasto nuevos: {created}."
        )

    def import_expenses(self, path, preflight):
        headers = ["bank_receipt", "expense_type", "description"]
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, strict=True)
            if reader.fieldnames != headers:
                raise CommandError(
                    "Columnas incorrectas: se requieren bank_receipt, expense_type, description."
                )
            rows = list(reader)

        receipts = set()
        pending = []
        for index, row in enumerate(rows, 2):
            if None in row or any(row[key] is None for key in headers):
                raise CommandError(f"Fila {index}: número de columnas incorrecto.")
            receipt = row["bank_receipt"]
            name = row["expense_type"]
            if not receipt.strip() or not name.strip():
                raise CommandError(f"Fila {index}: faltan valores requeridos.")
            BankTransaction._meta.get_field("bank_receipt").clean(receipt, None)
            ExpenseType._meta.get_field("name").clean(name, None)
            if receipt in receipts:
                raise CommandError(f"Fila {index}: recibo duplicado: {receipt}.")
            receipts.add(receipt)
            matches = BankTransaction.objects.filter(
                bank_receipt=receipt, line_type=BankTransaction.LineType.DEBIT
            ).order_by("pk")
            if not preflight:
                matches = matches.select_for_update()
            matches = list(matches[:2])
            if not matches:
                raise CommandError(
                    f"Fila {index}: no existe un movimiento DEBIT con recibo {receipt}."
                )
            if len(matches) > 1:
                raise CommandError(f"Fila {index}: recibo DEBIT ambiguo: {receipt}.")
            pending.append((matches[0], name, row["description"]))

        types = dict(
            ExpenseType.objects.filter(
                name__in={name for _, name, _ in pending}
            ).values_list("name", "pk")
        )
        missing = {name for _, name, _ in pending} - types.keys()
        created = 0
        if preflight:
            created = len(missing)
        else:
            for name in sorted(missing):
                expense_type, added = ExpenseType.objects.get_or_create(name=name)
                types[name] = expense_type.pk
                created += added
        updated = unchanged = 0
        for bank, name, description in pending:
            if (
                name in types
                and bank.expense_type_id == types[name]
                and bank.description == description
            ):
                unchanged += 1
                continue
            updated += 1
            if not preflight:
                bank.expense_type_id = types[name]
                bank.description = description
                bank.save(update_fields=["expense_type", "description"])
        return updated, unchanged, created
