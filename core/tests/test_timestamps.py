from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import ChargeConcept, Contact, FeeConfiguration, House, HouseContact, PayerAccount


class TimestampTests(TestCase):
    def test_created_and_updated_timestamps_for_all_domain_models(self):
        created_time = timezone.now()
        updated_time = created_time + timedelta(hours=1)
        with patch('django.utils.timezone.now', return_value=created_time):
            house = House.objects.create(number='001')
            contact = Contact.objects.create(name='Alex Smith')
            instances = [
                house,
                contact,
                HouseContact.objects.create(house=house, contact=contact),
                PayerAccount.objects.create(contact=contact, bank_name='Bank A', account_number='000123'),
                ChargeConcept.objects.create(code='TEST', name='Test'),
                FeeConfiguration.objects.create(
                    monthly_fee=Decimal('1.00'), late_fee=Decimal('0.00'),
                    payment_due_day=5, valid_from=date(2030, 1, 1),
                ),
            ]
        for instance in instances:
            with self.subTest(model=type(instance).__name__):
                instance.refresh_from_db()
                self.assertEqual(instance.created_at, created_time)
                self.assertEqual(instance.updated_at, created_time)
                with patch('django.utils.timezone.now', return_value=updated_time):
                    instance.save()
                instance.refresh_from_db()
                self.assertEqual(instance.created_at, created_time)
                self.assertEqual(instance.updated_at, updated_time)
