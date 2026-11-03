from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from treasury.models import BankTransaction, Charge, ChargeType, ExpenseType, House

from .filters import (
    BankTransactionFilter,
    BankTransactionSearchFilter,
    ChargeFilter,
    HouseFilter,
)
from .permissions import CanChangeBankTransaction
from .serializers import (
    BankTransactionSerializer,
    ChargeSerializer,
    ChargeTypeSerializer,
    ExpenseTypeSerializer,
    HouseSerializer,
)


class HouseViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = House.objects.order_by("number", "id")
    serializer_class = HouseSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = HouseFilter

    def get_queryset(self):
        money = DecimalField(max_digits=24, decimal_places=2)
        charges = (
            Charge.objects.filter(house_id=OuterRef("pk"))
            .order_by()
            .values("house_id")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        payments = (
            BankTransaction.objects.filter(
                house_id=OuterRef("pk"), line_type=BankTransaction.LineType.CREDIT
            )
            .order_by()
            .values("house_id")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        return (
            super()
            .get_queryset()
            .annotate(
                total_charges=Coalesce(
                    Subquery(charges, output_field=money),
                    Value(Decimal("0.00"), output_field=money),
                ),
                total_payments=Coalesce(
                    Subquery(payments, output_field=money),
                    Value(Decimal("0.00"), output_field=money),
                ),
            )
            .annotate(balance=F("total_charges") - F("total_payments"))
        )

    @action(detail=True)
    def statement(self, request, pk=None):
        house = self.get_object()
        # Evaluate both sources in one SQL statement to share a database snapshot.
        charges = (
            Charge.objects.filter(house=house)
            .order_by()
            .values("id", "period", "amount", "description")
            .annotate(transaction_type=Value("charge"))
        )
        payments = (
            BankTransaction.objects.filter(
                house=house, line_type=BankTransaction.LineType.CREDIT
            )
            .order_by()
            .annotate(
                effective_date=F("transaction_timestamp__date"),
                statement_description=F("concept"),
            )
            .values("id", "effective_date", "amount", "statement_description")
            .annotate(transaction_type=Value("payment"))
        )
        # The date transform uses Django's configured timezone.
        with timezone.override(timezone.get_default_timezone()):
            rows = list(charges.union(payments, all=True))
        rows.sort(
            key=lambda row: (
                row["period"],
                row["transaction_type"] != "charge",
                row["id"],
            )
        )
        total_charges = total_payments = balance = Decimal("0.00")
        movements = []
        for row in rows:
            charge = (
                row["amount"]
                if row["transaction_type"] == "charge"
                else Decimal("0.00")
            )
            payment = (
                row["amount"]
                if row["transaction_type"] == "payment"
                else Decimal("0.00")
            )
            total_charges += charge
            total_payments += payment
            balance += charge - payment
            movements.append(
                {
                    "id": row["id"],
                    "transaction_type": row["transaction_type"],
                    "date": row["period"].isoformat(),
                    "description": row["description"],
                    "charge_amount": format(charge, ".2f"),
                    "payment_amount": format(payment, ".2f"),
                    "running_balance": format(balance, ".2f"),
                }
            )
        return Response(
            {
                "house": {"id": house.pk, "number": house.number},
                "total_charges": format(total_charges, ".2f"),
                "total_payments": format(total_payments, ".2f"),
                "balance": format(balance, ".2f"),
                "results": movements,
            }
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class BankTransactionViewSet(mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    queryset = BankTransaction.objects.select_related("house", "expense_type").order_by(
        "-transaction_timestamp", "id"
    )
    serializer_class = BankTransactionSerializer
    permission_classes = (IsAuthenticated, CanChangeBankTransaction)
    http_method_names = ("get", "patch", "head", "options")
    filter_backends = (DjangoFilterBackend, BankTransactionSearchFilter, OrderingFilter)
    ordering_fields = (
        "transaction_timestamp",
        "line_type",
        "amount",
        "concept",
        "description",
        "is_reviewed",
    )

    filterset_class = BankTransactionFilter
    search_fields = ("bank_receipt", "concept")

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        return queryset.order_by(*queryset.query.order_by, "pk")

    @action(detail=False)
    def summary(self, request):
        dates = {
            key: request.query_params[key]
            for key in ("date_from", "date_to", "timestamp_from", "timestamp_before")
            if key in request.query_params
        }
        date_filter = BankTransactionFilter(dates, queryset=self.get_queryset())
        if not date_filter.is_valid():
            raise ValidationError(date_filter.errors)
        totals = date_filter.qs.aggregate(
            income=Sum("amount", filter=Q(line_type="CREDIT"), default=0),
            expense=Sum("amount", filter=Q(line_type="DEBIT"), default=0),
            commission=Sum(
                "amount",
                filter=Q(line_type__in=["COMMISSION", "COMMISSION_VAT"]),
                default=0,
            ),
        )
        latest = (
            BankTransaction.objects.order_by(
                "bank_account", "-transaction_timestamp", "-pk"
            )
            .distinct("bank_account")
            .values_list("balance", flat=True)
        )
        return Response(
            {
                **{key: str(value) for key, value in totals.items()},
                "balance": str(sum(latest)),
                "can_change": request.user.has_perm("treasury.change_banktransaction"),
            }
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        return (
            queryset.select_for_update(of=("self",))
            if self.request.method == "PATCH"
            else queryset
        )

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)


class ChargeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Charge.objects.order_by("-period", "id")
    serializer_class = ChargeSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ChargeFilter


class ChargeTypeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ChargeType.objects.order_by("name", "id")
    serializer_class = ChargeTypeSerializer


class ExpenseTypeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ExpenseType.objects.order_by("name", "id")
    serializer_class = ExpenseTypeSerializer
