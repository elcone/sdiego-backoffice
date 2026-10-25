from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from core.historical_payments import import_payments


class Command(BaseCommand):
    help = 'Stage C: import approved payment decisions, CF reconciliation and receipt evidence after Stage A/B and bank import.'

    def add_arguments(self, parser):
        parser.add_argument('package', type=Path, help='Increment 03 package; referenced immutable Increment 02 package must be its sibling.')
        parser.add_argument('--preflight', '--dry-run', action='store_true', dest='preflight', help='Validate all sources, references and stored conflicts without writing.')

    def handle(self, *args, **options):
        try:
            report = import_payments(options['package'], preflight=options['preflight'])
        except (ValidationError, DatabaseError) as error:
            raise CommandError(str(error)) from error
        for name, counts in report.items():
            self.stdout.write(f'{name}: ' + ', '.join(f'{key}={value}' for key, value in counts.items()))
        self.stdout.write('No bank movements, historical catalogs, charges or fee configurations were imported or changed.')
