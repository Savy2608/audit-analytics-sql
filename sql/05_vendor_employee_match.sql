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

-- Two equality joins instead of one join on "address OR bank account":
-- an OR condition forces a nested loop, equality joins can use hash joins.
address_matches AS (
    SELECT v.vendor_id, e.employee_id, 'address' AS matched_on
    FROM normalised_vendors v
    JOIN normalised_employees e ON v.address_key = e.address_key
),

bank_account_matches AS (
    SELECT v.vendor_id, e.employee_id, 'bank account' AS matched_on
    FROM normalised_vendors v
    JOIN normalised_employees e ON v.bank_account = e.bank_account
),

matches AS (
    SELECT
        vendor_id,
        employee_id,
        string_agg(matched_on, ' and ' ORDER BY matched_on) AS matched_on
    FROM (
        SELECT * FROM address_matches
        UNION ALL
        SELECT * FROM bank_account_matches
    ) all_matches
    GROUP BY vendor_id, employee_id
),

payments_to_matched_vendors AS (
    SELECT p.vendor_id, count(*) AS payments_to_vendor, sum(p.amount_usd) AS paid_to_vendor_usd
    FROM payments p
    WHERE p.vendor_id IN (SELECT vendor_id FROM matches)
    GROUP BY p.vendor_id
)

SELECT
    m.vendor_id   AS record_id,
    v.name        AS vendor_name,
    m.employee_id,
    e.name        AS employee_name,
    e.department,
    m.matched_on,
    coalesce(p.payments_to_vendor, 0)   AS payments_to_vendor,
    coalesce(p.paid_to_vendor_usd, 0)   AS paid_to_vendor_usd
FROM matches m
JOIN vendors v   USING (vendor_id)
JOIN employees e USING (employee_id)
LEFT JOIN payments_to_matched_vendors p USING (vendor_id)
ORDER BY m.vendor_id;
