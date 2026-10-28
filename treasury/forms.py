from django import forms

from .models import (
    BankTransaction,
    Charge,
    ChargeConcept,
    Contact,
    FeeConfiguration,
    House,
    HouseContact,
    PayerAccount,
    Payment,
    PaymentApplication,
)


class HouseForm(forms.ModelForm):
    class Meta:
        model = House
        fields = ("number",)


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ("name", "email", "phone")


class HouseContactForm(forms.ModelForm):
    class Meta:
        model = HouseContact
        fields = ("house", "contact", "is_responsible", "is_active")


class PayerAccountForm(forms.ModelForm):
    class Meta:
        model = PayerAccount
        fields = ("contact", "bank", "account_number")


class ChargeConceptForm(forms.ModelForm):
    class Meta:
        model = ChargeConcept
        fields = ("name", "kind", "is_surcharge_eligible")


class FeeConfigurationForm(forms.ModelForm):
    class Meta:
        model = FeeConfiguration
        fields = (
            "concept",
            "amount",
            "frequency",
            "start_date",
            "end_date",
            "generation_date",
            "due_date",
        )


class ChargeForm(forms.ModelForm):
    class Meta:
        model = Charge
        fields = ("house", "concept", "adjusts_charge", "amount", "period", "due_date")


class FeeGenerationForm(forms.Form):
    configuration = forms.ModelChoiceField(
        label="Configuración de cuota",
        queryset=FeeConfiguration.objects.exclude(
            concept__kind__in=(
                ChargeConcept.Kind.SURCHARGE,
                ChargeConcept.Kind.ADJUSTMENT,
            )
        ).select_related("concept"),
    )
    period = forms.DateField(
        label="Periodo", help_text="Primer día del mes que se generará."
    )
    preview_token = forms.CharField(
        label="Confirmación", required=False, widget=forms.HiddenInput
    )


class BankTransactionForm(forms.ModelForm):
    class Meta:
        model = BankTransaction
        fields = (
            "bank_account",
            "bank_receipt",
            "line_type",
            "transaction_date",
            "description",
            "amount",
        )


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ("bank_transaction", "house", "payer_account")


class PaymentApplicationForm(forms.ModelForm):
    class Meta:
        model = PaymentApplication
        fields = ("charge", "amount")

    def __init__(self, *args, payment, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.payment = payment
        self.fields["charge"].queryset = Charge.objects.filter(
            house=payment.house, amount__gt=0
        ).select_related("house", "concept")
