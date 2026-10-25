from django.contrib import admin

from .models import (
    BankTransaction,
    Charge,
    ChargeExclusion,
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


class ChargeInline(admin.TabularInline):
    model = Charge
    extra = 0
    fields = ['source_id', 'concept', 'effective_date', 'period', 'amount']
    readonly_fields = fields
    show_change_link = True
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = ["number"]
    search_fields = ["number"]
    inlines = [HouseContactInline, ChargeInline]


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


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "transaction_date",
        "description",
        "amount",
        "transaction_code",
        "source_file",
    ]
    list_filter = ["transaction_date", "line_type", "bank_account", "source_file"]
    search_fields = ["description", "transaction_code", "bank_receipt", "source_identifiers"]
    date_hierarchy = "transaction_date"
    readonly_fields = [
        "bank_account", "bank_receipt", "line_type", "transaction_time",
        "transaction_date",
        "description",
        "amount",
        "transaction_code",
        "source_identifiers",
        "source_values",
        "source_file",
        "source_file_sha256",
        "source_row_number",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class HistoricalChargeAdmin(admin.ModelAdmin):
    """Only view permissions apply, including for users granted change permissions."""
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Charge)
class ChargeAdmin(HistoricalChargeAdmin):
    list_display = ['source_id', 'house', 'concept', 'effective_date', 'period', 'amount', 'original_amount']
    list_filter = ['concept', 'effective_date', 'source_status']
    search_fields = ['source_id', 'house__number', 'reference', 'detail']
    list_select_related = ['house', 'concept']
    date_hierarchy = 'effective_date'


@admin.register(ChargeExclusion)
class ChargeExclusionAdmin(HistoricalChargeAdmin):
    list_display = ['source_id', 'house', 'reason', 'revision_updated']
    list_filter = ['revision_updated']
    search_fields = ['source_id', 'house__number', 'reason', 'evidence_ids']
    list_select_related = ['house']
