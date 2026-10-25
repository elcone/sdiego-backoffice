from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION core_stage_c_group_member() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM core_cfpayment c JOIN core_cfreconciliationgroup g ON g.id=NEW.group_id
        WHERE c.id=NEW.cf_payment_id AND c.treatment IN ('CF_GROUP_RECOGNIZED','RECONSTRUCTED_USER_CONFIRMED')) THEN
        RAISE EXCEPTION 'CF group member requires a valid group and eligible CF payment' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
"""

REVERSE_SQL = """
CREATE OR REPLACE FUNCTION core_stage_c_group_member() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM core_cfpayment c JOIN core_cfreconciliationgroup g ON g.id=NEW.group_id
        WHERE c.id=NEW.cf_payment_id AND c.treatment IN ('CF_GROUP_RECOGNIZED','RECONSTRUCTED_USER_CONFIRMED')
        AND c.source_values->>'group_code'=g.source_id) THEN
        RAISE EXCEPTION 'CF group member contradicts approved source relationship' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [('core', '0007_historical_payments')]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
