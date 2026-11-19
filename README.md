# Audit Analytics in SQL

Seven internal-audit checks written as plain PostgreSQL queries, run against a synthetic
accounts-payable dataset that has known fraud and control failures planted in it. Because the
answer key is known, every check is scored on precision and recall instead of just "it returns rows".

<!-- BEGIN:summary -->
On the default dataset (seed 42) the seven checks caught **157 of 157** planted anomalies and raised **253** false positives, all from `split_invoices` (253).
<!-- END:summary -->

The split-invoice false positives are explained under [Design trade-offs](#design-trade-offs); they
are reported as produced, not tuned away.

All data is synthetic and generated from a fixed seed. No real company data is used.

## How to run

Requirements: Python 3.11+ and PostgreSQL 16 (via Docker or a local install).

```bash
git clone https://github.com/<your-user>/audit-analytics-sql.git
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

Before any check runs, `run_checks.py` prints row counts and reconciles payments with invoices:
the gap between the two totals has to be fully explained by unpaid invoices minus overpayments,
and every payment has to match its invoice's vendor and amount.

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
| split_invoices | 44 | 297 | 44 | 253 | 0 | 0.148 | 1.000 | 0 |
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
| PAY00323 | V0088 | 1,337.13 | 2025-01-27 | PAY00162 | 7 | yes |
| PAY00462 | V0067 | 1,813.08 | 2025-01-31 | PAY00331 | 4 | yes |

**2. `split_invoices`** (297 flagged in total)

| record_id | vendor_id | created_by | invoice_date | amount_usd | invoices_in_group | group_total_usd | planted? |
|---|---|---|---|---|---|---|---|
| INV00552 | V0135 | E012 | 2025-01-20 | 4,752.62 | 3 | 11,268.51 | yes |
| INV00591 | V0135 | E012 | 2025-01-21 | 3,838.92 | 3 | 11,268.51 | yes |
| INV00003 | V0117 | E014 | 2025-01-01 | 2,575.34 | 2 | 6,763.19 | no |

**3. `self_approval`** (40 flagged in total)

| record_id | record_type | employee_id | role | record_date | amount_usd | planted? |
|---|---|---|---|---|---|---|
| INV00114 | invoice | E003 | manager | 2025-01-03 | 2,792.02 | yes |
| INV01980 | invoice | E004 | manager | 2025-03-14 | 2,855.09 | yes |

**4. `weekend_round_entries`** (30 flagged in total)

| record_id | entry_datetime | timing | account | amount_usd | source | planted? |
|---|---|---|---|---|---|---|
| JE01621 | 2025-02-08 10:52:00 | weekend | 6300 Professional Fees | 50,000.00 | manual | yes |
| JE02180 | 2025-02-22 10:50:00 | weekend | 2100 Accrued Liabilities | 5,000.00 | manual | yes |

**5. `vendor_employee_match`** (5 flagged in total)

| record_id | vendor_name | employee_id | matched_on | paid_to_vendor_usd | planted? |
|---|---|---|---|---|---|
| V0032 | Rivera, Martinez and Richardson | E055 | address | 61,322.34 | yes |
| V0070 | Taylor-Mccoy | E015 | address | 129,465.60 | yes |

**6. `inactive_vendor_payments`** (12 flagged in total)

| record_id | vendor_id | inactive_date | payment_date | days_after_inactive | amount_usd | planted? |
|---|---|---|---|---|---|---|
| PAY01838 | V0017 | 2025-03-20 | 2025-03-24 | 4 | 2,138.40 | yes |
| PAY02237 | V0124 | 2025-03-05 | 2025-04-09 | 35 | 4,287.01 | yes |

**7. `benford_first_digit`** (1 flagged in total)

| record_id | department | n_entries | chi_square | share_digits_5_to_9 | benford_share_digits_5_to_9 | planted? |
|---|---|---|---|---|---|---|
| E021 | General Ledger | 300 | 406.10 | 0.817 | 0.301 | yes |
<!-- END:examples -->

## Design trade-offs

What a tighter rule would miss, and what a looser one would add:

1. **Duplicate payments.** Tighter (same day only) misses duplicates paid a few days apart in a later batch. Looser (30+ days) starts flagging legitimate recurring bills such as rent.
2. **Split invoices.** Tighter (also require each invoice to be close to the limit, e.g. > 2,500) cuts the noise from busy vendors but misses splits into many small pieces. Looser (drop the same-clerk condition, as in the original spec) flags many more invoices, because busy vendors legitimately send several invoices a week.
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
- **Exact matching only.** Duplicate payments need identical amounts, and vendor matching needs identical normalised addresses or bank accounts.
- **Scale.** The queries run in milliseconds on ~30k rows. For 10M+ rows the window functions would need partition-friendly indexes (for example `payments (vendor_id, amount_usd, payment_date)`, already created) and possibly date-partitioned tables.

## Repository layout

```
generate_data.py      synthetic data + planted anomalies, fixed seed
run_checks.py         schema, COPY load, validation, checks, scorecard
sql/                  00_schema.sql and one file per check
data/                 generated CSVs, ground_truth.csv, near_misses.csv
results/              one CSV per check + scorecard.csv
tests/                pytest on a tiny generated dataset in a separate database
docs/spec.md          the build spec
```
