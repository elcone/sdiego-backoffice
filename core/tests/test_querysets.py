from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from core.admin import ContactHouseInline, HouseContactInline
from core.models import Contact, House, HouseContact


class HouseContactQuerySetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        house = House.objects.create(number='001')
        for name in ['Alex Smith', 'Sam Smith', 'Pat Smith']:
            contact = Contact.objects.create(name=name)
            HouseContact.objects.create(house=house, contact=contact)

    def test_related_strings_use_one_query(self):
        with self.assertNumQueries(1):
            labels = [str(link) for link in HouseContact.objects.with_related()]
        self.assertEqual(labels, ['001 — Alex Smith', '001 — Pat Smith', '001 — Sam Smith'])

    def test_admin_and_inline_querysets_eagerly_load_related_objects(self):
        request = RequestFactory().get('/admin/')
        request.user = get_user_model()(is_active=True, is_staff=True, is_superuser=True)
        admins = [
            admin.site._registry[HouseContact],
            HouseContactInline(House, admin.site),
            ContactHouseInline(Contact, admin.site),
        ]
        for model_admin in admins:
            with self.subTest(admin=type(model_admin).__name__), self.assertNumQueries(1):
                self.assertEqual(len([str(link) for link in model_admin.get_queryset(request)]), 3)
