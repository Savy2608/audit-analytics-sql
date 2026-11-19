-- Check 7: Benford first-digit test per employee
-- Risk:      invented amounts miss Benford's first-digit pattern, P(d) = log10(1 + 1/d).
-- Rule:      per employee with 100+ manual journal entries, chi-square of observed vs
--            expected first-digit counts (8 degrees of freedom).
-- Threshold: chi-square > 20.09 (p < 0.01). Flags a population (employee), not single entries.

WITH entry_first_digits AS (
    SELECT
        created_by AS employee_id,
        -- '0.07' -> '7', '1534.20' -> '1'
        substring(ltrim(amount_usd::text, '0.') FROM 1 FOR 1)::int AS first_digit
    FROM journal_entries
    WHERE source = 'manual'
      AND amount_usd > 0
),

employee_totals AS (
    SELECT employee_id, count(*) AS n_entries
    FROM entry_first_digits
    GROUP BY employee_id
    HAVING count(*) >= 100
),

observed AS (
    SELECT employee_id, first_digit, count(*) AS observed_count
    FROM entry_first_digits
    GROUP BY employee_id, first_digit
),

expected AS (
    SELECT
        t.employee_id,
        t.n_entries,
        d.digit AS first_digit,
        t.n_entries * log(1 + 1.0 / d.digit) AS expected_count
    FROM employee_totals t
    CROSS JOIN generate_series(1, 9) AS d(digit)
),

chi_square_by_employee AS (
    SELECT
        e.employee_id,
        e.n_entries,
        round(sum((coalesce(o.observed_count, 0) - e.expected_count) ^ 2 / e.expected_count), 2) AS chi_square,
        round(sum(coalesce(o.observed_count, 0)) FILTER (WHERE e.first_digit >= 5) / e.n_entries, 3)
            AS share_digits_5_to_9
    FROM expected e
    LEFT JOIN observed o USING (employee_id, first_digit)
    GROUP BY e.employee_id, e.n_entries
)

SELECT
    c.employee_id AS record_id,
    emp.name      AS employee_name,
    emp.department,
    c.n_entries,
    c.chi_square,
    c.share_digits_5_to_9,
    0.301 AS benford_share_digits_5_to_9
FROM chi_square_by_employee c
JOIN employees emp USING (employee_id)
WHERE c.chi_square > 20.09
ORDER BY c.chi_square DESC;
