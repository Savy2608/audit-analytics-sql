"""Load the generated CSVs into Postgres, validate them, run every audit check
and score the results against the planted ground truth.

Steps:
    1. create the database if needed and (re)create the schema (sql/00_schema.sql)
    2. load data/*.csv with COPY
    3. print row counts and a payments-vs-invoices reconciliation
    4. run sql/01_*.sql ... sql/07_*.sql, save each output to results/<check>.csv
    5. compare flagged IDs with data/ground_truth.csv -> results/scorecard.csv

Connection settings come from .env (see .env.example).

Usage:
    python run_checks.py
    python run_checks.py --data-dir data --results-dir results --database audit
"""

import argparse
import os
import time
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv
from psycopg import sql

ROOT = Path(__file__).resolve().parent
SQL_DIR = ROOT / "sql"

# Load order respects foreign keys
TABLES = ["employees", "vendors", "invoices", "payments", "journal_entries"]

SCORECARD_COLUMNS = ["check_name", "planted", "flagged", "true_positives", "false_positives",
                     "missed", "precision", "recall", "near_misses_flagged"]


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def connection_settings(database=None):
    load_dotenv(ROOT / ".env")
    settings = {
        "host": os.getenv("PGHOST", "localhost"),
        "port": os.getenv("PGPORT", "5432"),
        "user": os.getenv("PGUSER", "postgres"),
        "dbname": database or os.getenv("PGDATABASE", "audit"),
    }
    if os.getenv("PGPASSWORD"):
        settings["password"] = os.getenv("PGPASSWORD")
    return settings


def ensure_database(database=None):
    """Create the target database if it does not exist yet."""
    settings = connection_settings(database)
    target = settings["dbname"]
    with psycopg.connect(**{**settings, "dbname": "postgres"}, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))


def connect(database=None):
    return psycopg.connect(**connection_settings(database), autocommit=True)


def create_schema(conn):
    conn.execute((SQL_DIR / "00_schema.sql").read_text())


def load_csvs(conn, data_dir):
    data_dir = Path(data_dir)
    with conn.cursor() as cur:
        for table in TABLES:
            path = data_dir / f"{table}.csv"
            header = path.open(encoding="utf-8").readline().strip().split(",")
            statement = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true)").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, header)))
            with cur.copy(statement) as copy, path.open("rb") as f:
                while chunk := f.read(1 << 16):
                    copy.write(chunk)
    conn.execute("ANALYZE")


def setup_database(data_dir, database=None):
    """Create database + schema and load the CSVs. Returns an open connection."""
    ensure_database(database)
    conn = connect(database)
    create_schema(conn)
    load_csvs(conn, data_dir)
    return conn


# --------------------------------------------------------------------------- #
# Data validation
# --------------------------------------------------------------------------- #
def row_counts(conn):
    return {t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
            for t in TABLES}


RECONCILIATION_SQL = """
WITH paid_per_invoice AS (
    SELECT invoice_id, count(*) AS n_payments, sum(amount_usd) AS paid_usd
    FROM payments
    GROUP BY invoice_id
)
SELECT
    (SELECT sum(amount_usd) FROM invoices)                                AS invoices_total_usd,
    (SELECT sum(amount_usd) FROM payments)                                AS payments_total_usd,
    (SELECT count(*) FROM invoices i
       WHERE NOT EXISTS (SELECT 1 FROM payments p WHERE p.invoice_id = i.invoice_id))
                                                                          AS unpaid_invoices,
    (SELECT coalesce(sum(i.amount_usd), 0) FROM invoices i
       WHERE NOT EXISTS (SELECT 1 FROM payments p WHERE p.invoice_id = i.invoice_id))
                                                                          AS unpaid_invoices_usd,
    (SELECT count(*) FROM payments p
       LEFT JOIN invoices i ON i.invoice_id = p.invoice_id
       WHERE i.invoice_id IS NULL
          OR i.vendor_id <> p.vendor_id
          OR i.amount_usd <> p.amount_usd)                                AS unmatched_payments,
    (SELECT count(*) FROM paid_per_invoice WHERE n_payments > 1)          AS invoices_paid_more_than_once,
    (SELECT coalesce(sum(p.paid_usd - i.amount_usd), 0)
       FROM paid_per_invoice p JOIN invoices i USING (invoice_id)
       WHERE p.paid_usd > i.amount_usd)                                   AS overpaid_usd
"""


def reconciliation(conn):
    cur = conn.execute(RECONCILIATION_SQL)
    names = [d.name for d in cur.description]
    return dict(zip(names, cur.fetchone()))


