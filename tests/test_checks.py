"""Each check must find all of its planted cases, flag none of its near-misses, and
(where the tiny dataset has no natural look-alikes) flag nothing else either."""

import pandas as pd


def assert_exact(check_result, name):
    """Flagged set must equal the answer key: catches both misses and over-flagging."""
    flagged, planted, near_misses = check_result(name)
    assert planted, f"no planted cases for {name}"
    assert not (flagged & near_misses), f"flagged near-misses: {sorted(flagged & near_misses)}"
    assert planted - flagged == set(), f"missed: {sorted(planted - flagged)}"
    assert flagged - planted == set(), f"unexpected flags: {sorted(flagged - planted)}"


def test_duplicate_payments(check_result):
    assert_exact(check_result, "duplicate_payments")


def test_split_invoices(check_result, tiny_data):
    flagged, planted, near_misses = check_result("split_invoices")
    assert planted <= flagged, f"missed: {sorted(planted - flagged)}"
    assert not (flagged & near_misses)
    # Busy vendors produce legitimate false positives, so instead of an exact match
    # recompute the rule independently in pandas and require the same result.
    inv = pd.read_csv(tiny_data / "invoices.csv", parse_dates=["invoice_date"])
    small = inv[inv["amount_usd"] < 5000].copy()
    small["week_start"] = small["invoice_date"] - pd.to_timedelta(small["invoice_date"].dt.day_of_week, unit="D")
    groups = small.groupby(["vendor_id", "created_by", "week_start"])["amount_usd"]
    small["n"], small["total"] = groups.transform("size"), groups.transform("sum")
    expected = set(small.loc[(small["n"] >= 2) & (small["total"] > 5000), "invoice_id"])
    assert flagged == expected


def test_self_approval(check_result):
    assert_exact(check_result, "self_approval")
    flagged, _, _ = check_result("self_approval")
    assert any(i.startswith("INV") for i in flagged) and any(i.startswith("JE") for i in flagged)


def test_weekend_round_entries(check_result, tiny_data):
    assert_exact(check_result, "weekend_round_entries")
    # planted cases cover all three branches: weekend, before midnight, after midnight
    je = pd.read_csv(tiny_data / "journal_entries.csv", parse_dates=["entry_datetime"])
    planted = je[je["entry_id"].isin(check_result("weekend_round_entries")[1])]
    hours = planted["entry_datetime"].dt.hour
    assert (planted["entry_datetime"].dt.day_of_week >= 5).any()
    assert (hours >= 22).any() and (hours < 6).any()


def test_vendor_employee_match(check_result):
    assert_exact(check_result, "vendor_employee_match")


def test_inactive_vendor_payments(check_result, tiny_data):
    assert_exact(check_result, "inactive_vendor_payments")
    # boundary: some near-misses are paid on the inactive date itself (must not flag)
    pay = pd.read_csv(tiny_data / "payments.csv")
    ven = pd.read_csv(tiny_data / "vendors.csv")
    near = pay[pay["payment_id"].isin(check_result("inactive_vendor_payments")[2])].merge(ven, on="vendor_id")
    assert (near["payment_date"] == near["inactive_date"]).any()


def test_benford_first_digit(check_result):
    assert_exact(check_result, "benford_first_digit")


def test_all_data_inside_2025(tiny_data):
    je = pd.read_csv(tiny_data / "journal_entries.csv", parse_dates=["entry_datetime"])
    pay = pd.read_csv(tiny_data / "payments.csv", parse_dates=["payment_date"])
    inv = pd.read_csv(tiny_data / "invoices.csv", parse_dates=["invoice_date"])
    for series in (je["entry_datetime"], pay["payment_date"], inv["invoice_date"]):
        assert series.dt.year.eq(2025).all()
