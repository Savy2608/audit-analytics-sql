-- Check 1: Duplicate payments
-- Risk:      the same bill is paid twice (re-keyed invoice, double run of a payment batch).
-- Rule:      two payments to the same vendor for exactly the same amount within 7 days.
-- Threshold: 7 days. Recurring monthly bills of the same amount are ~30 days apart and pass.
-- Output:    the later payment of each pair (the one that should not have happened).

WITH ordered_payments AS (
    SELECT
        payment_id,
        invoice_id,
        vendor_id,
        amount_usd,
        payment_date,
        LAG(payment_id)   OVER same_vendor_and_amount AS previous_payment_id,
        LAG(payment_date) OVER same_vendor_and_amount AS previous_payment_date
    FROM payments
    WINDOW same_vendor_and_amount AS (
        PARTITION BY vendor_id, amount_usd
        ORDER BY payment_date, payment_id
    )
),

suspected_duplicates AS (
    SELECT *,
           payment_date - previous_payment_date AS days_since_previous
    FROM ordered_payments
    WHERE previous_payment_date IS NOT NULL
      AND payment_date - previous_payment_date <= 7
)

SELECT
    d.payment_id AS record_id,
    d.vendor_id,
    v.name AS vendor_name,
    d.amount_usd,
    d.payment_date,
    d.previous_payment_id,
    d.previous_payment_date,
    d.days_since_previous
FROM suspected_duplicates d
JOIN vendors v USING (vendor_id)
ORDER BY d.payment_date, d.payment_id;
