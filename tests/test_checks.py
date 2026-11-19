"""Each check must find all of its planted cases and none of its near-misses."""


def assert_catches_planted_and_ignores_near_misses(check_result, name):
    flagged, planted, near_misses = check_result(name)
    assert planted, f"no planted cases for {name}"
    assert planted <= flagged, f"missed: {sorted(planted - flagged)}"
    assert not (flagged & near_misses), f"flagged near-misses: {sorted(flagged & near_misses)}"


def test_duplicate_payments(check_result):
    assert_catches_planted_and_ignores_near_misses(check_result, "duplicate_payments")


def test_split_invoices(check_result):
    assert_catches_planted_and_ignores_near_misses(check_result, "split_invoices")


def test_self_approval(check_result):
    flagged, planted, _ = check_result("self_approval")
    assert planted <= flagged
    # both tables must be covered
    assert any(i.startswith("INV") for i in flagged)
    assert any(i.startswith("JE") for i in flagged)


def test_weekend_round_entries(check_result):
    assert_catches_planted_and_ignores_near_misses(check_result, "weekend_round_entries")


def test_vendor_employee_match(check_result):
    assert_catches_planted_and_ignores_near_misses(check_result, "vendor_employee_match")


def test_inactive_vendor_payments(check_result):
    assert_catches_planted_and_ignores_near_misses(check_result, "inactive_vendor_payments")


def test_benford_first_digit(check_result):
    flagged, planted, _ = check_result("benford_first_digit")
    assert planted <= flagged
