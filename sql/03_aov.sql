-- =============================================================================
-- 03_aov.sql: average order value by month.
--   AOV = net revenue (as in monthly_pnl) / completed orders in the month.
--   First/repeat AOV split net revenue by the order it came from: recognized revenue
--   of first orders (order_number = 1) vs renewals/win-backs, each less its own
--   refunds and chargebacks. So first + repeat revenue = net revenue.
--   Because prepaid revenue is recognized over 3 months, a month's revenue includes
--   prepaid orders billed in earlier months. avg_net_charge_per_order shows the plain
--   average ticket (net charge after promo per completed order) for comparison.
-- =============================================================================
-- name: aov
WITH rev_lines AS (
    SELECT s.service_month AS month, o.order_number = 1 AS is_first,
           o.net_price / s.term_months AS amount
    FROM order_service_months s JOIN paid_orders o USING (order_id)
    UNION ALL
    SELECT r.refund_month, o.order_number = 1, -r.amount
    FROM refunds_m r JOIN orders o USING (order_id)
),
rev AS (
    SELECT month,
           sum(amount)                             AS net_revenue,
           sum(amount) FILTER (WHERE is_first)     AS first_order_revenue,
           sum(amount) FILTER (WHERE NOT is_first) AS repeat_order_revenue
    FROM rev_lines GROUP BY 1
),
cnt AS (
    SELECT order_month AS month,
           count(*)                                 AS completed_orders,
           count(*) FILTER (WHERE order_number = 1) AS first_orders,
           count(*) FILTER (WHERE order_number > 1) AS repeat_orders,
           sum(net_price)                           AS net_charges
    FROM paid_orders GROUP BY 1
)
SELECT strftime(m.month, '%Y-%m') AS month,
       c.completed_orders,
       r.net_revenue,
       r.net_revenue / nullif(c.completed_orders, 0)            AS aov_overall,
       c.first_orders,
       r.first_order_revenue / nullif(c.first_orders, 0)        AS aov_first_order,
       c.repeat_orders,
       r.repeat_order_revenue / nullif(c.repeat_orders, 0)      AS aov_repeat_order,
       c.net_charges / nullif(c.completed_orders, 0)            AS avg_net_charge_per_order
FROM p_months m
LEFT JOIN rev r USING (month)
LEFT JOIN cnt c USING (month)
ORDER BY m.month;
