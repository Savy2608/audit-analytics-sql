-- Check 5: Vendor-employee match
-- Risk:      an employee sets up a fictitious vendor and routes payments to themselves.
-- Rule:      a vendor shares its address or its bank account with an employee.
--            Addresses are compared after lower-casing and collapsing whitespace.
-- Threshold: exact match after normalisation (no fuzzy matching).

WITH normalised_vendors AS (
    SELECT
        vendor_id,
        name,
        bank_account,
        lower(trim(regexp_replace(address, '\s+', ' ', 'g'))) AS address_key
    FROM vendors
),

normalised_employees AS (
    SELECT
        employee_id,
        name,
        department,
        bank_account,
        lower(trim(regexp_replace(address, '\s+', ' ', 'g'))) AS address_key
    FROM employees
),

matches AS (
    SELECT
        v.vendor_id,
        v.name AS vendor_name,
        e.employee_id,
        e.name AS employee_name,
        e.department,
        CASE
            WHEN v.address_key = e.address_key AND v.bank_account = e.bank_account THEN 'address and bank account'
            WHEN v.address_key = e.address_key THEN 'address'
            ELSE 'bank account'
        END AS matched_on
    FROM normalised_vendors v
    JOIN normalised_employees e
      ON v.address_key = e.address_key
      OR v.bank_account = e.bank_account
)

SELECT
    m.vendor_id AS record_id,
    m.vendor_name,
    m.employee_id,
    m.employee_name,
    m.department,
    m.matched_on,
    (SELECT count(*) FROM payments p WHERE p.vendor_id = m.vendor_id)                    AS payments_to_vendor,
    (SELECT coalesce(sum(amount_usd), 0) FROM payments p WHERE p.vendor_id = m.vendor_id) AS paid_to_vendor_usd
FROM matches m
ORDER BY m.vendor_id;
