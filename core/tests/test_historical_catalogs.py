"""Self-contained Stage A fixtures; no private historical CSV required."""
import csv
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.historical_catalogs import COLUMNS, COUNTS, VERSION, import_catalogs
from core.models import BankTransaction, ChargeConcept, Contact, FeeConfiguration, House, HouseContact, PayerAccount


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class CatalogImportTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.package = Path(self.tmp.name) / 'package'
        self.package.mkdir()
        original = self.package.parent / 'import-layouts-increment02-stable-identity'
        original.mkdir()
        source_schema = json.dumps({'houses.csv': {c: 'text nullable' for c in COLUMNS['houses']}}).encode()
        (original / 'schema.json').write_bytes(source_schema)
        self.manifest = dict(contract_version=VERSION, cutoff='2026-09-25', counts=COUNTS.copy(), files={}, source_sha256={'outputs/import-layouts-increment02-stable-identity/schema.json': sha(source_schema)})
        layouts = {n + '.csv': {'column_order': cols, 'columns': {c: {'type': 'text'} for c in cols}} for n, cols in COLUMNS.items()}
        for name in ['contacts', 'house_contacts', 'charge_concepts']:
            nullable = {'contacts': {'rfc', 'phone', 'email'}, 'house_contacts': {'is_responsible'}, 'charge_concepts': set()}[name]
            for field in layouts[name + '.csv']['columns']:
                layouts[name + '.csv']['columns'][field] = dict(type={'source_values': 'JSON', 'is_responsible': 'boolean true/false', 'is_adjustment': 'boolean true/false'}.get(field, 'text'), nullable=field in nullable)
            layouts[name + '.csv']['primary_key'] = {'contacts': ['contact_id'], 'house_contacts': ['house_number', 'contact_id'], 'charge_concepts': ['code']}[name]
        layouts['houses.csv'] = {'contract_reference': '../import-layouts-increment02-stable-identity/schema.json#houses.csv'}
        self.write('schema.json', json.dumps(dict(contract_version=VERSION, encoding='UTF-8 without BOM', delimiter=',', newline='LF', layouts=layouts)).encode())
        self.write('validation.json', json.dumps({'counts': COUNTS}).encode())
        self.rows = {n: [] for n in COUNTS}
        for number in [str(n) for n in range(6101, 6179)] + ['6185']:
            named = number != '6185'
            house = dict(number=number, owner_name='Directory owner ' + number if named else '', phone='', email='', source_reference='approved-directory')
            self.rows['houses'].append(house)
            if named:
                cid = 'DIRECTORY-OWNER-' + number
                self.rows['contacts'].append(dict(contact_id=cid, name=house['owner_name'], rfc='', phone='', email='', identity_scope='SOURCE_DIRECTORY_ROW', source_reference='approved-directory', source_values=json.dumps(house)))
                self.rows['house_contacts'].append(dict(house_number=number, contact_id=cid, role='OWNER', is_responsible='', relationship_status='DIRECTORY_RECORDED', source_reference='approved-directory'))
        self.rows['charge_concepts'] = [dict(code=code, name=name, description='Historical ' + name, is_adjustment='false', source_reference='approved-source') for code, name in [('CUOTA', 'Regular fee'), ('RECARGO', 'Late fee'), ('MULTA', 'Fine')]]
        for name in self.rows:
            self.write_rows(name)

    def write(self, name, raw):
        (self.package / name).write_bytes(raw)
        self.manifest['files'][name] = sha(raw)
        (self.package / 'manifest.json').write_text(json.dumps(self.manifest))

    def write_rows(self, name):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=COLUMNS[name], lineterminator='\n')
        writer.writeheader(); writer.writerows(self.rows[name])
        self.write(name + '.csv', stream.getvalue().encode())

    def assert_empty(self):
        self.assertEqual([House.objects.count(), Contact.objects.count(), HouseContact.objects.count(), ChargeConcept.objects.count()], [0, 0, 0, 0])

    def test_incompatible_houses_definitions_rejected_with_valid_manifest_hash(self):
        path = self.package.parent / 'import-layouts-increment02-stable-identity/schema.json'
        original = json.loads(path.read_bytes())
        for changes in [
            {'number': 'integer nullable'},
            {'owner_name': 'boolean nullable'},
            {'number': 'integer nullable', 'owner_name': 'boolean nullable'},
            {'number': 'text required'},
        ]:
            with self.subTest(changes=changes):
                schema = {'houses.csv': dict(original['houses.csv'], **changes)}
                raw = json.dumps(schema).encode()
                path.write_bytes(raw)
                key = 'outputs/import-layouts-increment02-stable-identity/schema.json'
                self.manifest['source_sha256'][key] = sha(raw)
                (self.package / 'manifest.json').write_text(json.dumps(self.manifest))
                self.assertEqual(self.manifest['source_sha256'][key], sha(path.read_bytes()))
                self.assertEqual(set(schema['houses.csv']), set(COLUMNS['houses']))
                with self.assertRaisesMessage(ValidationError, 'houses schema field definitions'):
                    import_catalogs(self.package)
                self.assert_empty()

    def test_preflight_and_reimports_preserve_ids_timestamps_and_evidence(self):
        report = import_catalogs(self.package, preflight=True)
        self.assertEqual({n: r['would_import'] for n, r in report.items()}, COUNTS)
        self.assert_empty()
        fees = list(FeeConfiguration.objects.values())
        report = import_catalogs(self.package)
        self.assertEqual({n: r['imported'] for n, r in report.items()}, COUNTS)
        models = [House, Contact, HouseContact, ChargeConcept]
        before = [list(m.objects.order_by('pk').values()) for m in models]
        report = import_catalogs(self.package)
        self.assertEqual({n: r['unchanged'] for n, r in report.items()}, COUNTS)
        self.assertEqual(before, [list(m.objects.order_by('pk').values()) for m in models])
        self.assertEqual(list(FeeConfiguration.objects.values()), fees)
        self.assertEqual((PayerAccount.objects.count(), BankTransaction.objects.count()), (0, 0))
        self.assertFalse(House.objects.get(number='6185').house_contacts.exists())
        self.assertEqual(HouseContact.objects.filter(is_responsible__isnull=True).count(), 78)
        c = Contact.objects.get(source_id='DIRECTORY-OWNER-6101')
        self.assertEqual(c.source_values, self.rows['contacts'][0])
        self.assertEqual(c.source_file_sha256, self.manifest['files']['contacts.csv'])
        self.assertEqual(c.source_row_number, 2)

    def test_directory_contacts_do_not_merge_with_existing_names(self):
        other = Contact.objects.create(name=self.rows['contacts'][0]['name'])
        import_catalogs(self.package)
        self.assertEqual(Contact.objects.filter(name=other.name).count(), 2)
        self.assertIsNone(other.source_id)

    def test_invalid_last_file_rejects_whole_package(self):
        self.rows['charge_concepts'][-1]['is_adjustment'] = 'true'
        self.write_rows('charge_concepts')
        with self.assertRaisesMessage(ValidationError, 'adjustment'):
            import_catalogs(self.package)
        self.assert_empty()

    def test_hash_header_counts_required_fields_and_fk_validation(self):
        path = self.package / 'contacts.csv'; raw = path.read_bytes()
        path.write_bytes(raw + b'bad')
        with self.assertRaisesMessage(ValidationError, 'SHA-256'): import_catalogs(self.package)
        path.write_bytes(raw)
        self.write('contacts.csv', raw.replace(b'contact_id,', b'wrong_header,', 1))
        with self.assertRaisesMessage(ValidationError, 'header'): import_catalogs(self.package)
        self.write('contacts.csv', raw)
        self.rows['contacts'][0]['name'] = ''
        self.write_rows('contacts')
        with self.assertRaisesMessage(ValidationError, 'required'): import_catalogs(self.package)
        self.rows['contacts'][0]['name'] = 'Directory owner 6101'; self.write_rows('contacts')
        self.rows['house_contacts'][0]['contact_id'] = 'MISSING'; self.write_rows('house_contacts')
        with self.assertRaisesMessage(ValidationError, 'reference'): import_catalogs(self.package)
        self.rows['house_contacts'][0]['contact_id'] = 'DIRECTORY-OWNER-6101'; self.write_rows('house_contacts')
        self.manifest['counts']['houses'] = 80
        (self.package / 'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaisesMessage(ValidationError, '79'): import_catalogs(self.package)
        self.assert_empty()

    def test_duplicate_source_id_and_unknown_role_rejected(self):
        self.rows['contacts'][1]['contact_id'] = self.rows['contacts'][0]['contact_id']; self.write_rows('contacts')
        with self.assertRaisesMessage(ValidationError, 'Duplicate'): import_catalogs(self.package)
        self.rows['contacts'][1]['contact_id'] = 'DIRECTORY-OWNER-6102'; self.write_rows('contacts')
        self.rows['house_contacts'][0]['is_responsible'] = 'false'; self.write_rows('house_contacts')
        with self.assertRaisesMessage(ValidationError, 'unknown'): import_catalogs(self.package)
        self.assert_empty()

    def test_late_database_conflict_rolls_back_prior_creates(self):
        conflict = ChargeConcept.objects.create(code='MULTA', name='Conflicting existing fine')
        before = list(ChargeConcept.objects.values())
        with self.assertRaisesMessage(ValidationError, 'conflicts'): import_catalogs(self.package)
        self.assertFalse(House.objects.exists()); self.assertFalse(Contact.objects.exists()); self.assertFalse(HouseContact.objects.exists())
        self.assertEqual(list(ChargeConcept.objects.values()), before)
        conflict.refresh_from_db(); self.assertIsNone(conflict.source_id)

    def test_existing_changes_rejected_in_preflight_and_import(self):
        import_catalogs(self.package)
        Contact.objects.filter(source_id='DIRECTORY-OWNER-6101').update(name='Reviewed elsewhere')
        for preflight in [True, False]:
            with self.assertRaisesMessage(ValidationError, 'conflicts'): import_catalogs(self.package, preflight=preflight)
        self.assertEqual(Contact.objects.get(source_id='DIRECTORY-OWNER-6101').name, 'Reviewed elsewhere')

    def test_schema_types_and_directory_evidence_are_validated(self):
        schema = json.loads((self.package / 'schema.json').read_bytes())
        schema['layouts']['contacts.csv']['columns']['contact_id']['nullable'] = True
        self.write('schema.json', json.dumps(schema).encode())
        with self.assertRaisesMessage(ValidationError, 'schema type'): import_catalogs(self.package)
        schema['layouts']['contacts.csv']['columns']['contact_id']['nullable'] = False
        self.write('schema.json', json.dumps(schema).encode())
        self.rows['contacts'][0]['source_values'] = '{}'; self.write_rows('contacts')
        with self.assertRaisesMessage(ValidationError, 'evidence differs'): import_catalogs(self.package)
        self.assert_empty()

    def test_source_ids_database_unique(self):
        Contact.objects.create(name='A', source_id='same')
        with self.assertRaises(IntegrityError), transaction.atomic(): Contact.objects.create(name='B', source_id='same')

    def test_command_preflight_and_atomic_validation_error(self):
        out = io.StringIO()
        call_command('import_historical_catalogs', str(self.package), '--dry-run', stdout=out)
        self.assertIn('would_import=79', out.getvalue()); self.assert_empty()
        self.rows['contacts'][0]['email'] = 'invalid'; self.write_rows('contacts')
        with self.assertRaises(CommandError): call_command('import_historical_catalogs', str(self.package), stdout=out)
        self.assert_empty()


@skipUnless(os.environ.get('SDIEGO_HISTORY_PACKAGE'), 'Set SDIEGO_HISTORY_PACKAGE for private historical integration tests.')
class HistoricalCatalogIntegrationTests(TestCase):
    def test_approved_package_counts_provenance_and_idempotency(self):
        directory = Path(os.environ['SDIEGO_HISTORY_PACKAGE'])
        self.assertEqual({n: r['would_import'] for n, r in import_catalogs(directory, preflight=True).items()}, COUNTS)
        self.assertEqual({n: r['imported'] for n, r in import_catalogs(directory).items()}, COUNTS)
        models = [House, Contact, HouseContact, ChargeConcept]
        before = [list(m.objects.order_by('pk').values()) for m in models]
        self.assertEqual({n: r['unchanged'] for n, r in import_catalogs(directory).items()}, COUNTS)
        self.assertEqual(before, [list(m.objects.order_by('pk').values()) for m in models])
        self.assertEqual((BankTransaction.objects.count(), PayerAccount.objects.count()), (0, 0))
        self.assertEqual(HouseContact.objects.filter(is_responsible=None).count(), 78)
        self.assertFalse(House.objects.get(number='6185').house_contacts.exists())
