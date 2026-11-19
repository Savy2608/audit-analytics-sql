"""Scale benchmark: run every check on a ~1 million row dataset, with and without
the secondary indexes, and record timings, accuracy and query plans.

The large dataset is generated into a temporary directory and never committed;
only the measurements are written to results/:
    results/benchmark.csv                 one row per check
    results/explain_01_no_indexes.txt     EXPLAIN ANALYZE of check 1, before indexes
    results/explain_01_with_indexes.txt   EXPLAIN ANALYZE of check 1, after indexes

Usage:
    python benchmark.py                   # ~1M rows into database audit_bench
    python benchmark.py --update-readme   # also refresh the Scale section of README.md
"""

import argparse
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import pandas as pd

import run_checks
from generate_data import generate

ROOT = Path(__file__).resolve().parent

# Default and scaled sizes keep the same ratios (about 40 invoices per vendor,
# about 150 invoices per employee) so the checks see a comparable business.
SCALE_SIZES = dict(employees=2000, vendors=7500, invoices=300000, journal_entries=410000)

# Secondary indexes from sql/00_schema.sql (primary keys are always kept)
SECONDARY_INDEXES = {
    "idx_invoices_vendor_date": "invoices (vendor_id, invoice_date)",
    "idx_payments_invoice": "payments (invoice_id)",
    "idx_payments_vendor_amount": "payments (vendor_id, amount_usd, payment_date)",
    "idx_journal_created_by": "journal_entries (created_by)",
    "idx_journal_entry_datetime": "journal_entries (entry_datetime)",
}


def drop_indexes(conn):
    for name in SECONDARY_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.execute("ANALYZE")


def create_indexes(conn):
    for name, target in SECONDARY_INDEXES.items():
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
    conn.execute("ANALYZE")


def time_check(conn, path, repeats):
    """Median wall-clock time in milliseconds, after one warm-up run."""
    query = Path(path).read_text()
    conn.execute(query).fetchall()
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        conn.execute(query).fetchall()
        timings.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(timings), 1)


def explain(conn, path):
    query = Path(path).read_text().strip().rstrip(";")
    rows = conn.execute(f"EXPLAIN (ANALYZE, BUFFERS) {query}").fetchall()
    return "\n".join(r[0] for r in rows)


def machine_description(conn):
    cpu = platform.processor() or platform.machine()
    if platform.system() == "Darwin":
        try:
            cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    pg = conn.execute("SHOW server_version").fetchone()[0].split()[0]
    return f"{cpu}, PostgreSQL {pg}, local server"


def run_benchmark(sizes, database, seed=42, repeats=3, results_dir=None):
    with tempfile.TemporaryDirectory() as tmp:
        start = time.perf_counter()
        generate(seed=seed, out_dir=tmp, **sizes)
        generate_s = time.perf_counter() - start

        start = time.perf_counter()
        conn = run_checks.setup_database(tmp, database)
        load_s = time.perf_counter() - start
        counts = run_checks.row_counts(conn)

        recon = run_checks.reconciliation(conn)
        gap = (recon["invoices_total_usd"] - recon["payments_total_usd"]
               - recon["unpaid_invoices_usd"] + recon["overpaid_usd"])
        if gap != 0 or recon["unmatched_payments"] > 0:
            raise SystemExit("Reconciliation failed on the benchmark dataset")

        scorecard, _ = run_checks.run_all_checks(conn, tmp)

        files = run_checks.check_files()
        drop_indexes(conn)
        without = {run_checks.check_name(p): time_check(conn, p, repeats) for p in files}
        plan_before = explain(conn, files[0])
        create_indexes(conn)
        with_idx = {run_checks.check_name(p): time_check(conn, p, repeats) for p in files}
        plan_after = explain(conn, files[0])
        machine = machine_description(conn)
        conn.close()

    table = scorecard[["check_name", "flagged", "precision", "recall"]].copy()
    table["ms_without_secondary_indexes"] = table["check_name"].map(without)
    table["ms_with_secondary_indexes"] = table["check_name"].map(with_idx)
    meta = {
        "total_rows": sum(counts.values()),
        "counts": counts,
        "generate_s": round(generate_s, 1),
        "load_s": round(load_s, 1),
        "machine": machine,
        "repeats": repeats,
    }
    if results_dir is not None:
        results_dir = Path(results_dir)
        table.to_csv(results_dir / "benchmark.csv", index=False)
        (results_dir / "explain_01_no_indexes.txt").write_text(plan_before + "\n")
        (results_dir / "explain_01_with_indexes.txt").write_text(plan_after + "\n")
        pd.DataFrame([{k: v for k, v in meta.items() if k != "counts"} | counts]).to_csv(
            results_dir / "benchmark_meta.csv", index=False)
    return table, meta


def readme_block(table, meta):
    counts = meta["counts"]
    lines = [
        f"Measured with `python benchmark.py` on **{meta['total_rows']:,} rows** "
        f"({counts['invoices']:,} invoices, {counts['payments']:,} payments, "
        f"{counts['journal_entries']:,} journal entries, {counts['vendors']:,} vendors, "
        f"{counts['employees']:,} employees; same seed and planted anomalies). "
        f"Machine: {meta['machine']}. Generating the data took {meta['generate_s']} s and "
        f"loading it with `COPY` took {meta['load_s']} s. Each timing is the median of "
        f"{meta['repeats']} runs after a warm-up.",
        "",
    ]
    shown = table.assign(precision=table["precision"].map(run_checks.ratio),
                         recall=table["recall"].map(run_checks.ratio))
    lines.append(run_checks.markdown_table(shown))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the checks on ~1M rows and time them.")
    parser.add_argument("--database", default="audit_bench")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--update-readme", action="store_true")
    args = parser.parse_args(argv)

    table, meta = run_benchmark(SCALE_SIZES, args.database, repeats=args.repeats,
                                results_dir=ROOT / "results")
    print(f"{meta['total_rows']:,} rows | generate {meta['generate_s']} s | load {meta['load_s']} s")
    print(meta["machine"])
    print(table.to_string(index=False))
    if args.update_readme:
        run_checks.update_readme({"benchmark": readme_block(table, meta)})
        print("\nREADME.md Scale section updated from this run")


if __name__ == "__main__":
    main()
