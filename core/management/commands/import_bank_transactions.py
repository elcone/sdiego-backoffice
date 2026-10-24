from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.bank_import import persist_transactions
from core.mallard_bank import preflight_mallard_file


class Command(BaseCommand):
    help = "Import the verified 25-column Mallard v1.1 bank transaction CSV atomically."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=Path)
        parser.add_argument("--preflight", action="store_true", help="Validate and compare without writing.")
        parser.add_argument("--manifest", type=Path, help="Also verify the Mallard manifest hash/counts/totals.")

    def handle(self, *args, **options):
        try:
            records, report = preflight_mallard_file(options["csv_path"], manifest_path=options["manifest"])
            self.stdout.write(
                f"Total: {report['total']}; accepted: {report['accepted']}; rejected: {report['rejected']}; "
                f"duplicate identities: {report['duplicate_identities']}; conflicting values: {report['conflicting_values']}; "
                f"unchanged: {report['unchanged']}."
            )
            self.stdout.write(
                f"Credits: {report['credit_total']:.2f}; debits: {report['debit_total']:.2f}; "
                f"net: {report['net_total']:.2f}."
            )
            self.stdout.write("Line types: " + "; ".join(
                f"{line_type}: {report['line_types'].get(line_type, 0)}"
                for line_type in ["CREDIT", "DEBIT", "COMMISSION", "VAT"]
            ) + ".")
            for error in report["errors"]:
                self.stderr.write(f"CSV line {error['row']}: {' '.join(error['messages'])}")
            if report["rejected"]:
                raise CommandError("The entire file was rejected. No transactions were imported.")
            if options["preflight"]:
                self.stdout.write("Preflight only. No transactions were imported.")
                return
            result = persist_transactions(records)
        except ValidationError as error:
            raise CommandError(" ".join(error.messages) + " No transactions were imported.") from error
        self.stdout.write(f"Imported: {result['imported']}; unchanged: {result['unchanged']}.")
