from functools import partial

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import path
from django.views.generic import FormView
from viewflow import Icon
from viewflow.urls import (
    Application,
    CreateViewMixin,
    ModelViewset,
    ReadonlyModelViewset,
    Site,
    current_viewset_reverse,
    route,
)
from viewflow.urls.model import DeleteViewMixin, DetailViewMixin
from viewflow.views import Action, CreateModelView, DetailModelView, UpdateModelView

from .forms import (
    BankTransactionForm,
    ChargeConceptForm,
    ChargeForm,
    ContactForm,
    FeeConfigurationForm,
    FeeGenerationForm,
    HouseContactForm,
    HouseForm,
    PayerAccountForm,
    PaymentApplicationForm,
    PaymentForm,
)
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


class CompleteDetailModelView(DetailModelView):
    def get_object_data(self):
        primary_key = self.object._meta.pk
        yield primary_key, primary_key.verbose_name.capitalize(), self.object.pk
        for field, label, value in super().get_object_data():
            if isinstance(value, bool):
                value = "Sí" if value else "No"
            yield field, label, value


class FinancialCreateModelView(CreateModelView):
    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
            return self.form_invalid(form)


class FinancialUpdateModelView(UpdateModelView):
    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
            return self.form_invalid(form)


class HouseDetailModelView(CompleteDetailModelView):
    template_name = "treasury/house_detail.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            balances=self.object.financial_balances(), **kwargs
        )


class CatalogModelViewset(DetailViewMixin, DeleteViewMixin, ModelViewset):
    detail_view_class = CompleteDetailModelView

    def get_list_bulk_actions(self, request, *actions):
        return actions

    bulk_delete_path = None

    def get_update_page_actions(self, request, obj, *actions):
        result = super().get_update_page_actions(request, obj, *actions)
        for action in result:
            if action.name == "Delete":
                action.name = "Eliminar"
        return result

    def get_detail_page_actions(self, request, obj, *actions):
        result = super().get_detail_page_actions(request, obj, *actions)
        for action in result:
            if action.name == "Delete":
                action.name = "Eliminar"
        return result


class HouseViewset(CatalogModelViewset):
    model = House
    title = "Casas"
    icon = "home"
    form_class = HouseForm
    list_columns = ("number",)
    list_search_fields = ("number",)
    detail_view_class = HouseDetailModelView


class ContactViewset(CatalogModelViewset):
    model = Contact
    title = "Contactos"
    icon = "person"
    form_class = ContactForm
    list_columns = ("name", "email", "phone")
    list_search_fields = ("name", "email", "phone")


class PayerAccountViewset(CatalogModelViewset):
    model = PayerAccount
    title = "Cuentas pagadoras"
    icon = "credit_card"
    form_class = PayerAccountForm
    queryset = PayerAccount.objects.with_related()
    list_columns = ("bank", "account_number", "contact")
    list_search_fields = ("bank", "account_number", "contact__name")
    list_filter_fields = ("bank", "contact")


class HouseContactViewset(CatalogModelViewset):
    model = HouseContact
    title = "Contactos de casas"
    icon = "call"
    form_class = HouseContactForm
    queryset = HouseContact.objects.with_related()
    list_columns = ("house", "contact", "is_responsible", "is_active")
    list_search_fields = ("house__number", "contact__name")
    list_filter_fields = ("house", "contact", "is_active", "is_responsible")


class ChargeConceptViewset(CatalogModelViewset):
    model = ChargeConcept
    title = "Conceptos de cargos"
    icon = "sell"
    form_class = ChargeConceptForm
    list_columns = ("name", "kind", "is_surcharge_eligible")
    list_search_fields = ("name",)
    list_filter_fields = ("kind", "is_surcharge_eligible")


class FeeGenerationView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    permission_required = ("treasury.add_charge", "treasury.view_feeconfiguration")
    form_class = FeeGenerationForm
    template_name = "treasury/fee_generation.html"
    viewset = None

    def form_valid(self, form):
        configuration = form.cleaned_data["configuration"]
        period = form.cleaned_data["period"]
        try:
            if "_confirm" in self.request.POST:
                if not form.cleaned_data["preview_token"]:
                    raise ValidationError("Previsualice los cargos antes de confirmar.")
                created, skipped = configuration.generate(
                    period, preview_token=form.cleaned_data["preview_token"]
                )
                messages.success(
                    self.request,
                    f"Cargos creados: {created}. Cargos omitidos: {skipped}.",
                )
                return HttpResponseRedirect(self.viewset.reverse("list"))
            rows = configuration.preview(period)
            data = form.data.copy()
            data["preview_token"] = signing.dumps(
                configuration.preview_signature(period, rows),
                salt="treasury.fee-generation",
            )
            skipped = sum(row["exists"] for row in rows)
            return self.render_to_response(
                self.get_context_data(
                    form=self.form_class(data=data),
                    rows=rows,
                    configuration=configuration,
                    period=period,
                    summary={
                        "total": len(rows),
                        "pending": len(rows) - skipped,
                        "skipped": skipped,
                    },
                )
            )
        except signing.BadSignature:
            form.add_error(
                None, "La confirmación no es válida. Previsualice nuevamente."
            )
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        return self.form_invalid(form)


