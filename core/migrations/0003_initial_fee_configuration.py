from datetime import date
from decimal import Decimal

from django.db import migrations


INITIAL_PERIODS = [
    {
        "valid_from": date(2026, 1, 1),
        "valid_until": date(2026, 1, 31),
        "monthly_fee": Decimal("0.00"),
        "late_fee": Decimal("150.00"),
        "payment_due_day": 5,
    },
    {
        "valid_from": date(2026, 2, 1),
        "valid_until": None,
        "monthly_fee": Decimal("1000.00"),
        "late_fee": Decimal("150.00"),
        "payment_due_day": 5,
    },
]


def seed_initial_fee_configuration(apps, schema_editor):
    FeeConfiguration = apps.get_model("core", "FeeConfiguration")
    configurations = FeeConfiguration.objects.using(schema_editor.connection.alias)
    missing = []
    existing = list(configurations.all())
    for values in INITIAL_PERIODS:
        matching = [config for config in existing if all(
            getattr(config, field) == value for field, value in values.items()
        )]
        if matching:
            continue
        for config in existing:
            if (config.valid_until is None or config.valid_until >= values["valid_from"]) and (
                values["valid_until"] is None or config.valid_from <= values["valid_until"]
            ):
                raise RuntimeError(
                    "Initial fee configuration conflicts with existing data. "
                    "Manual review is required; no existing configuration was changed."
                )
        missing.append(values)
    for values in missing:
        configurations.create(**values)


class Migration(migrations.Migration):
    dependencies = [("core", "0002_banktransaction")]

    operations = [
        migrations.RunPython(seed_initial_fee_configuration, migrations.RunPython.noop),
    ]
