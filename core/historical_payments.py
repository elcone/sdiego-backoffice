"""Stage C: persist frozen review decisions, not a matching/reconciliation engine."""
import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from .historical_catalogs import load_json, read_package as read_catalogs, require
from .historical_charges import timestamp
from .models import (
    BankTransaction, House, Payment, CFPayment, CFReconciliationGroup, CFGroupPayment,
    CFGroupDeposit, ReceiptEvidence, PaymentEvidenceLink, HistoricalHouseCase,
)

COLUMNS = {
    'reviewed_payments': 'payment_id house real_date effective_date amount origin reference transaction_code group_code status validation identification blocked reason flag followup evidence_ids original_base source_reference'.split(),
    'comunidad_feliz': 'cf_payment_id official_folio original_house assigned_house original_date original_amount effective_date recognized_amount additional_credit_amount treatment recognition settlement group_code transaction_code reviewed_payment_id tracking_key payment_method resident comment original_source_record source_reference'.split(),
    'cf_reconciliation_groups': 'group_code payment_count deposit_count payment_total bank_total difference status evidence_level observations original_metadata resolution_metadata'.split(),
    'cf_group_payments': 'group_code cf_payment_id'.split(),
    'cf_group_deposits': 'group_code transaction_code bank_receipt'.split(),
    'receipt_evidence': 'evidence_id document_name sha256 archive_path bank_reference reviewed_payment_id filename_house result method note metadata'.split(),
    'payment_evidence_links': 'payment_id evidence_id link_kind'.split(),
    'house_cases': 'house status note version updated'.split(),
    'administrative_decisions': 'source_table source_id record_json'.split(),
}
COUNTS = dict(reviewed_payments=596, comunidad_feliz=291, cf_reconciliation_groups=9,
              cf_group_payments=95, cf_group_deposits=16, receipt_evidence=208,
              payment_evidence_links=209, house_cases=4, administrative_decisions=55)
TREATMENTS = {'BANK_DIRECT_ALREADY_REPRESENTED': 110, 'CF_GROUP_RECOGNIZED': 94,
              'JANUARY_ADVANCE': 8, 'PRIOR_CLOSURE_NOT_APPLIED': 78, 'RECONSTRUCTED_USER_CONFIRMED': 1}
MONEY = set('amount original_amount recognized_amount additional_credit_amount payment_total bank_total difference'.split())
DATES = {'real_date', 'effective_date', 'original_date'}
PENDING_6114 = {'175af7a08b72b6d5fc18fdf1', 'd6116250a7c464f3dbf96f0d'}
MODELS = {'reviewed_payments': Payment, 'comunidad_feliz': CFPayment,
          'cf_reconciliation_groups': CFReconciliationGroup, 'cf_group_payments': CFGroupPayment,
          'cf_group_deposits': CFGroupDeposit, 'receipt_evidence': ReceiptEvidence,
          'payment_evidence_links': PaymentEvidenceLink, 'house_cases': HistoricalHouseCase}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def money(value, signed=False):
    require(isinstance(value, str) and bool(re.fullmatch(r'-?[0-9]+\.[0-9]{2}' if signed else r'[0-9]+\.[0-9]{2}', value)), 'Invalid exact monetary value.')
    result = Decimal(value)
    require(abs(result) < Decimal('10000000000000000'), 'Money exceeds decimal(18,2).')
    return result


def day(value, optional=False):
    if optional and not value:
        return None
    require(bool(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value)), 'Invalid ISO date.')
    return date.fromisoformat(value)


def integer(value):
    require(bool(re.fullmatch(r'0|[1-9][0-9]*', value)), 'Invalid integer.')
    return int(value)


def json_object(value, label):
    result = load_json(value, label)
    require(isinstance(result, dict), f'{label}: expected a JSON object.')
    return result


