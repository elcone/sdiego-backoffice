from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from core.historical_catalogs import import_catalogs


class Command(BaseCommand):
    help = ('Import confirmed Increment 03 catalogs atomically: houses -> directory contacts -> '
            'house contacts -> charge concepts. Payer accounts and financial data are excluded.')

    def add_arguments(self, parser):
        parser.add_argument('package', type=Path)
        parser.add_argument('--preflight', '--dry-run', action='store_true', dest='preflight', help='Validate package and stored conflicts without writing.')

    def handle(self, *args, **options):
        try:
            report = import_catalogs(options['package'], preflight=options['preflight'])
        except (ValidationError, DatabaseError) as error:
            raise CommandError(str(error)) from error
        for name, counts in report.items():
            self.stdout.write(f'{name}: ' + ', '.join(f'{key}={value}' for key, value in counts.items()))
        self.stdout.write('Unresolved payer ownership and responsibility remain unassigned; no financial records changed.')
