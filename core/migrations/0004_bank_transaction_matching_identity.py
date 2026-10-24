from django.db import migrations, models


def require_empty_legacy_bank_table(apps, schema_editor):
    BankTransaction = apps.get_model("core", "BankTransaction")
    if BankTransaction.objects.using(schema_editor.connection.alias).exists():
        raise RuntimeError(
            "Existing bank records require a reviewed identity backfill before this migration. "
            "No matching attributes or external references will be invented."
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0003_initial_fee_configuration")]

    operations = [
        migrations.RunPython(require_empty_legacy_bank_table, migrations.RunPython.noop),
        migrations.RenameField("banktransaction", "source_identity", "transaction_code"),
        migrations.AlterField(
            model_name="banktransaction", name="transaction_code",
            field=models.CharField(max_length=255, unique=True,
                help_text="Original external reconciliation reference; not the matching identity."),
        ),
        migrations.AddField("banktransaction", "bank_account", models.CharField(max_length=30)),
        migrations.AddField("banktransaction", "bank_receipt", models.CharField(max_length=100)),
        migrations.AddField("banktransaction", "line_type", models.CharField(max_length=10, choices=[
            ("CREDIT", "Credit"), ("DEBIT", "Debit"), ("COMMISSION", "Commission"), ("VAT", "VAT"),
        ])),
        migrations.AddField("banktransaction", "transaction_time", models.TimeField(
            help_text="Local bank wall time; no timezone conversion.")),
        migrations.AddConstraint("banktransaction", models.UniqueConstraint(
            fields=["bank_account", "bank_receipt", "line_type"],
            name="unique_bank_account_receipt_line_type")),
        migrations.AddConstraint("banktransaction", models.CheckConstraint(
            condition=(models.Q(line_type="CREDIT", amount__gt=0)
                       | models.Q(line_type__in=["DEBIT", "COMMISSION", "VAT"], amount__lt=0)),
            name="bank_amount_matches_line_type")),
    ]
