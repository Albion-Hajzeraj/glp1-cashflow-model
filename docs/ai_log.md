# AI usage log

Sources: the step-by-step development history (Oct 2026), docs/project_notes.md, assumptions.yaml and the AI session transcript.

## 1. Summary

- **Tools:** an AI chat assistant to plan the step prompts [TO CONFIRM], an AI coding assistant to build everything, Python (pandas, DuckDB, openpyxl, matplotlib, pytest), and LibreOffice only to recalculate the workbook for tests.
- **Workflow:** 7 step prompts, each ending "do only this step, then stop". The AI coding assistant built, ran, summarised and committed each step; I reviewed the summary and sent the next step.
- **Who did what:** the AI wrote essentially all code, data, calculations, the model, charts, reports and slides. I directed, reviewed and approved.
- **Verification:** 36 automated tests, 5 data sanity checks and 45 live checks inside the workbook.

## 2. What the AI produced

| Step | Purpose | Output | Key AI choices |
|---|---|---|---|
| 1 | Setup and inputs | `assumptions.yaml`, project notes, `unit_economics.py` | All assumption values; consult cost of declined applicants spread over approved customers |
| 2 | Synthetic data | `generate_data.py`, 10 CSVs | Each customer simulated cycle by cycle (seed 42); dunning on days 1/3/5; nothing dated after Sep 30 |
| 3 | Metrics | `sql/*.sql`, `run_metrics.py`, `ltv_projection.py` | No numbers in SQL (read from YAML); retention counts prepaid months and pauses; Weibull retention fit |
| 4 | Reconciliation tests | `test_reconcile.py`, `shipments.csv` | Expected values recomputed independently; known differences tested as named items |
| 5 | Excel/Sheets model | `build_workbook.py`, `omzo_model.xlsx` | Data sheets hold volumes, Inputs hold prices; only Sheets-safe functions |
| 6 | Forecast and plan | `forecast_sheets.py` | Sep 2026 baseline; cohort roll-forward; lever values; plan-mix lever switched off |
| 7 | Charts and reports | `charts.py`, `retention_report.py`, PNGs, 2 PDFs | Colour-blind-checked palette; no dual axes; supported findings kept apart from hypotheses |
| Extra | Study guide, slides | `README.md`, 8-slide deck | Answer-first storyline |

## 3. My role

- Wrote or approved each step's scope, rules and checkpoints.
- Asked for the study guide, slide prompt, deck and this log.
- Completed the LibreOffice install and chose the deck style.
- Left the "opening customer base" question unanswered, so the model starts from zero customers.
- No edits by me appear in the development history. Depth of review and any offline edits: [TO CONFIRM].

## 4. Verification

- **Data checks (every generator run):** no negative prices; no orders after a cancel; no overlapping paid terms; one cost row per order; pharmacy bills match shipments.
- **Reconciliation tests (22):** cash rolls forward to the cent; receipts tie to payments; revenue follows the recognition rule; AOV × orders = revenue; medication counted once; costs tie to orders, marketing and opex; CPA recomputed; retention ≤ 100% with future months blank; same seed gives identical files.
- **Workbook tests (9):** after recalculation in LibreOffice the workbook matches the SQL within $1, has no formula errors and uses only allowed functions.
- **Forecast tests (5):** forecast starts from the last actual cash balance; bridge bars sum exactly; the scenario switch changes the outputs.
- **Workbook Checks sheet (45 rows):** cash roll-forward, channel totals, P&L identities, pharmacy accrued = paid + owed, cohort sizes, bridge sum.
- **Manual review by the AI:** simulated behaviour compared with the assumptions; the $6.2M profit-vs-cash gap explained; each lever's sign and size checked; every chart viewed as an image.

## 5. Corrections

All found by the AI (tests, its own checks or viewing output); none reported by me.

| Issue | Caught by | Cause | Fix | Step |
|---|---|---|---|---|
| Customers won back before their paid term ended (double billing) | AI gap check | Win-back date ignored the paid-through date | Win-back starts after the term; new check added | 2 |
| AOV × orders ≠ net revenue ($227K off in Sep) | Test | SQL used average charge, not net revenue ÷ orders | Definition fixed in SQL and project notes | 4 |
| $1.13M of pharmacy bills dated after Sep 30; accrual $60K off | Tests | Future shipments billed; P&L spread costs evenly | Added `shipments.csv`; costs booked per shipment | 4 |
| Forecast `#VALUE!` errors in months 1–8 | Recalculation | Dose lookup for cohorts not yet started | Index clamped | 6 |
| Checks summary showed TRUE despite errors | AI review | Counted FALSE only | Errors now count as failures | 6 |
| Plan-mix lever cost $206K/month | AI plausibility check | Discount applied to existing renewals | Limited to new customers; still negative, so switched off | 6 |
| Chart labels overlapping or clipped | AI viewing images | Layout | Repositioned and shortened | 7 |
| Missing "no-new-installs" rule in the project notes | AI check | Rule never written | Rule added | 7 |

Smaller fixes: coding slips in the generator and DuckDB syntax limits (steps 2–3), consult-cost rounding (step 5), an unverified detail in an earlier log (step 5), and a "$" crash in the report (step 7).

Effect on results: booking costs per shipment moved monthly operating profit by up to $70K (July: $189K → $258K). Cash flow was unchanged.

## 6. Limitations

- **Synthetic data:** every pattern was set in `assumptions.yaml`; this shows a method, not market facts.
- **Assumed lever effects:** the base case (3.3× by Jun 2027) relies partly on unvalidated price and medication levers; the downside does not reach 2×.
- **Short history:** at most 9 months observed, so 12-month LTV and later retention are extrapolated.
- **Simplifications:** zero customers at the start; refunds booked in the month of the charge; opex held flat.
- **AI risk:** two errors looked reasonable until checked (AOV definition, discount scope). The tests prove the numbers are consistent with each other, not that the assumptions are right.

## 7. How I can explain it

- **AOV:** net revenue ÷ completed orders in the month. *Unit_Economics, top block.*
- **CPA:** a channel's spend ÷ its new paying customers. *Channels, column D.*
- **Blended CPA:** all spend ÷ all new customers, $275. Paid channels alone cost $308, which the blend hides. *Channels, ALL_BLENDED and PAID_ONLY rows.*
- **LTV:** expected 12-month contribution per customer = retention × (revenue − variable cost) each month, discounted 1% a month. *Calc_LTV; summary on Unit_Economics.*
- **Payback:** months until cumulative contribution covers the CPA. Target CPA = LTV ÷ 3; max CPA = what is earned back in 4 months. *Calc_LTV, Channels.*
- **Retention:** share of a signup cohort still active N months later (a payment covers the month, or the customer is paused). *Cohorts sheet.*
- **Net operating cash flow:** cash in minus cash out each month. Cards pay out after 2 days, the pharmacy is paid after 30, ads a month later, so cash differs from profit. *Cash_Flow; continued on Forecast.*
- **Doubling bridge:** from September's $1.03M to June 2027's $3.42M. Levers are added one at a time in a fixed order, so the bars add up exactly. *Doubling_Bridge.*
