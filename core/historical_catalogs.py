"""Explicit Stage A adapter for the immutable Increment 03 v1.0 package."""

import csv
import hashlib
import io
import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import ChargeConcept, Contact, House, HouseContact

VERSION = 'sdiego-increment03-history-v1.0'
COUNTS = {'houses': 79, 'contacts': 78, 'house_contacts': 78, 'charge_concepts': 3}
COLUMNS = {
    'houses': 'number owner_name phone email source_reference'.split(),
    'contacts': 'contact_id name rfc phone email identity_scope source_reference source_values'.split(),
    'house_contacts': 'house_number contact_id role is_responsible relationship_status source_reference'.split(),
    'charge_concepts': 'code name description is_adjustment source_reference'.split(),
}
MODELS = {'houses': House, 'contacts': Contact, 'house_contacts': HouseContact, 'charge_concepts': ChargeConcept}
BUSINESS = {
    'houses': ('number',), 'contacts': ('name', 'rfc', 'phone', 'email'),
    'house_contacts': ('house_id', 'contact_id', 'is_responsible'),
    'charge_concepts': ('code', 'name', 'description', 'is_adjustment', 'is_active'),
}
SOURCE = ('source_id', 'source_values')
PROVENANCE = ('source_file', 'source_file_sha256', 'source_row_number')


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def load_json(raw, label):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, f'{label}: duplicate JSON key {key}.')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as error:
        raise ValidationError(f'{label}: invalid JSON.') from error


def read_package(directory):
    """Validate bytes/contracts/relationships before consulting or writing the database."""
    directory = Path(directory)
    try:
        manifest = load_json((directory / 'manifest.json').read_bytes(), 'manifest.json')
        require(manifest['contract_version'] == VERSION, 'Unsupported historical contract version.')
        require(manifest['cutoff'] == '2026-09-25', 'Unexpected historical cutoff.')
        contents = {}
        for name, expected in manifest['files'].items():
            require(Path(name).name == name, 'Manifest file must be a local filename.')
            raw = (directory / name).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == expected, f'{name}: manifest SHA-256 mismatch.')
            contents[name] = raw
        require({'schema.json', 'validation.json', *(n + '.csv' for n in COUNTS)} <= contents.keys(), 'Manifest missing required files.')
        schema = load_json(contents['schema.json'], 'schema.json')
        validation = load_json(contents['validation.json'], 'validation.json')
        require(schema['contract_version'] == VERSION and schema['encoding'] == 'UTF-8 without BOM' and schema['delimiter'] == ',' and schema['newline'] == 'LF', 'Unsupported schema contract.')
        # Resolve the unchanged houses layout using the package's explicit source hash.
        house_contract = '../import-layouts-increment02-stable-identity/schema.json#houses.csv'
        require(schema['layouts']['houses.csv']['contract_reference'] == house_contract, 'Unexpected houses schema reference.')
        original_schema = directory.parent / 'import-layouts-increment02-stable-identity/schema.json'
        expected = manifest['source_sha256']['outputs/import-layouts-increment02-stable-identity/schema.json']
        original_raw = original_schema.read_bytes()
        require(hashlib.sha256(original_raw).hexdigest() == expected, 'Referenced houses schema SHA-256 mismatch.')
        houses_schema = load_json(original_raw, 'referenced schema')['houses.csv']
        expected_houses_schema = {
            'number': 'text nullable',
            'owner_name': 'text nullable',
            'phone': 'text nullable',
            'email': 'text nullable',
            'source_reference': 'text nullable',
        }
        require(houses_schema == expected_houses_schema, 'Referenced houses schema field definitions differ.')
        candidates = {}
        for name, expected_count in COUNTS.items():
            filename = name + '.csv'
            if name != 'houses':
                declared = schema['layouts'][filename]
                require(declared['column_order'] == COLUMNS[name] and set(declared['columns']) == set(COLUMNS[name]), f'{filename}: schema columns differ.')
                nullable = {'contacts': {'rfc', 'phone', 'email'}, 'house_contacts': {'is_responsible'}, 'charge_concepts': set()}[name]
                for field, definition in declared['columns'].items():
                    expected_type = {'source_values': 'JSON', 'is_responsible': 'boolean true/false', 'is_adjustment': 'boolean true/false'}.get(field, 'text')
                    require(definition == {'type': expected_type, 'nullable': field in nullable}, f'{filename}: schema type/nullability differs for {field}.')
                primary_keys = {'contacts': ['contact_id'], 'house_contacts': ['house_number', 'contact_id'], 'charge_concepts': ['code']}
                require(declared['primary_key'] == primary_keys[name], f'{filename}: schema primary key differs.')
            raw = contents[filename]
            require(not raw.startswith(b'\xef\xbb\xbf'), f'{filename}: UTF-8 BOM is not allowed.')
            reader = csv.reader(io.StringIO(raw.decode('utf-8'), newline=''), strict=True)
            require(next(reader, None) == COLUMNS[name], f'{filename}: incorrect CSV header.')
            entries = []
            for values in reader:
                require(len(values) == len(COLUMNS[name]), f'{filename} line {reader.line_num}: incorrect column count.')
                row = dict(zip(COLUMNS[name], values))
                required = [k for k in COLUMNS[name] if k not in {'owner_name', 'phone', 'email', 'rfc', 'is_responsible'}]
                require(all(row[k].strip() for k in required), f'{filename} line {reader.line_num}: required values missing.')
                entries.append((reader.line_num, row))
            require(len(entries) == manifest['counts'][name] == validation['counts'][name] == expected_count, f'{filename}: expected {expected_count} source rows.')
            candidates[name] = entries
        return validate_rows(candidates, contents)
    except (OSError, KeyError, TypeError, AttributeError, UnicodeError, csv.Error) as error:
        raise ValidationError(f'Historical package is unreadable or malformed: {error}') from error


