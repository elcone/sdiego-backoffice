from django import forms

from .models import Contact, House, HouseContact, PayerAccount


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
