# Increment 03 — Stage B historical charges

The command reads the immutable `sdiego-increment03-history-v1.0` package after
Stage A has imported its houses and charge concepts. No warehouse transformation,
fee configuration lookup, bank import or historical recalculation is performed.

```sh
python manage.py migrate
python manage.py import_historical_charges /path/to/import-layouts-increment03-history-v1.0 --preflight
python manage.py import_historical_charges /path/to/import-layouts-increment03-history-v1.0
```

`--dry-run` aliases `--preflight`. Preflight validates source and stored data and
reports would-import/unchanged counts without inserting records. The command checks
all package output hashes through the unchanged Stage A adapter, then independently
checks exact charge/exclusion schemas (including field definitions and relationships),
CSV headers, required values, identifiers, revision evidence, dates, money, counts,
foreign keys and financial totals. Files are parsed and all records validated before
persistence. Every stored conflict is checked before insertion. The whole import is
atomic, including charge and exclusion records. Advisory transaction locking serializes
Stage B runs; unique constraints and foreign keys protect against concurrent writers.

Migration `0006_historical_charges` creates:

- `Charge`: a historical obligation linked to protected House and ChargeConcept
  records. The exported `charge_id` is stored verbatim as unique nonempty `source_id`.
  Nominal, discount, effective and original amounts remain distinct decimal values.
  Effective dates and `YYYY-MM` periods are preserved independently. Original strings,
  original/approved JSON evidence, external references, filename, hash, CSV line,
  revision timestamp/version and Django import timestamps are retained.
- `ChargeExclusion`: audit-only persistence of the three approved exclusions. It has
  no charge amount and never appears in Charge queries. It retains original revision
  strings and evidence IDs without repairing or reinterpreting legacy nested data.

The database prohibits excluded IDs in Charge and accepts only the three approved IDs
in ChargeExclusion. Charge constraints enforce valid monthly periods, original/approved
status, nonnegative nominal/original/discount amounts, discount <= nominal, and effective
amount = nominal - discount. Zero charges and future historical periods are valid.
There is no uniqueness assumption by house/period: external source IDs distinguish
records. No opening balance or adjustment identifier is invented. Negative adjustments
are outside this historical Stage B model and dataset; future work must use the approved
adjustment contract rather than weakening these import rules.

Identical reimports preserve primary keys, values, timestamps and original provenance.
Any changed original source string or persisted business value is a conflict requiring
review; nothing is silently overwritten. Moving the unchanged package does not alter
record identity. Original revision timestamp strings remain in source_values, alongside
the timezone-aware revision_updated field.

Charge and exclusion Admin views permit inspection, searching/filtering and links to
houses. A read-only charge inline appears on House. Add/change/delete and bulk delete
are unavailable even to superusers. Users still require view/change permissions to view
financial evidence; no permission grants are created by this migration.

Approved expected results:

- CUOTA: 948; RECARGO: 150; MULTA: 8; total: 1,106 Charge records.
- Total: 880,500.00 MXN; effective through September 25, 2026: 655,500.00 MXN.
- Three ChargeExclusion audit records, with no financial effect.

Run ordinary PostgreSQL tests without private files:

```sh
python manage.py test --settings=core.tests.settings
```

Enable both catalog/charge and existing bank private integrations in Django's isolated
test database. This does not reimport bank transactions into development:

```sh
SDIEGO_HISTORY_PACKAGE=/path/to/import-layouts-increment03-history-v1.0 \
MALLARD_BANK_CSV=/path/to/import-layouts-increment02-stable-identity/bank_transactions.csv \
python manage.py test --settings=core.tests.settings
```

Synthetic tests cover complete-package validation, immutable source evidence, model
constraints, protected references, rollback after inserts, conflicts, idempotency,
preflight, excluded records, totals, unchanged catalogs/fees, and Admin permissions.
The optional approved-source test validates all historical charges and repeated imports.
Stage C is not implemented here.