def csv_rows(raw, columns, label):
    require(not raw.startswith(b'\xef\xbb\xbf'), f'{label}: BOM is not allowed.')
    reader = csv.reader(io.StringIO(raw.decode('utf-8'), newline=''), strict=True)
    require(next(reader, None) == columns, f'{label}: header differs.')
    rows = []
    for values in reader:
        require(len(values) == len(columns), f'{label} line {reader.line_num}: column count differs.')
        rows.append((reader.line_num, dict(zip(columns, values))))
    return rows


def read_sources(package):
    catalogs = read_catalogs(package)  # Unchanged Stage A package/hash/schema checks.
    package = Path(package)
    outer = load_json((package / 'manifest.json').read_bytes(), 'catalog manifest')
    ref_raw = (package / 'existing_exports.json').read_bytes()
    require(digest(ref_raw) == outer['files']['existing_exports.json'], 'Existing export references changed.')
    refs = load_json(ref_raw, 'existing exports')
    bank_package = package.parent / 'import-layouts-increment02-stable-identity'
    manifest_raw = (bank_package / 'manifest.json').read_bytes()
    require(digest(manifest_raw) == outer['source_sha256']['outputs/import-layouts-increment02-stable-identity/manifest.json'], 'Referenced bank/payment manifest changed.')
    manifest = load_json(manifest_raw, 'bank/payment manifest')
    require(manifest['contract_version'] == 'sdiego-history-v1.1' and manifest['source_namespace'] == 'sdiego-banbajio-history' and manifest['reviewed_cutoff'] == '2026-09-25', 'Unsupported payment package contract.')
    contents = {}
    for name, expected in manifest['files'].items():
        path = (bank_package / name).resolve()
        require(not Path(name).is_absolute() and bank_package.resolve() in path.parents, 'Unsafe manifest file path.')
        raw = path.read_bytes()
        require(digest(raw) == expected, f'{name}: manifest SHA-256 differs.')
        contents[name] = raw
    schema = load_json(contents['schema.json'], 'payment schema')
    rows = {}
    for name, columns in COLUMNS.items():
        filename = name + '.csv'
        require(refs[filename]['path'] == '../import-layouts-increment02-stable-identity/' + filename and refs[filename]['sha256'] == digest(contents[filename]), f'{filename}: approved export reference differs.')
        expected_schema = {field: 'decimal(18,2) nullable' if field in MONEY else 'date ISO nullable' if field in DATES else 'integer' if field in {'payment_count', 'deposit_count', 'version'} else 'text nullable' for field in columns}
        require(schema[filename] == expected_schema, f'{filename}: schema field definitions differ.')
        rows[name] = csv_rows(contents[filename], columns, filename)
        require(len(rows[name]) == manifest['counts'][name] == refs[filename]['records'] == COUNTS[name], f'{filename}: source count differs.')
    # Bank rows are references, never import candidates. Compare every original source cell.
    bank_raw = contents['bank_transactions.csv']
    require(refs['bank_transactions.csv']['sha256'] == digest(bank_raw), 'Bank export reference differs.')
    from .mallard_bank import COLUMNS as BANK_COLUMNS, parse_mallard_row
    from .bank_import import MATCH_FIELDS, source_differs
    bank_rows = csv_rows(bank_raw, list(BANK_COLUMNS), 'bank_transactions.csv')
    require(len(bank_rows) == manifest['counts']['bank_transactions'] == refs['bank_transactions.csv']['records'] == 989, 'Bank reference coverage differs.')
    bank = {b.transaction_code: b for b in BankTransaction.objects.all()}
    require(len({r['transaction_code'] for _, r in bank_rows}) == len(bank_rows), 'Duplicate bank source reference.')
    for line, row in bank_rows:
        existing = bank.get(row['transaction_code'])
        candidate = parse_mallard_row(row, filename='bank_transactions.csv', digest=digest(bank_raw), line=line)
        require(existing is not None and not source_differs(existing, candidate)
                and all(getattr(existing, field) == getattr(candidate, field) for field in MATCH_FIELDS),
                f"{row['transaction_code']}: bank source/reference conflict; no bank import will be attempted.")
    package_houses = {h.source_id: h for h in catalogs['houses']}
    houses = {h.number: h for h in House.objects.filter(source_id__in=package_houses)}
    require(all(h.source_id == h.number and h.number in package_houses for h in houses.values()), 'Imported house identity conflict.')
    return rows, contents, manifest, houses, bank


