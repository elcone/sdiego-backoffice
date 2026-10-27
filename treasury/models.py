from calendar import monthrange
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models.functions import ExtractDay
from django.db.models.lookups import Exact

from .querysets import HouseContactQuerySet, PayerAccountQuerySet


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(
        "fecha de creación",
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        "última actualización",
        auto_now=True,
    )

    class Meta:
        abstract = True


class House(TimestampedModel):
    number = models.CharField("número", max_length=4, unique=True)

    class Meta:
        verbose_name = "casa"
        verbose_name_plural = "casas"
        ordering = ("number",)

    def __str__(self):
        return f"Casa {self.number}"


class Contact(TimestampedModel):
    name = models.CharField("nombre", max_length=200)
    email = models.EmailField(
        "correo electrónico",
        blank=True,
    )
    phone = models.CharField(
        "teléfono",
        max_length=30,
        blank=True,
    )

    class Meta:
        verbose_name = "contacto"
        verbose_name_plural = "contactos"
        ordering = ("name", "pk")

    def __str__(self):
        return self.name


class HouseContact(TimestampedModel):
    house = models.ForeignKey(
        House,
        verbose_name="casa",
        on_delete=models.PROTECT,
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name="contacto",
        on_delete=models.PROTECT,
    )
    is_responsible = models.BooleanField(
        "es responsable",
        default=False,
    )
    is_active = models.BooleanField(
        "relación activa",
        default=True,
    )

    objects = HouseContactQuerySet.as_manager()

    class Meta:
        verbose_name = "contacto de casa"
        verbose_name_plural = "contactos de casas"
        ordering = ("house__number", "contact__name", "pk")
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=("house", "contact"),
                name="treasury_unique_house_contact",
                violation_error_message=(
                    "Ya existe una relación entre esta casa y este contacto. "
                    "Modifique el registro existente para desactivarlo o reactivarlo."
                ),
            )
        ]

    def __str__(self):
        return f"{self.house} · {self.contact}"


class PayerAccount(TimestampedModel):
    contact = models.ForeignKey(
        Contact,
        verbose_name="titular",
        on_delete=models.PROTECT,
    )
    bank = models.CharField("banco", max_length=100)
    account_number = models.CharField(
        "número de cuenta o CLABE",
        max_length=30,
    )

    objects = PayerAccountQuerySet.as_manager()

    class Meta:
        verbose_name = "cuenta pagadora"
        verbose_name_plural = "cuentas pagadoras"
        ordering = ("bank", "account_number")
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=("bank", "account_number"),
                name="treasury_unique_payer_account",
                violation_error_message="Ya existe una cuenta con este banco y número de cuenta o CLABE.",
            )
        ]

    def __str__(self):
        return f"{self.bank} · {self.account_number} · {self.contact}"


