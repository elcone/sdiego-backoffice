from viewflow import Icon
from viewflow.urls import Application, ModelViewset, Site
from viewflow.urls.model import DeleteViewMixin, DetailViewMixin
from viewflow.views import DetailModelView

from .forms import ContactForm, HouseContactForm, HouseForm, PayerAccountForm
from .models import Contact, House, HouseContact, PayerAccount


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


treasury = Application(
    app_name="treasury",
    title="Tesorería",
    icon=Icon("account_balance"),
    viewsets=[
        HouseViewset(),
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