# --------------------------------------------------------------------------- #
# Checks and scoring
# --------------------------------------------------------------------------- #
def check_files():
    return sorted(p for p in SQL_DIR.glob("[0-9][0-9]_*.sql") if not p.name.startswith("00_"))


def check_name(path):
    return Path(path).stem.split("_", 1)[1]


def run_check(conn, path):
    """Run one check file. Every check returns a record_id column plus context."""
    cur = conn.execute(Path(path).read_text())
    columns = [d.name for d in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns)


def score(flagged_ids, planted_ids, near_miss_ids=()):
    flagged, planted, near = set(flagged_ids), set(planted_ids), set(near_miss_ids)
    tp = len(flagged & planted)
    fp = len(flagged - planted)
    return {
        "planted": len(planted),
        "flagged": len(flagged),
        "true_positives": tp,
        "false_positives": fp,
        "missed": len(planted - flagged),
        "precision": round(tp / len(flagged), 3) if flagged else None,
        "recall": round(tp / len(planted), 3) if planted else None,
        "near_misses_flagged": len(flagged & near),
    }


def load_labels(data_dir, filename):
    path = Path(data_dir) / filename
    if not path.exists():
        return {}
    labels = pd.read_csv(path, dtype=str)
    return labels.groupby("check_name")["record_id"].apply(set).to_dict()


def run_all_checks(conn, data_dir, results_dir=None):
    truth = load_labels(data_dir, "ground_truth.csv")
    near = load_labels(data_dir, "near_misses.csv")
    rows, outputs = [], {}
    for path in check_files():
        name = check_name(path)
        result = run_check(conn, path)
        outputs[name] = result
        if results_dir is not None:
            result.to_csv(Path(results_dir) / f"{path.stem}.csv", index=False)
        rows.append({"check_name": name,
                     **score(result["record_id"], truth.get(name, set()), near.get(name, set()))})
    return pd.DataFrame(rows, columns=SCORECARD_COLUMNS), outputs


# --------------------------------------------------------------------------- #
# README blocks (numbers are only ever written from a real run)
# --------------------------------------------------------------------------- #
CHECK_DOCS = {
    "duplicate_payments": ("Paying the same bill twice",
                           "Same vendor, same amount, paid within 7 days"),
    "split_invoices": ("Splitting a purchase to stay under the 5,000 USD approval limit",
                       "Same vendor + same clerk + same week, each < 5,000, total > 5,000"),
    "self_approval": ("Segregation of duties failure",
                      "created_by = approved_by (invoices and journal entries)"),
    "weekend_round_entries": ("Manual entries outside the normal process",
                              "Weekend or 22:00-05:59, and amount a multiple of 1,000"),
    "vendor_employee_match": ("Fictitious vendor set up by an employee",
                              "Vendor address (normalised) or bank account equals an employee's"),
    "inactive_vendor_payments": ("Paying a vendor that should be closed",
                                 "Payment date after the vendor's inactive date"),
    "benford_first_digit": ("Fabricated amounts in bulk",
                            "Per-employee first-digit chi-square vs Benford > 20.09 (p < 0.01)"),
}

EXAMPLE_COLUMNS = {
    "duplicate_payments": ["record_id", "vendor_id", "amount_usd", "payment_date",
                           "previous_payment_id", "days_since_previous"],
    "split_invoices": ["record_id", "vendor_id", "created_by", "invoice_date", "amount_usd",
                       "invoices_in_group", "group_total_usd"],
    "self_approval": ["record_id", "record_type", "employee_id", "role", "record_date", "amount_usd"],
    "weekend_round_entries": ["record_id", "entry_datetime", "timing", "account", "amount_usd", "source"],
    "vendor_employee_match": ["record_id", "vendor_name", "employee_id", "matched_on", "paid_to_vendor_usd"],
    "inactive_vendor_payments": ["record_id", "vendor_id", "inactive_date", "payment_date",
                                 "days_after_inactive", "amount_usd"],
    "benford_first_digit": ["record_id", "department", "n_entries", "chi_square",
                            "share_digits_5_to_9", "benford_share_digits_5_to_9"],
}


