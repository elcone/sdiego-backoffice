from django.contrib import admin

from .models import (
    ChargeConcept,
    Contact,
    FeeConfiguration,
    House,
    HouseContact,
    PayerAccount,
)


class HouseContactInline(admin.TabularInline):
    def get_queryset(self, request):
        return super().get_queryset(request).with_related()

    model = HouseContact
    extra = 0
    autocomplete_fields = ["contact"]


class ContactHouseInline(admin.TabularInline):
    def get_queryset(self, request):
        return super().get_queryset(request).with_related()

    model = HouseContact
    extra = 0
    autocomplete_fields = ["house"]


class PayerAccountInline(admin.TabularInline):
    model = PayerAccount
    extra = 0


@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = ["number"]
    search_fields = ["number"]
    inlines = [HouseContactInline]


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ["name", "rfc", "phone", "email"]
    search_fields = ["name", "rfc", "phone", "email"]
    inlines = [ContactHouseInline, PayerAccountInline]


@admin.register(HouseContact)
class HouseContactAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).with_related()

    list_display = ["house", "contact", "is_responsible"]
    list_filter = ["is_responsible"]
    search_fields = ["house__number", "contact__name"]
    autocomplete_fields = ["house", "contact"]


@admin.register(PayerAccount)
class PayerAccountAdmin(admin.ModelAdmin):
    list_display = ["contact", "bank_name", "account_number"]
    list_filter = ["bank_name"]
    search_fields = ["contact__name", "bank_name", "account_number"]
    autocomplete_fields = ["contact"]
    list_select_related = ["contact"]


@admin.register(ChargeConcept)
class ChargeConceptAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "is_adjustment", "is_active"]
    list_filter = ["is_adjustment", "is_active"]
    search_fields = ["code", "name", "description"]


@admin.register(FeeConfiguration)
class FeeConfigurationAdmin(admin.ModelAdmin):
    list_display = [
        "valid_from",
        "valid_until",
        "monthly_fee",
        "late_fee",
        "payment_due_day",
    ]
    list_filter = ["valid_from", "valid_until"]
    date_hierarchy = "valid_from"