class FeeConfigurationViewset(CatalogModelViewset):
    model = FeeConfiguration
    title = "Configuraciones de cuotas"
    icon = "settings"
    form_class = FeeConfigurationForm
    queryset = FeeConfiguration.objects.select_related("concept")
    list_columns = (
        "concept",
        "amount",
        "frequency",
        "start_date",
        "end_date",
        "generation_date",
        "due_date",
    )
    list_search_fields = ("concept__name",)
    list_filter_fields = ("concept", "frequency")

    @property
    def generate_path(self):
        return path(
            "generate/", FeeGenerationView.as_view(viewset=self), name="generate"
        )

    def get_list_page_actions(self, request, *actions):
        if request.user.has_perms(FeeGenerationView.permission_required):
            actions = (
                Action(
                    name="Generar cuotas",
                    url=self.reverse("generate"),
                    icon=Icon("playlist_add"),
                ),
                *actions,
            )
        return super().get_list_page_actions(request, *actions)


class ChargeViewset(CatalogModelViewset):
    model = Charge
    title = "Cargos"
    icon = "receipt_long"
    form_class = ChargeForm
    create_view_class = FinancialCreateModelView
    update_view_class = FinancialUpdateModelView
    queryset = Charge.objects.select_related(
        "house", "concept", "fee_configuration", "adjusts_charge"
    )
    list_columns = (
        "house",
        "concept",
        "amount",
        "period",
        "due_date",
        "is_surcharge_eligible",
    )
    list_search_fields = ("house__number", "concept__name")
    list_filter_fields = ("house", "concept", "period", "is_surcharge_eligible")

    def has_delete_permission(self, user, obj=None):
        return super().has_delete_permission(user, obj) and (
            obj is None or not PaymentApplication.objects.filter(charge=obj).exists()
        )


class BankTransactionViewset(CreateViewMixin, ReadonlyModelViewset):
    model = BankTransaction
    title = "Movimientos bancarios"
    icon = "account_balance"
    create_form_class = BankTransactionForm
    create_view_class = FinancialCreateModelView
    detail_view_class = CompleteDetailModelView
    list_columns = (
        "transaction_date",
        "bank_account",
        "bank_receipt",
        "line_type",
        "amount",
        "description",
    )
    list_search_fields = ("bank_account", "bank_receipt", "description")
    list_filter_fields = ("line_type", "bank_account", "transaction_date")


class PaymentApplicationDetailModelView(CompleteDetailModelView):
    def get_queryset(self):
        return self.viewset.get_queryset(self.request)


class PaymentApplicationViewset(CatalogModelViewset):
    model = PaymentApplication
    title = "Aplicaciones del pago"
    detail_view_class = PaymentApplicationDetailModelView
    create_view_class = FinancialCreateModelView
    update_view_class = FinancialUpdateModelView
    list_columns = ("charge", "amount")

    def get_payment(self, request):
        payment = get_object_or_404(
            Payment.objects.with_related(),
            pk=request.resolver_match.kwargs["payment_pk"],
        )
        if not self.parent.has_view_permission(request.user, payment):
            raise PermissionDenied
        return payment

    def get_queryset(self, request):
        return PaymentApplication.objects.with_related().filter(
            payment=self.get_payment(request)
        )

    def get_create_form_class(self, request):
        return partial(PaymentApplicationForm, payment=self.get_payment(request))

    get_update_form_class = get_create_form_class

    def get_object_url(self, request, obj):
        if self.has_view_permission(request.user, obj):
            return current_viewset_reverse(
                request, self, "detail", kwargs={"pk": obj.pk}
            )

    def get_success_url(self, request, obj=None):
        return self.parent.reverse(
            "detail", args=[request.resolver_match.kwargs["payment_pk"]]
        )

    def get_list_page_actions(self, request, *actions):
        if self.has_add_permission(request.user):
            return (
                Action(
                    name="Agregar aplicación",
                    url=current_viewset_reverse(request, self, "add"),
                    icon=Icon("add_circle"),
                ),
                *actions,
            )
        return actions

    def get_update_page_actions(self, request, obj, *actions):
        if self.has_delete_permission(request.user, obj):
            return (
                Action(
                    name="Eliminar",
                    url=current_viewset_reverse(
                        request, self, "delete", kwargs={"pk": obj.pk}
                    ),
                    icon=Icon("delete"),
                ),
                *actions,
            )
        return actions

    get_detail_page_actions = get_update_page_actions


class PaymentDetailModelView(CompleteDetailModelView):
    template_name = "treasury/payment_detail.html"

    def get_context_data(self, **kwargs):
        applications = self.object.paymentapplication_set.with_related()
        return super().get_context_data(applications=applications, **kwargs)


class PaymentViewset(CatalogModelViewset):
    model = Payment
    title = "Pagos"
    icon = "payments"
    form_class = PaymentForm
    create_view_class = FinancialCreateModelView
    update_view_class = FinancialUpdateModelView
    detail_view_class = PaymentDetailModelView
    queryset = Payment.objects.with_related()
    list_columns = ("bank_transaction", "house", "payer_account", "amount")
    list_search_fields = (
        "house__number",
        "bank_transaction__bank_receipt",
        "payer_account__contact__name",
    )
    list_filter_fields = ("house", "payer_account")
    applications_path = route(
        "<int:payment_pk>/applications/", PaymentApplicationViewset()
    )

    def has_delete_permission(self, user, obj=None):
        return super().has_delete_permission(user, obj) and (
            obj is None or not PaymentApplication.objects.filter(payment=obj).exists()
        )


treasury = Application(
    app_name="treasury",
    title="Tesorería",
    icon=Icon("account_balance"),
    viewsets=[
        ChargeViewset(),
        HouseViewset(),
        ChargeConceptViewset(),
        FeeConfigurationViewset(),
        ContactViewset(),
        HouseContactViewset(),
        PayerAccountViewset(),
        BankTransactionViewset(),
        PaymentViewset(),
    ],
)
site = Site(
    title="Privada San Diego",
    primary_color="#0D47A1",
    secondary_color="#FFA726",
    viewsets=[treasury],
)
