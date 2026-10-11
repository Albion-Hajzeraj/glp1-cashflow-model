-- =============================================================================
-- 06_retention_by_channel.sql: retention curve by acquisition channel.
--   Same definition as 05_cohorts.sql. For each months-since-signup k, only cohorts
--   observable at k are pooled (eligible customers), so later points rest on fewer,
--   earlier cohorts. paid_share = share of retained customers paying (not paused).
-- =============================================================================
-- name: retention_by_channel
WITH cust AS (
    SELECT customer_id, channel, cohort_month,
           datediff('month', cohort_month, (SELECT max(month) FROM p_months)) AS max_k
    FROM customers_m
),
ks AS (SELECT unnest(range((SELECT count(*) FROM p_months))) AS k),
eligible AS (
    SELECT c.channel, ks.k, count(*) AS eligible_customers
    FROM cust c JOIN ks ON ks.k <= c.max_k
    GROUP BY 1, 2
),
act AS (
    SELECT c.channel, datediff('month', c.cohort_month, a.activity_month) AS k,
           count(*) AS retained, count(*) FILTER (WHERE a.paid) AS retained_paid
    FROM customer_month_activity a JOIN cust c USING (customer_id)
    GROUP BY 1, 2
)
SELECT e.channel, e.k AS months_since_signup, e.eligible_customers,
       coalesce(a.retained, 0)      AS retained,
       coalesce(a.retained_paid, 0) AS retained_paid,
       coalesce(a.retained, 0) / e.eligible_customers      AS retention_pct,
       coalesce(a.retained_paid, 0) / nullif(a.retained, 0) AS paid_share
FROM eligible e
LEFT JOIN act a USING (channel, k)
ORDER BY e.channel, e.k;

-- name: retention_by_channel_plan
-- Same curve split by channel x plan (feeds the forecast's plan-specific survival).
WITH cust AS (
    SELECT customer_id, channel, plan_type, cohort_month,
           datediff('month', cohort_month, (SELECT max(month) FROM p_months)) AS max_k
    FROM customers_m
),
ks AS (SELECT unnest(range((SELECT count(*) FROM p_months))) AS k),
eligible AS (
    SELECT c.channel, c.plan_type, ks.k, count(*) AS eligible_customers
    FROM cust c JOIN ks ON ks.k <= c.max_k
    GROUP BY 1, 2, 3
),
act AS (
    SELECT c.channel, c.plan_type, datediff('month', c.cohort_month, a.activity_month) AS k,
           count(*) AS retained
    FROM customer_month_activity a JOIN cust c USING (customer_id)
    GROUP BY 1, 2, 3
)
SELECT e.channel, e.plan_type, e.k AS months_since_signup, e.eligible_customers,
       coalesce(a.retained, 0) AS retained,
       coalesce(a.retained, 0) / e.eligible_customers AS retention_pct
FROM eligible e
LEFT JOIN act a USING (channel, plan_type, k)
ORDER BY e.channel, e.plan_type, e.k;

-- name: active_at_period_end
-- Customers active (paying or paused) in the last modeled month, by signup cohort x
-- channel x plan: the starting point of the forecast roll-forward.
SELECT strftime(c.cohort_month, '%Y-%m') AS cohort, c.channel, c.plan_type,
       count(*) AS active_customers,
       count(*) FILTER (WHERE a.paid) AS paying_customers
FROM customer_month_activity a
JOIN customers_m c USING (customer_id)
WHERE a.activity_month = (SELECT max(month) FROM p_months)
GROUP BY ALL
ORDER BY cohort, c.channel, c.plan_type;
