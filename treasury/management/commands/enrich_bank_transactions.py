from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.models import Payment
from treasury.bank_matching import build_trusted_evidence, identify_house
from treasury.bank_notes import update_automatic_notes
from treasury.bank_parsing import parse_bank_concept
from treasury.models import BankTransaction, House


class Command(BaseCommand):
    help = (
        "Extrae metadatos e identifica casas sin sobrescribir asociaciones existentes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run and connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, BankTransaction._meta.db_table
                )
            }
        schema_ready = {"metadata", "is_reviewed"} <= columns
        if not schema_ready and not dry_run:
            raise CommandError(
                "Debe aplicar la migración 0002 antes de persistir el enriquecimiento."
            )
        if not schema_ready:
            self.stdout.write(
                "Previsualización sin migración 0002: metadatos vacíos y revisión falsa."
            )
        houses = {house.number: house for house in House.objects.all()}
        houses_by_id = {house.pk: house for house in houses.values()}
        fields = [
            field.attname
            for field in BankTransaction._meta.fields
            if field.column in columns
        ]
        queryset = BankTransaction.objects.order_by("transaction_timestamp", "id")
        if not dry_run:
            queryset = queryset.select_for_update()
        banks = [BankTransaction(**row) for row in queryset.values(*fields)]
        historical = {}
        receipt_counts = Counter((bank.bank_receipt, bank.line_type) for bank in banks)
        for receipt, line_type, number, amount in Payment.objects.filter(
            assignment_approved=True,
            bank_transaction__line_type="CREDIT",
            house__isnull=False,
            effective_amount__gt=0,
        ).values_list(
            "bank_transaction__bank_receipt",
            "bank_transaction__line_type",
            "house__number",
            "bank_transaction__amount",
        ):
            key = (receipt, line_type)
            historical.setdefault(key, []).append((int(number), amount))
        stats = {
            "processed": len(banks),
            "metadata_updated": 0,
            "historical": 0,
            "assigned": 0,
            "ambiguous": 0,
            "unmatched": 0,
            "suggested": 0,
            "notes_updated": 0,
        }
        originals = {}
        historical_conflicts = []
        for bank in banks:
            if bank.house_id:
                bank.house = houses_by_id[bank.house_id]
            originals[bank.pk] = (
                bank.metadata.copy(),
                bank.description,
                bank.house_id,
                bank.is_reviewed,
            )
            metadata = bank.metadata.copy()
            metadata.pop("house_candidates", None)
            metadata.update(parse_bank_concept(bank.concept, houses))
            key = (bank.bank_receipt, bank.line_type)
            historical_rows = historical.get(key, [])
            if (
                bank.house_id
                and receipt_counts[key] == 1
                and historical_rows == [(bank.house.number, bank.amount)]
            ):
                metadata["assignment_source"] = "historical_core"
                stats["historical"] += 1
            else:
                if metadata.get("assignment_source") == "historical_core":
                    metadata.pop("assignment_source")
                if historical_rows:
                    historical_conflicts.append(bank.bank_receipt)
            bank.metadata = metadata
        evidence = build_trusted_evidence(banks)
        for bank in banks:
            if bank.line_type == "CREDIT":
                matching = identify_house(bank.metadata, evidence)
                bank.metadata["matching"] = matching
                if matching["ambiguous"]:
                    stats["ambiguous"] += 1
                    self.stdout.write(
                        f"Ambiguo: recibo={bank.bank_receipt}, fuente={matching['source']}, casas={matching['candidate_houses']}"
                    )
                if bank.house_id is None and not matching["ambiguous"]:
                    candidates = matching["candidate_houses"]
                    if candidates and matching["source"] in {
                        "concept",
                        "origin_account",
                    }:
                        bank.house = houses[candidates[0]]
                        bank.is_reviewed = False
                        stats["assigned"] += 1
                if (
                    bank.house_id is None
                    and matching["source"] == "ordering_party"
                    and not matching["ambiguous"]
                    and matching["candidate_houses"]
                ):
                    stats["suggested"] += 1
                    self.stdout.write(
                        f"Sugerencia: recibo={bank.bank_receipt}, casa={matching['candidate_houses'][0]}"
                    )
                if bank.house_id is None and not matching["candidate_houses"]:
                    stats["unmatched"] += 1
            else:
                bank.metadata.pop("matching", None)
            bank.description = update_automatic_notes(bank.description, bank.metadata)
            old_metadata, old_description, old_house_id, old_reviewed = originals[
                bank.pk
            ]
            changed = []
            if bank.metadata != old_metadata:
                stats["metadata_updated"] += 1
                changed.append("metadata")
            if bank.description != old_description:
                stats["notes_updated"] += 1
                changed.append("description")
            if bank.house_id != old_house_id:
                changed.append("house")
            if bank.is_reviewed != old_reviewed:
                changed.append("is_reviewed")
            if changed and not dry_run:
                bank.save(update_fields=changed)
        if historical_conflicts:
            self.stdout.write(
                f"Asociaciones históricas no coincidentes, conservadas: {historical_conflicts}"
            )
        self.stdout.write(
            f"{'Previsualización' if dry_run else 'Procesamiento'}: {stats}"
        )