def markdown_table(df):
    def fmt(value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return "-"
        if isinstance(value, float):
            return f"{value:,.3f}" if abs(value) < 1 else f"{value:,.2f}"
        return str(value)

    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def ratio(value):
    return "-" if value is None or pd.isna(value) else f"{value:.3f}"


def readme_blocks(scorecard, outputs, truth):
    planted = int(scorecard["planted"].sum())
    caught = int(scorecard["true_positives"].sum())
    fp = int(scorecard["false_positives"].sum())
    noisy = scorecard[scorecard["false_positives"] > 0]
    fp_text = ", ".join(f"`{r.check_name}` ({r.false_positives})" for r in noisy.itertuples())
    summary = (f"On the committed dataset (`data/`, seed 42) the seven checks caught **{caught} of {planted}** "
               f"planted anomalies and raised **{fp}** false positives"
               + (f", all from {fp_text}." if fp_text else "."))

    checks = pd.DataFrame([{
        "#": i, "check": f"`{r.check_name}`", "risk": CHECK_DOCS[r.check_name][0],
        "rule": CHECK_DOCS[r.check_name][1], "precision": ratio(r.precision), "recall": ratio(r.recall),
    } for i, r in enumerate(scorecard.itertuples(), start=1)])

    examples = []
    for i, (name, df) in enumerate(outputs.items(), start=1):
        # Two true positives plus one false positive if the check has any, else three true positives
        is_planted = df["record_id"].isin(truth.get(name, set()))
        false_positives = df[~is_planted].head(1)
        picks = pd.concat([df[is_planted].head(3 - len(false_positives)), false_positives])
        sample = picks[EXAMPLE_COLUMNS[name]].copy()
        sample["planted?"] = ["yes" if x in truth.get(name, set()) else "no" for x in sample["record_id"]]
        sample = sample.astype(object).where(sample.notna(), None)
        for col in sample.columns:
            sample[col] = [float(v) if hasattr(v, "is_finite") else v for v in sample[col]]  # Decimal -> float
        examples.append(f"**{i}. `{name}`** ({len(df)} flagged in total)\n\n{markdown_table(sample)}")

    return {
        "summary": summary,
        "checks": markdown_table(checks),
        "scorecard": markdown_table(scorecard.assign(precision=scorecard["precision"].map(ratio),
                                                     recall=scorecard["recall"].map(ratio))),
        "examples": "\n\n".join(examples),
    }


def update_readme(blocks, path=ROOT / "README.md"):
    text = path.read_text()
    for name, content in blocks.items():
        start, end = f"<!-- BEGIN:{name} -->", f"<!-- END:{name} -->"
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        text = f"{before}{start}\n{content}\n{end}{after}"
    path.write_text(text)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(description="Load data, run audit checks, write the scorecard.")
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--results-dir", default=str(ROOT / "results"))
    parser.add_argument("--database", default=None, help="override PGDATABASE")
    parser.add_argument("--update-readme", action="store_true",
                        help="rewrite the generated tables in README.md from this run")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    with setup_database(args.data_dir, args.database) as conn:
        print("Row counts")
        for table, count in row_counts(conn).items():
            print(f"  {table:<16} {count:>7,}")

        recon = reconciliation(conn)
        print("\nReconciliation")
        print(f"  invoices total            {recon['invoices_total_usd']:>16,.2f} USD")
        print(f"  payments total            {recon['payments_total_usd']:>16,.2f} USD")
        print(f"  difference                {recon['invoices_total_usd'] - recon['payments_total_usd']:>16,.2f} USD")
        print(f"  unpaid invoices           {recon['unpaid_invoices']:>9,}  ({recon['unpaid_invoices_usd']:,.2f} USD)")
        print(f"  unmatched payments        {recon['unmatched_payments']:>9,}  (no invoice, or vendor/amount differs)")
        print(f"  invoices paid twice       {recon['invoices_paid_more_than_once']:>9,}  ({recon['overpaid_usd']:,.2f} USD overpaid)")
        explained = recon["unpaid_invoices_usd"] - recon["overpaid_usd"]
        gap = recon["invoices_total_usd"] - recon["payments_total_usd"] - explained
        print(f"  unexplained difference    {gap:>16,.2f} USD")
        if gap != 0 or recon["unmatched_payments"] > 0:
            raise SystemExit("Data validation failed: reconciliation does not tie out. Checks not run.")

        scorecard, outputs = run_all_checks(conn, args.data_dir, results_dir)

    scorecard.to_csv(results_dir / "scorecard.csv", index=False)
    print("\nScorecard (results/scorecard.csv)")
    print(scorecard.to_string(index=False))

    if args.update_readme:
        if Path(args.data_dir).resolve() != (ROOT / "data").resolve():
            raise SystemExit("--update-readme only works with the default data/ directory")
        truth = load_labels(args.data_dir, "ground_truth.csv")
        update_readme(readme_blocks(scorecard, outputs, truth))
        print("\nREADME.md tables updated from this run")
    print(f"\nDone in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
