# Audit Analytics in SQL

[![tests](https://github.com/Savy2608/audit-analytics-sql/actions/workflows/tests.yml/badge.svg)](https://github.com/Savy2608/audit-analytics-sql/actions/workflows/tests.yml)

Seven internal-audit checks written as plain PostgreSQL queries, run against a synthetic
accounts-payable dataset that has known fraud and control failures planted in it. Because the
answer key is known, every check is scored on precision and recall instead of just "it returns rows".

<!-- BEGIN:summary -->
On the committed dataset (`data/`, seed 42) the seven checks caught **156 of 156** planted anomalies and raised **248** false positives, all from `split_invoices` (248).
<!-- END:summary -->

The split-invoice false positives are explained under [Design trade-offs](#design-trade-offs); they
are reported as produced, not tuned away.

All data is synthetic and generated from a fixed seed. No real company data is used.

## Who would use this

- **What the data is for:** testing accounts-payable and journal-entry controls on the full population of transactions instead of a sample, and getting a ranked list of exceptions to review.
- **Who uses it:** internal auditors planning and running control tests, and controllers or accounting managers who clear the exceptions.
- **How often it runs:** monthly after the books are closed, or continuously (daily) in a continuous-auditing setup. The checks are plain SQL, so they can be scheduled next to the ledger database.

## How to run

Requirements: Python 3.11+ and PostgreSQL 16 (via Docker or a local install).

```bash
git clone https://github.com/Savy2608/audit-analytics-sql.git
cd audit-analytics-sql
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edit if you use a local Postgres instead of Docker
docker compose up -d          # skip if you already run Postgres locally
python generate_data.py       # writes data/*.csv and data/ground_truth.csv
python run_checks.py          # loads data, validates it, runs checks, writes results/
pytest                        # tests use a separate database (TEST_PGDATABASE)
```

The whole pipeline runs in a few seconds. `run_checks.py` creates the database if it does not
exist. Useful options:

- `python generate_data.py --seed 7 --invoices 20000 --journal-entries 50000` - sizes and seed are CLI options; the same seed always gives byte-identical CSVs.
- `python run_checks.py --update-readme` - refreshes the tables in this README from the run, so no number here is typed by hand.

Outputs: one CSV per check in `results/` (every flagged record with context columns) and
`results/scorecard.csv`.

## Data model

One fictional company, calendar year 2025. Default sizes:

| Table | Rows | Key columns |
|---|---|---|
| `employees` | 60 | employee_id, name, department, role, address, bank_account, approval_limit_usd |
| `vendors` | 200 | vendor_id, name, address, bank_account, status, created_date, inactive_date |
| `invoices` | 8,000 | invoice_id, vendor_id, invoice_number, invoice_date, amount_usd, created_by, approved_by, approved_date |
| `payments` | ~7,700 | payment_id, invoice_id, vendor_id, payment_date, amount_usd |
| `journal_entries` | 15,000 | entry_id, entry_datetime, account, amount_usd, source, created_by, approved_by, description |

Schema with primary keys, foreign keys and indexes: [`sql/00_schema.sql`](sql/00_schema.sql).

How the data is made realistic:

- Amounts are log-normal, so first digits follow Benford's Law closely.
- Most activity happens on weekdays in business hours; a small share happens in the evening or on weekends.
- Invoices above 5,000 USD are approved by a manager; smaller ones by a manager or AP supervisor, never by the clerk who entered them.
- Some vendors bill much more often than others (log-normal vendor weights).
- Invoices issued late in December are still unpaid at year end.
- Bank accounts are obviously fake strings (`FAKEBANK-...`).

Before any check runs, `run_checks.py` prints row counts and reconciles payments with invoices.
The gap between the two totals must be fully explained by unpaid invoices minus overpayments,
and every payment must match its invoice's vendor and amount. If either test fails, the run
stops before any check is executed.

## Checks

Each check is one file in [`sql/`](sql/). It is written with named CTEs and starts with a short
comment giving the risk, the rule and the threshold. Every query returns `record_id` plus
context columns.

<!-- BEGIN:checks -->
| # | check | risk | rule | precision | recall |
|---|---|---|---|---|---|
| 1 | `duplicate_payments` | Paying the same bill twice | Same vendor, same amount, paid within 7 days | 1.000 | 1.000 |
| 2 | `split_invoices` | Splitting a purchase to stay under the 5,000 USD approval limit | Same vendor + same clerk + same week, each < 5,000, total > 5,000 | 0.148 | 1.000 |
| 3 | `self_approval` | Segregation of duties failure | created_by = approved_by (invoices and journal entries) | 1.000 | 1.000 |
| 4 | `weekend_round_entries` | Manual entries outside the normal process | Weekend or 22:00-05:59, and amount a multiple of 1,000 | 1.000 | 1.000 |
| 5 | `vendor_employee_match` | Fictitious vendor set up by an employee | Vendor address (normalised) or bank account equals an employee's | 1.000 | 1.000 |
| 6 | `inactive_vendor_payments` | Paying a vendor that should be closed | Payment date after the vendor's inactive date | 1.000 | 1.000 |
| 7 | `benford_first_digit` | Fabricated amounts in bulk | Per-employee first-digit chi-square vs Benford > 20.09 (p < 0.01) | 1.000 | 1.000 |
<!-- END:checks -->

How the answer key works: `generate_data.py` plants a fixed number of cases for each check and
writes their IDs to `data/ground_truth.csv`. It also plants **near-misses**: legitimate records
that look similar and should not be flagged. They are listed in `data/near_misses.csv`:

| Check | Near-misses planted |
|---|---|
| Duplicate payments | 3 vendors with monthly invoices of the same amount, paid ~30 days apart |
| Split invoices | Same vendor, same clerk, same week, but the total stays under 5,000 |
| Weekend / round entries | Automated month-end entries on Saturday 23:30 with non-round amounts; round accruals posted on weekdays in business hours |
| Inactive vendors | Payments a few days before the vendor's inactive date |

Check 7 flags a population (an employee) rather than single entries. It is scored as caught
or missed for that employee.

## Scorecard

Produced by `run_checks.py` and copied from `results/scorecard.csv`. `near_misses_flagged` counts
how many of the planted look-alikes a check wrongly picked up.

<!-- BEGIN:scorecard -->
| check_name | planted | flagged | true_positives | false_positives | missed | precision | recall | near_misses_flagged |
|---|---|---|---|---|---|---|---|---|
| duplicate_payments | 25 | 25 | 25 | 0 | 0 | 1.000 | 1.000 | 0 |
| split_invoices | 43 | 291 | 43 | 248 | 0 | 0.148 | 1.000 | 0 |
| self_approval | 40 | 40 | 40 | 0 | 0 | 1.000 | 1.000 | 0 |
| weekend_round_entries | 30 | 30 | 30 | 0 | 0 | 1.000 | 1.000 | 0 |
| vendor_employee_match | 5 | 5 | 5 | 0 | 0 | 1.000 | 1.000 | 0 |
| inactive_vendor_payments | 12 | 12 | 12 | 0 | 0 | 1.000 | 1.000 | 0 |
| benford_first_digit | 1 | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 0 |
<!-- END:scorecard -->

## Example flagged records

Two planted cases per check, plus one false positive where the check has any. `planted?` shows whether the record is in the answer key.

<!-- BEGIN:examples -->
**1. `duplicate_payments`** (25 flagged in total)

| record_id | vendor_id | amount_usd | payment_date | previous_payment_id | days_since_previous | planted? |
|---|---|---|---|---|---|---|
| PAY00375 | V0007 | 1,727.35 | 2025-01-29 | PAY00311 | 2 | yes |
| PAY00545 | V0089 | 4,633.27 | 2025-02-05 | PAY00448 | 2 | yes |
| PAY00992 | V0116 | 7,366.89 | 2025-02-21 | PAY00937 | 2 | yes |

**2. `split_invoices`** (291 flagged in total)

| record_id | vendor_id | created_by | invoice_date | amount_usd | invoices_in_group | group_total_usd | planted? |
|---|---|---|---|---|---|---|---|
| INV00536 | V0135 | E013 | 2025-01-21 | 4,922.40 | 2 | 9,082.77 | yes |
| INV00571 | V0135 | E013 | 2025-01-22 | 4,160.37 | 2 | 9,082.77 | yes |
| INV00001 | V0116 | E014 | 2025-01-01 | 2,575.34 | 2 | 6,763.19 | no |

**3. `self_approval`** (40 flagged in total)

| record_id | record_type | employee_id | role | record_date | amount_usd | planted? |
|---|---|---|---|---|---|---|
| INV00537 | invoice | E010 | ap_supervisor | 2025-01-21 | 1,785.14 | yes |
| INV00795 | invoice | E003 | manager | 2025-01-30 | 4,247.12 | yes |
| INV00834 | invoice | E008 | ap_supervisor | 2025-01-31 | 1,761.60 | yes |

**4. `weekend_round_entries`** (30 flagged in total)

| record_id | entry_datetime | timing | account | amount_usd | source | planted? |
|---|---|---|---|---|---|---|
| JE00166 | 2025-01-04 16:31:00 | weekend | 2100 Accrued Liabilities | 10,000.00 | manual | yes |
| JE00464 | 2025-01-13 01:53:00 | late night | 2100 Accrued Liabilities | 100,000.00 | manual | yes |
| JE01731 | 2025-02-11 23:02:00 | late night | 6300 Professional Fees | 25,000.00 | manual | yes |

**5. `vendor_employee_match`** (5 flagged in total)

| record_id | vendor_name | employee_id | matched_on | paid_to_vendor_usd | planted? |
|---|---|---|---|---|---|
| V0032 | Rivera, Martinez and Richardson | E055 | address | 25,762.68 | yes |
| V0070 | Taylor-Mccoy | E015 | address | 89,397.35 | yes |
| V0087 | Lopez, Davidson and Reynolds | E040 | address | 52,223.98 | yes |

**6. `inactive_vendor_payments`** (12 flagged in total)

| record_id | vendor_id | inactive_date | payment_date | days_after_inactive | amount_usd | planted? |
|---|---|---|---|---|---|---|
| PAY02468 | V0120 | 2025-03-19 | 2025-04-23 | 35 | 4,774.27 | yes |
| PAY02516 | V0023 | 2025-04-02 | 2025-04-25 | 23 | 1,433.28 | yes |
| PAY02895 | V0016 | 2025-05-05 | 2025-05-13 | 8 | 12,631.70 | yes |

**7. `benford_first_digit`** (1 flagged in total)

| record_id | department | n_entries | chi_square | share_digits_5_to_9 | benford_share_digits_5_to_9 | planted? |
|---|---|---|---|---|---|---|
| E021 | General Ledger | 300 | 389.11 | 0.810 | 0.301 | yes |
<!-- END:examples -->

## Scale

The committed dataset is small so that CI can verify it byte for byte. `benchmark.py` generates a
dataset of about a million rows into a temporary directory (it is never committed), runs every check
with and without the secondary indexes, and writes `results/benchmark.csv`.

<!-- BEGIN:benchmark -->
Measured with `python benchmark.py` on **1,009,072 rows** (300,000 invoices, 289,572 payments, 410,000 journal entries, 7,500 vendors, 2,000 employees; same seed and planted anomalies). Machine: Apple M5 Pro, PostgreSQL 16.16, local server. Generating the data took 8.3 s and loading it with `COPY` took 16.4 s. Each timing is the median of 3 runs after a warm-up.

| check_name | flagged | precision | recall | ms_without_secondary_indexes | ms_with_secondary_indexes |
|---|---|---|---|---|---|
| duplicate_payments | 28 | 0.893 | 1.000 | 435.70 | 119.10 |
| split_invoices | 269 | 0.178 | 1.000 | 563.10 | 370.40 |
| self_approval | 40 | 1.000 | 1.000 | 13.10 | 12.30 |
| weekend_round_entries | 30 | 1.000 | 1.000 | 12.20 | 12.20 |
| vendor_employee_match | 5 | 1.000 | 1.000 | 67.90 | 61.70 |
| inactive_vendor_payments | 12 | 1.000 | 1.000 | 8.80 | 8.40 |
| benford_first_digit | 5 | 0.200 | 1.000 | 157.40 | 159.00 |
<!-- END:benchmark -->

What the benchmark showed:

- **An `OR` in a join condition does not scale.** The first version of check 5 joined vendors to employees `ON address = ... OR bank_account = ...`. Postgres cannot hash-join on an `OR`, so it compared every vendor with every employee and ran the address normalisation for each pair. That took tens of seconds at this size. Rewriting it as two equality joins combined with `UNION ALL` brought it down to tens of milliseconds, with identical output.
- **The composite index helps check 1 most.** `payments (vendor_id, amount_usd, payment_date)` matches the window's `PARTITION BY vendor_id, amount_usd ORDER BY payment_date`. Without it, Postgres sorts every payment and the sort spills to disk (`external merge`). With it, Postgres reads the index in order and only needs a small in-memory `Incremental Sort`. Query plans before and after: [`results/explain_01_no_indexes.txt`](results/explain_01_no_indexes.txt), [`results/explain_01_with_indexes.txt`](results/explain_01_with_indexes.txt). Checks that scan a whole table (Benford, self-approval) gain little or nothing from indexes.
- **Precision changes with scale, in ways you can explain.** With hundreds of accountants, a p < 0.01 Benford test is expected to flag a few honest people by chance (multiple testing). With hundreds of thousands of payments, a few unrelated invoices share a vendor and an exact amount. Both are reported as produced.

## Design trade-offs

What a tighter rule would miss, and what a looser one would add:

1. **Duplicate payments.** Tighter (same day only) misses duplicates paid a few days apart in a later batch. Looser (30+ days) starts flagging legitimate recurring bills such as rent.
2. **Split invoices.** Tighter (also require each invoice to be close to the limit, e.g. > 2,500) cuts the noise from busy vendors but misses splits into many small pieces. Looser (drop the same-clerk condition, the first-draft rule described in `docs/spec.md` section 9) flags many more invoices, because busy vendors legitimately send several invoices a week.
3. **Self-approval.** There is no threshold to tune. A looser version that also flags approvers who report to the creator would need an org chart that this dataset does not have.
4. **Weekend / round entries.** Tighter (only multiples of 10,000) misses smaller round adjustments. Looser (any weekend entry, round or not) flags every automated month-end batch.
5. **Vendor-employee match.** Tighter (bank account only) misses fake vendors that use a home address. Looser (fuzzy address matching) catches typos but also flags neighbours and shared office buildings.
6. **Inactive vendors.** Tighter (a grace period of e.g. 30 days) misses payments made right after closing. Looser (flagging payments shortly before the inactive date) adds normal final payments.
7. **Benford.** Tighter (p < 0.001) needs a larger deviation and a larger sample to fire. Looser (p < 0.05) flags around one honest employee in twenty just by chance, since each employee is a separate test.

Why the split-invoice check is noisy: most of its false positives come from high-volume vendors,
where two ordinary invoices from the same clerk in the same week often add up to more than
5,000 USD. In a real audit this check is a triage list: a reviewer looks at the group, the
purchase order and the requester, so a low precision is acceptable as long as recall stays high.

Why most checks score 100%: the synthetic data has no noise beyond what was deliberately put in.
Real ledgers contain legitimate weekend work, shared addresses (couples, family businesses)
and data-entry errors, so precision would be lower in practice. The near-misses show that the
rules are precise, but they do not show how they behave on real-world noise.

## Limitations

- **Synthetic data, one company, one year.** The anomalies are planted by the same author who wrote the checks, so recall here is an upper bound.
- **Simple Benford test.** It looks at the first digit only, uses chi-square with a fixed p-value and needs 100+ entries per employee. It does not use second-digit or MAD tests and does not correct for multiple testing beyond the strict threshold.
- **Checks interact.** Round amounts (check 4) are part of the same population that the Benford test (check 7) looks at. With some seeds, an honest accountant who posts many round accruals ends up just over the chi-square cutoff. Two genuinely different invoices can also share a vendor and an exact amount by chance and be flagged by check 1. Both are honest false positives that show up with other seeds; seed 42 happens to have none.
- **Exact matching only.** Duplicate payments need identical amounts, and vendor matching needs identical normalised addresses or bank accounts.
- **Scale.** Tested up to about a million rows on one local server (see [Scale](#scale)). At 10M+ rows the next steps would be date-partitioned tables, running the checks per month, and incremental runs that only look at new transactions.

## Repository layout

```
generate_data.py      synthetic data + planted anomalies, fixed seed
run_checks.py         schema, COPY load, validation, checks, scorecard
benchmark.py          ~1M-row run, timings with and without indexes
sql/                  00_schema.sql and one file per check
data/                 generated CSVs, ground_truth.csv, near_misses.csv
results/              one CSV per check, scorecard.csv, benchmark results
tests/                pytest on a tiny generated dataset in a separate database
docs/spec.md          the specification and design decisions
```
