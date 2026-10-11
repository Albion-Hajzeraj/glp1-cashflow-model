-- =============================================================================
-- 04_acquisition.sql: spend, new paying customers and CPA by month x channel.
--   New paying customer = first order paid (order_number = 1, not failed).
--   Channel CPA   = channel spend / channel new paying customers (same month).
--   Blended CPA   = ALL spend / ALL new paying customers, organic included.
--   Paid-only CPA = paid-media spend / new customers from paid media only. The gap
--   to blended CPA shows how much cheap organic volume flatters the blended number.
--   Extra rows per month: channel = 'PAID_ONLY' and 'ALL_BLENDED'.
-- =============================================================================
-- name: acquisition
WITH new_cust AS (
    SELECT c.cohort_month AS month, c.channel, count(*) AS new_paying_customers
    FROM customers_m c
    JOIN paid_orders o ON o.customer_id = c.customer_id AND o.order_number = 1
    GROUP BY 1, 2
),
spend AS (
    SELECT CAST(month || '-01' AS DATE) AS month, channel, spend FROM marketing_spend
),
by_channel AS (
    SELECT s.month, s.channel, ch.paid, s.spend,
           coalesce(n.new_paying_customers, 0) AS new_paying_customers
    FROM spend s
    JOIN p_channels ch USING (channel)
    LEFT JOIN new_cust n USING (month, channel)
),
rolled AS (
    SELECT month, channel, spend, new_paying_customers FROM by_channel
    UNION ALL
    SELECT month, 'PAID_ONLY', sum(spend), sum(new_paying_customers) FROM by_channel WHERE paid GROUP BY month
    UNION ALL
    SELECT month, 'ALL_BLENDED', sum(spend), sum(new_paying_customers) FROM by_channel GROUP BY month
)
SELECT strftime(month, '%Y-%m') AS month, channel, spend, new_paying_customers,
       spend / nullif(new_paying_customers, 0) AS cpa
FROM rolled
ORDER BY month, CASE channel WHEN 'ALL_BLENDED' THEN 2 WHEN 'PAID_ONLY' THEN 1 ELSE 0 END, channel;

-- name: acquisition_period
-- Same metrics over the whole period, one row per channel plus the two rollups.
SELECT channel, sum(spend) AS spend, sum(new_paying_customers) AS new_paying_customers,
       sum(spend) / nullif(sum(new_paying_customers), 0) AS cpa
FROM acquisition
GROUP BY channel
ORDER BY CASE channel WHEN 'ALL_BLENDED' THEN 2 WHEN 'PAID_ONLY' THEN 1 ELSE 0 END, channel;
