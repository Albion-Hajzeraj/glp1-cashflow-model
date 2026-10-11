-- =============================================================================
-- 01_monthly_pnl.sql: accrual P&L by month (definitions in docs/project_notes.md).
--   Net revenue = gross − discounts − refunds − chargebacks. No sales tax is charged
--   in this dataset, so all revenue is excl. tax.
--   Prepaid charges are recognized ratably over their 3-month term.
--   Shipment costs are accrued in the month each shipment goes out (shipments.csv):
--     medication = base cost of the ship month x dose for the patient's tenure
--     (med_cost_monthly), plus per-shipment rates (p_unit_costs). This is the same
--     basis the pharmacy invoices on, so accruals tie exactly to cash_payables.
--   Payment processing is expensed when the charge happens. Refunds, chargebacks
--   and chargeback fees are booked in the month they occur.
-- =============================================================================
-- name: monthly_pnl
WITH rev AS (                         -- recognized revenue by service month
    SELECT s.service_month AS month,
           sum(o.gross_price / s.term_months) AS gross_revenue,
           sum(o.discount    / s.term_months) AS discounts
    FROM order_service_months s JOIN paid_orders o USING (order_id)
    GROUP BY 1
),
ret AS (                              -- refunds / chargebacks (contra-revenue) + fees
    SELECT refund_month AS month,
           sum(amount) FILTER (WHERE type = 'refund')     AS refunds,
           sum(amount) FILTER (WHERE type = 'chargeback') AS chargebacks,
           sum(fee) AS chargeback_fees
    FROM refunds_m GROUP BY 1
),
vc AS (                               -- shipment costs, accrued in the month shipped
    SELECT date_trunc('month', s.ship_date)::DATE AS month,
           sum(m.base_med_cost * m.dose_multiplier) AS medication,
           sum(u.pharmacy_dispensing)                AS pharmacy_dispensing,
           sum(CASE WHEN s.tenure_month = 1 THEN u.consult_initial
                    ELSE u.consult_follow_up END)    AS clinical_consult,
           sum(u.shipping)                           AS shipping,
           sum(u.packaging_fulfillment)              AS packaging_fulfillment
    FROM shipments s
    JOIN med_cost_monthly m
      ON m.month = strftime(s.ship_date, '%Y-%m')
     AND m.tenure_month = least(s.tenure_month, (SELECT max(tenure_month) FROM med_cost_monthly))
    CROSS JOIN p_unit_costs u
    GROUP BY 1
),
proc AS (                             -- processing fees + order counts by charge month
    SELECT o.order_month AS month,
           count(*) AS completed_orders,
           sum(c.payment_processing) AS payment_processing
    FROM paid_orders o JOIN order_costs c USING (order_id)
    GROUP BY 1
),
mkt AS (SELECT CAST(month || '-01' AS DATE) AS month, sum(spend) AS marketing_spend
        FROM marketing_spend GROUP BY 1),
opex AS (SELECT CAST(month || '-01' AS DATE) AS month, sum(amount) AS fixed_opex
         FROM fixed_opex GROUP BY 1),
pnl AS (
    SELECT m.month,
           coalesce(rev.gross_revenue, 0)        AS gross_revenue,
           coalesce(rev.discounts, 0)            AS discounts,
           coalesce(ret.refunds, 0)              AS refunds,
           coalesce(ret.chargebacks, 0)          AS chargebacks,
           coalesce(proc.completed_orders, 0)    AS completed_orders,
           coalesce(vc.medication, 0)            AS medication,
           coalesce(vc.pharmacy_dispensing, 0)   AS pharmacy_dispensing,
           coalesce(vc.clinical_consult, 0)      AS clinical_consult,
           coalesce(vc.shipping, 0)              AS shipping,
           coalesce(vc.packaging_fulfillment, 0) AS packaging_fulfillment,
           coalesce(proc.payment_processing, 0)  AS payment_processing,
           coalesce(ret.chargeback_fees, 0)      AS chargeback_fees,
           coalesce(mkt.marketing_spend, 0)      AS marketing_spend,
           coalesce(opex.fixed_opex, 0)          AS fixed_opex
    FROM p_months m
    LEFT JOIN rev  USING (month) LEFT JOIN ret USING (month) LEFT JOIN vc   USING (month)
    LEFT JOIN proc USING (month) LEFT JOIN mkt USING (month) LEFT JOIN opex USING (month)
)
SELECT strftime(month, '%Y-%m') AS month,
       gross_revenue, discounts, refunds, chargebacks,
       gross_revenue - discounts - refunds - chargebacks AS net_revenue,
       completed_orders,
       medication, pharmacy_dispensing, clinical_consult, shipping,
       packaging_fulfillment, payment_processing, chargeback_fees,
       medication + pharmacy_dispensing + clinical_consult + shipping
         + packaging_fulfillment + payment_processing + chargeback_fees AS total_variable_cost,
       net_revenue - total_variable_cost                  AS contribution_profit,
       contribution_profit / nullif(net_revenue, 0)       AS contribution_margin_pct,
       marketing_spend, fixed_opex,
       contribution_profit - marketing_spend - fixed_opex AS operating_profit
FROM pnl
ORDER BY month;
