"""Operational business rules using native ORM records, independent of private CSVs."""
from datetime import date, datetime, time, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from core.balances import ZERO, account_statement, house_balances
from core.models import BankTransaction, CFGroupDeposit, CFPayment, CFReconciliationGroup, Charge, ChargeConcept, ChargeExclusion, House, Payment


class BalanceTests(TestCase):
    def setUp(self):
        self.houses = [House.objects.create(number=number) for number in ['6101', '6103', '6105']]
        self.concept = ChargeConcept.objects.create(code='FEE', name='Regular fee')
        self.sequence = 0
        self.cutoff = date(2026, 9, 25)

    def key(self):
        self.sequence += 1
        return f'operational-{self.sequence}'

    def charge(self, house, amount, day=date(2026, 9, 1), discount=ZERO):
        return Charge.objects.create(source_id=self.key(), house=house, concept=self.concept, effective_date=day,
                                     period=day.strftime('%Y-%m'), nominal_amount=Decimal(amount)+discount,
                                     discount_amount=discount, amount=amount, original_amount=Decimal(amount)+discount,
                                     source_status='ORIGINAL', revision_version=0)

    def payment(self, house, amount, day=date(2026, 9, 2), *, approved=True, bank=None, blocked=''):
        return Payment.objects.create(source_id=self.key(), house=house, bank_transaction=bank,
                                      origin='BANCO_DIRECTO' if bank else 'COMUNIDAD_FELIZ',
                                      real_date=day, effective_date=day, amount=amount,
                                      effective_amount=amount if approved else ZERO,
                                      assignment_approved=approved, source_status='APROBADO', blocked=blocked)

    def bank(self, amount):
        return BankTransaction.objects.create(bank_account='account', bank_receipt=self.key(), line_type='CREDIT',
                                              transaction_code=self.key(), transaction_date=self.cutoff, transaction_time=time(12),
                                              description='Bank receipt', amount=amount, source_identifiers={}, source_values={},
                                              source_file='', source_file_sha256='', source_row_number=1)

    def test_positive_zero_credit_and_empty_house_balances_are_decimal(self):
        for house in self.houses:
            self.charge(house, Decimal('1000.00'))
        self.payment(self.houses[0], Decimal('250.25'))
        self.payment(self.houses[1], Decimal('1000.00'))
        self.payment(self.houses[2], Decimal('1250.50'))
        empty = House.objects.create(number='empty')
        results = {h.pk: h for h in house_balances(as_of=self.cutoff)}
        self.assertEqual([results[h.pk].balance for h in self.houses], [Decimal('749.75'), ZERO, Decimal('-250.50')])
        for row in results.values():
            self.assertTrue(all(isinstance(getattr(row, attr), Decimal) for attr in ['total_charges', 'total_payments', 'balance']))
        self.assertEqual((results[empty.pk].total_charges, results[empty.pk].total_payments, results[empty.pk].balance), (ZERO, ZERO, ZERO))
        self.assertEqual(account_statement(empty, as_of=self.cutoff), [])

    def test_independent_sums_do_not_multiply_two_charges_and_three_payments(self):
        house, other = self.houses[:2]
        for value in ['100.00', '200.00']: self.charge(house, Decimal(value))
        for value in ['10.00', '20.00', '30.00']: self.payment(house, Decimal(value))
        self.charge(other, Decimal('999.00')); self.payment(other, Decimal('123.00'))
        result = house_balances(House.objects.filter(pk=house.pk), as_of=self.cutoff).get()
        self.assertEqual((result.total_charges, result.total_payments, result.balance), (Decimal('300'), Decimal('60'), Decimal('240')))
        self.assertEqual(sum(r['charge_amount'] for r in account_statement(house, as_of=self.cutoff)), Decimal('300'))

    def test_cutoff_uses_effective_dates_inclusively_and_today_excludes_future(self):
        house = self.houses[0]
        self.charge(house, Decimal('1000'), self.cutoff, discount=Decimal('100'))
        self.charge(house, Decimal('2000'), date(2026, 10, 1))
        payment = self.payment(house, Decimal('600'), self.cutoff)
        payment.real_date = date(2026, 1, 1); payment.save()
        self.payment(house, Decimal('700'), date(2026, 10, 2))
        self.assertEqual(house_balances(as_of=date(2026, 9, 24)).get(pk=house.pk).balance, ZERO)
        current = house_balances(as_of=self.cutoff).get(pk=house.pk)
        self.assertEqual((current.total_charges, current.total_payments, current.balance), (Decimal('1000'), Decimal('600'), Decimal('400')))
        self.assertEqual(account_statement(house, as_of=self.cutoff)[-1]['running_balance'], current.balance)
        with patch('core.balances.timezone.localdate', return_value=self.cutoff):
            self.assertEqual(house_balances().get(pk=house.pk).balance, Decimal('400'))
            self.assertEqual(len(account_statement(house)), 2)

    def test_cf_bank_settlements_and_duplicate_evidence_do_not_create_payments(self):
        house = self.houses[0]
        self.charge(house, Decimal('3000'))
        payment = self.payment(house, Decimal('1000'))
        cf = CFPayment.objects.create(source_id=self.key(), payment=payment, original_house=house, assigned_house=house,
                                      original_date=payment.real_date, effective_date=payment.effective_date,
                                      original_amount=1000, recognized_amount=1000, additional_credit_amount=1000,
                                      treatment='CF_GROUP_RECOGNIZED', recognition='APPROVED', settlement='PENDING')
        group = CFReconciliationGroup.objects.create(source_id=self.key(), payment_count=1, deposit_count=1,
                                                     payment_total=1000, bank_total=1000, difference=0, status='SETTLED', evidence_level='CONFIRMED')
        settlement = self.bank(Decimal('1000'))
        self.payment(None, Decimal('1000'), approved=False, bank=settlement, blocked='Settlement')
        CFGroupDeposit.objects.create(group=group, bank_transaction=settlement)
        direct = self.payment(house, Decimal('500'), bank=self.bank(Decimal('500')))
        CFPayment.objects.create(source_id=self.key(), payment=direct, bank_transaction=direct.bank_transaction,
                                 original_house=house, assigned_house=house, original_date=direct.real_date,
                                 effective_date=direct.effective_date, original_amount=500, recognized_amount=500,
                                 additional_credit_amount=0, treatment='BANK_DIRECT_ALREADY_REPRESENTED', recognition='APPROVED', settlement='DIRECT_BANK_MATCH')
        CFPayment.objects.create(source_id=self.key(), original_house=house, assigned_house=house,
                                 original_date=date(2026, 1, 1), original_amount=900,
                                 recognized_amount=0, additional_credit_amount=0, treatment='PRIOR_CLOSURE_NOT_APPLIED',
                                 recognition='NOT_APPLIED', settlement='NOT_ESTABLISHED')
        result = house_balances(as_of=self.cutoff).get(pk=house.pk)
        self.assertEqual((result.total_payments, result.balance), (Decimal('1500'), Decimal('1500')))
        self.assertEqual(len(account_statement(house, as_of=self.cutoff)), 3)
        self.assertEqual(cf.payment.effective_amount, Decimal('1000'))

    def test_unapproved_unassigned_blocked_zero_and_audit_records_are_excluded(self):
        house = self.houses[0]
        self.charge(house, ZERO)
        self.payment(house, Decimal('900'), approved=False)
        self.payment(None, Decimal('800'), approved=False)
        self.payment(house, Decimal('700'), approved=False, blocked='Unapplied')
        ChargeExclusion.objects.create(source_id='N-51296e66ae61daf5', house=house, reason='Excluded charge', revision_version=2,
                                       revision_updated=datetime(2026, 10, 3, tzinfo=datetime_timezone.utc), evidence_ids=[])
        row = house_balances(as_of=self.cutoff).get(pk=house.pk)
        self.assertEqual((row.total_charges, row.total_payments, row.balance), (ZERO, ZERO, ZERO))
        self.assertEqual(account_statement(house, as_of=self.cutoff), [])

    def test_statement_is_chronological_deterministic_and_one_query(self):
        house = self.houses[0]
        self.payment(house, Decimal('50'), date(2026, 9, 3))
        self.charge(house, Decimal('100'), date(2026, 9, 3))
        self.charge(house, Decimal('200'), date(2026, 9, 3))
        self.payment(house, Decimal('25'), date(2026, 9, 3))
        self.charge(house, Decimal('10'), date(2026, 9, 1))
        with self.assertNumQueries(1): rows = account_statement(house, as_of=self.cutoff)
        self.assertEqual([r['transaction_type'] for r in rows], ['charge', 'charge', 'charge', 'payment', 'payment'])
        self.assertEqual([r['running_balance'] for r in rows], [Decimal(v) for v in ['10', '110', '310', '260', '235']])
        self.assertEqual(rows, account_statement(house, as_of=self.cutoff))
        self.assertTrue(all(isinstance(r[key], Decimal) for r in rows for key in ['charge_amount', 'payment_amount', 'running_balance']))
        self.assertEqual(rows[-1]['running_balance'], house_balances(as_of=self.cutoff).get(pk=house.pk).balance)

    def test_all_79_houses_are_one_query_and_match_persisted_totals_at_same_cutoff(self):
        for i in range(76): House.objects.create(number=f'house-{i:02d}')
        houses = list(House.objects.all())
        for i, house in enumerate(houses):
            self.charge(house, Decimal('100'))
            self.payment(house, Decimal(i + 1))
            self.charge(house, Decimal('200'), date(2026, 10, 1))
        with self.assertNumQueries(1):
            results = list(house_balances(as_of=self.cutoff))
            totals = [sum((getattr(h, key) for h in results), ZERO) for key in ['total_charges', 'total_payments', 'balance']]
        self.assertEqual(len(results), 79)
        charges = Charge.objects.filter(effective_date__lte=self.cutoff).aggregate(total=Sum('amount'))['total']
        payments = Payment.objects.filter(effective_date__lte=self.cutoff, assignment_approved=True).aggregate(total=Sum('effective_amount'))['total']
        self.assertEqual(totals, [charges, payments, charges-payments])

    def test_cutoff_type_is_explicit(self):
        for value in ['2026-09-25', datetime(2026, 9, 25)]:
            with self.assertRaises(TypeError): house_balances(as_of=value)
            with self.assertRaises(TypeError): account_statement(self.houses[0], as_of=value)


