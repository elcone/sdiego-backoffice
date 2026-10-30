from django.db.models import Q
from django_filters import rest_framework as filters

from treasury.models import BankTransaction, Charge


class BankTransactionFilter(filters.FilterSet):
    date_from = filters.DateFilter(
        field_name="transaction_timestamp", lookup_expr="date__gte"
    )
    date_to = filters.DateFilter(
        field_name="transaction_timestamp", lookup_expr="date__lte"
    )
    status = filters.ChoiceFilter(
        choices=[
            ("all", "Todos"),
            ("pending", "Pendientes"),
            ("classified", "Clasificados"),
        ],
        method="filter_status",
    )

    class Meta:
        model = BankTransaction
        fields = ("line_type", "house", "expense_type", "is_reviewed")

    def filter_status(self, queryset, name, value):
        if value == "all":
            return queryset
        pending = Q(line_type=BankTransaction.LineType.CREDIT, house__isnull=True) | Q(
            line_type__in=[
                BankTransaction.LineType.DEBIT,
                BankTransaction.LineType.COMMISSION,
                BankTransaction.LineType.COMMISSION_VAT,
            ],
            expense_type__isnull=True,
        )
        return (
            queryset.filter(pending)
            if value == "pending"
            else queryset.exclude(pending)
        )


class ChargeFilter(filters.FilterSet):
    class Meta:
        model = Charge
        fields = ("house", "charge_type", "period")