def build_payments(rows, contents, manifest, houses, bank):
    output = {name: [] for name in MODELS}
    indexes = {name: {} for name in MODELS}
    def house(number):
        if not number:
            return None
        require(number in houses, f'House {number} is absent from the package or imported Stage A identities.')
        return houses[number]
    def credit(code):
        if not code:
            return None
        require(code in bank and bank[code].line_type == 'CREDIT', f'Bank reference {code} is missing or is not a credit.')
        return bank[code]
    def add(name, key, row, line, **fields):
        require(isinstance(key, str) and key.strip() and key not in indexes[name], f'{name}: duplicate/empty source identity {key}.')
        record = MODELS[name](source_id=key, source_values=row.copy(), source_file=name + '.csv', source_file_sha256=digest(contents[name + '.csv']), source_row_number=line, **fields)
        # Related instances are validated package candidates, persisted only after all checks pass.
        exclude = [f.name for f in record._meta.fields if f.is_relation]
        record.full_clean(exclude=exclude, validate_unique=False, validate_constraints=False)
        indexes[name][key] = record
        output[name].append(record)
        return record
    revisions = {}
    for _, row in rows['administrative_decisions']:
        if row['source_table'] == 'payment_revisions':
            rev = json_object(row['record_json'], 'payment revision')
            require(row['source_id'] == rev['id'] and rev['id'] not in revisions, 'Revision identity conflict.')
            revisions[rev['id']] = rev
    for line, r in rows['reviewed_payments']:
        try:
            require(r['source_reference'].strip() and r['reference'].strip(), 'Payment references are required.')
            base = json_object(r['original_base'], 'original payment base')
            amount = money(r['amount'])
            require(type(base['amount']) is int and amount == Decimal(base['amount']) / 100 and amount > 0, 'Reviewed amount differs from original payment.')
            require(r['real_date'] == base['real_date'] and r['effective_date'] == base['date'] and r['reference'] == base['reference'] and r['origin'] == base.get('origin', base['origin_source']), 'Payment dates/origin/reference differ from original base.')
            require(r['blocked'] == (base.get('blocked') or '') and r['validation'] == (base.get('validation') or ''), 'Payment block/validation differs from original base.')
            rev = revisions.get(r['payment_id'])
            require(r['status'] in {'ORIGINAL', 'APROBADO'}, 'Unapproved/conflicting payment source status.')
            if r['status'] == 'ORIGINAL':
                require(not rev and r['house'] == (str(base['house']) if base['house'] is not None else ''), 'ORIGINAL house assignment differs from original evidence.')
            else:
                require(rev is not None and rev['status'] == 'APROBADO', 'Approved payment revision evidence is missing.')
                canonical = json.dumps(base, ensure_ascii=False, sort_keys=True, default=str)
                require(digest(canonical.encode()) == rev['base_hash'], 'Payment revision base hash conflict.')
                edited = json_object(rev['data'], 'approved payment revision')
                require(r['house'] == (str(edited['house']) if edited['house'] is not None else '') and r['reason'] == rev['reason'], 'Approved assignment differs from persisted revision.')
                require(load_json(r['evidence_ids'], 'payment evidence IDs') == edited.get('evidence_ids', []), 'Approved evidence links differ.')
                timestamp(rev['updated'])
            assigned = house(r['house'])
            approved = bool(assigned and not r['blocked'] and (r['status'] == 'APROBADO' or r['validation'] == 'CONFIRMADO'))
            direct = r['origin'] in {'BANCO_DIRECTO', 'duckdb'}
            require(direct or r['origin'] in {'COMUNIDAD_FELIZ', 'ANTICIPO_ENERO'}, 'Unknown payment origin.')
            b = credit(r['transaction_code']) if direct else None
            require((b is not None and b.bank_receipt == r['reference'] and b.amount == amount) if direct else not r['transaction_code'], 'Payment bank receipt/amount/origin mismatch.')
            if direct:
                require(not r['group_code'], 'Direct payment cannot be a CF group member.')
                require(b.source_values['reviewed_payment_id'] == r['payment_id'] and b.source_values['assigned_house'] == r['house'], 'Bank reviewed identity/assignment differs.')
            add('reviewed_payments', r['payment_id'], r, line, house=assigned, bank_transaction=b, origin=r['origin'], real_date=day(r['real_date']), effective_date=day(r['effective_date']), amount=amount, effective_amount=amount if approved else Decimal('0'), assignment_approved=approved, source_status=r['status'], validation=r['validation'], blocked=r['blocked'], approval_evidence=rev or {})
        except (ValidationError, KeyError, ValueError, TypeError) as e:
            raise ValidationError(f"reviewed_payments.csv line {line}: {e}") from e
    used_banks = [p.bank_transaction_id for p in output['reviewed_payments'] if p.bank_transaction_id]
    require(len(used_banks) == len(set(used_banks)), 'Bank movement has multiple reviewed payment identities.')
    for line, r in rows['cf_reconciliation_groups']:
        original = json_object(r['original_metadata'], 'group metadata')
        effective = original.copy()
        if r['resolution_metadata']:
            resolution = json_object(r['resolution_metadata'], 'group resolution')
            require(resolution['id'] == r['group_code'], 'Group resolution identity differs.')
            effective.update(json_object(resolution['data'], 'group resolution data'))
        require(original['Grupo'] == r['group_code'] and effective['Estado'] == r['status'], 'Group identity/status differs from approved metadata.')
        require(money(r['payment_total']) == Decimal(str(effective['Importe pagos'])) and money(r['bank_total']) == Decimal(str(effective['Importe banco'])), 'Group amounts differ from persisted decision.')
        require(integer(r['payment_count']) == effective['Pagos individuales'] and integer(r['deposit_count']) == effective['Depositos'], 'Group counts differ from persisted decision.')
        add('cf_reconciliation_groups', r['group_code'], r, line, payment_count=integer(r['payment_count']), deposit_count=integer(r['deposit_count']), payment_total=money(r['payment_total']), bank_total=money(r['bank_total']), difference=money(r['difference'], signed=True), status=r['status'], evidence_level=r['evidence_level'])
    for line, r in rows['comunidad_feliz']:
        try:
            require(r['treatment'] in TREATMENTS and r['source_reference'].strip(), 'Unknown CF treatment or missing provenance.')
            original = json_object(r['original_source_record'], 'original CF record')
            reconstructed = r['treatment'] == 'RECONSTRUCTED_USER_CONFIRMED'
            if not reconstructed:
                require(money(r['original_amount']) == Decimal(str(original['Monto'])) and r['original_date'] == original['Fecha de pago'][:10] and Decimal(r['original_house']) == Decimal(str(original['Unidad'])), 'Original CF amount/date/house evidence differs.')
                if r['official_folio']:
                    require(r['cf_payment_id'] == 'CF-' + r['official_folio'] and Decimal(r['official_folio']) == Decimal(str(original['Folio'])), 'Official CF folio differs.')
                else:
                    require(original['Folio'] is None and r['cf_payment_id'] == 'CF-AJUSTE-' + r['source_reference'].split('row=')[-1], 'CF adjustment source identity differs.')
            p = indexes['reviewed_payments'].get(r['reviewed_payment_id'])
            assigned = house(r['assigned_house'])
            recognized = money(r['recognized_amount']); additional = money(r['additional_credit_amount'])
            b = credit(r['transaction_code'])
            if r['treatment'] == 'PRIOR_CLOSURE_NOT_APPLIED':
                require(not r['reviewed_payment_id'] and not r['effective_date'] and not r['group_code'] and b is None and recognized == additional == 0 and r['recognition'] == 'NOT_APPLIED_PRIOR_CLOSURE' and r['settlement'] == 'NOT_ESTABLISHED', 'Unapplied prior CF evidence gained financial effect.')
            else:
                require(p is not None and p.house == assigned and p.amount == recognized and p.effective_date == day(r['effective_date']), 'CF assigned payment/date/amount differs from reviewed payment.')
                if r['treatment'] == 'BANK_DIRECT_ALREADY_REPRESENTED':
                    require(p.origin == 'BANCO_DIRECTO' and p.bank_transaction == b and b is not None and additional == 0 and not r['group_code'] and r['settlement'] == 'DIRECT_BANK_MATCH', 'Duplicate CF record would create another financial effect.')
                else:
                    require(p.assignment_approved and p.bank_transaction is None and p.effective_amount == additional == recognized, 'CF effective amount/approval differs from reviewed decision.')
                    if r['treatment'] == 'JANUARY_ADVANCE':
                        require(p.origin == 'ANTICIPO_ENERO' and not r['group_code'] and b is None and r['settlement'] == 'NOT_ESTABLISHED' and r['effective_date'] == '2026-02-01', 'Advance effect/settlement decision differs.')
                    else:
                        require(p.origin == 'COMUNIDAD_FELIZ' and r['group_code'] in indexes['cf_reconciliation_groups'] and r['group_code'] == p.source_values['group_code'] == p.source_values['reference'], 'CF group relationship differs.')
                        if reconstructed:
                            require(r['cf_payment_id'] == p.source_id == 'BANK-173392010109' and r['assigned_house'] == '6131' and r['group_code'] == 'CF-G09' and r['recognition'] == 'APROBADO' and r['settlement'] == 'USER_CONFIRMED_BANK_LINK' and p.source_status == 'APROBADO' and b is not None and original == json_object(p.source_values['original_base'], 'manual base') and b.bank_receipt == original['bank_reference'] and r['original_date'] == p.source_values['real_date'] and money(r['original_amount']) == p.amount, 'Approved 6131 reconstruction differs.')
                        else:
                            require(b is None and r['settlement'] == 'GROUP_MATCH', 'Unapproved individual settlement link.')
                if not reconstructed:
                    require(r['recognition'] == 'RECOGNIZED_IN_REVIEW', 'CF recognition differs.')
            add('comunidad_feliz', r['cf_payment_id'], r, line, payment=p, bank_transaction=b, original_house=house(r['original_house']), assigned_house=assigned, official_folio=r['official_folio'], original_date=day(r['original_date']), effective_date=day(r['effective_date'], optional=True), original_amount=money(r['original_amount']), recognized_amount=recognized, additional_credit_amount=additional, treatment=r['treatment'], recognition=r['recognition'], settlement=r['settlement'])
        except (ValidationError, KeyError, ValueError, TypeError, InvalidOperation) as e:
            raise ValidationError(f'comunidad_feliz.csv line {line}: {e}') from e
    require(Counter(c.treatment for c in output['comunidad_feliz']) == TREATMENTS, 'CF treatment coverage differs.')
    linked = [c.payment.source_id for c in output['comunidad_feliz'] if c.payment]
    require(len(linked) == len(set(linked)), 'Multiple CF records reference the same reviewed payment.')
    require({p.source_id for p in output['reviewed_payments'] if p.origin in {'COMUNIDAD_FELIZ', 'ANTICIPO_ENERO'}} <= set(linked), 'CF reviewed payment lacks original evidence.')
    for line, r in rows['cf_group_payments']:
        g = indexes['cf_reconciliation_groups'].get(r['group_code']); c = indexes['comunidad_feliz'].get(r['cf_payment_id'])
        require(g is not None and c is not None and c.source_values['group_code'] == g.source_id and c.treatment in {'CF_GROUP_RECOGNIZED', 'RECONSTRUCTED_USER_CONFIRMED'}, 'CF group payment reference differs.')
        key = json.dumps([r['group_code'], r['cf_payment_id']], separators=(',', ':'))
        add('cf_group_payments', key, r, line, group=g, cf_payment=c)
    for line, r in rows['cf_group_deposits']:
        g = indexes['cf_reconciliation_groups'].get(r['group_code']); b = credit(r['transaction_code'])
        require(g is not None and b is not None and b.bank_receipt == r['bank_receipt'] and b.source_values['classification'] == 'CF_SETTLEMENT', 'CF bank settlement identity/classification differs.')
        require(not any(p.bank_transaction == b and p.effective_amount > 0 for p in output['reviewed_payments']), 'Settlement would create a second house payment.')
        key = json.dumps([r['group_code'], r['transaction_code']], separators=(',', ':'))
        add('cf_group_deposits', key, r, line, group=g, bank_transaction=b)
    cf_members = [link.cf_payment.source_id for link in output['cf_group_payments']]
    deposits = [link.bank_transaction_id for link in output['cf_group_deposits']]
    require(len(cf_members) == len(set(cf_members)) and len(deposits) == len(set(deposits)), 'Payment/deposit belongs to multiple groups.')
    require(set(cf_members) == {c.source_id for c in output['comunidad_feliz'] if c.source_values['group_code']}, 'Incomplete CF group membership.')
    for g in output['cf_reconciliation_groups']:
        members = [link.cf_payment for link in output['cf_group_payments'] if link.group == g]
        bs = [link.bank_transaction for link in output['cf_group_deposits'] if link.group == g]
        metadata = json_object(g.source_values['original_metadata'], 'group metadata')
        if g.source_values['resolution_metadata']:
            metadata.update(json_object(json_object(g.source_values['resolution_metadata'], 'resolution')['data'], 'resolution data'))
        require(len(members) == g.payment_count and len(bs) == g.deposit_count and sum(c.additional_credit_amount for c in members) == g.payment_total == sum(b.amount for b in bs) == g.bank_total and g.difference == 0, f'{g.source_id}: group financial totals differ.')
        require(all(c.bank_transaction in bs for c in members if c.treatment == 'RECONSTRUCTED_USER_CONFIRMED'), 'Confirmed CF bank link is absent from its approved group deposits.')
        require({b.bank_receipt for b in bs} == {v.strip() for v in metadata['Recibos bancarios'].split(';')}, 'Group deposit receipts differ from approved metadata.')
    advances = [c for c in output['comunidad_feliz'] if c.treatment == 'JANUARY_ADVANCE']
    require(sum(c.original_amount for c in advances) == Decimal('12000.00') and sum(c.additional_credit_amount for c in advances) == Decimal('9000.00'), 'Original/effective advances differ from approved policy.')
    require(sum(c.additional_credit_amount for c in output['comunidad_feliz']) == money(manifest['additional_cf_credit_total']), 'Total CF additional credit differs.')
    for line, r in rows['receipt_evidence']:
        require(bool(re.fullmatch(r'[0-9a-f]{64}', r['sha256'])), 'Receipt document hash is invalid.')
        require(r['archive_path'] in contents and digest(contents[r['archive_path']]) == r['sha256'], 'Receipt archive evidence differs.')
        p = indexes['reviewed_payments'].get(r['reviewed_payment_id'])
        require(not r['reviewed_payment_id'] or p is not None, 'Receipt references a missing reviewed payment.')
        json_object(r['metadata'], 'receipt metadata')
        add('receipt_evidence', r['evidence_id'], r, line, document_name=r['document_name'], sha256=r['sha256'], archive_path=r['archive_path'], filename_house=house(r['filename_house']), reviewed_payment=p, result=r['result'])
    for line, r in rows['payment_evidence_links']:
        require(r['evidence_id'] not in PENDING_6114, 'Pending 6114 receipts cannot be linked as payment evidence.')
        p = indexes['reviewed_payments'].get(r['payment_id']); e = indexes['receipt_evidence'].get(r['evidence_id'])
        require(p is not None and e is not None and r['link_kind'] in {'APPROVED_LINK', 'CATALOG_ASSOCIATION_NOT_NEW_APPROVAL'}, 'Invalid payment/receipt reference.')
        if r['link_kind'] == 'APPROVED_LINK':
            require(e.source_id in load_json(p.source_values['evidence_ids'], 'approved evidence IDs'), 'Unapproved receipt link.')
        else:
            require(e.reviewed_payment == p, 'Receipt catalog association differs.')
        key = json.dumps([r['payment_id'], r['evidence_id'], r['link_kind']], separators=(',', ':'))
        add('payment_evidence_links', key, r, line, payment=p, evidence=e, link_kind=r['link_kind'])
    for p in output['reviewed_payments']:
        ids = load_json(p.source_values['evidence_ids'], 'payment evidence IDs')
        require(isinstance(ids, list) and set(ids) == {link.evidence.source_id for link in output['payment_evidence_links'] if link.payment == p and link.link_kind == 'APPROVED_LINK'}, 'Payment approved evidence coverage differs.')
    for line, r in rows['house_cases']:
        add('house_cases', r['house'], r, line, house=house(r['house']), status=r['status'], note=r['note'], version=integer(r['version']), source_updated=timestamp(r['updated']))
    require('6114' in indexes['house_cases'], '6114 pending historical case is missing.')
    for key in PENDING_6114:
        e = indexes['receipt_evidence'].get(key)
        require(e is not None and e.filename_house.number == '6114' and e.result == 'PENDIENTE_LIQUIDACION_STP' and e.reviewed_payment is None and not e.source_values['bank_reference'], '6114 evidence gained an unconfirmed payment/settlement.')
    return output


