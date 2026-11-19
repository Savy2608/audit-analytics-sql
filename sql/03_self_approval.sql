-- Check 3: Self-approval
-- Risk:      segregation-of-duties failure; one person both records and approves a
--            transaction, so no second person ever reviews it.
-- Rule:      created_by = approved_by, on invoices and on journal entries.
-- Threshold: none; any single occurrence is a control failure.

WITH self_approved_invoices AS (
    SELECT
        invoice_id    AS record_id,
        'invoice'     AS record_type,
        created_by    AS employee_id,
        invoice_date  AS record_date,
        amount_usd
    FROM invoices
    WHERE created_by = approved_by
),

self_approved_journal_entries AS (
    SELECT
        entry_id              AS record_id,
        'journal_entry'       AS record_type,
        created_by            AS employee_id,
        entry_datetime::date  AS record_date,
        amount_usd
    FROM journal_entries
    WHERE created_by = approved_by
),

all_self_approvals AS (
    SELECT * FROM self_approved_invoices
    UNION ALL
    SELECT * FROM self_approved_journal_entries
)

SELECT
    s.record_id,
    s.record_type,
    s.employee_id,
    e.name        AS employee_name,
    e.department,
    e.role,
    s.record_date,
    s.amount_usd
FROM all_self_approvals s
JOIN employees e USING (employee_id)
ORDER BY s.record_type, s.record_date, s.record_id;
