from django.db import models


class HouseContactQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related("house", "contact")
