"""Small synthetic reconciliations plus opt-in, complete immutable-package tests."""
import hashlib
import io
import json
import os
import tempfile
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import Sum
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core import historical_payments as hp
from core.historical_catalogs import import_catalogs
from core.bank_import import persist_transactions
from core.mallard_bank import preflight_mallard_file
from core.models import (
    House, Contact, HouseContact, ChargeConcept, Charge, ChargeExclusion, FeeConfiguration,
    BankTransaction, PayerAccount, Payment, CFPayment, CFReconciliationGroup, CFGroupPayment,
    CFGroupDeposit, ReceiptEvidence, PaymentEvidenceLink, HistoricalHouseCase,
)


class PaymentTests(TestCase):
    def setUp(self):
        self.houses = {number: House.objects.create(number=number, source_id=number) for number in ['6101','6112','6114','6131','6211']}
        self.bank = {}
        for code, receipt, amount, assigned, reviewed, classification in [
            ('bank-direct','direct-receipt','1000.00','6101','B-direct','DIRECT_PAYMENT'),
            ('bank-settlement','173392010109','2000.00','','B-settlement','CF_SETTLEMENT'),
            ('bank-unassigned','unknown-receipt','5000.00','','B-unassigned','UNIDENTIFIED'),
        ]:
            values = dict(reviewed_payment_id=reviewed, assigned_house=assigned, classification=classification)
            self.bank[code] = BankTransaction.objects.create(bank_account='0392369300201', bank_receipt=receipt, line_type='CREDIT', transaction_date=date(2026,4,29), transaction_time=time(12), description='Synthetic bank source', amount=Decimal(amount), transaction_code=code, source_identifiers={'id':code}, source_values=values, source_file='bank.csv', source_file_sha256='0'*64, source_row_number=2)
        self.rows = {name: [] for name in hp.COLUMNS}
        self.contents = {name+'.csv': b'synthetic CSV provenance' for name in hp.COLUMNS}
        self.manifest = {'additional_cf_credit_total':'11000.00'}
        self.treatments = {key:1 for key in hp.TREATMENTS}
        self.review('B-direct','6101','1000.00','BANCO_DIRECTO','direct-receipt','bank-direct')
        self.review('B-settlement','','2000.00','duckdb','173392010109','bank-settlement',blocked='Liquidación CF: ya representada por pagos individuales')
        self.review('B-unassigned','','5000.00','duckdb','unknown-receipt','bank-unassigned')
        self.review('CF-2','6211','1000.00','COMUNIDAD_FELIZ','CF-G09',group='CF-G09')
        self.review('CF-11','6112','9000.00','ANTICIPO_ENERO','CF-11',real='2026-01-02',effective='2026-02-01')
        self.review('BANK-173392010109','6131','1000.00','COMUNIDAD_FELIZ','CF-G09',group='CF-G09',approved=True)
        original = {'Grupo':'CF-G09','Estado':'CUADRA_POR_GRUPO','Importe pagos':2000,'Importe banco':2000,'Pagos individuales':2,'Depositos':1,'Recibos bancarios':'173392010109'}
        self.append('cf_reconciliation_groups',dict(group_code='CF-G09',payment_count='2',deposit_count='1',payment_total='2000.00',bank_total='2000.00',difference='0.00',status='CUADRA_POR_GRUPO',evidence_level='GROUP_DATE_AMOUNT_INFERENCE',observations='Frozen evidence',original_metadata=json.dumps(original),resolution_metadata=''))
        self.cf('CF-1','6101','1000.00','1000.00','0.00','BANK_DIRECT_ALREADY_REPRESENTED','B-direct',bank='bank-direct')
        self.cf('CF-2','6211','1000.00','1000.00','1000.00','CF_GROUP_RECOGNIZED','CF-2',group='CF-G09')
        self.cf('CF-11','6112','12000.00','9000.00','9000.00','JANUARY_ADVANCE','CF-11',original_date='2026-01-02',effective='2026-02-01')
        self.cf('CF-51','6101','1000.00','0.00','0.00','PRIOR_CLOSURE_NOT_APPLIED','',original_date='2026-01-01',effective='')
        self.cf('BANK-173392010109','6131','1000.00','1000.00','1000.00','RECONSTRUCTED_USER_CONFIRMED','BANK-173392010109',group='CF-G09',bank='bank-settlement')
        for cf_id in ['CF-2','BANK-173392010109']:
            self.append('cf_group_payments',dict(group_code='CF-G09',cf_payment_id=cf_id))
        self.append('cf_group_deposits',dict(group_code='CF-G09',transaction_code='bank-settlement',bank_receipt='173392010109'))
        for key in hp.PENDING_6114:
            self.receipt(key,'6114','', 'PENDIENTE_LIQUIDACION_STP')
        self.receipt('direct-evidence','6101','B-direct','COINCIDE')
        self.append('payment_evidence_links',dict(payment_id='B-direct',evidence_id='direct-evidence',link_kind='CATALOG_ASSOCIATION_NOT_NEW_APPROVAL'))
        self.append('house_cases',dict(house='6114',status='EN_REVISION',note='Both receipts awaiting confirmation; no bank settlement established.',version='1',updated='2026-10-07T03:31:01+00:00'))
        self.source_patch = patch.object(hp,'read_sources',side_effect=lambda package:(self.rows,self.contents,self.manifest,self.houses,self.bank))
        self.source_patch.start(); self.addCleanup(self.source_patch.stop)
        self.treatment_patch = patch.dict(hp.TREATMENTS,self.treatments,clear=True)
        self.treatment_patch.start(); self.addCleanup(self.treatment_patch.stop)

    def append(self,name,row):
        self.rows[name].append((len(self.rows[name])+2,row))

    def review(self,key,house,amount,origin,reference,bank='',group='',blocked='',approved=False,real='2026-04-28',effective='2026-04-28'):
        base = dict(amount=int(Decimal(amount)*100),house=None if approved or not house else int(house),real_date=real,date=effective,reference=reference,origin=origin,origin_source='manual' if approved else 'excel' if origin!='duckdb' else 'duckdb',blocked=blocked,validation='CONFIRMADO' if origin!='duckdb' and not approved else '')
        if approved: base['bank_reference']='173392010109'
        row = dict(payment_id=key,house=house,real_date=real,effective_date=effective,amount=amount,origin=origin,reference=reference,transaction_code=bank,group_code=group,status='APROBADO' if approved else 'ORIGINAL',validation=base['validation'],identification='',blocked=blocked,reason='User confirmed' if approved else '',flag='',followup='',evidence_ids='[]',original_base=json.dumps(base),source_reference='synthetic#snapshot=1')
        if approved:
            edited = dict(base,house=int(house),evidence_ids=[])
            rev = dict(id=key,base_hash=hp.digest(json.dumps(base,ensure_ascii=False,sort_keys=True,default=str).encode()),data=json.dumps(edited),status='APROBADO',reason='User confirmed',version=1,updated='2026-10-03T17:24:23+00:00')
            self.append('administrative_decisions',dict(source_table='payment_revisions',source_id=key,record_json=json.dumps(rev)))
        self.append('reviewed_payments',row)

    def cf(self,key,house,original_amount,recognized,additional,treatment,payment,group='',bank='',original_date='2026-04-28',effective='2026-04-28'):
        manual=treatment=='RECONSTRUCTED_USER_CONFIRMED'
        folio=key[3:] if key.startswith('CF-') else ''
        original = json.loads(next(r['original_base'] for _,r in self.rows['reviewed_payments'] if r['payment_id']==payment)) if manual else {'Monto':float(original_amount),'Fecha de pago':original_date+' 00:00:00','Unidad':int(house),'Folio':int(folio)}
        settlement={'BANK_DIRECT_ALREADY_REPRESENTED':'DIRECT_BANK_MATCH','CF_GROUP_RECOGNIZED':'GROUP_MATCH','RECONSTRUCTED_USER_CONFIRMED':'USER_CONFIRMED_BANK_LINK'}.get(treatment,'NOT_ESTABLISHED')
        self.append('comunidad_feliz',dict(cf_payment_id=key,official_folio=folio,original_house='' if manual else house,assigned_house=house,original_date=original_date,original_amount=original_amount,effective_date=effective,recognized_amount=recognized,additional_credit_amount=additional,treatment=treatment,recognition='APROBADO' if manual else 'NOT_APPLIED_PRIOR_CLOSURE' if not payment else 'RECOGNIZED_IN_REVIEW',settlement=settlement,group_code=group,transaction_code=bank,reviewed_payment_id=payment,tracking_key='',payment_method='',resident='',comment='',original_source_record=json.dumps(original),source_reference='synthetic-cf#row=1'))

    def receipt(self,key,house,payment,result):
        raw=b'Original document '+key.encode(); hash=hp.digest(raw); path='evidence/'+hash+'.pdf'; self.contents[path]=raw
        self.append('receipt_evidence',dict(evidence_id=key,document_name=key+'.pdf',sha256=hash,archive_path=path,bank_reference='',reviewed_payment_id=payment,filename_house=house,result=result,method='',note='',metadata='{}'))

    def import_all(self,preflight=False):
        return hp.import_payments('/unused-synthetic-package',preflight=preflight)

    def test_preflight_idempotency_pending_advances_and_single_effect(self):
        before=list(BankTransaction.objects.order_by('pk').values())
        report=self.import_all(True); self.assertEqual(report['reviewed_payments']['would_import'],6); self.assertFalse(Payment.objects.exists())
        self.import_all()
        self.assertEqual(Payment.objects.aggregate(total=Sum('effective_amount'))['total'],Decimal('12000.00'))
        self.assertEqual(CFPayment.objects.get(source_id='CF-1').additional_credit_amount,0)
        advance=CFPayment.objects.get(source_id='CF-11')
        self.assertEqual((advance.original_amount,advance.payment.effective_amount,advance.settlement),(Decimal('12000'),Decimal('9000'),'NOT_ESTABLISHED'))
        self.assertEqual(Payment.objects.get(source_id='B-settlement').effective_amount,0)
        self.assertIsNone(Payment.objects.get(source_id='B-unassigned').house)
        manual=CFPayment.objects.get(source_id='BANK-173392010109')
        self.assertEqual((manual.assigned_house.number,manual.group_link.group.source_id),('6131','CF-G09'))
        self.assertEqual(manual.payment.effective_amount,1000)
        self.assertEqual(ReceiptEvidence.objects.filter(filename_house__number='6114',reviewed_payment__isnull=True).count(),2)
        self.assertIn('Both receipts',HistoricalHouseCase.objects.get(house__number='6114').note)
        self.assertFalse(Payment.objects.filter(house__number='6114').exists())
        snapshots={m:list(m.objects.order_by('pk').values()) for m in hp.MODELS.values()}
        self.assertEqual(self.import_all()['reviewed_payments']['unchanged'],6)
        self.assertEqual(snapshots,{m:list(m.objects.order_by('pk').values()) for m in hp.MODELS.values()})
        self.assertEqual(before,list(BankTransaction.objects.order_by('pk').values()))
        self.assertFalse(PayerAccount.objects.exists())
        p=Payment.objects.get(source_id='BANK-173392010109'); self.assertIn('updated',p.approval_evidence)
        self.assertEqual(p.source_values,next(r for _,r in self.rows['reviewed_payments'] if r['payment_id']==p.source_id))

    def test_approved_assignment_overrides_legacy_pending_flag(self):
        row=next(r for _,r in self.rows['reviewed_payments'] if r['payment_id']=='CF-11')
        base=json.loads(row['original_base']); base['validation']='PENDIENTE'
        row.update(status='APROBADO',validation='PENDIENTE',reason='Confirmed by user',original_base=json.dumps(base))
        rev=dict(id='CF-11',base_hash=hp.digest(json.dumps(base,ensure_ascii=False,sort_keys=True,default=str).encode()),data=json.dumps(dict(base,evidence_ids=[])),status='APROBADO',reason=row['reason'],version=1,updated='2026-10-03T17:24:23+00:00')
        self.append('administrative_decisions',dict(source_table='payment_revisions',source_id='CF-11',record_json=json.dumps(rev)))
        self.import_all()
        p=Payment.objects.get(source_id='CF-11')
        self.assertTrue(p.assignment_approved); self.assertEqual(p.effective_amount,9000)
        self.assertEqual(p.validation,'PENDIENTE')

    def test_invalid_original_assignment_and_unverified_house_reject_without_writes(self):
        row=self.rows['reviewed_payments'][0][1]
        for change in [{'house':'6131'},{'house':'9999'},{'amount':'1001.00'},{'status':'APROBADO'}]:
            saved=row.copy(); row.update(change)
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(ValidationError): self.import_all()
            self.assertFalse(any(q['sql'].lstrip().split()[0] in {'INSERT','UPDATE','DELETE'} for q in queries))
            row.clear(); row.update(saved)
        self.assertFalse(Payment.objects.exists())

    def test_invalid_duplicate_effect_and_group_references_rejected(self):
        c=self.rows['comunidad_feliz'][0][1]; c['additional_credit_amount']='1000.00'
        with self.assertRaisesMessage(ValidationError,'Duplicate CF'): self.import_all()
        c['additional_credit_amount']='0.00'
        self.rows['cf_group_payments'][0][1]['group_code']='MISSING'
        with self.assertRaisesMessage(ValidationError,'group payment'): self.import_all()
        self.assertFalse(Payment.objects.exists())

    def test_pending_evidence_cannot_gain_payment_or_bank_link(self):
        self.rows['receipt_evidence'][0][1]['bank_reference']='unconfirmed'
        with self.assertRaisesMessage(ValidationError,'6114'): self.import_all()
        self.assertFalse(Payment.objects.exists())

    def test_pending_6114_receipts_cannot_approve_reconstructed_6131_payment(self):
        payment=next(r for _,r in self.rows['reviewed_payments'] if r['payment_id']=='BANK-173392010109')
        revision_row=self.rows['administrative_decisions'][0][1]
        original_links=self.rows['payment_evidence_links'].copy()
        for evidence_id in hp.PENDING_6114:
            with self.subTest(evidence_id=evidence_id):
                payment['evidence_ids']=json.dumps([evidence_id])
                revision=json.loads(revision_row['record_json'])
                edited=json.loads(revision['data']); edited['evidence_ids']=[evidence_id]
                revision['data']=json.dumps(edited); revision_row['record_json']=json.dumps(revision)
                self.rows['payment_evidence_links']=original_links.copy()
                self.append('payment_evidence_links',dict(payment_id=payment['payment_id'],evidence_id=evidence_id,link_kind='APPROVED_LINK'))
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaisesMessage(ValidationError,'Pending 6114 receipts'):
                        self.import_all()
                self.assertFalse(any(q['sql'].lstrip().split()[0] in {'INSERT','UPDATE','DELETE'} for q in queries))
                self.assertTrue(all(not model.objects.exists() for model in hp.MODELS.values()))

    def test_native_cf_group_membership_requires_no_csv_metadata(self):
        payment=Payment.objects.create(source_id='native-payment',house=self.houses['6131'],origin='COMUNIDAD_FELIZ',real_date=date(2026,5,1),effective_date=date(2026,5,1),amount=50,effective_amount=50,assignment_approved=True,source_status='APROBADO')
        cf=CFPayment.objects.create(source_id='native-cf',payment=payment,assigned_house=payment.house,original_house=payment.house,original_date=payment.real_date,effective_date=payment.effective_date,original_amount=50,recognized_amount=50,additional_credit_amount=50,treatment='CF_GROUP_RECOGNIZED',recognition='APPROVED',settlement='PENDING')
        group=CFReconciliationGroup.objects.create(source_id='native-group',payment_count=1,deposit_count=0,payment_total=50,bank_total=0,difference=-50,status='PENDING',evidence_level='NATIVE')
        link=CFGroupPayment.objects.create(group=group,cf_payment=cf)
        for record in [payment,cf,group,link]:
            record.refresh_from_db(); self.assertEqual(record.source_values,{})
        self.assertEqual(link.group,group); self.assertEqual(link.cf_payment,cf)
        self.assertEqual(Payment.objects.aggregate(total=Sum('effective_amount'))['total'],50)
        with self.assertRaises(IntegrityError),transaction.atomic():
            CFGroupPayment.objects.create(group=group,cf_payment=cf)
        with self.assertRaises(IntegrityError),transaction.atomic():
            CFGroupPayment.objects.create(group_id=999999,cf_payment=cf)
            connection.check_constraints()
        self.assertEqual(CFGroupPayment.objects.count(),1)

    def test_late_conflicting_case_rolls_back_entire_import(self):
        self.import_all()
        HistoricalHouseCase.objects.update(note='conflicting audit')
        for model in [PaymentEvidenceLink,ReceiptEvidence,CFGroupPayment,CFGroupDeposit,CFPayment,CFReconciliationGroup,Payment]: model.objects.all().delete()
        with self.assertRaisesMessage(ValidationError,'conflict'): self.import_all()
        self.assertFalse(Payment.objects.exists()); self.assertFalse(CFPayment.objects.exists())
        self.assertEqual(HistoricalHouseCase.objects.get().note,'conflicting audit')

    def test_database_error_after_inserts_rolls_back_all(self):
        with patch.object(HistoricalHouseCase,'save',side_effect=IntegrityError('concurrent writer')):
            with self.assertRaisesMessage(ValidationError,'rolled back'): self.import_all()
        self.assertTrue(all(not m.objects.exists() for m in hp.MODELS.values()))

    def test_constraints_prevent_duplicate_and_incompatible_financial_roles(self):
        self.import_all(); direct=Payment.objects.get(source_id='B-direct'); settlement=Payment.objects.get(source_id='B-settlement')
        for changes in [{'house':None},{'effective_amount':0},{'assignment_approved':False},{'bank_transaction':settlement.bank_transaction},{'amount':999}]:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                Payment.objects.filter(pk=direct.pk).update(**changes); connection.check_constraints()
        with self.assertRaises(IntegrityError),transaction.atomic():
            Payment.objects.filter(pk=settlement.pk).update(house=self.houses['6101'],assignment_approved=True,source_status='APROBADO',blocked='',effective_amount=2000)
        group=CFReconciliationGroup.objects.get()
        with self.assertRaises(IntegrityError),transaction.atomic():
            CFGroupDeposit.objects.create(source_id='invalid-deposit',group=group,bank_transaction=direct.bank_transaction)
        with self.assertRaises(IntegrityError),transaction.atomic():
            CFPayment.objects.filter(source_id='CF-1').update(additional_credit_amount=1000)
        with self.assertRaises(IntegrityError),transaction.atomic():
            CFPayment.objects.filter(source_id='CF-2').update(payment=direct)
        with self.assertRaises(IntegrityError),transaction.atomic():
            Payment.objects.filter(source_id='CF-2').update(house=self.houses['6101'])
        with self.assertRaises(IntegrityError),transaction.atomic():
            CFGroupPayment.objects.create(source_id='invalid-cf-member',group=group,cf_payment=CFPayment.objects.get(source_id='CF-1'))
        link=CFGroupPayment.objects.first()
        with self.assertRaises(IntegrityError),transaction.atomic():
            CFGroupPayment.objects.create(source_id='second-group-link',group=group,cf_payment=link.cf_payment)

    def test_admin_evidence_is_view_only_and_command_dry_run(self):
        out=io.StringIO(); call_command('import_historical_payments','/unused','--dry-run',stdout=out)
        self.assertIn('would_import=6',out.getvalue()); self.assertFalse(Payment.objects.exists())
        self.import_all()
        admin=get_user_model().objects.create_superuser(username='payment-admin')
        viewer=get_user_model().objects.create_user(username='payment-viewer',is_staff=True)
        viewer.user_permissions.add(*Permission.objects.filter(content_type__model__in=[m._meta.model_name for m in hp.MODELS.values()],codename__startswith='view_'))
        before={m:list(m.objects.order_by('pk').values()) for m in hp.MODELS.values()}
        for user in [admin,viewer]:
            self.client.force_login(user)
            for model in hp.MODELS.values():
                name=model._meta.model_name; record=model.objects.first()
                self.assertEqual(self.client.get(reverse(f'admin:core_{name}_changelist')).status_code,200)
                change=reverse(f'admin:core_{name}_change',args=[record.pk])
                self.assertEqual(self.client.get(change).status_code,200)
                self.assertEqual(self.client.post(change,{'effective_amount':'9999','source_values':'{}'}).status_code,403)
                self.assertEqual(self.client.post(reverse(f'admin:core_{name}_add'),{}).status_code,403)
                self.assertEqual(self.client.post(reverse(f'admin:core_{name}_delete',args=[record.pk]),{'post':'yes'}).status_code,403)
        self.assertEqual(before,{m:list(m.objects.order_by('pk').values()) for m in hp.MODELS.values()})


