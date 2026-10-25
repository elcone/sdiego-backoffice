"""Synthetic Stage B contracts; private source integration is opt-in."""
import io
import json
import os
from datetime import date
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.historical_catalogs import import_catalogs
from core.historical_charges import COLUMNS, TYPES, import_charges
from core.models import Charge, ChargeExclusion, HISTORICAL_EXCLUDED_CHARGE_IDS, House, ChargeConcept, BankTransaction, Contact, HouseContact, FeeConfiguration
from core.tests import test_historical_catalogs as catalogs


class ChargeImportTests(TestCase):
    write = catalogs.CatalogImportTests.write
    write_rows = catalogs.CatalogImportTests.write_rows

    def setUp(self):
        catalogs.CatalogImportTests.setUp(self)
        import_catalogs(self.package)
        self.rows['charges'] = []
        for i in range(1106):
            if i < 948:
                code = 'CUOTA'; nominal = 100000 if i < 855 else 0
                day = '2026-10-01' if i < 225 else '2026-02-01'
            elif i < 1098:
                code = 'RECARGO'; nominal = 15000; day = '2026-02-06'
            else:
                code = 'MULTA'; nominal = 37500; day = '2026-02-10'
            original = dict(amount=nominal, date=day, detail='Approved historical charge', discount=0, house=6101, nominal=nominal, period=day[:7], reference='', type=code)
            key = f'SOURCE-{i:04d}'
            approved = dict(original, id=key, original=original.copy(), conflict=False, excluded=False, status='ORIGINAL', version=0)
            amount = format(Decimal(nominal) / 100, '.2f')
            self.rows['charges'].append(dict(charge_id=key, house_number='6101', concept_code=code, effective_date=day, period=day[:7], nominal_amount=amount, discount_amount='0.00', amount=amount, original_amount=amount, source_status='ORIGINAL', revision_version='0', revision_updated='', reference='', detail=original['detail'], source_reference='approved-source#' + key, original_record=json.dumps(original), approved_record=json.dumps(approved)))
        self.rows['excluded_charges'] = []
        for key in HISTORICAL_EXCLUDED_CHARGE_IDS:
            updated = '2026-10-03T17:24:23.908244+00:00'
            original = dict(id=key, reason='Approved duplicate exclusion', version=2, updated=updated, status='APROBADO', data='Legacy evidence retained verbatim')
            self.rows['excluded_charges'].append(dict(charge_id=key, house_number='6114', reason=original['reason'], revision_version='2', revision_updated=updated, evidence_ids=json.dumps(['receipt-evidence']), original_revision=json.dumps(original), source_reference='approved-source#' + key))
        schema = json.loads((self.package / 'schema.json').read_bytes())
        for name in ['charges', 'excluded_charges']:
            nullable = {'revision_updated', 'reference', 'detail'} if name == 'charges' else {'revision_updated'}
            layout = dict(column_order=COLUMNS[name], columns={key: dict(type=TYPES.get(key, 'text'), nullable=key in nullable) for key in COLUMNS[name]}, primary_key=['charge_id'])
            if name == 'charges': layout['foreign_keys'] = {'house_number': 'houses.csv:number', 'concept_code': 'charge_concepts.csv:code'}
            schema['layouts'][name + '.csv'] = layout
            self.manifest['counts'][name] = len(self.rows[name])
        self.write('schema.json', json.dumps(schema).encode())
        self.validation = dict(counts=self.manifest['counts'].copy(), charge_types={'CUOTA': 948, 'RECARGO': 150, 'MULTA': 8}, charge_total='880500.00', charge_total_at_cutoff='655500.00', excluded_charge_ids=list(HISTORICAL_EXCLUDED_CHARGE_IDS))
        self.write('validation.json', json.dumps(self.validation).encode())
        self.write_charges(); self.write_charges('excluded_charges')

    def write_charges(self, name='charges'):
        stream = io.StringIO(newline='')
        import csv
        writer = csv.DictWriter(stream, fieldnames=COLUMNS[name], lineterminator='\n')
        writer.writeheader(); writer.writerows(self.rows[name])
        self.write(name + '.csv', stream.getvalue().encode())

    def assert_no_charges(self):
        self.assertFalse(Charge.objects.exists()); self.assertFalse(ChargeExclusion.objects.exists())

    def assert_rejected_without_writes(self, message):
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(ValidationError, message):
                import_charges(self.package)
        self.assertFalse(any(query['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}
                             for query in queries.captured_queries))
        self.assert_no_charges()

    def test_original_amount_manipulation_rejected_despite_unchanged_totals(self):
        for index, amount in [(225, '1001.00'), (226, '999.00')]:
            row = self.rows['charges'][index]
            row.update(nominal_amount=amount, amount=amount)
            approved = json.loads(row['approved_record'])
            approved.update(nominal=int(Decimal(amount) * 100), amount=int(Decimal(amount) * 100))
            row['approved_record'] = json.dumps(approved)
        self.assertEqual(sum(Decimal(row['amount']) for row in self.rows['charges']), Decimal('880500.00'))
        self.assertEqual(sum(Decimal(row['amount']) for row in self.rows['charges']
                             if row['effective_date'] <= '2026-09-25'), Decimal('655500.00'))
        self.write_charges()  # Recalculates the CSV hash and manifest reference.
        self.assert_rejected_without_writes('ORIGINAL charge nominal differs')

    def test_original_house_and_other_source_fields_cannot_be_rewritten(self):
        original_row = self.rows['charges'][0].copy()
        for field, value, evidence_key in [
            ('house_number', '6103', 'house'),
            ('concept_code', 'RECARGO', 'type'),
            ('effective_date', '2026-10-02', 'date'),
            ('period', '2026-11', 'period'),
            ('reference', 'changed reference', 'reference'),
            ('detail', 'changed detail', 'detail'),
        ]:
            with self.subTest(field=field):
                row = dict(original_row, **{field: value})
                approved = json.loads(row['approved_record'])
                approved[evidence_key] = int(value) if evidence_key == 'house' else value
                row['approved_record'] = json.dumps(approved)
                self.rows['charges'][0] = row
                self.write_charges()
                self.assert_rejected_without_writes('ORIGINAL charge')

    def test_database_only_house_rejected_even_with_consistent_original_evidence(self):
        House.objects.create(number='9999', source_id='9999')
        row = self.rows['charges'][0]
        row['house_number'] = '9999'
        original = json.loads(row['original_record'])
        original['house'] = 9999
        approved = json.loads(row['approved_record'])
        approved.update(house=9999, original=original)
        row['original_record'] = json.dumps(original)
        row['approved_record'] = json.dumps(approved)
        self.write_charges()
        self.assert_rejected_without_writes('approved houses.csv package')

    def test_package_house_must_resolve_to_imported_stage_a_identity(self):
        House.objects.filter(number='6101').update(source_id=None)
        self.assert_rejected_without_writes('Imported Stage A house does not exist')

    def test_preflight_import_totals_provenance_idempotency_and_untouched_catalogs(self):
        models = [House, Contact, HouseContact, ChargeConcept, BankTransaction, FeeConfiguration]
        baseline = [list(m.objects.order_by('pk').values()) for m in models]
        preflight = import_charges(self.package, preflight=True)
        self.assertEqual(preflight['charges']['would_import'], 1106); self.assert_no_charges()
        result = import_charges(self.package)
        self.assertEqual(result['charges']['imported'], 1106)
        self.assertEqual(result['excluded_charges']['imported'], 3)
        self.assertEqual(Charge.objects.aggregate(total=Sum('amount'))['total'], Decimal('880500.00'))
        self.assertEqual(Charge.objects.filter(effective_date__lte='2026-09-25').aggregate(total=Sum('amount'))['total'], Decimal('655500.00'))
        before = [list(m.objects.order_by('pk').values()) for m in [Charge, ChargeExclusion]]
        result = import_charges(self.package)
        self.assertEqual(result['charges']['unchanged'], 1106); self.assertEqual(result['excluded_charges']['unchanged'], 3)
        self.assertEqual(before, [list(m.objects.order_by('pk').values()) for m in [Charge, ChargeExclusion]])
        self.assertEqual(baseline, [list(m.objects.order_by('pk').values()) for m in models])
        first = Charge.objects.get(source_id='SOURCE-0000')
        self.assertEqual(first.source_values, self.rows['charges'][0]); self.assertEqual(first.source_row_number, 2)
        self.assertEqual(first.source_file_sha256, self.manifest['files']['charges.csv'])
        self.assertFalse(Charge.objects.filter(source_id__in=HISTORICAL_EXCLUDED_CHARGE_IDS).exists())
        self.assertEqual(set(ChargeExclusion.objects.values_list('source_id', flat=True)), set(HISTORICAL_EXCLUDED_CHARGE_IDS))

    def test_approved_revision_discount_and_original_timestamp_preserved(self):
        row = self.rows['charges'][0]
        row.update(nominal_amount='1050.00', discount_amount='50.00', source_status='APROBADO', revision_version='1', revision_updated='2026-10-03T11:24:23.908244-06:00',
                   house_number='6103', effective_date='2026-10-02', period='2026-11', reference='Approved correction', detail='Revised detail')
        approved = json.loads(row['approved_record'])
        approved.update(nominal=105000, discount=5000, status='APROBADO', version=1,
                        house=6103, date='2026-10-02', period='2026-11', reference=row['reference'], detail=row['detail'])
        row['approved_record'] = json.dumps(approved)
        self.write_charges()
        import_charges(self.package)
        record = Charge.objects.get(source_id=row['charge_id'])
        self.assertEqual(record.amount, Decimal('1000.00'))
        self.assertEqual(record.original_amount, Decimal('1000.00'))
        self.assertEqual(record.discount_amount, Decimal('50.00'))
        self.assertEqual(record.house.number, '6103')
        self.assertEqual(record.effective_date, date(2026, 10, 2))
        self.assertEqual(record.period, '2026-11')
        self.assertEqual(record.revision_updated.isoformat(), '2026-10-03T17:24:23.908244+00:00')
        self.assertEqual(record.source_values['revision_updated'], row['revision_updated'])

    def test_hash_schema_and_count_rejected(self):
        path = self.package / 'charges.csv'; raw = path.read_bytes(); path.write_bytes(raw + b'invalid')
        with self.assertRaisesMessage(ValidationError, 'SHA-256'): import_charges(self.package)
        path.write_bytes(raw)
        schema = json.loads((self.package / 'schema.json').read_bytes()); schema['layouts']['charges.csv']['columns']['amount']['type'] = 'integer'
        self.write('schema.json', json.dumps(schema).encode())
        with self.assertRaisesMessage(ValidationError, 'schema'): import_charges(self.package)
        schema['layouts']['charges.csv']['columns']['amount']['type'] = 'decimal(18,2)'; self.write('schema.json', json.dumps(schema).encode())
        self.rows['charges'].pop(); self.write_charges()
        with self.assertRaisesMessage(ValidationError, 'count'): import_charges(self.package)
        self.assert_no_charges()

    def test_required_dates_amounts_ids_and_evidence_validation(self):
        original = self.rows['charges'][-1].copy()
        for changes in [{'effective_date': '2026-02-30'}, {'amount': '-1.00'}, {'amount': '1.001'}, {'amount': 'NaN'}, {'amount': '376.00'}, {'charge_id': HISTORICAL_EXCLUDED_CHARGE_IDS[0]}, {'charge_id': self.rows['charges'][0]['charge_id']}, {'source_reference': ''}, {'approved_record': '{}'}, {'original_amount': '1.00'}]:
            with self.subTest(changes=changes):
                self.rows['charges'][-1] = dict(original, **changes); self.write_charges()
                with self.assertRaises(ValidationError): import_charges(self.package)
                self.assert_no_charges()

    def test_nonexistent_house_and_concept_rejected(self):
        for field, value in [('house_number', 'missing'), ('concept_code', 'missing')]:
            old = self.rows['charges'][-1][field]; self.rows['charges'][-1][field] = value; self.write_charges()
            with self.assertRaisesMessage(ValidationError, 'does not exist'): import_charges(self.package)
            self.assert_no_charges(); self.rows['charges'][-1][field] = old

    def test_invalid_exclusion_and_financial_total_reject_all(self):
        self.rows['excluded_charges'][-1]['reason'] = 'Unsupported change'; self.write_charges('excluded_charges')
        with self.assertRaisesMessage(ValidationError, 'Exclusion evidence'): import_charges(self.package)
        self.assert_no_charges()
        self.rows['excluded_charges'][-1]['reason'] = 'Approved duplicate exclusion'; self.write_charges('excluded_charges')
        self.validation['charge_total'] = '1.00'; self.write('validation.json', json.dumps(self.validation).encode())
        with self.assertRaisesMessage(ValidationError, 'total'): import_charges(self.package)
        self.assert_no_charges()

    def test_conflicting_audit_blocks_import_without_changes(self):
        import_charges(self.package)
        Charge.objects.all().delete()
        ChargeExclusion.objects.filter(source_id=HISTORICAL_EXCLUDED_CHARGE_IDS[-1]).update(reason='Conflicting evidence')
        before = list(ChargeExclusion.objects.order_by('pk').values())
        for preflight in [True, False]:
            with self.assertRaisesMessage(ValidationError, 'conflicts'): import_charges(self.package, preflight=preflight)
        self.assertFalse(Charge.objects.exists()); self.assertEqual(before, list(ChargeExclusion.objects.order_by('pk').values()))

    def test_database_failure_after_charge_inserts_rolls_back_everything(self):
        with patch.object(ChargeExclusion, 'save', side_effect=IntegrityError('simulated concurrent failure')):
            with self.assertRaisesMessage(ValidationError, 'rolled back'): import_charges(self.package)
        self.assert_no_charges()

    def test_database_constraints_and_protected_references(self):
        import_charges(self.package)
        first = Charge.objects.first()
        for changes in [{'source_id': ''}, {'source_id': HISTORICAL_EXCLUDED_CHARGE_IDS[0]}, {'nominal_amount': -1}, {'discount_amount': -1}, {'discount_amount': 1001}, {'amount': -1}, {'original_amount': -1}, {'period': '2026-13'}, {'source_status': 'PENDING'}, {'house_id': 999999}]:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                Charge.objects.filter(pk=first.pk).update(**changes)
                connection.check_constraints()
        with self.assertRaises(IntegrityError), transaction.atomic():
            first.pk = None; first.save(force_insert=True)
        with self.assertRaises(ProtectedError): House.objects.get(number='6101').delete()
        with self.assertRaises(ProtectedError): ChargeConcept.objects.get(code='CUOTA').delete()

    def test_command_and_admin_permissions(self):
        out = io.StringIO(); call_command('import_historical_charges', str(self.package), '--dry-run', stdout=out)
        self.assertIn('would_import=1106', out.getvalue()); self.assert_no_charges()
        import_charges(self.package)
        superuser = get_user_model().objects.create_superuser(username='charges-admin')
        viewer = get_user_model().objects.create_user(username='charges-viewer', is_staff=True)
        viewer.user_permissions.add(*Permission.objects.filter(codename__in=['view_charge', 'view_chargeexclusion']))
        blocked = get_user_model().objects.create_user(username='charges-blocked', is_staff=True)
        for user in [superuser, viewer]:
            self.client.force_login(user)
            for model in [Charge, ChargeExclusion]:
                record = model.objects.first(); name = model._meta.model_name
                self.assertEqual(self.client.get(reverse(f'admin:core_{name}_changelist')).status_code, 200)
                url = reverse(f'admin:core_{name}_change', args=[record.pk])
                self.assertEqual(self.client.get(url).status_code, 200)
                before = list(model.objects.order_by('pk').values())
                self.assertEqual(self.client.post(url, {'amount': '1.00', 'source_values': '{}'}).status_code, 403)
                self.assertEqual(self.client.post(reverse(f'admin:core_{name}_add'), {}).status_code, 403)
                self.assertEqual(self.client.post(reverse(f'admin:core_{name}_delete', args=[record.pk]), {'post': 'yes'}).status_code, 403)
                self.assertEqual(before, list(model.objects.order_by('pk').values()))
        self.client.force_login(blocked)
        self.assertEqual(self.client.get(reverse('admin:core_charge_changelist')).status_code, 403)
        self.client.force_login(superuser)
        self.assertContains(self.client.get(reverse('admin:core_house_change', args=[House.objects.get(number='6101').pk])), 'SOURCE-0000')


@skipUnless(os.environ.get('SDIEGO_HISTORY_PACKAGE'), 'Set SDIEGO_HISTORY_PACKAGE for approved private package integration.')
class HistoricalChargeIntegrationTests(TestCase):
    def test_approved_source_totals_exclusions_and_identical_reimports(self):
        package = os.environ['SDIEGO_HISTORY_PACKAGE']; import_catalogs(package)
        baseline = [list(m.objects.order_by('pk').values()) for m in [House, Contact, HouseContact, ChargeConcept, FeeConfiguration]]
        self.assertEqual(import_charges(package, preflight=True)['charges']['would_import'], 1106)
        self.assertFalse(Charge.objects.exists())
        self.assertEqual(import_charges(package)['charges']['imported'], 1106)
        before = [list(m.objects.order_by('pk').values()) for m in [Charge, ChargeExclusion]]
        self.assertEqual(import_charges(package)['charges']['unchanged'], 1106)
        self.assertEqual(before, [list(m.objects.order_by('pk').values()) for m in [Charge, ChargeExclusion]])
        self.assertEqual(Charge.objects.aggregate(total=Sum('amount'))['total'], Decimal('880500.00'))
        self.assertEqual(Charge.objects.filter(effective_date__lte=date(2026,9,25)).aggregate(total=Sum('amount'))['total'], Decimal('655500.00'))
        self.assertEqual(Charge.objects.filter(house__isnull=False, concept__isnull=False).count(), 1106)
        self.assertEqual(ChargeExclusion.objects.count(), 3)
        self.assertEqual(baseline, [list(m.objects.order_by('pk').values()) for m in [House, Contact, HouseContact, ChargeConcept, FeeConfiguration]])
        self.assertFalse(BankTransaction.objects.exists())
