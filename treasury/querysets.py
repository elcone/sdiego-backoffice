from django.db import models


class HouseContactQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def responsible(self):
        return self.active().filter(is_responsible=True)

    def for_house(self, house):
        return self.filter(house=house)

    def with_related(self):
        return self.select_related("house", "contact")


class PayerAccountQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related("contact")