@skipUnless(os.environ.get('SDIEGO_HISTORY_PACKAGE') and os.environ.get('MALLARD_BANK_CSV'),'Set both historical package variables for private Stage C integration.')
class HistoricalPaymentTests(TestCase):
    def test_approved_package_counts_totals_and_idempotency(self):
        package=os.environ['SDIEGO_HISTORY_PACKAGE']; import_catalogs(package)
        from core.historical_charges import import_charges
        import_charges(package)
        records,report=preflight_mallard_file(Path(os.environ['MALLARD_BANK_CSV']))
        self.assertEqual(report['rejected'],0); persist_transactions(records)
        models=[House,Contact,HouseContact,ChargeConcept,FeeConfiguration,BankTransaction,Charge,ChargeExclusion]
        before={m:list(m.objects.order_by('pk').values()) for m in models}
        self.assertEqual(hp.import_payments(package,preflight=True)['reviewed_payments']['would_import'],596)
        self.assertFalse(Payment.objects.exists())
        report=hp.import_payments(package)
        self.assertEqual({n:r['imported'] for n,r in report.items()},{n:hp.COUNTS[n] for n in hp.MODELS})
        self.assertEqual(Payment.objects.aggregate(total=Sum('effective_amount'))['total'],Decimal('599900.00'))
        self.assertEqual(Payment.objects.filter(bank_transaction__isnull=False).aggregate(total=Sum('effective_amount'))['total'],Decimal('491500.00'))
        self.assertEqual(CFPayment.objects.aggregate(total=Sum('additional_credit_amount'))['total'],Decimal('108400.00'))
        self.assertEqual(CFPayment.objects.filter(treatment='BANK_DIRECT_ALREADY_REPRESENTED').count(),110)
        self.assertEqual(CFReconciliationGroup.objects.aggregate(total=Sum('payment_total'))['total'],Decimal('99400.00'))
        self.assertEqual(CFPayment.objects.get(source_id='BANK-173392010109').assigned_house.number,'6131')
        self.assertEqual(ReceiptEvidence.objects.filter(source_id__in=hp.PENDING_6114,reviewed_payment__isnull=True).count(),2)
        snapshots={m:list(m.objects.order_by('pk').values()) for m in hp.MODELS.values()}
        report=hp.import_payments(package)
        self.assertTrue(all(r['imported']==0 and r['unchanged']==hp.COUNTS[n] for n,r in report.items()))
        self.assertEqual(snapshots,{m:list(m.objects.order_by('pk').values()) for m in hp.MODELS.values()})
        self.assertEqual(before,{m:list(m.objects.order_by('pk').values()) for m in models})


