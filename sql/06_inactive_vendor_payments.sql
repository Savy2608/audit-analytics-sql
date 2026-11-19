-- Check 6: Payments to inactive vendors
-- Risk:      money is paid to a vendor that should already be closed (stale master data,
--            or a dormant vendor record reused to divert funds).
-- Rule:      payment_date is strictly after the vendor's inactive_date.
-- Threshold: 0 days of grace; a payment on the inactive date itself is not flagged.

WITH inactive_vendors AS (
    SELECT vendor_id, name, inactive_date
    FROM vendors
    WHERE status = 'inactive'
),

payments_after_closing AS (
    SELECT
        p.payment_id,
        p.invoice_id,
        p.vendor_id,
        iv.name AS vendor_name,
        iv.inactive_date,
        p.payment_date,
        p.payment_date - iv.inactive_date AS days_after_inactive,
        p.amount_usd
    FROM payments p
    JOIN inactive_vendors iv USING (vendor_id)
    WHERE p.payment_date > iv.inactive_date
)

SELECT
    payment_id AS record_id,
    invoice_id,
    vendor_id,
    vendor_name,
    inactive_date,
    payment_date,
    days_after_inactive,
    amount_usd
FROM payments_after_closing
ORDER BY payment_date, payment_id;
