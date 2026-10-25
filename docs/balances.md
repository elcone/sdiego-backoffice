# Operational house balances and account statements

`core.balances.house_balances(houses=None, *, as_of=None)` returns a House queryset
annotated with Decimal `total_charges`, `total_payments` and `balance`. Pass a filtered
House queryset to restrict the scope. All houses, including empty accounts, are returned
in one SQL query. Independent aggregate subqueries avoid charge/payment join multiplication.

`core.balances.account_statement(house, *, as_of=None)` returns a list of rows with
`date`, `transaction_type`, `description`, `charge_amount`, `payment_amount` and
`running_balance`. A House instance or its primary key can identify the account.
The statement uses one SQL UNION ALL, so all its movements share one database snapshot.
Its totals and closing running balance can be derived directly from those same rows.

For both functions, None means today in Django's configured timezone. An explicit
Python date is an inclusive cutoff; future obligations are excluded until effective.
Dates use Charge.effective_date and Payment.effective_date, independent of original
payment receipt dates or nominal fee periods. Monetary values are Decimal throughout.

Balance is effective charges minus effective assigned payments. Charge.amount already
reflects discounts. Zero charges and zero payment effects do not appear in statements.
Payments must be assigned, approved, unblocked and have a positive effective_amount.
CF contributions use their existing Payment representation; bank settlements, duplicate
CF evidence and unapproved receipts are never additional financial movements. Credits
remain negative balances; they are not clamped to zero. Audit-only charge exclusions
are not Charge records and are not included.

Statements order by effective date, with charges before payments on the same date,
then permanent primary key within each type. Descriptions show charge concept/period
or Payment. They do not depend on import metadata or expose reconciliation details.

The House Admin list shows today's balance and an Account statement link. The House
change form also links to the statement. The statement page shows a date cutoff form,
effective charge/payment totals, balance and running statement. Summary totals use the
same statement rows, avoiding disagreements between separate reads during concurrent
posting. Invalid date input returns an error, rather than silently choosing a cutoff.

Existing House view/change permission plus Charge and Payment view/change permissions
are required for financial data. Users lacking those permissions retain their existing
House access without financial columns or statement links. The account endpoint is
read-only and rejects POST requests. Existing record administration is unchanged.

Global totals must use the same cutoff and house scope for charges, payments and net
balances. Do not compare obligations that include future periods against payments at
an earlier cutoff. No balances are stored, no migrations or models are added, and no
financial records or fee configuration are changed by these queries.

Run PostgreSQL tests with `python manage.py test --settings=core.tests.settings`.
The operational tests use native model fixtures, independent of private source files.
They cover balances/credits, effective dates, discounts, assignment, CF/settlement
separation, audit exclusions, deterministic ordering, one-query aggregation for 79
houses, statement consistency and Admin permissions.
