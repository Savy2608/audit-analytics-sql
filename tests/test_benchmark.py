"""The benchmark must run end to end and keep accuracy (smoke test at a small size)."""

import benchmark
from conftest import TINY, database_name_for_tests


def test_benchmark_runs_and_keeps_recall():
    sizes = {k: v for k, v in TINY.items() if k != "seed"}
    table, meta = benchmark.run_benchmark(sizes, database_name_for_tests() + "_bench", repeats=1)
    assert (table["recall"] == 1.0).all()
    assert (table["ms_without_secondary_indexes"] > 0).all() and (table["ms_with_secondary_indexes"] > 0).all()
    assert meta["total_rows"] > TINY["invoices"]
    assert "benchmark" in benchmark.readme_block(table, meta)
