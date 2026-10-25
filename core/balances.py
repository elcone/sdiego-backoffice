"""Operational balances and statements from effective movements; no stored balances."""
from datetime import date, datetime
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, TextField, Value
from django.db.models.functions import Coalesce, Concat
from django.utils import timezone

from .models import Charge, House, Payment

ZERO = Decimal('0.00')
MONEY = DecimalField(max_digits=24, decimal_places=2)


def _movements(as_of):
    cutoff = timezone.localdate() if as_of is None else as_of
    if not isinstance(cutoff, date) or isinstance(cutoff, datetime):
        raise TypeError('as_of must be a date or None.')
    charges = Charge.objects.filter(effective_date__lte=cutoff).exclude(amount=0)
    payments = Payment.objects.filter(
        effective_date__lte=cutoff, assignment_approved=True,
        house__isnull=False, blocked='', effective_amount__gt=0,
    )
    return charges, payments


def house_balances(houses=None, *, as_of=None):
    """Return a House queryset with Decimal total_charges, total_payments and balance.

    None means today; explicit cutoffs are inclusive. Independent aggregate subqueries
    avoid multiplying charges by payments and return all houses in one SQL query.
    """
    houses = House.objects.all() if houses is None else houses
    charges, payments = _movements(as_of)
    charge_total = charges.filter(house_id=OuterRef('pk')).order_by().values('house_id').annotate(total=Sum('amount')).values('total')
    payment_total = payments.filter(house_id=OuterRef('pk')).order_by().values('house_id').annotate(total=Sum('effective_amount')).values('total')
    return houses.annotate(
        total_charges=Coalesce(Subquery(charge_total, output_field=MONEY), Value(ZERO, output_field=MONEY)),
        total_payments=Coalesce(Subquery(payment_total, output_field=MONEY), Value(ZERO, output_field=MONEY)),
    ).annotate(balance=F('total_charges') - F('total_payments'))


def account_statement(house, *, as_of=None):
    """Return effective movements and Decimal running balances in a single SQL snapshot.

    Order: effective date, charges before payments, then permanent record PK.
    Descriptions use operational fields only; no import/reconciliation metadata.
    """
    charges, payments = _movements(as_of)
    columns = ('date', 'kind_order', 'movement_id', 'transaction_type', 'description', 'charge_amount', 'payment_amount')
    charges = charges.filter(house=house).order_by().annotate(
        date=F('effective_date'), kind_order=Value(0), movement_id=F('pk'),
        transaction_type=Value('charge'),
        description=Concat('concept__name', Value(' — '), 'period', output_field=TextField()),
        charge_amount=F('amount'), payment_amount=Value(ZERO, output_field=MONEY),
    ).values(*columns)
    payments = payments.filter(house=house).order_by().annotate(
        date=F('effective_date'), kind_order=Value(1), movement_id=F('pk'),
        transaction_type=Value('payment'), description=Value('Payment', output_field=TextField()),
        charge_amount=Value(ZERO, output_field=MONEY), payment_amount=F('effective_amount'),
    ).values(*columns)
    rows = []
    running = ZERO
    for row in charges.union(payments, all=True).order_by('date', 'kind_order', 'movement_id'):
        running += row['charge_amount'] - row['payment_amount']
        row.pop('kind_order')
        row.pop('movement_id')
        row['running_balance'] = running
        rows.append(row)
    return rows
