from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import path
from django.views.generic import FormView
from viewflow import Icon
from viewflow.urls import Application, ModelViewset, Site
from viewflow.urls.model import DeleteViewMixin, DetailViewMixin
from viewflow.views import Action, DetailModelView

from .forms import (
    ChargeConceptForm,
    ChargeForm,
    ContactForm,
    FeeConfigurationForm,
    FeeGenerationForm,
    HouseContactForm,
    HouseForm,
    PayerAccountForm,
)
from .models import (
    Charge,
    ChargeConcept,
    Contact,
    FeeConfiguration,
    House,
    HouseContact,
    PayerAccount,
)


class CompleteDetailModelView(DetailModelView):
    def get_object_data(self):
        primary_key = self.object._meta.pk
        yield primary_key, primary_key.verbose_name.capitalize(), self.object.pk
        for field, label, value in super().get_object_data():
            if isinstance(value, bool):
                value = "Sí" if value else "No"
            yield field, label, value


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
    ],
)
site = Site(
    title="Privada San Diego",
    primary_color="#0D47A1",
    secondary_color="#FFA726",
    viewsets=[treasury],
)
