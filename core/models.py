from decimal import Decimal

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .querysets import HouseContactQuerySet


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class HistoricalReference(TimestampedModel):
    source_id = models.CharField(max_length=255, unique=True, null=True, blank=True, editable=False)
    source_values = models.JSONField(default=dict, blank=True, editable=False)
    source_file = models.CharField(max_length=255, blank=True, editable=False)
    source_file_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    source_row_number = models.PositiveIntegerField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True


class House(HistoricalReference):
    number = models.CharField(max_length=30, unique=True)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.number


class Contact(HistoricalReference):
    name = models.CharField(max_length=200)
    rfc = models.CharField("RFC", max_length=13, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class HouseContact(HistoricalReference):
    objects = HouseContactQuerySet.as_manager()

    house = models.ForeignKey(
        House, on_delete=models.CASCADE, related_name="house_contacts"
    )
    contact = models.ForeignKey(
        Contact, on_delete=models.CASCADE, related_name="house_contacts"
    )
    is_responsible = models.BooleanField(default=False, null=True, blank=True)

    class Meta:
        ordering = ["house__number", "contact__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["house", "contact"], name="unique_house_contact"
            ),
        ]

    def __str__(self):
        return f"{self.house} — {self.contact}"


class PayerAccount(TimestampedModel):
    contact = models.ForeignKey(
        Contact, on_delete=models.CASCADE, related_name="payer_accounts"
    )
    account_number = models.CharField(max_length=100)
    bank_name = models.CharField(max_length=150)

    class Meta:
        ordering = ["contact__name", "bank_name", "account_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["contact", "bank_name", "account_number"],
                name="unique_contact_bank_account",
            ),
        ]

    def __str__(self):
        return f"{self.bank_name} — {self.account_number}"


class ChargeConcept(HistoricalReference):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_adjustment = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name


class FeeConfiguration(TimestampedModel):
    monthly_fee = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    late_fee = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    payment_due_day = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(28)]
    )
    valid_from = models.DateField()
    valid_until = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ["-valid_from"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(monthly_fee__gte=0), name="monthly_fee_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(late_fee__gte=0), name="late_fee_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(payment_due_day__gte=1, payment_due_day__lte=28),
                name="payment_due_day_1_to_28",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True)
                | models.Q(valid_until__gte=models.F("valid_from")),
                name="fee_period_dates_ordered",
            ),
            ExclusionConstraint(
                name="fee_periods_do_not_overlap",
                expressions=[
                    (
                        models.Func(
                            "valid_from",
                            "valid_until",
                            models.Value("[]"),
                            function="DATERANGE",
                            output_field=DateRangeField(),
                        ),
                        RangeOperators.OVERLAPS,
                    )
                ],
                violation_error_message="Validity periods cannot overlap.",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.valid_from:
            return
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError(
                {"valid_until": "The ending date cannot precede the starting date."}
            )
        overlapping = (
            type(self)
            .objects.exclude(pk=self.pk)
            .filter(
                models.Q(valid_until__isnull=True)
                | models.Q(valid_until__gte=self.valid_from)
            )
        )
        if self.valid_until:
            overlapping = overlapping.filter(valid_from__lte=self.valid_until)
        if overlapping.exists():
            raise ValidationError("Validity periods cannot overlap.")

    def __str__(self):
        return f"{self.valid_from} to {self.valid_until or 'ongoing'}"


class BankTransaction(TimestampedModel):
    bank_account = models.CharField(max_length=30)
    bank_receipt = models.CharField(max_length=100)
    line_type = models.CharField(max_length=10, choices=[
        ("CREDIT", "Credit"), ("DEBIT", "Debit"),
        ("COMMISSION", "Commission"), ("VAT", "VAT"),
    ])
    transaction_date = models.DateField()
    transaction_time = models.TimeField(help_text="Local bank wall time; no timezone conversion.")
    description = models.TextField()
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Signed amount: positive for credits, negative for debits.",
    )
    transaction_code = models.CharField(
        max_length=255,
        unique=True,
        help_text="Original external reconciliation reference; not the matching identity.",
    )
    source_identifiers = models.JSONField()
    source_values = models.JSONField(
        help_text="Original column names and unmodified source values."
    )
    source_file = models.CharField(max_length=255)
    source_file_sha256 = models.CharField(max_length=64)
    source_row_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["-transaction_date", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["bank_account", "bank_receipt", "line_type"],
                name="unique_bank_account_receipt_line_type",
            ),
            models.CheckConstraint(
                condition=(models.Q(line_type="CREDIT", amount__gt=0)
                           | models.Q(line_type__in=["DEBIT", "COMMISSION", "VAT"], amount__lt=0)),
                name="bank_amount_matches_line_type",
            ),
        ]

    def __str__(self):
        return f"{self.transaction_date} — {self.amount:+.2f} — {self.description}"
