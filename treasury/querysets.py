from decimal import Decimal

from django.db import models
from django.db.models.functions import Coalesce, Greatest


class HouseContactQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def responsible(self):
        return self.active().filter(is_responsible=True)

    def for_house(self, house):
        return self.filter(house=house)

    def with_related(self):
        return self.select_related("house", "contact")


class PayerAccountQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related("contact")


class ChargeQuerySet(models.QuerySet):
    def with_balances(self):
        from .models import PaymentApplication

        adjustments = (
            self.model.objects.filter(
                adjusts_charge_id=models.OuterRef("pk"), amount__lt=0
            )
            .order_by()
            .values("adjusts_charge_id")
            .annotate(total=models.Sum("amount"))
            .values("total")
        )
        applications = (
            PaymentApplication.objects.filter(charge_id=models.OuterRef("pk"))
            .order_by()
            .values("charge_id")
            .annotate(total=models.Sum("amount"))
            .values("total")
        )
        return self.annotate(
            exigible=Greatest(
                models.F("amount")
                + Coalesce(models.Subquery(adjustments), Decimal("0.00")),
                Decimal("0.00"),
            ),
            applied=Coalesce(models.Subquery(applications), Decimal("0.00")),
        ).annotate(
            pending=Greatest(
                models.F("exigible") - models.F("applied"), Decimal("0.00")
            )
        )


class PaymentQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related(
            "bank_transaction", "house", "payer_account__contact"
        )


class PaymentApplicationQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related(
            "payment__bank_transaction",
            "payment__house",
            "charge__concept",
            "charge__house",
        )
