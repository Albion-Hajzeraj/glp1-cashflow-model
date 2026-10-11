-- =============================================================================
-- 02_cash_flow.sql: operating cash flow by month on a cash basis (not accrual).
--   Receipts : card payouts by payout_date (T+2), minus refunds and chargebacks
--              by refund_date.
--   Payments : cash_payables by paid_date. Pharmacy (medication + dispensing) is
--              paid net-30 per shipment, ads at the end of the next month, other
--              variable costs and opex in the same month. Chargeback fees are
--              paid on the chargeback date.
-- =============================================================================
-- name: cash_flow
WITH receipts AS (
    SELECT date_trunc('month', payout_date)::DATE AS month, sum(amount) AS card_payouts
    FROM payments WHERE status IN ('success', 'recovered') GROUP BY 1
),
ret AS (
    SELECT refund_month AS month,
           sum(amount) FILTER (WHERE type = 'refund')     AS refunds_paid,
           sum(amount) FILTER (WHERE type = 'chargeback') AS chargebacks_paid,
           sum(fee) AS chargeback_fees_paid
    FROM refunds_m GROUP BY 1
),
pay AS (
    SELECT date_trunc('month', paid_date)::DATE AS month,
           sum(amount) FILTER (WHERE expense_type = 'pharmacy')   AS pharmacy_paid,
           sum(amount) FILTER (WHERE expense_type = 'marketing')  AS marketing_paid,
           sum(amount) FILTER (WHERE expense_type IN ('clinical_consult', 'shipping',
                               'packaging_fulfillment', 'payment_processing')) AS other_variable_paid,
           sum(amount) FILTER (WHERE expense_type = 'fixed_opex') AS fixed_opex_paid
    FROM cash_payables GROUP BY 1
),
cf AS (
    SELECT m.month,
           coalesce(r.card_payouts, 0)        AS card_payouts,
           coalesce(ret.refunds_paid, 0)      AS refunds_paid,
           coalesce(ret.chargebacks_paid, 0)  AS chargebacks_paid,
           coalesce(p.pharmacy_paid, 0)       AS pharmacy_paid,
           coalesce(p.marketing_paid, 0)      AS marketing_paid,
           coalesce(p.other_variable_paid, 0) + coalesce(ret.chargeback_fees_paid, 0) AS other_variable_paid,
           coalesce(p.fixed_opex_paid, 0)     AS fixed_opex_paid
    FROM p_months m
    LEFT JOIN receipts r USING (month) LEFT JOIN ret USING (month) LEFT JOIN pay p USING (month)
),
net AS (
    SELECT *,
           card_payouts - refunds_paid - chargebacks_paid AS cash_receipts,
           pharmacy_paid + marketing_paid + other_variable_paid + fixed_opex_paid AS cash_payments
    FROM cf
),
running AS (                          -- opening cash = opening balance + prior months' flows
    SELECT n.*,
           cash_receipts - cash_payments AS net_operating_cash_flow,
           p.opening_balance + coalesce(sum(cash_receipts - cash_payments) OVER (ORDER BY month
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS opening_cash
    FROM net n CROSS JOIN p_cash p
)
SELECT strftime(month, '%Y-%m') AS month,
       card_payouts, refunds_paid, chargebacks_paid, cash_receipts,
       pharmacy_paid, marketing_paid, other_variable_paid, fixed_opex_paid, cash_payments,
       net_operating_cash_flow,
       opening_cash,
       opening_cash + net_operating_cash_flow AS closing_cash
FROM running
ORDER BY month;

-- name: profit_to_cash_bridge
-- Explains net operating cash flow − operating profit. Each component is cash minus
-- accrual (positive = cash ahead of profit). The components sum exactly to the gap.
WITH billed AS (
    SELECT strftime(order_month, '%Y-%m') AS month, sum(net_price) AS billings
    FROM paid_orders GROUP BY 1
),
recognized AS (
    SELECT strftime(s.service_month, '%Y-%m') AS month, sum(o.net_price / s.term_months) AS recognized
    FROM order_service_months s JOIN paid_orders o USING (order_id) GROUP BY 1
)
SELECT p.month,
       p.operating_profit,
       c.net_operating_cash_flow,
       c.net_operating_cash_flow - p.operating_profit             AS gap,
       coalesce(b.billings, 0) - coalesce(r.recognized, 0)        AS prepaid_deferral,
       c.card_payouts - coalesce(b.billings, 0)                    AS payout_lag,
       p.marketing_spend - c.marketing_paid                        AS ad_payment_lag,
       p.medication + p.pharmacy_dispensing - c.pharmacy_paid      AS pharmacy_net30,
       p.clinical_consult + p.shipping + p.packaging_fulfillment + p.payment_processing
         + p.chargeback_fees - c.other_variable_paid               AS other_variable_timing,
       p.fixed_opex - c.fixed_opex_paid                            AS opex_timing
FROM monthly_pnl p
JOIN cash_flow c USING (month)
LEFT JOIN billed b     USING (month)
LEFT JOIN recognized r USING (month)
ORDER BY p.month;
