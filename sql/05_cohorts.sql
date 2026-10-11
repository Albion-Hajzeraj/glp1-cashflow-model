-- =============================================================================
-- 05_cohorts.sql: signup-cohort retention triangle, renewal rate, monthly churn.
--   Retained in month k = active in calendar month (signup month + k): a successful
--   payment covers that month (a prepaid charge covers 3 months) OR an active pause.
--   Reactivated customers stay in their original signup cohort.
--   Months after the last modeled month are not yet observable: NULL, not 0.
-- =============================================================================
-- name: cohort_retention_long
WITH sizes AS (
    SELECT cohort_month, count(*) AS cohort_size FROM customers_m GROUP BY 1
),
grid AS (
    SELECT s.cohort_month, s.cohort_size, ks.k
    FROM sizes s
    CROSS JOIN (SELECT unnest(range((SELECT count(*) FROM p_months))) AS k) ks
),
act AS (
    SELECT c.cohort_month,
           datediff('month', c.cohort_month, a.activity_month) AS k,
           count(*) AS retained,
           count(*) FILTER (WHERE a.paid) AS retained_paid
    FROM customer_month_activity a JOIN customers_m c USING (customer_id)
    GROUP BY 1, 2
),
obs AS (
    SELECT g.*,
           g.cohort_month + to_months(g.k) <= (SELECT max(month) FROM p_months) AS observable
    FROM grid g
)
SELECT strftime(o.cohort_month, '%Y-%m') AS cohort,
       o.k AS months_since_signup,
       o.cohort_size,
       CASE WHEN o.observable THEN coalesce(a.retained, 0) END      AS retained,
       CASE WHEN o.observable THEN coalesce(a.retained_paid, 0) END AS retained_paid,
       CASE WHEN o.observable THEN coalesce(a.retained, 0) / o.cohort_size END AS retention_pct
FROM obs o
LEFT JOIN act a USING (cohort_month, k)
ORDER BY cohort, months_since_signup;

-- name: cohort_retention
-- Triangle view: one row per cohort, one column per month since signup.
PIVOT cohort_retention_long
ON months_since_signup
USING first(retention_pct)
GROUP BY cohort, cohort_size
ORDER BY cohort;

-- name: renewal_churn
-- Monthly churn: customers active in month m−1 who are not active in m (cancelled or
--   failed payment; pauses count as active) / active in m−1.
-- Renewal rate: customers whose paid term ended in m−1 (renewal due in m) who paid
--   again in m / those due, excluding customers who paused instead.
WITH term_end AS (                    -- last service month of each paid order
    SELECT DISTINCT customer_id, service_month AS month
    FROM order_service_months WHERE term_offset = term_months - 1
),
due AS (
    SELECT (t.month + INTERVAL 1 MONTH)::DATE AS month, t.customer_id,
           coalesce(n.paid, FALSE)                             AS renewed,
           n.customer_id IS NOT NULL AND NOT n.paid            AS paused
    FROM term_end t
    LEFT JOIN customer_month_activity n
           ON n.customer_id = t.customer_id
          AND n.activity_month = t.month + INTERVAL 1 MONTH
),
churn AS (
    SELECT (p.activity_month + INTERVAL 1 MONTH)::DATE AS month,
           count(*) AS active_prev_month,
           count(*) FILTER (WHERE n.customer_id IS NULL) AS churned
    FROM customer_month_activity p
    LEFT JOIN customer_month_activity n
           ON n.customer_id = p.customer_id
          AND n.activity_month = p.activity_month + INTERVAL 1 MONTH
    GROUP BY 1
),
ren AS (
    SELECT month,
           count(*) FILTER (WHERE NOT paused) AS renewals_due,
           count(*) FILTER (WHERE renewed)    AS renewed
    FROM due GROUP BY 1
),
active AS (
    SELECT activity_month AS month, count(*) AS active_customers
    FROM customer_month_activity GROUP BY 1
)
SELECT strftime(m.month, '%Y-%m') AS month,
       a.active_customers,
       c.active_prev_month, c.churned,
       c.churned / nullif(c.active_prev_month, 0) AS monthly_churn,
       r.renewals_due, r.renewed,
       r.renewed / nullif(r.renewals_due, 0) AS renewal_rate
FROM p_months m
LEFT JOIN active a USING (month)
LEFT JOIN churn  c USING (month)
LEFT JOIN ren    r USING (month)
ORDER BY m.month;
