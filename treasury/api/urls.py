from django.urls import path
from rest_framework.routers import SimpleRouter

from .auth import login_view, logout_view, session_view
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
    path("auth/session/", session_view, name="auth-session"),
    path("auth/login/", login_view, name="auth-login"),
    path("auth/logout/", logout_view, name="auth-logout"),
    path("summary/", SummaryView.as_view(), name="treasury-summary"),
    *router.urls,
]
