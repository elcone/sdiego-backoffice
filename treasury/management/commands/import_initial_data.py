import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from treasury.models import BankTransaction, Charge, ChargeType, House


class Command(BaseCommand):
    help = "Importa los cinco CSV iniciales de treasury sin duplicar registros."

    def add_arguments(self, parser):
        parser.add_argument(
            "--resources", type=Path, default=settings.BASE_DIR / "resources"
        )

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                self.import_data(options["resources"])
        except (OSError, ValueError, InvalidOperation, ValidationError) as exc:
            raise CommandError(f"Importación cancelada: {exc}") from exc

    def read_csv(self, directory, name, headers):
        with (directory / name).open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != headers:
                raise CommandError(f"{name}: columnas incorrectas.")
            rows = list(reader)
        for index, row in enumerate(rows, 2):
            if None in row or any(row[key] is None for key in headers):
                raise CommandError(f"{name}:{index}: fila incompleta.")
            if any(not row[key].strip() for key in headers if key != "description"):
                raise CommandError(f"{name}:{index}: faltan valores requeridos.")
        return rows

    def import_data(self, directory):
        houses = self.read_csv(directory, "casas.csv", ["number"])
        banks = self.read_csv(
            directory,
            "movimientos_bancarios.csv",
            [
                "bank_account",
                "bank_receipt",
                "line_type",
                "transaction_timestamp",
                "amount",
                "balance",
                "concept",
            ],
        )
        assignments = self.read_csv(
            directory,
            "movimientos_casas.csv",
            [
                "bank_receipt",
                "house_number",
            ],
        )
        monthly = self.read_csv(
            directory,
            "cuotas_mensuales.csv",
            [
                "house_number",
                "charge_type",
                "period",
                "due_date",
                "amount",
                "description",
            ],
        )
        adjustments = self.read_csv(
            directory,
            "ajustes_cf.csv",
            [
                "house_number",
                "period",
                "amount",
                "description",
            ],
        )
        numbers = [
            House._meta.get_field("number").clean(row["number"], None) for row in houses
        ]
        if len(numbers) != len(set(numbers)):
            raise CommandError("casas.csv: números duplicados.")
        number_set = set(numbers)
        bank_values = {}
        credits = {}
        for row in banks:
            values = dict(row)
            stamp = datetime.fromisoformat(values["transaction_timestamp"])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp.utcoffset().total_seconds() != 0:
                raise CommandError("La fecha bancaria debe estar en UTC.")
            values["transaction_timestamp"] = stamp
            values["amount"] = Decimal(values["amount"])
            values["balance"] = Decimal(values["balance"])
            instance = BankTransaction(**values)
            instance.full_clean(validate_unique=False, validate_constraints=False)
            identity = tuple(
                values[key] for key in ("bank_account", "bank_receipt", "line_type")
            )
            if identity in bank_values:
                raise CommandError(f"Identidad bancaria duplicada: {identity}.")
            bank_values[identity] = values
            if values["line_type"] == BankTransaction.LineType.CREDIT:
                credits.setdefault(values["bank_receipt"], []).append(identity)
        assigned = {}
        for row in assignments:
            number = int(row["house_number"])
            matches = credits.get(row["bank_receipt"], [])
            if number not in number_set or len(matches) != 1:
                raise CommandError(f"Referencia de casa o abono ambigua: {row}.")
            identity = matches[0]
            if identity in assigned:
                raise CommandError(
                    f"Asignación bancaria repetida: {row['bank_receipt']}."
                )
            assigned[identity] = number
        charges = []
        for rows, is_adjustment in [(monthly, False), (adjustments, True)]:
            for row in rows:
                number = int(row["house_number"])
                if number not in number_set:
                    raise CommandError(f"Casa inexistente: {number}.")
                name = (
                    "Ajuste por pago en Comunidad Feliz"
                    if is_adjustment
                    else row["charge_type"]
                )
                if name not in {"Cuota mensual", "Ajuste por pago en Comunidad Feliz"}:
                    raise CommandError(f"Tipo de cargo inesperado: {name}.")
                period = date.fromisoformat(row["period"])
                due_date = (
                    period if is_adjustment else date.fromisoformat(row["due_date"])
                )
                amount = Charge._meta.get_field("amount").clean(
                    Decimal(row["amount"]), None
                )
                charges.append(
                    (number, name, period, due_date, amount, row["description"])
                )
        if len(charges) != len(set(charges)):
            raise CommandError("Existen cargos duplicados en los CSV.")

        existing_banks = {}
        for bank in BankTransaction.objects.select_related("house").all():
            identity = (bank.bank_account, bank.bank_receipt, bank.line_type)
            values = bank_values.get(identity)
            if values is None or any(
                getattr(bank, key) != value for key, value in values.items()
            ):
                raise CommandError(f"Movimiento existente en conflicto: {identity}.")
            if bank.house_id is not None and bank.house.number != assigned.get(
                identity
            ):
                raise CommandError(f"Asignación existente en conflicto: {identity}.")
            existing_banks[identity] = bank
        expected_charges = set(charges)
        existing_charges = set()
        for charge in Charge.objects.select_related("house", "charge_type").all():
            key = (
                charge.house.number,
                charge.charge_type.name,
                charge.period,
                charge.due_date,
                charge.amount,
                charge.description,
            )
            if key not in expected_charges or key in existing_charges:
                raise CommandError(
                    f"Cargo existente duplicado o en conflicto: {charge.pk}."
                )
            existing_charges.add(key)
        types = {}
        for name in ("Cuota mensual", "Ajuste por pago en Comunidad Feliz"):
            matches = list(ChargeType.objects.filter(name=name))
            if len(matches) > 1:
                raise CommandError(f"Tipo de cargo ambiguo: {name}.")
            types[name] = matches[0] if matches else None

        created = dict(
            houses=0, banks=0, assignments=0, monthly=0, adjustments=0, types=0
        )
        house_objects = {}
        for number in numbers:
            house_objects[number], added = House.objects.get_or_create(number=number)
            created["houses"] += added
        for identity, values in bank_values.items():
            if identity not in existing_banks:
                existing_banks[identity] = BankTransaction.objects.create(**values)
                created["banks"] += 1
        for identity, number in assigned.items():
            bank = existing_banks[identity]
            if bank.house_id is None:
                bank.house = house_objects[number]
                bank.save(update_fields=["house"])
                created["assignments"] += 1
        for name, instance in types.items():
            if instance is None:
                types[name] = ChargeType.objects.create(name=name)
                created["types"] += 1
        for key in charges:
            if key in existing_charges:
                continue
            number, name, period, due_date, amount, description = key
            Charge.objects.create(
                house=house_objects[number],
                charge_type=types[name],
                period=period,
                due_date=due_date,
                amount=amount,
                description=description,
            )
            created["monthly" if name == "Cuota mensual" else "adjustments"] += 1
        self.stdout.write(f"Registros creados o asignados: {created}")
        self.stdout.write(
            f"Fuentes verificadas: casas={len(numbers)}, movimientos={len(banks)}, "
            f"asignaciones={len(assigned)}, cuotas={len(monthly)}, ajustes={len(adjustments)}. "
            "Registros rechazados o sin resolver: 0."
        )
