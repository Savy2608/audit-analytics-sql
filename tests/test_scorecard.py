"""Scorecard math: precision, recall and counts."""

import run_checks


def test_score_counts_and_ratios():
    result = run_checks.score(flagged_ids=["a", "b", "c", "x"], planted_ids=["a", "b", "c", "d", "e"],
                              near_miss_ids=["x"])
    assert result == {
        "planted": 5, "flagged": 4, "true_positives": 3, "false_positives": 1,
        "missed": 2, "precision": 0.75, "recall": 0.6, "near_misses_flagged": 1,
    }


def test_score_ignores_duplicate_ids():
    result = run_checks.score(["a", "a", "b"], ["a", "b"])
    assert result["flagged"] == 2 and result["precision"] == 1.0 and result["recall"] == 1.0


def test_score_with_nothing_flagged():
    result = run_checks.score([], ["a"])
    assert result["precision"] is None and result["recall"] == 0.0 and result["missed"] == 1


def test_full_scorecard_on_tiny_data(db, tiny_data):
    scorecard, outputs = run_checks.run_all_checks(db, tiny_data)
    assert list(scorecard["check_name"]) == [
        "duplicate_payments", "split_invoices", "self_approval", "weekend_round_entries",
        "vendor_employee_match", "inactive_vendor_payments", "benford_first_digit"]
    assert (scorecard["true_positives"] + scorecard["missed"] == scorecard["planted"]).all()
    assert (scorecard["true_positives"] + scorecard["false_positives"] == scorecard["flagged"]).all()
    assert all("record_id" in df.columns for df in outputs.values())
