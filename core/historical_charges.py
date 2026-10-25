"""Stage B: explicit immutable charge/exclusion adapter, with no fee recalculation."""
import csv
import hashlib
import io
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

from .historical_catalogs import load_json, read_package as read_catalog_package, require
from .models import Charge, ChargeConcept, ChargeExclusion, House, HISTORICAL_EXCLUDED_CHARGE_IDS

COLUMNS = {
    'charges': 'charge_id house_number concept_code effective_date period nominal_amount discount_amount amount original_amount source_status revision_version revision_updated reference detail source_reference original_record approved_record'.split(),
    'excluded_charges': 'charge_id house_number reason revision_version revision_updated evidence_ids original_revision source_reference'.split(),
}
COUNTS = {'charges': 1106, 'excluded_charges': 3}
TYPES = {
    **{key: 'decimal(18,2)' for key in ['nominal_amount', 'discount_amount', 'amount', 'original_amount']},
    **{key: 'JSON' for key in ['original_record', 'approved_record', 'evidence_ids', 'original_revision']},
    'effective_date': 'date YYYY-MM-DD', 'period': 'period YYYY-MM',
    'revision_version': 'integer', 'revision_updated': 'original ISO-8601 timestamp',
}
BUSINESS = {
    Charge: 'house_id concept_id effective_date period nominal_amount discount_amount amount original_amount source_status revision_version revision_updated reference detail'.split(),
    ChargeExclusion: 'house_id reason revision_version revision_updated evidence_ids'.split(),
}


def money(value):
    require(bool(re.fullmatch(r'[0-9]+\.[0-9]{2}', value)), 'Money must be nonnegative with exactly two decimal places.')
    require(len(value.split('.')[0]) <= 16, 'Money exceeds decimal(18,2).')
    return Decimal(value)


def timestamp(value):
    parsed = parse_datetime(value)
    require(parsed is not None and is_aware(parsed), 'Revision timestamp must include its original timezone.')
    return parsed


def provenance(instance, row, raw, filename, line):
    instance.source_id = row['charge_id']
    instance.source_values = row.copy()
    instance.source_file = filename
    instance.source_file_sha256 = hashlib.sha256(raw).hexdigest()
    instance.source_row_number = line
    instance.full_clean(exclude=['house', 'concept'], validate_unique=False, validate_constraints=False)
    return instance


