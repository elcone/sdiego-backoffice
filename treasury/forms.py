from django import forms

from .models import (
    Charge,
    ChargeConcept,
    Contact,
    FeeConfiguration,
    House,
    HouseContact,
    PayerAccount,
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
