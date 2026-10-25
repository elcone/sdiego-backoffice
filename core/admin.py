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
    Payment,
    CFPayment,
    CFReconciliationGroup,
    CFGroupPayment,
    CFGroupDeposit,
    ReceiptEvidence,
    PaymentEvidenceLink,
    HistoricalHouseCase,
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


@admin.register(Payment)
class PaymentAdmin(HistoricalChargeAdmin):
    list_display = ['source_id', 'house', 'origin', 'effective_date', 'amount', 'effective_amount', 'assignment_approved']
    list_filter = ['origin', 'assignment_approved', 'source_status', 'validation', 'effective_date']
    search_fields = ['source_id', 'house__number', 'bank_transaction__bank_receipt']
    list_select_related = ['house', 'bank_transaction']
    date_hierarchy = 'effective_date'


@admin.register(CFPayment)
class CFPaymentAdmin(HistoricalChargeAdmin):
    list_display = ['source_id', 'official_folio', 'assigned_house', 'original_amount', 'recognized_amount', 'additional_credit_amount', 'treatment', 'settlement']
    list_filter = ['treatment', 'recognition', 'settlement']
    search_fields = ['source_id', 'official_folio', 'assigned_house__number', 'payment__source_id']
    list_select_related = ['assigned_house', 'payment', 'bank_transaction']


class CFGroupPaymentInline(admin.TabularInline):
    model = CFGroupPayment
    fields = ['cf_payment']
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class CFGroupDepositInline(CFGroupPaymentInline):
    model = CFGroupDeposit
    fields = ['bank_transaction']
    readonly_fields = fields


@admin.register(CFReconciliationGroup)
class CFReconciliationGroupAdmin(HistoricalChargeAdmin):
    list_display = ['source_id', 'payment_count', 'deposit_count', 'payment_total', 'bank_total', 'difference', 'status']
    search_fields = ['source_id']
    list_filter = ['status', 'evidence_level']
    inlines = [CFGroupPaymentInline, CFGroupDepositInline]


@admin.register(CFGroupPayment)
class CFGroupPaymentAdmin(HistoricalChargeAdmin):
    list_display = ['group', 'cf_payment']
    list_select_related = ['group', 'cf_payment']
    search_fields = ['group__source_id', 'cf_payment__source_id']


@admin.register(CFGroupDeposit)
class CFGroupDepositAdmin(HistoricalChargeAdmin):
    list_display = ['group', 'bank_transaction']
    list_select_related = ['group', 'bank_transaction']
    search_fields = ['group__source_id', 'bank_transaction__bank_receipt']


@admin.register(ReceiptEvidence)
class ReceiptEvidenceAdmin(HistoricalChargeAdmin):
    list_display = ['source_id', 'document_name', 'filename_house', 'result', 'reviewed_payment']
    list_filter = ['result']
    search_fields = ['source_id', 'document_name', 'filename_house__number', 'sha256']
    list_select_related = ['filename_house', 'reviewed_payment']


@admin.register(PaymentEvidenceLink)
class PaymentEvidenceLinkAdmin(HistoricalChargeAdmin):
    list_display = ['payment', 'evidence', 'link_kind']
    list_filter = ['link_kind']
    list_select_related = ['payment', 'evidence']
    search_fields = ['payment__source_id', 'evidence__source_id']


@admin.register(HistoricalHouseCase)
class HistoricalHouseCaseAdmin(HistoricalChargeAdmin):
    list_display = ['house', 'status', 'version', 'source_updated']
    list_filter = ['status']
    list_select_related = ['house']
    search_fields = ['house__number', 'note']
