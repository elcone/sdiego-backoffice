"""Opt-in integration tests using a private, finalized Mallard export package.

Set MALLARD_BANK_CSV to its bank_transactions.csv path to enable these tests.
"""

import csv
import os
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from django.test import TestCase

from core.bank_import import persist_transactions
from core.mallard_bank import COLUMNS, preflight_mallard_file
from core.models import BankTransaction


@skipUnless(os.environ.get('MALLARD_BANK_CSV'), 'Set MALLARD_BANK_CSV to run private historical integration tests.')
class MallardFullFileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.path = Path(os.environ['MALLARD_BANK_CSV'])

    def test_complete_989_row_contract_read_only_preflight(self):
        self.assertTrue(self.path.is_file(), 'Provide the private finalized export via MALLARD_BANK_CSV.')
        records, report = preflight_mallard_file(self.path, manifest_path=self.path.with_name('manifest.json'))
        self.assertEqual(report['total'], 989)
        self.assertEqual(report['accepted'], 989)
        self.assertEqual(report['rejected'], 0)
        self.assertEqual(report['duplicate_identities'], 0)
        self.assertEqual(report['conflicting_values'], 0)
        self.assertEqual(report['line_types'], {'CREDIT': 552, 'DEBIT': 147, 'COMMISSION': 145, 'VAT': 145})
        self.assertEqual(report['credit_total'], Decimal('726623.20'))
        self.assertEqual(report['debit_total'], Decimal('613361.43'))
        self.assertEqual(report['net_total'], Decimal('113261.77'))
        self.assertEqual(BankTransaction.objects.count(), 0)
        codes = {r.transaction_code for r in records}
        self.assertEqual(len(codes), 989)
        for record in records:
            self.assertEqual(record.transaction_code, record.source_values['transaction_code'])
            self.assertEqual(set(record.source_values), set(COLUMNS))
        # Read supporting references only; never import these layouts.
        for name in ['cf_group_deposits.csv', 'reviewed_payments.csv', 'payer_account_observations.csv', 'comunidad_feliz.csv']:
            with self.path.with_name(name).open(encoding='utf-8', newline='') as stream:
                referenced = {row['transaction_code'] for row in csv.DictReader(stream) if row['transaction_code']}
            self.assertTrue(referenced <= codes, name)

    def test_complete_file_identical_reimports_preserve_primary_keys(self):
        records, report = preflight_mallard_file(self.path, manifest_path=self.path.with_name('manifest.json'))
        self.assertEqual(report['rejected'], 0)
        self.assertEqual(persist_transactions(records), {'imported': 989, 'unchanged': 0})
        before = list(BankTransaction.objects.order_by('pk').values())
        repeated, report = preflight_mallard_file(self.path)
        self.assertEqual(report['unchanged'], 989)
        self.assertEqual(persist_transactions(repeated), {'imported': 0, 'unchanged': 989})
        self.assertEqual(list(BankTransaction.objects.order_by('pk').values()), before)
