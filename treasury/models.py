from django.db import models


class House(models.Model):
    number = models.PositiveSmallIntegerField("número", unique=True)

    class Meta:
        verbose_name = "casa"
        verbose_name_plural = "casas"

    def __str__(self):
        return str(self.number)


class ChargeType(models.Model):
    name = models.CharField("nombre", max_length=200)

    class Meta:
        verbose_name = "tipo de cargo"
        verbose_name_plural = "tipos de cargo"

    def __str__(self):
        return self.name


class ExpenseType(models.Model):
    name = models.CharField("nombre", max_length=200, unique=True)

    class Meta:
        verbose_name = "tipo de gasto"
        verbose_name_plural = "tipos de gasto"

    def __str__(self):
        return self.name


class BankTransaction(models.Model):
    class LineType(models.TextChoices):
        DEBIT = "DEBIT", "Cargo"
        CREDIT = "CREDIT", "Abono"
        COMMISSION = "COMMISSION", "Comisión"
        COMMISSION_VAT = "COMMISSION_VAT", "IVA de comisión"

    bank_account = models.CharField("cuenta bancaria", max_length=100)
    bank_receipt = models.CharField("comprobante bancario", max_length=255)
    line_type = models.CharField(
        "tipo de línea", max_length=50, choices=LineType.choices
    )
    transaction_timestamp = models.DateTimeField("fecha y hora de transacción")
    amount = models.DecimalField("importe", max_digits=18, decimal_places=2)
    balance = models.DecimalField("saldo", max_digits=18, decimal_places=2)
    concept = models.TextField("concepto")
    house = models.ForeignKey(
        House, verbose_name="casa", on_delete=models.PROTECT, null=True, blank=True
    )
    expense_type = models.ForeignKey(
        ExpenseType,
        verbose_name="tipo de gasto",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    description = models.TextField("descripción", blank=True)

    class Meta:
        verbose_name = "movimiento bancario"
        verbose_name_plural = "movimientos bancarios"
        constraints = [
            models.UniqueConstraint(
                fields=["bank_account", "bank_receipt", "line_type"],
                name="treasury_unique_bank_identity",
            )
        ]


class Charge(models.Model):
    house = models.ForeignKey(House, verbose_name="casa", on_delete=models.PROTECT)
    charge_type = models.ForeignKey(
        ChargeType, verbose_name="tipo de cargo", on_delete=models.PROTECT
    )
    period = models.DateField("periodo")
    due_date = models.DateField("fecha de vencimiento")
    amount = models.DecimalField("importe", max_digits=18, decimal_places=2)
    description = models.TextField("descripción", blank=True)

    class Meta:
        verbose_name = "cargo"
        verbose_name_plural = "cargos"