def read_charges(directory):
    # Reuse Stage A's approved byte/hash and package validation without changing it.
    catalogs = read_catalog_package(directory)
    package_houses = {house.number: house for house in catalogs['houses']}
    directory = Path(directory)
    try:
        manifest = load_json((directory / 'manifest.json').read_bytes(), 'manifest.json')
        schema_raw = (directory / 'schema.json').read_bytes()
        require(hashlib.sha256(schema_raw).hexdigest() == manifest['files']['schema.json'], 'schema.json: hash mismatch.')
        schema = load_json(schema_raw, 'schema.json')['layouts']
        validation_raw = (directory / 'validation.json').read_bytes()
        require(hashlib.sha256(validation_raw).hexdigest() == manifest['files']['validation.json'], 'validation.json: hash mismatch.')
        validation = load_json(validation_raw, 'validation.json')
        houses = {h.source_id: h for h in House.objects.filter(
            source_id__in=[house.source_id for house in package_houses.values()]
        )}
        concepts = {c.code: c for c in ChargeConcept.objects.all()}
        candidates = {'charges': [], 'excluded_charges': []}
        seen = set()
        for name in candidates:
            filename = name + '.csv'
            nullable = {'revision_updated', 'reference', 'detail'} if name == 'charges' else {'revision_updated'}
            expected = dict(column_order=COLUMNS[name], columns={key: {'type': TYPES.get(key, 'text'), 'nullable': key in nullable} for key in COLUMNS[name]}, primary_key=['charge_id'])
            if name == 'charges':
                expected['foreign_keys'] = {'house_number': 'houses.csv:number', 'concept_code': 'charge_concepts.csv:code'}
            require(schema[filename] == expected, f'{filename}: schema field definitions or relationships differ.')
            raw = (directory / filename).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == manifest['files'][filename], f'{filename}: SHA-256 mismatch.')
            require(not raw.startswith(b'\xef\xbb\xbf'), f'{filename}: UTF-8 BOM is not allowed.')
            reader = csv.reader(io.StringIO(raw.decode('utf-8'), newline=''), strict=True)
            require(next(reader, None) == COLUMNS[name], f'{filename}: incorrect header.')
            for values in reader:
                label = f'{filename} line {reader.line_num}'
                try:
                    require(len(values) == len(COLUMNS[name]), 'Incorrect column count.')
                    row = dict(zip(COLUMNS[name], values))
                    require(all(row[key].strip() for key in row if key not in nullable), 'Required values are missing.')
                    require(row['charge_id'] not in seen, 'Duplicate or excluded charge identifier.')
                    seen.add(row['charge_id'])
                    package_house = package_houses.get(row['house_number'])
                    require(package_house is not None, 'House does not exist in the approved houses.csv package.')
                    house = houses.get(package_house.source_id)
                    require(house is not None and house.number == package_house.number,
                            'Imported Stage A house does not exist or its identity differs; review Stage A first.')
                    require(bool(re.fullmatch(r'0|[1-9][0-9]*', row['revision_version'])), 'Invalid revision version.')
                    version = int(row['revision_version'])
                    updated = timestamp(row['revision_updated']) if row['revision_updated'] else None
                    if name == 'charges':
                        require(row['charge_id'] not in HISTORICAL_EXCLUDED_CHARGE_IDS, 'Excluded charge cannot become a financial charge.')
                        concept = concepts.get(row['concept_code'])
                        require(concept is not None, 'Charge concept does not exist; import Stage A first.')
                        require(row['concept_code'] in {'CUOTA', 'RECARGO', 'MULTA'} and not concept.is_adjustment, 'Unapproved charge or adjustment concept.')
                        require(bool(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', row['effective_date'])), 'Invalid effective date.')
                        effective = date.fromisoformat(row['effective_date'])
                        require(bool(re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', row['period'])), 'Invalid charge period.')
                        date.fromisoformat(row['period'] + '-01')
                        amounts = {key: money(row[key]) for key in ['nominal_amount', 'discount_amount', 'amount', 'original_amount']}
                        require(amounts['discount_amount'] <= amounts['nominal_amount'] and amounts['amount'] == amounts['nominal_amount'] - amounts['discount_amount'], 'Charge amounts contradict nominal/discount values.')
                        original = load_json(row['original_record'], label + ' original_record')
                        approved = load_json(row['approved_record'], label + ' approved_record')
                        require(isinstance(original, dict) and isinstance(approved, dict), 'Original/approved evidence must be objects.')
                        require(approved['id'] == row['charge_id'] and approved['original'] == original, 'Source identifiers or original evidence disagree.')
                        require(approved['conflict'] is False and approved['excluded'] is False, 'Conflicting/excluded source charge.')
                        require(row['source_status'] in {'ORIGINAL', 'APROBADO'} and approved['status'] == row['source_status'] and type(approved['version']) is int and approved['version'] == version, 'Unapproved or inconsistent charge revision.')
                        require((version == 0 and updated is None) if row['source_status'] == 'ORIGINAL' else (version > 0 and updated is not None), 'Revision version/timestamp contradicts status.')
                        for key, field in [('nominal', 'nominal_amount'), ('discount', 'discount_amount'), ('amount', 'amount')]:
                            require(type(approved[key]) is int and Decimal(approved[key]) / 100 == amounts[field], f'Approved {key} differs from exported amount.')
                        require(type(original['amount']) is int and Decimal(original['amount']) / 100 == amounts['original_amount'], 'Original amount differs from evidence.')
                        require(str(approved['house']) == row['house_number'] and approved['type'] == row['concept_code'] and approved['date'] == row['effective_date'] and approved['period'] == row['period'], 'Approved relationship/date/period differs.')
                        require(approved.get('reference', '') == row['reference'] and approved.get('detail', '') == row['detail'], 'Approved reference/detail differs.')
                        if row['source_status'] == 'ORIGINAL':
                            for key in ['nominal', 'discount', 'amount']:
                                require(type(original[key]) is int and approved[key] == original[key],
                                        f'ORIGINAL charge {key} differs from original evidence.')
                            require(str(original['house']) == row['house_number']
                                    and original['type'] == row['concept_code']
                                    and original['date'] == row['effective_date']
                                    and original['period'] == row['period'],
                                    'ORIGINAL charge house/concept/date/period differs from original evidence.')
                            require(original.get('reference', '') == row['reference']
                                    and original.get('detail', '') == row['detail'],
                                    'ORIGINAL charge reference/detail differs from original evidence.')
                        instance = Charge(house=house, concept=concept, effective_date=effective, period=row['period'], **amounts, source_status=row['source_status'], revision_version=version, revision_updated=updated, reference=row['reference'], detail=row['detail'])
                    else:
                        require(row['charge_id'] in HISTORICAL_EXCLUDED_CHARGE_IDS, 'Unexpected exclusion identifier.')
                        require(updated is not None and version > 0, 'Exclusion revision metadata required.')
                        evidence = load_json(row['evidence_ids'], label + ' evidence_ids')
                        original = load_json(row['original_revision'], label + ' original_revision')
                        require(isinstance(evidence, list) and evidence and all(isinstance(key, str) and key.strip() for key in evidence), 'Invalid receipt evidence identifiers.')
                        require(isinstance(original, dict) and original['id'] == row['charge_id'] and original['status'] == 'APROBADO' and original['version'] == version and original['reason'] == row['reason'] and original['updated'] == row['revision_updated'], 'Exclusion evidence/revision differs.')
                        # Nested legacy revision data is retained verbatim, never repaired/reinterpreted.
                        instance = ChargeExclusion(house=house, reason=row['reason'], revision_version=version, revision_updated=updated, evidence_ids=evidence)
                    candidates[name].append(provenance(instance, row, raw, filename, reader.line_num))
                except (ValidationError, ValueError, KeyError, TypeError) as error:
                    messages = error.messages if isinstance(error, ValidationError) else [str(error)]
                    raise ValidationError([f'{label}: {message}' for message in messages]) from error
            require(len(candidates[name]) == manifest['counts'][name] == validation['counts'][name] == COUNTS[name], f'{filename}: unexpected source count.')
        charges = candidates['charges']
        require(Counter(c.concept.code for c in charges) == {'CUOTA': 948, 'RECARGO': 150, 'MULTA': 8}, 'Charge type coverage differs.')
        require(validation['charge_types'] == {'CUOTA': 948, 'RECARGO': 150, 'MULTA': 8}, 'Validation charge type counts differ.')
        total = sum(c.amount for c in charges)
        cutoff_total = sum(c.amount for c in charges if c.effective_date <= date(2026, 9, 25))
        require(total == Decimal(validation['charge_total']) == Decimal('880500.00'), 'Historical total differs from 880500.00 MXN.')
        require(cutoff_total == Decimal(validation['charge_total_at_cutoff']) == Decimal('655500.00'), 'Historical cutoff total differs from 655500.00 MXN.')
        excluded = {c.source_id for c in candidates['excluded_charges']}
        require(excluded == set(HISTORICAL_EXCLUDED_CHARGE_IDS) == set(validation['excluded_charge_ids']), 'Historical exclusions differ.')
        return candidates
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError, csv.Error, InvalidOperation) as error:
        raise ValidationError(f'Invalid charge package: {error}') from error


def import_charges(directory, *, preflight=False):
    candidates = read_charges(directory)
    summary = {name: {'imported': 0, 'unchanged': 0, 'would_import': 0} for name in candidates}
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [30260302])
            pending = []
            # Check every database conflict before inserting any charge or audit record.
            for name, records in candidates.items():
                for candidate in records:
                    existing = type(candidate).objects.select_for_update().filter(source_id=candidate.source_id).first()
                    if existing:
                        fields = BUSINESS[type(candidate)] + ['source_id', 'source_values']
                        require(all(getattr(existing, field) == getattr(candidate, field) for field in fields), f'{candidate.source_id}: stored charge/audit conflicts; no records imported.')
                        summary[name]['unchanged'] += 1
                    else:
                        pending.append((name, candidate))
                        summary[name]['would_import' if preflight else 'imported'] += 1
            if not preflight:
                for name, candidate in pending:
                    candidate.save(force_insert=True)
    except IntegrityError as error:
        raise ValidationError('Charge identity/reference/constraint conflict; entire Stage B import rolled back.') from error
    return summary