class PaymentSourceContractTests(TestCase):
    """Corrupt only temporary files; never touch private historical packages."""
    def test_hash_schema_header_and_count_failures_before_persistence(self):
        from core.tests.test_historical_catalogs import CatalogImportTests
        self.write=CatalogImportTests.write.__get__(self)
        self.write_rows=CatalogImportTests.write_rows.__get__(self)
        CatalogImportTests.setUp(self)
        old=self.package.parent/'import-layouts-increment02-stable-identity'
        schema=json.loads((old/'schema.json').read_bytes())
        raw_files={}
        for name,columns in hp.COLUMNS.items():
            schema[name+'.csv']={field:'decimal(18,2) nullable' if field in hp.MONEY else 'date ISO nullable' if field in hp.DATES else 'integer' if field in {'payment_count','deposit_count','version'} else 'text nullable' for field in columns}
            raw_files[name+'.csv']=(','.join(columns)+'\n').encode()
        raw_files['schema.json']=json.dumps(schema).encode()
        def publish():
            for filename,raw in raw_files.items(): (old/filename).write_bytes(raw)
            old_manifest=dict(contract_version='sdiego-history-v1.1',source_namespace='sdiego-banbajio-history',reviewed_cutoff='2026-09-25',files={n:hp.digest(raw) for n,raw in raw_files.items()},counts={n:0 for n in hp.COLUMNS})
            raw=json.dumps(old_manifest).encode(); (old/'manifest.json').write_bytes(raw)
            self.manifest['source_sha256']['outputs/import-layouts-increment02-stable-identity/manifest.json']=hp.digest(raw)
            self.manifest['source_sha256']['outputs/import-layouts-increment02-stable-identity/schema.json']=hp.digest(raw_files['schema.json'])
            references={name+'.csv':dict(path='../import-layouts-increment02-stable-identity/'+name+'.csv',sha256=hp.digest(raw_files[name+'.csv']),records=0) for name in hp.COLUMNS}
            self.write('existing_exports.json',json.dumps(references).encode())
        publish()
        with self.assertRaisesMessage(ValidationError,'source count'): hp.import_payments(self.package)
        original=raw_files['reviewed_payments.csv']
        raw_files['reviewed_payments.csv']=original.replace(b'payment_id,',b'wrong_header,',1); publish()
        with self.assertRaisesMessage(ValidationError,'header differs'): hp.import_payments(self.package)
        raw_files['reviewed_payments.csv']=original+b'broken,row\n'; publish()
        with self.assertRaisesMessage(ValidationError,'column count'): hp.import_payments(self.package)
        raw_files['reviewed_payments.csv']=original
        schema['reviewed_payments.csv']['amount']='integer'
        raw_files['schema.json']=json.dumps(schema).encode(); publish()
        with self.assertRaisesMessage(ValidationError,'schema field definitions'): hp.import_payments(self.package)
        (old/'reviewed_payments.csv').write_bytes(b'changed after manifest')
        with self.assertRaisesMessage(ValidationError,'SHA-256'): hp.import_payments(self.package)
        self.assertTrue(all(not model.objects.exists() for model in hp.MODELS.values()))
