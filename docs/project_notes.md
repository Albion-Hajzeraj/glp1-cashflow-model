# Project notes

## Business context
Financial analytics case study. The business is a **hypothetical** US telehealth company selling GLP-1 weight-management subscriptions (clinician consult + compounded medication shipped monthly). All data is **synthetic**, generated from `assumptions.yaml`. Period: **Jan–Sep 2026**.

**Goal:** build a plan to **double monthly net operating cash flow**.

- Plans: monthly $349 (70% of new customers) and 3-month prepaid $897 (30%). 20% first-charge promo on the monthly plan for meta, tiktok and affiliate.
- Channels: meta, google, tiktok, affiliate, organic_referral.
- Main levers: channel mix and CPA, retention (churn, failed-payment recovery, reactivation), medication cost (new pharmacy contract Jul 2026), plan mix (prepaid pulls cash forward), working-capital timing.

## Rules
- No new installs: use only the packages in `requirements.txt` (pandas, numpy, duckdb, openpyxl, matplotlib, pyyaml, pytest). LibreOffice is used only to recalculate the workbook for tests and summaries.

## Repo layout
- `assumptions.yaml`: the only source of input parameters. Never hard-code numbers in `src/`.
- `src/`: Python code (pandas / numpy / duckdb).
- `data/`: generated synthetic data. `output/`: tables, charts, Excel. `tests/`: pytest. `docs/`: write-ups.
- `python src/unit_economics.py`: prints a one-customer unit-economics check per channel.
- `python src/generate_data.py`: rebuilds all CSVs in `data/` (seeded, deterministic) and runs sanity checks.
  - `order_costs.csv` is one row per paid order and the only table storing medication cost. A prepaid order includes all 3 months of shipments (`shipments` column), even those after period end.
  - `shipments.csv` holds shipment timing only (no costs). It and `cash_payables.csv` are a period-end snapshot: nothing dated after Sep 30.
  - P&L shipment costs are accrued in the month shipped (med_cost_monthly × dose + per-shipment rates). This ties to pharmacy payables to the cent.
- `python src/run_metrics.py`: runs `sql/*.sql` in DuckDB in name order, writes `output/metrics/<name>.csv`, runs `src/ltv_projection.py` and prints a summary.
  - `00_setup.sql` holds the shared views. Every `-- name: x` block in the other files is one output and becomes a view later files can use.
  - SQL numbers come from parameter tables (`p_months`, `p_plans`, `p_channels`, `p_cash`) built from `assumptions.yaml`.
  - Retention = a payment covers the month (a prepaid charge covers 3 months) OR an active pause.
- `python src/build_workbook.py`: builds `output/omzo_model.xlsx` (Excel + Google Sheets).
  - Data_* sheets hold volumes; Inputs prices them through live formulas.
  - Only SUM/SUMIFS/COUNTIFS/INDEX/MATCH/IF/IFERROR/ROUND/MIN/MAX are used, with no named ranges.
  - `tests/test_workbook.py` recalculates it with LibreOffice headless and compares it with output/metrics (< $1).
- Forecast & plan (`src/forecast_sheets.py`, called by `build_workbook.py`):
  - Sheets: Forecast (follows the Inputs scenario switch), Levers, Scenarios, Doubling_Bridge.
  - Engine sheets at the end of the workbook: Eng_Base, Eng_Down, Eng_Bridge0-7. They share one layout; bridge step i has levers 1..i on, and step 8 = Forecast.
  - Baseline = Sep 2026 net operating cash flow; target = 2x by Jun 2027.
  - `python src/build_workbook.py` also recalculates the workbook in LibreOffice and prints the plan summary.
- Reporting (matplotlib only): `python src/charts.py` writes `output/charts/*.png` and `output/dashboard.pdf`; `python src/retention_report.py` writes `output/retention_report.pdf`.
  - Both read output/metrics, including the forecast CSVs that `build_workbook.py` exports after recalculation.
  - Files are overwritten each run, never versioned.
- `python -m pytest`: reconciliation tests (`tests/test_reconcile.py`). They regenerate the metrics first, and include a slow determinism test. Fixes found by the tests are logged in `docs/ai_log.md`.

## Metric definitions
- **Net revenue**: gross charges − promo discounts − refunds − chargebacks. Prepaid charges are recognized ratably over 3 months; cash is collected up front.
- **AOV**: net revenue ÷ completed orders in the month (first/repeat split net revenue by originating order). `avg_net_charge_per_order` is the plain ticket average.
- **Channel CPA**: channel marketing spend ÷ new paying customers from that channel in the same month.
- **Blended CPA**: total marketing spend (all channels, organic_referral included) ÷ total new paying customers.
- **Contribution**: net revenue − all variable costs (medication, dispensing, consults, shipping, packaging/fulfillment, payment processing, chargeback fees). Excludes marketing and fixed opex.
- **Operating profit**: contribution − marketing spend − fixed opex.
- **Net operating cash flow**: operating cash receipts − operating cash payments in the month, after timing (card payouts T+2, pharmacy net-30, ad spend paid next month, refund/chargeback lags). Excludes financing and capex.
- **Retention**: share of a starting cohort still active (paying or paused) N months after their first order.
- **Churn**: share of customers active at the start of a month who cancel (voluntary) or fail payment without recovery (involuntary) during that month.
- **LTV**: expected cumulative contribution per new customer over a stated horizon (default 24 months), before CPA. LTV ÷ CPA and payback months are compared against CPA.

## Cost categories (each cost belongs to exactly one bucket)
| Category | Costs | Notes |
|---|---|---|
| **COGS: medication** | Drug cost per monthly supply × dose/tenure multiplier | **The only place drug cost is counted.** The dispensing fee excludes drug cost. |
| **Variable: fulfillment** | Pharmacy dispensing fee, shipping, packaging/fulfillment | Per shipment. Shipments are monthly on both plans. |
| **Variable: clinical** | Initial consult (incl. allocation for declined applicants), follow-up consults | Per patient-month. |
| **Variable: payments** | Processing 2.9% + $0.30 per charge, chargeback fees | Per charge. |
| **Contra-revenue** | Promo discounts, refunds, chargeback amounts | Reduces revenue. Not a cost line. |
| **Marketing / CAC** | Channel spend (incl. affiliate bounties, referral credits) | Below contribution. Never inside contribution. |
| **Fixed opex** | Payroll, technology, legal/compliance, rent/G&A | Below contribution. Paid in month incurred. |

Do not double count: refunds and chargebacks reduce revenue only once, and the goods already shipped stay in COGS (they are not recovered).
