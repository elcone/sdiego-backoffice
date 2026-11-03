from django.db.models import Q
from django_filters import rest_framework as filters
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter

from treasury.models import BankTransaction, Charge


class BankTransactionSearchFilter(SearchFilter):
    def filter_queryset(self, request, queryset, view):
        scope = request.query_params.get("search_scope")
        if scope is None:
            return super().filter_queryset(request, queryset, view)
        if scope not in ("all", "house", "concept"):
            raise ValidationError({"search_scope": "Seleccione un alcance válido."})

        query = request.query_params.get(self.search_param, "").strip()
        if not query:
            return queryset

        concept = Q(concept__icontains=query)
        number = query.lstrip("0") or "0"
        house = Q(pk__in=[])
        if number.isascii() and number.isdecimal() and len(number) <= 5:
            house = Q(house__number=int(number))

        if scope == "house":
            return queryset.filter(house)
        if scope == "concept":
            return queryset.filter(concept)
        return queryset.filter(house | concept)


class BankTransactionFilter(filters.FilterSet):
    tab = filters.ChoiceFilter(
        choices=[("all", "Todos"), ("credit", "Ingresos"), ("debit", "Egresos")],
        method="filter_tab",
    )

    timestamp_from = filters.IsoDateTimeFilter(
        field_name="transaction_timestamp", lookup_expr="gte"
    )
    timestamp_before = filters.IsoDateTimeFilter(
        field_name="transaction_timestamp", lookup_expr="lt"
    )
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

    def filter_tab(self, queryset, name, value):
        if value == "credit":
            return queryset.filter(line_type=BankTransaction.LineType.CREDIT)
        if value == "debit":
            return queryset.exclude(line_type=BankTransaction.LineType.CREDIT)
        return queryset

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