def import_payments(package, *, preflight=False):
    try:
        candidates = build_payments(*read_sources(package))
    except (OSError, KeyError, ValueError, TypeError, AttributeError, csv.Error, InvalidOperation) as error:
        raise ValidationError(f'Invalid historical payment package: {error}') from error
    report = {name: {'imported': 0, 'unchanged': 0, 'would_import': 0} for name in candidates}
    resolved = {}
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [30260303])
            for name, records in candidates.items():
                for candidate in records:
                    for field in candidate._meta.fields:
                        if field.is_relation and field.related_model in MODELS.values():
                            related = getattr(candidate, field.name)
                            if related is not None:
                                setattr(candidate, field.name, resolved[(type(related), related.source_id)])
                    existing = type(candidate).objects.select_for_update().filter(source_id=candidate.source_id).first()
                    if existing:
                        fields = [f.attname for f in candidate._meta.fields if f.name not in {'id', 'created_at', 'updated_at', 'source_file', 'source_file_sha256', 'source_row_number'}]
                        require(all(getattr(existing, f) == getattr(candidate, f) for f in fields), f'{name} {candidate.source_id}: historical source/value conflict; entire import rejected.')
                        stored = existing; report[name]['unchanged'] += 1
                    elif preflight:
                        stored = candidate; report[name]['would_import'] += 1
                    else:
                        candidate.save(force_insert=True)
                        stored = candidate; report[name]['imported'] += 1
                    resolved[(type(candidate), candidate.source_id)] = stored
    except IntegrityError as error:
        raise ValidationError('Payment identity/relationship/financial constraint conflict; entire Stage C import rolled back.') from error
    return report
