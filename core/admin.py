from django import forms
from django.contrib import admin
from django.contrib.admin.utils import quote, unquote
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .balances import ZERO, account_statement, house_balances

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
    change_form_template = 'admin/core/house/change_form.html'

    def has_financial_permission(self, request, obj=None):
        return self.has_view_or_change_permission(request, obj) and all(
            request.user.has_perm(f'core.view_{model}') or request.user.has_perm(f'core.change_{model}')
            for model in ['charge', 'payment']
        )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return house_balances(queryset) if self.has_financial_permission(request) else queryset

    def get_list_display(self, request):
        if self.has_financial_permission(request):
            return ['number', 'current_balance', 'account_statement_link']
        return super().get_list_display(request)

    @admin.display(description='Current balance (MXN)')
    def current_balance(self, obj):
        return obj.balance

    @admin.display(description='Account statement')
    def account_statement_link(self, obj):
        return format_html('<a href="{}">Account statement</a>', self.statement_url(obj))

    def statement_url(self, obj):
        return reverse(f'{self.admin_site.name}:core_house_account_statement', args=[quote(obj.pk)])

    def render_change_form(self, request, context, add=False, change=False, form_url='', obj=None):
        if obj is not None and self.has_financial_permission(request, obj):
            context['account_statement_url'] = self.statement_url(obj)
        return super().render_change_form(request, context, add, change, form_url, obj)

    def get_urls(self):
        return [path('<path:object_id>/account-statement/', self.admin_site.admin_view(self.account_statement_view), name='core_house_account_statement')] + super().get_urls()

    def account_statement_view(self, request, object_id):
        if not self.has_financial_permission(request):
            raise PermissionDenied
        if request.method != 'GET':
            return HttpResponseNotAllowed(['GET'])
        house = self.get_object(request, unquote(object_id))
        if house is None:
            raise Http404
        if not self.has_financial_permission(request, house):
            raise PermissionDenied
        today = timezone.localdate()
        form = StatementCutoffForm(request.GET if 'as_of' in request.GET else None, initial={'as_of': today})
        valid = not form.is_bound or form.is_valid()
        cutoff = (form.cleaned_data['as_of'] or today) if form.is_bound and valid else today
        rows = account_statement(house, as_of=cutoff) if valid else []
        charges = sum((row['charge_amount'] for row in rows), ZERO)
        payments = sum((row['payment_amount'] for row in rows), ZERO)
        context = {
            **self.admin_site.each_context(request), 'opts': self.model._meta,
            'title': f'House {house.number} — Account statement', 'house': house,
            'house_url': reverse(f'{self.admin_site.name}:core_house_change', args=[quote(house.pk)]),
            'house_list_url': reverse(f'{self.admin_site.name}:core_house_changelist'),
            'form': form, 'valid_cutoff': valid, 'as_of': cutoff, 'rows': rows,
            'total_charges': charges, 'total_payments': payments, 'balance': charges - payments,
        }
        return TemplateResponse(request, 'admin/core/house/account_statement.html', context, status=200 if valid else 400)


class StatementCutoffForm(forms.Form):
    as_of = forms.DateField(required=False, label='As of', widget=forms.DateInput(attrs={'type': 'date'}))


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