class ChargeConcept(TimestampedModel):
    class Kind(models.TextChoices):
        ORDINARY_FEE = "ORDINARY_FEE", "Cuota ordinaria"
        EXTRAORDINARY_FEE = "EXTRAORDINARY_FEE", "Cuota extraordinaria"
        SURCHARGE = "SURCHARGE", "Recargo"
        FINE = "FINE", "Multa"
        ADJUSTMENT = "ADJUSTMENT", "Ajuste"

    name = models.CharField("nombre", max_length=200, unique=True)
    kind = models.CharField("tipo", max_length=17, choices=Kind.choices)
    is_surcharge_eligible = models.BooleanField("genera recargos", default=False)

    class Meta:
        verbose_name = "concepto de cargo"
        verbose_name_plural = "conceptos de cargos"
        ordering = ("name",)
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=~models.Q(kind="SURCHARGE")
                | models.Q(is_surcharge_eligible=False),
                name="treasury_surcharge_not_eligible",
                violation_error_message="Un recargo no puede generar otros recargos.",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=(
                        "ORDINARY_FEE",
                        "EXTRAORDINARY_FEE",
                        "SURCHARGE",
                        "FINE",
                        "ADJUSTMENT",
                    )
                ),
                name="treasury_concept_valid_kind",
                violation_error_message="Seleccione un tipo de concepto válido.",
            ),
        ]

    def clean(self):
        super().clean()
        if self.kind == self.Kind.SURCHARGE and self.is_surcharge_eligible:
            raise ValidationError(
                {"is_surcharge_eligible": "Un recargo no puede generar otros recargos."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class FeeConfiguration(TimestampedModel):
    class Frequency(models.TextChoices):
        MONTHLY = "MONTHLY", "Mensual"
        ONE_TIME = "ONE_TIME", "Única ocasión"

    concept = models.ForeignKey(
        ChargeConcept, verbose_name="concepto", on_delete=models.PROTECT
    )
    amount = models.DecimalField(
        "importe",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    frequency = models.CharField(
        "periodicidad", max_length=8, choices=Frequency.choices
    )
    start_date = models.DateField("inicio de vigencia", null=True, blank=True)
    end_date = models.DateField("fin de vigencia", null=True, blank=True)
    generation_date = models.DateField("fecha de generación", null=True, blank=True)
    due_date = models.DateField("fecha de vencimiento", null=True, blank=True)

    class Meta:
        verbose_name = "configuración de cuota"
        verbose_name_plural = "configuraciones de cuotas"
        ordering = ("concept__name", "start_date", "generation_date", "pk")
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="treasury_fee_positive_amount",
                violation_error_message="El importe debe ser mayor que cero.",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        frequency="MONTHLY",
                        start_date__isnull=False,
                        generation_date__isnull=True,
                        due_date__isnull=True,
                    )
                    & (
                        models.Q(end_date__isnull=True)
                        | models.Q(end_date__gte=models.F("start_date"))
                    )
                )
                | models.Q(
                    frequency="ONE_TIME",
                    start_date__isnull=True,
                    end_date__isnull=True,
                    generation_date__isnull=False,
                    due_date__isnull=False,
                    due_date__gte=models.F("generation_date"),
                ),
                name="treasury_fee_frequency_dates",
                violation_error_message="Las fechas no son coherentes con la periodicidad.",
            ),
            models.CheckConstraint(
                condition=~models.Q(frequency="MONTHLY")
                | (
                    models.Q(start_date__day=1)
                    & (
                        models.Q(end_date__isnull=True)
                        | models.Q(
                            Exact(
                                ExtractDay(
                                    models.F("end_date")
                                    + models.Value(timedelta(days=1))
                                ),
                                1,
                            )
                        )
                    )
                ),
                name="treasury_fee_complete_months",
                violation_error_message="La vigencia debe abarcar meses completos.",
            ),
            ExclusionConstraint(
                name="treasury_fee_no_monthly_overlap",
                expressions=[
                    ("concept", RangeOperators.EQUAL),
                    (
                        models.Func(
                            "start_date",
                            "end_date",
                            models.Value("[]"),
                            function="DATERANGE",
                            output_field=DateRangeField(),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(frequency="MONTHLY"),
                violation_error_message="Ya existe una configuración mensual con vigencia superpuesta para este concepto.",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.frequency == self.Frequency.MONTHLY:
            if not self.start_date or self.start_date.day != 1:
                errors["start_date"] = "El inicio debe ser el primer día del mes."
            if self.end_date:
                if (
                    self.end_date.day
                    != monthrange(self.end_date.year, self.end_date.month)[1]
                ):
                    errors["end_date"] = "El fin debe ser el último día del mes."
                elif self.start_date and self.end_date < self.start_date:
                    errors["end_date"] = "El fin no puede ser anterior al inicio."
            for field in ("generation_date", "due_date"):
                if getattr(self, field) is not None:
                    errors[field] = (
                        "Las cuotas mensuales se generan el día 1 y vencen el día 5; deje esta fecha vacía."
                    )
        elif self.frequency == self.Frequency.ONE_TIME:
            for field in ("start_date", "end_date"):
                if getattr(self, field) is not None:
                    errors[field] = (
                        "La única ocasión utiliza fechas concretas, sin vigencia mensual."
                    )
            for field in ("generation_date", "due_date"):
                if not getattr(self, field):
                    errors[field] = "Esta fecha es obligatoria para única ocasión."
            if (
                self.generation_date
                and self.due_date
                and self.due_date < self.generation_date
            ):
                errors["due_date"] = (
                    "El vencimiento no puede ser anterior a la generación."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def preview(self, period):
        self.full_clean()
        if period.day != 1:
            raise ValidationError("El periodo debe ser el primer día del mes.")
        if self.concept.kind in (
            ChargeConcept.Kind.SURCHARGE,
            ChargeConcept.Kind.ADJUSTMENT,
        ):
            raise ValidationError("El generador no admite recargos ni ajustes.")
        if self.frequency == self.Frequency.MONTHLY:
            if period < self.start_date or (self.end_date and period > self.end_date):
                raise ValidationError(
                    "El periodo está fuera de la vigencia de la configuración."
                )
            due_date = period.replace(day=5)
        else:
            if period != self.generation_date.replace(day=1):
                raise ValidationError(
                    "El periodo debe corresponder al mes de la fecha de generación."
                )
            due_date = self.due_date
        existing = set(
            Charge.objects.filter(fee_configuration=self, period=period).values_list(
                "house_id", flat=True
            )
        )
        return [
            {
                "house": house,
                "amount": self.amount,
                "due_date": due_date,
                "exists": house.pk in existing,
            }
            for house in House.objects.all()
        ]

    def generate(self, period, *, preview_token):
        from django.core import signing

        with transaction.atomic():
            configuration = (
                type(self)
                .objects.select_for_update()
                .select_related("concept")
                .get(pk=self.pk)
            )
            rows = configuration.preview(period)
            if signing.loads(
                preview_token, salt="treasury.fee-generation"
            ) != configuration.preview_signature(period, rows):
                raise ValidationError(
                    "La configuración o las casas cambiaron. Previsualice nuevamente antes de confirmar."
                )
            created = 0
            for row in rows:
                _, was_created = Charge.objects.get_or_create(
                    house=row["house"],
                    fee_configuration=configuration,
                    period=period,
                    defaults={
                        "concept": configuration.concept,
                        "amount": row["amount"],
                        "due_date": row["due_date"],
                    },
                )
                created += was_created
            return created, len(rows) - created

    def preview_signature(self, period, rows):
        return {
            "configuration": self.pk,
            "period": period.isoformat(),
            "concept": self.concept_id,
            "kind": self.concept.kind,
            "name": self.concept.name,
            "amount": str(self.amount),
            "frequency": self.frequency,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "generation_date": str(self.generation_date),
            "eligible": self.concept.is_surcharge_eligible,
            "houses": [row["house"].pk for row in rows],
            "due_date": rows[0]["due_date"].isoformat() if rows else None,
        }

    def __str__(self):
        return f"{self.concept} · {self.amount} · {self.get_frequency_display()}"


class Charge(TimestampedModel):
    house = models.ForeignKey(House, verbose_name="casa", on_delete=models.PROTECT)
    concept = models.ForeignKey(
        ChargeConcept, verbose_name="concepto", on_delete=models.PROTECT
    )
    fee_configuration = models.ForeignKey(
        FeeConfiguration,
        verbose_name="configuración de cuota",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    adjusts_charge = models.ForeignKey(
        "self",
        verbose_name="cargo corregido",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    amount = models.DecimalField("importe", max_digits=12, decimal_places=2)
    period = models.DateField("periodo")
    due_date = models.DateField("fecha de vencimiento")
    is_surcharge_eligible = models.BooleanField(
        "genera recargos", default=False, editable=False
    )

    class Meta:
        verbose_name = "cargo"
        verbose_name_plural = "cargos"
        ordering = ("-period", "house__number", "pk")
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(period__day=1),
                name="treasury_charge_month_start",
                violation_error_message="El periodo debe ser el primer día del mes.",
            ),
            models.CheckConstraint(
                condition=~models.Q(adjusts_charge=models.F("pk")),
                name="treasury_charge_not_self",
                violation_error_message="Un cargo no puede ajustarse a sí mismo.",
            ),
            models.UniqueConstraint(
                fields=("house", "fee_configuration", "period"),
                name="treasury_unique_generated_charge",
                violation_error_message="Ya existe un cargo para esta casa, configuración y periodo.",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.period and self.period.day != 1:
            errors["period"] = "El periodo debe ser el primer día del mes."
        concept = ChargeConcept.objects.filter(pk=self.concept_id).first()
        original = type(self).objects.filter(pk=self.adjusts_charge_id).first()
        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        visited = {self.pk} if self.pk is not None else set()
        current = original
        while current is not None:
            if current.pk in visited:
                errors["adjusts_charge"] = (
                    "La referencia al cargo original no puede formar un ciclo."
                )
                break
            visited.add(current.pk)
            current = type(self).objects.filter(pk=current.adjusts_charge_id).first()
        if (
            self.pk
            and type(self)
            .objects.filter(adjusts_charge_id=self.pk)
            .exclude(house_id=self.house_id)
            .exists()
        ):
            errors["house"] = "La casa debe coincidir con la de los ajustes vinculados."
        if concept:
            if concept.kind == ChargeConcept.Kind.ADJUSTMENT:
                if self.fee_configuration_id:
                    errors["fee_configuration"] = (
                        "Los ajustes se registran manualmente, sin configuración de cuota."
                    )
                if not original:
                    errors["adjusts_charge"] = "Un ajuste requiere el cargo original."
                elif original.house_id != self.house_id:
                    errors["adjusts_charge"] = (
                        "El cargo original debe pertenecer a la misma casa."
                    )
                else:
                    self.is_surcharge_eligible = original.is_surcharge_eligible
            else:
                if self.adjusts_charge_id:
                    errors["adjusts_charge"] = (
                        "Solo un concepto de ajuste puede corregir otro cargo."
                    )
                if self.amount is not None and self.amount < 0:
                    errors["amount"] = "Solo los ajustes admiten importes negativos."
                self.is_surcharge_eligible = concept.is_surcharge_eligible
        if (
            previous
            and previous.concept_id == self.concept_id
            and previous.adjusts_charge_id == self.adjusts_charge_id
        ):
            self.is_surcharge_eligible = previous.is_surcharge_eligible
        if self.fee_configuration_id:
            configuration = FeeConfiguration.objects.filter(
                pk=self.fee_configuration_id
            ).first()
            if configuration and configuration.concept_id != self.concept_id:
                errors["fee_configuration"] = (
                    "La configuración debe corresponder al concepto del cargo."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.house} · {self.concept} · {self.period:%Y-%m} · {self.amount}"
