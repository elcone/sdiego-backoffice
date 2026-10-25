# Increment 03 — Stage C payments and Comunidad Feliz

Base: reviewed Stage B commit `41e48050589780ae6f2103f3e35414a13e3e89cc`.
No balances, allocation engine, automatic matching, contact ownership decisions or
Stage D interface are included.

## Sources and command

Use the unchanged `import-layouts-increment03-history-v1.0` directory as PACKAGE.
Its verified `existing_exports.json` points to the sibling
`import-layouts-increment02-stable-identity` directory. The latter's manifest is
anchored by the former's source hash; all listed files, including physical receipt
archives, are hash-checked before persistence. Source databases are never read.

Authoritative contracts:

- reviewed_payments.csv: 596 current review decisions, original bases and approved
  assignments. This is metadata about bank/CF contributions, not another bank ledger.
- comunidad_feliz.csv: 290 original CF rows and one user-approved reconstruction.
- cf_reconciliation_groups.csv, cf_group_payments.csv, cf_group_deposits.csv:
  nine frozen groups, 95 members and 16 existing bank settlements.
- receipt_evidence.csv, payment_evidence_links.csv, house_cases.csv: 208 receipt
  references, 209 explicitly typed links and four cases, including pending 6114.
- administrative_decisions.csv: source approval revisions. Relevant revision IDs,
  versions, timestamps, reasons, payloads and base hashes are preserved on Payment.

```sh
python manage.py migrate
python manage.py import_historical_payments PACKAGE --preflight
python manage.py import_historical_payments PACKAGE
```

`--dry-run` aliases `--preflight`. Dependencies are existing Stage A identities,
Stage B and 989 already imported bank transactions. There is no bank persistence in
this command. The existing Increment 02 parser is reused only to validate bank
reference fields and source values. Missing/conflicting bank or house references stop
processing. Package schema definitions, CSV headers, hashes, counts, source IDs,
amounts, dates, review base/approval hashes, group relationships/totals and receipt
hashes/links are checked. Approved assignments are never inferred from suggestions,
account observations or filenames. The entire candidate set is validated before
saving. Persistence is one transaction; errors and conflicts roll back every new
Stage C row. Advisory locking serializes this importer's runs. Exact reimports
preserve all IDs, timestamps, source strings and original provenance.

## Persistence and financial interpretation

Migration 0007 creates eight models, each retaining source IDs/values and CSV
provenance. Composite source keys are compact JSON arrays of the exported key fields.

Payment stores each reviewed assignment and its effective amount. It references an
existing bank movement at most once for direct/duckdb candidates; CF contributions
have no direct bank FK on Payment. The assignment is effective only with a house,
no block and either an explicit APROBADO revision or an ORIGINAL/CONFIRMADO baseline.
An old PENDIENTE validation flag does not undo an explicit approved revision.
Original bases and approval evidence remain distinguishable from effective values.

CFPayment is the original CF evidence. Its additional_credit_amount is source
reconciliation evidence, not an additional amount to sum with Payment. Its unique
reviewed-payment link means the 110 direct-bank duplicates reuse the same Payment;
their additional credit is zero. The 78 prior-closure records have no Payment/effect.
Eight advances retain 12,000 original and 9,000 effective, dated February 1, with
settlement NOT_ESTABLISHED. Approval does not depend on a later bank settlement.

CFReconciliationGroup, CFGroupPayment and CFGroupDeposit preserve membership without
inventing one-to-one allocations inside a group. Settlements are only references to
existing BankTransaction rows. CF-G09 includes the approved 6131 reconstruction and
its explicit bank reference. ReceiptEvidence, PaymentEvidenceLink and
HistoricalHouseCase preserve evidence without treating catalog associations as
approvals. The two 6114 May/August STP receipts remain unlinked to a payment or bank
settlement; their case remains unresolved. PaymentEvidenceLink records referencing
either pending receipt are rejected, including approvals for another house. No new
financial record is fabricated.

Database constraints protect source/composite identities, one reviewed payment per
direct bank credit, one CF record per reviewed payment, one group per member/deposit,
approval/effect shapes and nonnegative monetary relationships. PostgreSQL triggers
also enforce credit-only bank roles, reject direct-payment effects on group deposits
in either insertion order, validate CF/reviewed-payment links in both directions and
restrict group membership to eligible CF payments. Native membership uses relational
fields without CSV metadata; historical source-group validation stays in the importer.
Migration 0008 updates only the membership trigger function, leaving applied 0007 unchanged.
Row locks serialize bank role checks. Existing bank tables and financial data are not updated.

Admin exposes read-only payment, CF, group/member/deposit, receipt/link and case views
with filters, searches and relation links. Group views include read-only member and
deposit inlines. Add/change/delete are disabled even for superusers. View permissions
are still required. Physical documents remain in the immutable source archive;
no private documents or datasets are copied into this repository.

## Expected approved totals and unresolved records

- 596 reviewed payments: 464 effective direct payments plus 103 effective CF/advance
  payments. The other 29 are ten unassigned and 19 blocked (settlements/returns).
- Direct effective amount: 491,500.00 MXN.
- CF incremental effective amount: 108,400.00 MXN, comprising 99,400.00 in nine groups
  and 9,000.00 in advances. Combined effective Payment amount: 599,900.00 MXN.
- CF originals versus physical rows: 290 originals + one reconstruction = 291 rows.
- 110 CF duplicate records have zero additional credit; 78 prior-closure records have
  zero recognized/effective credit. Two pending receipts for 6114 create no payments.
- The 58 bank credits absent from the reviewed snapshot remain bank source records
  with no invented reviewed assignment. Unverified payer-account ownership remains
  unresolved in the source package. No PayerAccount is created.

## Tests

Ordinary PostgreSQL tests use a small synthetic reconciliation, synthetic documents
and temporary malformed contracts; they do not depend on private CSVs. They cover
approval/original-source integrity, pending flags/advances, duplicates, unresolved
assignments, 6114 evidence, 6131/G09, schema/hash/count corruption, transactional
rollback, conflicting reimports, database role constraints, idempotency/provenance
and Admin restrictions. An opt-in test imports Stage A/B and bank fixtures only into
Django's isolated test database, then validates the complete Stage C package.

```sh
python manage.py test --settings=core.tests.settings
SDIEGO_HISTORY_PACKAGE=/path/to/import-layouts-increment03-history-v1.0 \
MALLARD_BANK_CSV=/path/to/import-layouts-increment02-stable-identity/bank_transactions.csv \
python manage.py test --settings=core.tests.settings
```
