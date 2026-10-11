-- =============================================================================
-- 00_setup.sql: shared views used by every metric query (no output files).
--
-- Registered by src/run_metrics.py before this runs:
--   raw CSVs : customers, orders, payments, refunds, subscription_events,
--              order_costs, shipments, med_cost_monthly, marketing_spend,
--              fixed_opex, cash_payables
--   params   : p_months(month DATE)            modeled months (from assumptions.yaml)
--              p_plans(plan_type, term_months)  months of service per charge
--              p_channels(channel, paid)        paid media vs organic
--              p_cash(opening_balance)
--              p_unit_costs(...)                per-shipment cost rates (cents)
-- Months are DATEs on the 1st; outputs format them as 'YYYY-MM'.
-- =============================================================================

-- Orders whose payment succeeded (first try or recovered by dunning).
-- 'refunded' orders were paid and shipped, so they count as completed orders.
CREATE OR REPLACE VIEW paid_orders AS
SELECT o.*,
       date_trunc('month', o.order_date)::DATE AS order_month,
       p.term_months
FROM orders o
JOIN p_plans p USING (plan_type)
WHERE o.status <> 'failed';

-- One row per order per calendar month of service it covers (prepaid = 3 rows).
-- Basis for ratable revenue recognition and for spreading the order's variable costs.
CREATE OR REPLACE VIEW order_service_months AS
SELECT order_id, customer_id, plan_type, term_months, order_month,
       k AS term_offset,
       (order_month + to_months(k))::DATE AS service_month
FROM (SELECT *, unnest(range(term_months)) AS k FROM paid_orders);

-- Refunds and chargebacks by the month the money left (refund_date).
CREATE OR REPLACE VIEW refunds_m AS
SELECT *, date_trunc('month', refund_date)::DATE AS refund_month FROM refunds;

-- Pause intervals: a pause lasts until the next reactivate/cancel (open = still paused).
CREATE OR REPLACE VIEW pause_intervals AS
SELECT p.customer_id,
       p.event_date AS pause_date,
       (SELECT min(e.event_date) FROM subscription_events e
         WHERE e.customer_id = p.customer_id
           AND e.event_type IN ('reactivate', 'cancel')
           AND e.event_date > p.event_date) AS end_date
FROM subscription_events p
WHERE p.event_type = 'pause';

-- Customer x month activity: active if a paid order covers the month (prepaid covers 3)
-- OR the customer is in an active pause. paid = TRUE when a payment covers the month.
CREATE OR REPLACE VIEW customer_month_activity AS
WITH paused AS (
    SELECT customer_id,
           unnest(generate_series(
               date_trunc('month', pause_date),
               coalesce(date_trunc('month', end_date) - INTERVAL 1 MONTH,
                        (SELECT max(month) FROM p_months)),
               INTERVAL 1 MONTH))::DATE AS activity_month
    FROM pause_intervals
),
spells AS (
    SELECT customer_id, service_month AS activity_month, TRUE AS paid FROM order_service_months
    UNION ALL
    SELECT customer_id, activity_month, FALSE FROM paused
)
SELECT customer_id, activity_month, bool_or(paid) AS paid
FROM spells
WHERE activity_month BETWEEN (SELECT min(month) FROM p_months) AND (SELECT max(month) FROM p_months)
GROUP BY ALL;

CREATE OR REPLACE VIEW customers_m AS
SELECT *, date_trunc('month', signup_date)::DATE AS cohort_month FROM customers;
