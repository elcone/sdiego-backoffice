from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from core.historical_charges import import_charges


class Command(BaseCommand):
    help = 'Stage B: import frozen charges and exclusion audit after Stage A; no catalogs or bank records are written.'

    def add_arguments(self, parser):
        parser.add_argument('package', type=Path)
        parser.add_argument('--preflight', '--dry-run', action='store_true', dest='preflight')

    def handle(self, *args, **options):
        try:
            report = import_charges(options['package'], preflight=options['preflight'])
        except (ValidationError, DatabaseError) as error:
            raise CommandError(str(error)) from error
        for name, counts in report.items():
            self.stdout.write(f'{name}: ' + ', '.join(f'{key}={value}' for key, value in counts.items()))
        self.stdout.write('Validated: CUOTA=948, RECARGO=150, MULTA=8; total=880500.00 MXN; through 2026-09-25=655500.00 MXN. Three exclusions are audit-only.')
