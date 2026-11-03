from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    BankTransactionViewSet,
    ChargeTypeViewSet,
    ChargeViewSet,
    ExpenseTypeViewSet,
    HouseViewSet,
    SummaryView,
)

router = SimpleRouter()
router.register("house", HouseViewSet)
router.register("bank-transaction", BankTransactionViewSet)
router.register("charge", ChargeViewSet)
router.register("charge-type", ChargeTypeViewSet)
router.register("expense-type", ExpenseTypeViewSet)

urlpatterns = [
    path("summary/", SummaryView.as_view(), name="treasury-summary"),
    *router.urls,
]
