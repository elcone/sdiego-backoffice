from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ChargeTypeMigrationTests(TransactionTestCase):
    def test_existing_types_become_active_without_changing_historical_charges(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [("treasury", "0003_charge_due_date_optional")]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        kind = apps.get_model("treasury", "ChargeType").objects.create(name="Cuota")
        house = apps.get_model("treasury", "House").objects.create(number=6101)
        charge = apps.get_model("treasury", "Charge").objects.create(
            house_id=house.pk,
            charge_type_id=kind.pk,
            period="2026-11-01",
            amount="100.00",
        )
        original = (
            apps.get_model("treasury", "Charge").objects.values().get(pk=charge.pk)
        )

        executor = MigrationExecutor(connection)
        after = [("treasury", "0004_chargetype_is_active")]
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        types = apps.get_model("treasury", "ChargeType")
        self.assertTrue(types.objects.get(pk=kind.pk).is_active)
        self.assertEqual(types.objects.get(pk=kind.pk).name, "Cuota")
        self.assertEqual(
            apps.get_model("treasury", "Charge").objects.values().get(pk=charge.pk),
            original,
        )
