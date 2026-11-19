-- Check 2: Split invoices
-- Risk:      one person splits a purchase into several smaller invoices so that each one
--            stays under the 5,000 USD limit and avoids manager approval.
-- Rule:      2+ invoices from the same vendor, entered by the same employee in the same
--            calendar week (Mon-Sun), each under 5,000 USD, together over 5,000 USD.
-- Threshold: 5,000 USD approval limit; grouping window = one calendar week.

WITH invoices_under_limit AS (
    SELECT
        invoice_id,
        vendor_id,
        created_by,
        approved_by,
        invoice_date,
        amount_usd,
        date_trunc('week', invoice_date)::date AS week_start
    FROM invoices
    WHERE amount_usd < 5000
),

weekly_groups AS (
    SELECT
        *,
        COUNT(*)        OVER same_vendor_clerk_week AS invoices_in_group,
        SUM(amount_usd) OVER same_vendor_clerk_week AS group_total_usd
    FROM invoices_under_limit
    WINDOW same_vendor_clerk_week AS (PARTITION BY vendor_id, created_by, week_start)
)

SELECT
    g.invoice_id AS record_id,
    g.vendor_id,
    v.name AS vendor_name,
    g.created_by,
    g.approved_by,
    g.week_start,
    g.invoice_date,
    g.amount_usd,
    g.invoices_in_group,
    g.group_total_usd
FROM weekly_groups g
JOIN vendors v USING (vendor_id)
WHERE g.invoices_in_group >= 2
  AND g.group_total_usd > 5000
ORDER BY g.week_start, g.vendor_id, g.invoice_date, g.invoice_id;
