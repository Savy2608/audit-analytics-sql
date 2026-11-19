-- Check 4: Weekend or late-night entries with round amounts
-- Risk:      manual entries outside the normal process, posted off-hours with round numbers.
-- Rule:      (Saturday/Sunday OR 22:00-05:59) AND amount is a whole multiple of 1,000 USD.
--            Both conditions must hold; the source label is not trusted.
-- Threshold: business hours 06:00-21:59; "round" = multiple of 1,000.00.

WITH entry_timing AS (
    SELECT
        entry_id,
        entry_datetime,
        account,
        amount_usd,
        source,
        created_by,
        approved_by,
        description,
        EXTRACT(dow  FROM entry_datetime) AS day_of_week,   -- 0 = Sunday, 6 = Saturday
        EXTRACT(hour FROM entry_datetime) AS hour_of_day
    FROM journal_entries
),

flagged_entries AS (
    SELECT
        *,
        CASE WHEN day_of_week IN (0, 6) THEN 'weekend' ELSE 'late night' END AS timing
    FROM entry_timing
    WHERE (day_of_week IN (0, 6) OR hour_of_day >= 22 OR hour_of_day < 6)
      AND amount_usd >= 1000
      AND amount_usd % 1000 = 0
)

SELECT
    entry_id AS record_id,
    entry_datetime,
    timing,
    account,
    amount_usd,
    source,
    created_by,
    approved_by,
    description
FROM flagged_entries
ORDER BY entry_datetime, entry_id;
