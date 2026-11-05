from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, F, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Abs, Coalesce
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import DjangoModelPermissions, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from treasury.bank_import import BankImportError, import_bank_zip
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
    BulkChargeSerializer,
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
    http_method_names = ("get", "post", "patch", "head", "options")
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

    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        parser_classes=[MultiPartParser],
    )
    def import_zip(self, request):
        if not request.user.has_perm("treasury.add_banktransaction"):
            raise PermissionDenied(
                "No tiene permiso para importar movimientos bancarios."
            )
        if set(request.data) != {"file"} or len(request.FILES.getlist("file")) != 1:
            return Response(
                {
                    "code": "invalid_file",
                    "detail": "Seleccione un único archivo ZIP en el campo file.",
                    "errors": [],
                },
                status=400,
            )
        try:
            result = import_bank_zip(request.FILES["file"])
        except BankImportError as error:
            return Response(
                {
                    "code": error.code,
                    "detail": error.detail,
                    "errors": error.errors,
                    "inserted_count": 0,
                },
                status=error.status,
            )
        return Response(result)

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
                "can_import": request.user.has_perm("treasury.add_banktransaction"),
                "latest_transaction_timestamp": BankTransaction.objects.aggregate(
                    latest=Max("transaction_timestamp")
                )["latest"],
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


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ChargeViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Charge.objects.order_by("-period", "id")
    serializer_class = ChargeSerializer
    permission_classes = (DjangoModelPermissions,)
    http_method_names = ("get", "post", "head", "options")
    filter_backends = (DjangoFilterBackend, SearchFilter)
    filterset_class = ChargeFilter
    search_fields = ("house__number", "charge_type__name", "period", "description")

    @action(detail=False, methods=["post"], url_path="generate-all")
    @transaction.atomic
    def generate_all(self, request):
        serializer = BulkChargeSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        # Serialize bulk generations of this type without blocking FK key-share
        # locks used by the unchanged individual creation endpoint.
        ChargeType.objects.select_for_update(no_key=True).get(
            pk=values["charge_type"].pk
        )
        house_ids = list(House.objects.order_by("pk").values_list("pk", flat=True))
        existing = set(
            Charge.objects.filter(
                house_id__in=house_ids,
                charge_type=values["charge_type"],
                period=values["period"],
                amount=values["amount"],
                due_date=values["due_date"],
            ).values_list("house_id", flat=True)
        )
        for house_id in house_ids:
            if house_id not in existing:
                Charge.objects.create(house_id=house_id, **values)
        return Response(
            {
                "created_count": len(house_ids) - len(existing),
                "skipped_count": len(existing),
                "total_count": len(house_ids),
            }
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ChargeTypeViewSet(viewsets.ModelViewSet):
    queryset = ChargeType.objects.order_by("name", "id")
    serializer_class = ChargeTypeSerializer
    permission_classes = (DjangoModelPermissions,)
    http_method_names = ("get", "post", "patch", "delete", "head", "options")

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError(
                {
                    "detail": "No se puede eliminar un tipo que tiene registros asociados."
                }
            ) from None


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ExpenseTypeViewSet(viewsets.ModelViewSet):
    queryset = ExpenseType.objects.order_by("name", "id")
    serializer_class = ExpenseTypeSerializer
    permission_classes = (DjangoModelPermissions,)
    http_method_names = ("get", "post", "patch", "delete", "head", "options")

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError(
                {
                    "detail": "No se puede eliminar un tipo que tiene registros asociados."
                }
            ) from None


class SummaryView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        now = timezone.localtime(timezone.now(), timezone.get_default_timezone())
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        monthly = BankTransaction.objects.filter(
            transaction_timestamp__gte=start,
            transaction_timestamp__lt=end,
        )
        totals = monthly.aggregate(
            income=Sum("amount", filter=Q(line_type="CREDIT"), default=0),
            expense=Sum(
                "amount",
                filter=Q(line_type__in=["DEBIT", "COMMISSION", "COMMISSION_VAT"]),
                default=0,
            ),
        )
        pending = BankTransaction.objects.filter(
            line_type__in=["DEBIT", "COMMISSION", "COMMISSION_VAT"]
        ).filter(Q(expense_type__isnull=True) | Q(is_reviewed=False))
        pending_total = pending.aggregate(total=Sum("amount", default=0))["total"]
        unrecognized_income = BankTransaction.objects.filter(
            line_type=BankTransaction.LineType.CREDIT, house__isnull=True, is_reviewed=False
        ).aggregate(total=Sum("amount", default=0))["total"]
        houses = HouseViewSet().get_queryset().filter(balance__gt=0)
        receivable = houses.aggregate(total=Sum("balance", default=0))["total"]
        top_houses = houses.order_by("-balance", "number", "pk").values(
            "number", "balance"
        )[:10]
        top_expenses = (
            monthly.filter(line_type__in=["DEBIT", "COMMISSION", "COMMISSION_VAT"])
            .values("expense_type_id", "expense_type__name")
            .annotate(total=Abs(Sum("amount")))
            .order_by("-total", "expense_type__name", "expense_type_id")[:10]
        )
        return Response(
            {
                "income": format(totals["income"], ".2f"),
                "expense": format(totals["expense"], ".2f"),
                "receivable": format(receivable, ".2f"),
                "unrecognized_income": format(unrecognized_income, ".2f"),
                "pending_expenses": format(pending_total, ".2f"),
                "top_houses": [
                    {"number": row["number"], "balance": format(row["balance"], ".2f")}
                    for row in top_houses
                ],
                "top_expenses": [
                    {
                        "name": (
                            row["expense_type__name"]
                            if row["expense_type_id"] is not None
                            else "Sin clasificar"
                        ),
                        "total": format(row["total"], ".2f"),
                    }
                    for row in top_expenses
                ],
            }
        )