class HouseAccountAdminTests(TestCase):
    key = BalanceTests.key
    charge = BalanceTests.charge
    payment = BalanceTests.payment

    def setUp(self):
        BalanceTests.setUp(self)
        self.house = self.houses[0]
        self.charge(self.house, Decimal('1000'), self.cutoff)
        self.payment(self.house, Decimal('1200'), self.cutoff)
        self.charge(self.house, Decimal('500'), date(2026, 10, 1))
        Charge.objects.filter(house=self.house).update(source_values={'importer':'private historical metadata'}, detail='CF-G09 CSV-only detail')
        self.viewer = get_user_model().objects.create_user(username='accounts', is_staff=True)
        self.viewer.user_permissions.add(*Permission.objects.filter(content_type__app_label='core', codename__in=['view_house','view_charge','view_payment']))
        self.client.force_login(self.viewer)
        self.url = reverse('admin:core_house_account_statement', args=[self.house.pk])

    def test_financial_view_has_totals_credit_statement_and_no_source_metadata(self):
        response = self.client.get(self.url, {'as_of':'2026-09-25'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['balance'], Decimal('-200'))
        self.assertEqual(response.context['total_charges'], Decimal('1000'))
        self.assertEqual(response.context['total_payments'], Decimal('1200'))
        self.assertEqual(len(response.context['rows']), 2)
        self.assertContains(response, 'Regular fee')
        self.assertContains(response, 'Running balance')
        self.assertContains(response, '-200.00')
        for hidden in ['CF-G09', 'CSV-only', 'private historical metadata', 'source_values', 'importer']:
            self.assertNotContains(response, hidden)
        with patch('core.admin.timezone.localdate', return_value=self.cutoff):
            self.assertEqual(self.client.get(self.url).context['balance'], Decimal('-200'))

    def test_links_from_house_list_and_change_form(self):
        self.assertContains(self.client.get(reverse('admin:core_house_changelist')), self.url)
        self.assertContains(self.client.get(reverse('admin:core_house_change', args=[self.house.pk])), self.url)

    def test_existing_permissions_protect_financial_view_and_list(self):
        for missing in ['view_house','view_charge','view_payment']:
            user = get_user_model().objects.create_user(username=missing, is_staff=True)
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label='core', codename__in=[p for p in ['view_house','view_charge','view_payment'] if p != missing]))
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            if missing != 'view_house':
                response = self.client.get(reverse('admin:core_house_changelist'))
                self.assertNotContains(response, 'Current balance')
                self.assertNotContains(response, self.url)

    def test_invalid_cutoff_missing_house_and_no_post_writes(self):
        response = self.client.get(self.url, {'as_of':'invalid'})
        self.assertContains(response, 'Enter a valid date.', status_code=400)
        self.assertNotContains(response, 'Account summary', status_code=400)
        self.assertEqual(self.client.get(reverse('admin:core_house_account_statement', args=[999999])).status_code, 404)
        before = list(Charge.objects.order_by('pk').values())
        self.assertEqual(self.client.post(self.url, {'balance': '0'}).status_code, 405)
        self.assertEqual(before, list(Charge.objects.order_by('pk').values()))
