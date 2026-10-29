from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from treasury.models import BankTransaction, Charge, ChargeType, ExpenseType, House

from .filters import BankTransactionFilter, ChargeFilter
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


class BankTransactionViewSet(mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    queryset = BankTransaction.objects.order_by("-transaction_timestamp", "id")
    serializer_class = BankTransactionSerializer
    permission_classes = (IsAuthenticated, CanChangeBankTransaction)
    http_method_names = ("get", "patch", "head", "options")
    filter_backends = (DjangoFilterBackend, SearchFilter)
    filterset_class = BankTransactionFilter
    search_fields = ("bank_receipt", "concept")

    def get_queryset(self):
        queryset = super().get_queryset()
        return (
            queryset.select_for_update() if self.request.method == "PATCH" else queryset
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
