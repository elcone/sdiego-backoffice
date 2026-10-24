# Mallard bank transaction adapter

Supported contract: `sdiego-history-v1.1`, the finalized 25-column UTF-8, comma-separated
`bank_transactions.csv` under `import-layouts-increment02-stable-identity`. Older
23-column exports are rejected. No Mallard files or reconciliation decisions are modified.

The matching identity is `(bank_account, bank_receipt, line_type)`, protected by a database
unique constraint. The verified historical account is `0392369300201`, preserved as text.
Line types are CREDIT, DEBIT, COMMISSION and VAT; the latter three all have DEBIT direction.
The adapter uses the exported line type, not description keyword inference.

The Django primary key remains the permanent internal identity. `transaction_code` retains
the original legacy fingerprint as a unique external reconciliation reference, never as the
matching key. A changed fingerprint or any source value under an existing matching identity
requires manual review; nothing is overwritten or automatically created under another key.
The matching tuple is validated for this finite dataset, not guaranteed for arbitrary future
bank exports. Future collisions require review rather than invented sequence numbers.

Dates use YYYY-MM-DD; times use HH:MM:SS and remain local bank wall times without timezone
conversion. Amounts must have exactly two decimal places. Source `amount` is positive magnitude;
credits map to positive stored amounts, and debits/commissions/VAT map to negative amounts.
The active credit/debit column must agree with that magnitude; the inactive column must be
empty or zero. Bank balance is validated and preserved as source data, never calculated.

Every original CSV column/value is retained in `source_values`, including transaction_code,
tracking_key, reviewed_payment_id, source_reference, source_namespace, payer observations,
classification and house suggestions/assignments. These remain audit data, without creating
houses, contacts, payments or allocations. `source_identifiers` exposes the available reference
keys. CSV file name, SHA-256, row number and model timestamps preserve import provenance;
no missing original bank TXT filename or row is fabricated.

Read-only preflight (the manifest flag additionally verifies contract, CSV hash, record count
and credit/debit totals):

```sh
python manage.py import_bank_transactions /path/to/bank_transactions.csv --preflight --manifest /path/to/manifest.json
```

Without `--preflight`, this command imports the entire validated file in one database
transaction. Do not run that against development historical data until approved. Invalid rows,
duplicate matching identities or conflicting source/reference values reject the entire file.
The summary reports total/accepted/rejected rows, duplicate identity groups, conflicting rows,
unchanged records, line types, credit/debit totals and net amount. Totals describe parsed valid
candidates; when errors exist, they are not a complete-file financial certification.

Identical reimports retain original IDs, timestamps and provenance even if the CSV filename or
row positions differ. Persistence rechecks identities and source values atomically, including
conflicts introduced after preflight. Unique external codes also prevent reusing reconciliation
references under a different matching identity. Admin keeps all source/audit fields read-only;
additions, modifications and deletions are disabled while authorized users retain view access.

Migration `0004_bank_transaction_matching_identity` adds matching fields and local time, renames
the provisional `source_identity` column to `transaction_code`, and preserves the primary key.
It stops if provisional bank records already exist, requiring a reviewed backfill instead of
inventing missing identities. The development bank table was empty before migration.

Run the default PostgreSQL suite without any private CSV files:

```sh
python manage.py test --settings=core.tests.settings
```

Synthetic adapter tests are self-contained. The two historical integration tests in
`core.tests.test_mallard_history` are skipped unless `MALLARD_BANK_CSV` is explicitly set.
Run them separately with the finalized private package (CSV, manifest and supporting
reference layouts in the same directory):

```sh
MALLARD_BANK_CSV=/path/to/bank_transactions.csv python manage.py test core.tests.test_mallard_history --settings=core.tests.settings
```

A configured path must exist; no developer-specific path is assumed. No private dataset is
copied into this repository. Full imports in integration tests use only the disposable
PostgreSQL test database. Synthetic fixtures cover malformed headers with populated rows,
invalid inputs, reimports, discrepancies, shared receipts, decimal/sign rules, database
constraints and atomic rollback. Admin request tests verify that GET access works for authorized
users while POSTs cannot add, change or delete bank transactions, including audit timestamps.

# Initial fee configuration

Migration `0003_initial_fee_configuration` seeds January 2026 at 0.00 and February
2026 onward at 1000.00, both with a 150.00 late fee and due day 5. Historical models
and the configured database alias are used. Repeated execution preserves exact
matches and timestamps. Existing nonoverlapping periods are untouched. Conflicting
periods stop the migration without overwriting data; the migration is atomic.
Reversing the data migration intentionally preserves existing fee records.
