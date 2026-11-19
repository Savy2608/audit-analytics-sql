"""The generator must be deterministic and plant exactly what the spec asks for."""

import filecmp

import pandas as pd

import generate_data as gd
import run_checks
from conftest import TINY

CSV_FILES = ["employees.csv", "vendors.csv", "invoices.csv", "payments.csv",
             "journal_entries.csv", "ground_truth.csv", "near_misses.csv"]


def test_same_seed_gives_identical_files(tmp_path):
    gd.generate(**TINY, out_dir=tmp_path / "a")
    gd.generate(**TINY, out_dir=tmp_path / "b")
    for name in CSV_FILES:
        assert filecmp.cmp(tmp_path / "a" / name, tmp_path / "b" / name, shallow=False), name


def test_different_seed_gives_different_data(tmp_path):
    gd.generate(**TINY, out_dir=tmp_path / "a")
    gd.generate(**{**TINY, "seed": 7}, out_dir=tmp_path / "b")
    assert not filecmp.cmp(tmp_path / "a" / "invoices.csv", tmp_path / "b" / "invoices.csv", shallow=False)


def test_requested_sizes(tiny_data):
    assert len(pd.read_csv(tiny_data / "employees.csv")) == TINY["employees"]
    assert len(pd.read_csv(tiny_data / "vendors.csv")) == TINY["vendors"]
    assert len(pd.read_csv(tiny_data / "invoices.csv")) == TINY["invoices"]
    assert len(pd.read_csv(tiny_data / "journal_entries.csv")) == TINY["journal_entries"]


def test_planted_counts_match_spec(tiny_data):
    truth = pd.read_csv(tiny_data / "ground_truth.csv").groupby("check_name").size().to_dict()
    assert truth["duplicate_payments"] == gd.N_DUPLICATE_PAYMENTS
    assert 2 * gd.N_SPLIT_GROUPS <= truth["split_invoices"] <= 4 * gd.N_SPLIT_GROUPS
    assert truth["self_approval"] == gd.N_SELF_APPROVED_INVOICES + gd.N_SELF_APPROVED_ENTRIES
    assert truth["weekend_round_entries"] == gd.N_WEEKEND_ROUND_ENTRIES
    assert truth["vendor_employee_match"] == gd.N_FICTITIOUS_VENDORS
    assert truth["inactive_vendor_payments"] == gd.N_INACTIVE_VENDOR_PAYMENTS
    assert truth["benford_first_digit"] == 1


def test_ground_truth_ids_exist(tiny_data):
    ids = set()
    for name in ["employees", "vendors", "invoices", "payments", "journal_entries"]:
        ids |= set(pd.read_csv(tiny_data / f"{name}.csv", dtype=str).iloc[:, 0])
    for name in ["ground_truth.csv", "near_misses.csv"]:
        assert set(pd.read_csv(tiny_data / name, dtype=str)["record_id"]) <= ids


def test_data_loads_with_all_constraints(db):
    counts = run_checks.row_counts(db)
    assert counts["invoices"] == TINY["invoices"]
    assert counts["journal_entries"] == TINY["journal_entries"]
    recon = run_checks.reconciliation(db)
    assert recon["unmatched_payments"] == 0
    assert recon["invoices_paid_more_than_once"] == gd.N_DUPLICATE_PAYMENTS
