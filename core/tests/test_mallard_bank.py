"""Self-contained synthetic tests for the Mallard bank CSV adapter."""

import csv
import hashlib
import json
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.bank_import import persist_transactions
from core.mallard_bank import COLUMNS, VERIFIED_ACCOUNT, preflight_mallard_file
from core.models import BankTransaction


class MallardAdapterTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'bank_transactions.csv'

    def row(self, **overrides):
        row = dict.fromkeys(COLUMNS, '')
        row.update(
            transaction_code='BT-' + 'a' * 64, source_namespace='sdiego-banbajio-history',
            bank_account=VERIFIED_ACCOUNT, bank_receipt='000123', line_type='CREDIT',
            transaction_date='2026-03-01', transaction_time='12:34:56',
            description='Synthetic credit', debit='', credit='1000.00', amount='1000.00',
            bank_balance='2000.00', direction='CREDIT', classification='UNIDENTIFIED',
            reconciliation_status='UNASSIGNED', source_reference='synthetic#silver',
            bronze_date_text='01/03/2026', tracking_key='0000456', reviewed_payment_id='PAYMENT-TEST',
        )
        row.update(overrides)
        return row

    def write_rows(self, rows, columns=COLUMNS):
        with self.path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)

    def command(self, *args):
        output = StringIO()
        call_command('import_bank_transactions', str(self.path), *args, stdout=output, stderr=StringIO())
        return output.getvalue()

    def test_valid_command_and_identical_reimport_keep_primary_key_and_provenance(self):
        row = self.row(description='Original, description; preserved')
        self.write_rows([row])
        self.assertIn('Imported: 1; unchanged: 0', self.command())
        stored = BankTransaction.objects.get()
        original = BankTransaction.objects.values().get()
        self.assertEqual(stored.bank_account, '0392369300201')
        self.assertEqual(stored.bank_receipt, '000123')
        self.assertEqual(stored.transaction_time.isoformat(), '12:34:56')
        self.assertIsNone(stored.transaction_time.tzinfo)
        self.assertEqual(stored.source_values, row)
        self.assertEqual(stored.source_identifiers['tracking_key'], '0000456')
        self.assertEqual(stored.source_identifiers['reviewed_payment_id'], 'PAYMENT-TEST')
        self.assertEqual(stored.source_file_sha256, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertIn('Imported: 0; unchanged: 1', self.command())
        self.assertEqual(BankTransaction.objects.values().get(), original)

    def test_preflight_does_not_persist(self):
        self.write_rows([self.row()])
        self.assertIn('Preflight only', self.command('--preflight'))
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_shared_receipt_transfer_commission_and_vat_are_distinct_and_signed(self):
        rows = [self.row()]
        for line_type, amount, letter in [('DEBIT', '100.00', 'b'), ('COMMISSION', '7.50', 'c'), ('VAT', '1.20', 'd')]:
            rows.append(self.row(
                line_type=line_type, amount=amount, credit='', debit=amount,
                direction='DEBIT', transaction_code='BT-' + letter * 64,
            ))
        self.write_rows(rows)
        self.command()
        self.assertEqual(BankTransaction.objects.count(), 4)
        self.assertEqual(dict(BankTransaction.objects.values_list('line_type', 'amount')), {
            'CREDIT': Decimal('1000.00'), 'DEBIT': Decimal('-100.00'),
            'COMMISSION': Decimal('-7.50'), 'VAT': Decimal('-1.20'),
        })
        for stored in BankTransaction.objects.all():
            self.assertGreater(Decimal(stored.source_values['amount']), 0)

    def test_matching_identity_database_constraint_is_independent_of_fingerprint(self):
        self.write_rows([self.row()])
        records, _ = preflight_mallard_file(self.path)
        persist_transactions(records)
        duplicate = records[0]
        duplicate.transaction_code = 'BT-' + 'b' * 64
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_account_scope_is_part_of_database_identity(self):
        self.write_rows([self.row()])
        records, _ = preflight_mallard_file(self.path)
        persist_transactions(records)
        other = records[0]
        other.bank_account = '0392369300202'
        other.transaction_code = 'BT-' + 'b' * 64
        other.save()
        self.assertEqual(BankTransaction.objects.count(), 2)

    def test_changed_fingerprint_and_source_under_same_identity_are_conflicts(self):
        self.write_rows([self.row()])
        self.command()
        snapshot = BankTransaction.objects.values().get()
        for override in [
            {'description': 'Changed', 'transaction_code': 'BT-' + 'b' * 64},
            {'transaction_time': '12:34:57'}, {'bank_balance': '2000.01'},
            {'amount': '1000.01', 'credit': '1000.01'}, {'assigned_house': 'TEST-HOUSE'},
        ]:
            with self.subTest(override=override):
                self.write_rows([self.row(**override)])
                _, report = preflight_mallard_file(self.path)
                self.assertEqual(report['conflicting_values'], 1)
                self.assertEqual(report['accepted'], 0)
                with self.assertRaisesMessage(CommandError, 'entire file was rejected'):
                    self.command()
                self.assertEqual(BankTransaction.objects.values().get(), snapshot)

    def test_same_external_reference_cannot_be_reused_under_different_identity(self):
        self.write_rows([self.row()])
        self.command()
        self.write_rows([self.row(bank_receipt='000124')])
        _, report = preflight_mallard_file(self.path)
        self.assertEqual(report['conflicting_values'], 1)
        with self.assertRaises(ValidationError):
            records, _ = preflight_mallard_file(self.path)
            persist_transactions(records)
        self.assertEqual(BankTransaction.objects.count(), 1)

    def test_duplicate_identities_reject_all_matching_rows(self):
        self.write_rows([self.row(), self.row()])
        _, report = preflight_mallard_file(self.path)
        self.assertEqual(report['duplicate_identities'], 1)
        self.assertEqual(report['rejected'], 2)
        with self.assertRaises(CommandError):
            self.command()
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_conflicting_duplicates_within_file_are_reported(self):
        self.write_rows([self.row(), self.row(description='Different')])
        _, report = preflight_mallard_file(self.path)
        self.assertEqual(report['duplicate_identities'], 1)
        self.assertEqual(report['conflicting_values'], 2)

    def test_partial_validation_failure_rejects_whole_file(self):
        for overrides in [
            {'transaction_date': '2026-02-30'}, {'transaction_date': '2026-3-01'},
            {'transaction_time': '25:00:00'}, {'transaction_time': '12:00:00Z'},
            {'amount': '1.001'}, {'amount': 'NaN'}, {'credit': '999.99'},
            {'debit': '1.00'}, {'direction': 'DEBIT'}, {'line_type': 'UNKNOWN'},
            {'bank_account': '0392369300202'}, {'bank_receipt': ''},
            {'description': ''}, {'transaction_code': 'unknown'},
        ]:
            with self.subTest(overrides=overrides):
                self.write_rows([
                    self.row(bank_receipt='FIRST', transaction_code='BT-' + 'b' * 64),
                    self.row(**overrides),
                ])
                _, report = preflight_mallard_file(self.path)
                self.assertEqual(report['total'], 2)
                self.assertEqual(report['accepted'], 1)
                self.assertEqual(report['rejected'], 1)
                with self.assertRaises(CommandError):
                    self.command()
                self.assertEqual(BankTransaction.objects.count(), 0)

    def test_partial_existing_conflict_rejects_file(self):
        self.write_rows([self.row()])
        self.command()
        snapshot = BankTransaction.objects.values().get()
        self.write_rows([
            self.row(bank_receipt='NEW', transaction_code='BT-' + 'b' * 64),
            self.row(description='Changed'),
        ])
        with self.assertRaises(CommandError):
            self.command()
        self.assertEqual(BankTransaction.objects.values().get(), snapshot)

    def test_manifest_hash_mismatch_rejects_import_without_writes(self):
        self.write_rows([self.row()])
        manifest = {
            'contract_version': 'sdiego-history-v1.1', 'source_namespace': 'sdiego-banbajio-history',
            'files': {'bank_transactions.csv': hashlib.sha256(self.path.read_bytes()).hexdigest()},
            'counts': {'bank_transactions': 1}, 'credit_total': '1000.00', 'debit_total': '0.00',
        }
        manifest_path = self.path.with_name('manifest.json')
        manifest_path.write_text(json.dumps(manifest))
        _, report = preflight_mallard_file(self.path, manifest_path=manifest_path)
        self.assertEqual(report['accepted'], 1)
        self.write_rows([self.row(description='Changed after packaging')])
        with self.assertRaisesMessage(CommandError, 'do not match the manifest'):
            self.command('--manifest', str(manifest_path))
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_persistence_rolls_back_on_late_conflict_after_preflight(self):
        self.write_rows([self.row()])
        records, _ = preflight_mallard_file(self.path)
        persist_transactions(records)
        self.write_rows([
            self.row(bank_receipt='NEW', transaction_code='BT-' + 'b' * 64),
            self.row(description='Changed'),
        ])
        records, report = preflight_mallard_file(self.path)
        self.assertEqual(report['conflicting_values'], 1)
        with self.assertRaisesMessage(ValidationError, 'manual review'):
            persist_transactions(records)
        self.assertEqual(BankTransaction.objects.count(), 1)
        self.assertFalse(BankTransaction.objects.filter(bank_receipt='NEW').exists())

    def test_invalid_headers_with_data_rows(self):
        cases = [
            (COLUMNS[:-1], 'Expected exactly the 25 Mallard v1.1 bank CSV columns.'),
            (COLUMNS + ('unexpected',), 'Expected exactly the 25 Mallard v1.1 bank CSV columns.'),
            (COLUMNS[:-1] + (COLUMNS[0],), 'The CSV contains duplicate column names.'),
        ]
        row = self.row()
        for columns, message in cases:
            with self.subTest(columns=columns):
                with self.path.open('w', encoding='utf-8', newline='') as stream:
                    writer = csv.writer(stream, lineterminator='\n')
                    writer.writerow(columns)
                    writer.writerow([row.get(column, 'unexpected value') for column in columns])
                with self.assertRaisesMessage(ValidationError, message):
                    preflight_mallard_file(self.path)
                with self.assertRaisesMessage(CommandError, message):
                    self.command()
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_invalid_encoding_and_bom(self):
        self.write_rows([self.row()])
        self.path.write_bytes(b'\xef\xbb\xbf' + self.path.read_bytes())
        with self.assertRaisesMessage(ValidationError, 'without BOM'):
            preflight_mallard_file(self.path)
        self.path.write_bytes(b'\xff\xfeinvalid')
        with self.assertRaisesMessage(ValidationError, 'UTF-8'):
            preflight_mallard_file(self.path)
