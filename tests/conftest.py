"""Shared fixtures: a tiny generated dataset loaded into a separate test database."""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_checks  # noqa: E402
from generate_data import generate  # noqa: E402

load_dotenv(ROOT / ".env")

TINY = dict(seed=42, employees=30, vendors=60, invoices=1500, journal_entries=3000)


def database_name_for_tests():
    return os.getenv("TEST_PGDATABASE", "audit_test")


@pytest.fixture(scope="session")
def tiny_data(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("tiny_data")
    generate(**TINY, out_dir=out_dir)
    return out_dir


@pytest.fixture(scope="session")
def db(tiny_data):
    conn = run_checks.setup_database(tiny_data, database_name_for_tests())
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def labels(tiny_data):
    return (run_checks.load_labels(tiny_data, "ground_truth.csv"),
            run_checks.load_labels(tiny_data, "near_misses.csv"))


@pytest.fixture(scope="session")
def check_result(db, labels):
    """Run one check by name; return (flagged, planted, near_misses) as sets."""
    truth, near = labels
    files = {run_checks.check_name(p): p for p in run_checks.check_files()}

    def _run(name):
        result = run_checks.run_check(db, files[name])
        return set(result["record_id"]), truth.get(name, set()), near.get(name, set())

    return _run
