from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = (("treasury", "0003_charge_due_date_optional"),)

    operations = (
        migrations.AddField(
            model_name="chargetype",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="activo"),
        ),
    )
