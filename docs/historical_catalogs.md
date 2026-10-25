# Increment 03 — Stage A historical catalogs

Supported immutable package: `sdiego-increment03-history-v1.0`, cutoff September 25,
2026. Only houses, directory-scoped contacts, house-contact relationships and the
three historical charge concepts are imported. Bank transactions, charges, fee
configuration, CF records and payer accounts are not written by this command.

```sh
python manage.py migrate
python manage.py import_historical_catalogs /path/to/import-layouts-increment03-history-v1.0 --preflight
python manage.py import_historical_catalogs /path/to/import-layouts-increment03-history-v1.0
```

`--dry-run` is an alias for `--preflight`. Preflight checks stored conflicts without
writing. Dependency order is houses -> contacts -> relationships -> concepts;
the entire package is validated before persistence and committed in one transaction.
A bad row, source hash, schema, count, relationship or stored conflict rejects the
whole package. PostgreSQL must be available; no fallback database is created.

The command checks all package output hashes listed in the manifest, the contract
version/cutoff, schema column order/types/nullability/keys, CSV structure, required
values, expected Stage A counts (79/78/78/3), model field validation, directory
provenance and relationship coverage. The referenced Increment 02 houses schema
must accompany the package in its original sibling directory; its source hash is
verified. Live warehouse/SQLite databases are not consulted. Those recorded source
hashes are provenance, not a dependency on mutable upstream databases.

Migration `0005_historical_reference_provenance` extends the existing four reference
models with nullable unique `source_id`, original `source_values`, filename, file
SHA-256 and CSV line number. No new reference model or mapping table is introduced.
Historical source fields are not editable in model forms. Source IDs are:

- House: exported `number`.
- Contact: exported `contact_id`, e.g. `DIRECTORY-OWNER-6101`.
- HouseContact: compact JSON encoding of the exported composite key
  `[house_number, contact_id]`; both original values remain in `source_values`.
- ChargeConcept: exported `code` (`CUOTA`, `RECARGO`, `MULTA`).

Contacts are matched only by source ID, never name, phone, email, RFC or payer
observations. Directory identities remain scoped to their source rows; a manually
created contact with the same name is not merged. Existing unprovenanced houses or
concepts sharing a natural key require manual review rather than automatic adoption.
Reimports require identical original source values and persisted business fields;
conflicts are rejected. Identical reimports retain primary keys, timestamps and
original provenance, even if the package directory changes. PostgreSQL advisory
transaction locking serializes this command's runs; database unique constraints
protect source and natural identities against other writers.

`HouseContact.is_responsible` now allows NULL, while preserving the existing False
default for ordinary records. The import explicitly stores NULL for all 78 unknown
roles. The directory OWNER role and DIRECTORY_RECORDED status remain source evidence;
they do not establish responsibility. House 6185 has no contact. The 120 account
observations remain unresolved in the historical package; no required-contact
PayerAccount is created. No adjustment code is invented.

Run ordinary PostgreSQL tests without private inputs:

```sh
python manage.py test --settings=core.tests.settings
```

Enable the Stage A private integration and existing bank regressions in the isolated
Django test database (this does not reimport bank rows into development):

```sh
SDIEGO_HISTORY_PACKAGE=/path/to/import-layouts-increment03-history-v1.0 \
MALLARD_BANK_CSV=/path/to/import-layouts-increment02-stable-identity/bank_transactions.csv \
python manage.py test --settings=core.tests.settings
```

Ordinary tests build synthetic complete-size layouts and manifest/schema fixtures.
They cover preflight, whole-package rejection, late database-conflict rollback,
source identifiers/provenance/timestamps, idempotency, non-merging, referential
integrity, unique constraints, unknown responsibility and untouched financial data.
The optional integration verifies the actual approved package and repeated imports.
Stage B is a separate authorization; this command neither imports nor creates charges.