def validate_rows(rows, contents):
    result = {}
    for name in COUNTS:
        result[name] = []
        for line, row in rows[name]:
            try:
                if name == 'houses':
                    key = row['number']
                    instance = House(number=key)
                elif name == 'contacts':
                    key = row['contact_id']
                    require(row['identity_scope'] == 'SOURCE_DIRECTORY_ROW', 'Contact identity must remain directory-scoped.')
                    instance = Contact(**{f: row[f] for f in BUSINESS[name]})
                    evidence = load_json(row['source_values'], 'contact source_values')
                    require(isinstance(evidence, dict), 'Contact source_values must be an object.')
                elif name == 'house_contacts':
                    key = json.dumps([row['house_number'], row['contact_id']], separators=(',', ':'))
                    require(row['role'] == 'OWNER' and row['relationship_status'] == 'DIRECTORY_RECORDED', 'Unsupported directory relationship.')
                    require(row['is_responsible'] == '', 'Historical responsibility must remain unknown.')
                    instance = HouseContact(is_responsible=None)
                else:
                    key = row['code']
                    require(row['is_adjustment'] == 'false', 'No adjustment concept is authorized in Stage A.')
                    instance = ChargeConcept(code=key, name=row['name'], description=row['description'], is_adjustment=False)
                instance.source_id = key
                instance.source_values = row.copy()
                instance.source_file = name + '.csv'
                instance.source_file_sha256 = hashlib.sha256(contents[name + '.csv']).hexdigest()
                instance.source_row_number = line
                instance.full_clean(exclude=['house', 'contact'] if name == 'house_contacts' else [], validate_unique=False, validate_constraints=False)
                require(not any(existing.source_id == key for existing in result[name]), 'Duplicate source identifier.')
                result[name].append(instance)
            except ValidationError as error:
                raise ValidationError([f'{name}.csv line {line}: {message}' for message in error.messages]) from error
    houses = {h.number: h for h in result['houses']}
    contacts = {c.source_id: c for c in result['contacts']}
    require(set(c.code for c in result['charge_concepts']) == {'CUOTA', 'RECARGO', 'MULTA'}, 'Unexpected charge concept codes.')
    related = set()
    for link in result['house_contacts']:
        row = link.source_values
        h, c = houses.get(row['house_number']), contacts.get(row['contact_id'])
        require(h is not None and c is not None, 'house_contacts.csv: missing house or contact reference.')
        require(h.number not in related, 'house_contacts.csv: duplicate directory owner relationship.')
        related.add(h.number)
        require(c.source_id == 'DIRECTORY-OWNER-' + h.number, 'Directory-scoped contact identifier differs from house.')
        original = load_json(c.source_values['source_values'], 'contact source_values')
        require(original == h.source_values, 'Contact directory evidence differs from house source.')
        require((c.name, c.phone, c.email) == (h.source_values['owner_name'], h.source_values['phone'], h.source_values['email']) and not c.rfc, 'Contact attributes differ from directory evidence.')
    require(related == {h.number for h in houses.values() if h.source_values['owner_name'].strip()}, 'Incomplete directory relationships.')
    require('6185' in houses and not houses['6185'].source_values['owner_name'] and '6185' not in related, 'House 6185 must remain without an owner.')
    require({c.source_id for c in result['contacts']} == {r.source_values['contact_id'] for r in result['house_contacts']}, 'Unreferenced directory contact.')
    return result


def check_existing(name, candidate, lookup):
    existing = MODELS[name].objects.filter(**lookup).first()
    if existing:
        require(all(getattr(existing, f) == getattr(candidate, f) for f in BUSINESS[name] + SOURCE),
                f'{name}.csv source {candidate.source_id}: stored record conflicts; manual review required.')
    # Detect source ID moved under another natural identity as well.
    other = MODELS[name].objects.filter(source_id=candidate.source_id).first()
    require(other is None or existing is not None and other.pk == existing.pk, f'{name}: source identifier conflicts with another record.')
    return existing


def import_catalogs(directory, *, preflight=False):
    """Whole-package atomic import. Preflight performs the same conflict checks without writes."""
    records = read_package(directory)
    summary = {name: {'imported': 0, 'unchanged': 0, 'would_import': 0} for name in COUNTS}
    resolved = {'houses': {}, 'contacts': {}}
    try:
        with transaction.atomic():
            # Serialize Stage A runs; unique constraints remain the final defense against other writers.
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [30260301])
            for name in COUNTS:
                for candidate in records[name]:
                    if name == 'houses': lookup = {'number': candidate.number}
                    elif name == 'contacts': lookup = {'source_id': candidate.source_id}
                    elif name == 'charge_concepts': lookup = {'code': candidate.code}
                    else:
                        row = candidate.source_values
                        house = resolved['houses'][row['house_number']]
                        contact = resolved['contacts'][row['contact_id']]
                        candidate.house_id = house.pk
                        candidate.contact_id = contact.pk
                        lookup = {'house_id': house.pk, 'contact_id': contact.pk}
                    existing = check_existing(name, candidate, lookup)
                    if existing:
                        summary[name]['unchanged'] += 1
                        stored = existing
                    elif preflight:
                        summary[name]['would_import'] += 1
                        stored = candidate
                    else:
                        candidate.save(force_insert=True)
                        summary[name]['imported'] += 1
                        stored = candidate
                    if name in resolved:
                        resolved[name][candidate.source_id] = stored
    except IntegrityError as error:
        raise ValidationError('Concurrent historical identity conflict; entire Stage A import rolled back.') from error
    return summary
