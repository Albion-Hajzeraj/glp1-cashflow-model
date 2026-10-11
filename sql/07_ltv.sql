-- =============================================================================
-- 07_ltv.sql: observed LTV to date per acquired customer (no projection).
--   Revenue LTV      = (net charges − refunds − chargebacks) / customers acquired
--   Contribution LTV = (revenue − variable costs − chargeback fees) / customers acquired
--   Cash/billing basis through the last modeled month. A prepaid order carries its
--   full charge and all 3 months of costs. Older cohorts have had more months to
--   earn, so compare cohorts at equal age (months_observed).
-- =============================================================================
-- name: ltv_observed
WITH order_totals AS (
    SELECT o.customer_id, sum(o.net_price) AS net_charges, sum(oc.total_variable_cost) AS var_cost
    FROM paid_orders o JOIN order_costs oc USING (order_id)
    GROUP BY 1
),
refund_totals AS (
    SELECT o.customer_id, sum(r.amount) AS refunded, sum(r.fee) AS fees
    FROM refunds r JOIN orders o USING (order_id)
    GROUP BY 1
),
per_customer AS (
    SELECT c.customer_id, c.channel, c.cohort_month,
           coalesce(o.net_charges, 0) - coalesce(r.refunded, 0) AS revenue,
           coalesce(o.var_cost, 0) + coalesce(r.fees, 0)        AS variable_cost
    FROM customers_m c
    LEFT JOIN order_totals o  USING (customer_id)
    LEFT JOIN refund_totals r USING (customer_id)
)
SELECT coalesce(strftime(cohort_month, '%Y-%m'), 'ALL') AS cohort,
       coalesce(channel, 'ALL') AS channel,
       CASE WHEN cohort_month IS NOT NULL
            THEN datediff('month', cohort_month, (SELECT max(month) FROM p_months)) + 1 END AS months_observed,
       count(*) AS customers_acquired,
       sum(revenue) / count(*)                 AS revenue_ltv_to_date,
       sum(revenue - variable_cost) / count(*) AS contribution_ltv_to_date
FROM per_customer
GROUP BY GROUPING SETS ((cohort_month, channel), (cohort_month), (channel))
ORDER BY cohort_month NULLS LAST, channel NULLS LAST;

-- name: first_order_contribution
-- First order only, per channel: net charge − refund/chargeback − variable costs − fees.
-- A prepaid first order includes 3 months of medication, shipments and consults.
SELECT c.channel,
       count(*) AS first_orders,
       avg(o.net_price)                                 AS first_order_net_charge,
       avg(o.net_price - coalesce(r.amount, 0))         AS first_order_net_revenue,
       avg(oc.total_variable_cost + coalesce(r.fee, 0)) AS first_order_variable_cost,
       avg(o.net_price - coalesce(r.amount, 0) - oc.total_variable_cost - coalesce(r.fee, 0))
                                                        AS first_order_contribution
FROM paid_orders o
JOIN customers c USING (customer_id)
JOIN order_costs oc USING (order_id)
LEFT JOIN refunds r USING (order_id)
WHERE o.order_number = 1
GROUP BY c.channel
ORDER BY c.channel;
