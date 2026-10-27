from typing import ClassVar

from django.db import models

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
