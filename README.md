# GLP-1 Telehealth: Cash Flow Model

A financial analytics case study of a hypothetical US telehealth business selling GLP-1 weight-management subscriptions. It uses **synthetic data** for Jan–Sep 2026 and builds a plan to double monthly net operating cash flow.

## Key results

| | |
|---|---|
| Baseline (Sep 2026 net operating cash flow) | $1.03M |
| Target (2×) | $2.06M |
| Base case | 2× reached Jan 2027 and sustained; $3.42M by Jun 2027 |
| Downside case | not reached; $873K by Jun 2027 |
| External funding needed | none in either case |

- **Biggest levers by June 2027:** a 5% price increase (+$393K/month), cheaper medication (+$369K/month), and clinician check-ins during dose titration (+$238K/month).
- **Retention:** the three retention levers together add +$651K/month.
- **Downside:** the downside case only reaches 2× if the levers deliver in full *and* retention holds.

## Pipeline

```
assumptions.yaml ─► src/generate_data.py ─► data/*.csv
                                              │
                    sql/*.sql (DuckDB) ◄──────┘  via src/run_metrics.py ─► output/metrics/*.csv
                                              │
                    src/build_workbook.py ────┴─► output/omzo_model.xlsx (live formulas, Excel + Google Sheets)
                    src/charts.py, src/retention_report.py ─► output/charts/, dashboard.pdf, retention_report.pdf
```

## How to run

```bash
pip install -r requirements.txt
python src/generate_data.py        # synthetic data (seeded, deterministic)
python src/run_metrics.py          # SQL metrics + LTV projection
python src/build_workbook.py       # workbook; recalculates with LibreOffice if installed and prints the plan summary
python src/charts.py               # charts + dashboard PDF
python src/retention_report.py     # retention report PDF
python -m pytest                   # 36 reconciliation, workbook and forecast tests
```

LibreOffice is needed only to recalculate the workbook for the workbook tests and the printed summary.

## Repository layout

| Path | Contents |
|---|---|
| `assumptions.yaml` | Every input: prices, channels, CPAs, churn, medication cost, unit costs, cash timing, levers |
| `docs/project_notes.md` | Business context, metric definitions, cost categories |
| `docs/ai_log.md` | How the project was built, verification and corrections |
| `src/` | Data generator, metrics runner, LTV projection, workbook and forecast builder, charts, report |
| `sql/` | DuckDB metric queries (P&L, cash flow, AOV, acquisition, cohorts, retention, LTV) |
| `tests/` | Reconciliation, workbook and forecast tests |
| `data/` | Generated synthetic dataset |
| `output/` | Workbook, charts, dashboard and retention report |

## Workbook guide (`output/omzo_model.xlsx`)

- **Inputs:** all assumptions (blue cells), plus the scenario switch.
- **Monthly_PnL, Cash_Flow:** actuals, Jan–Sep 2026.
- **Forecast, Levers, Scenarios, Doubling_Bridge:** the 9-month plan.
- **Unit_Economics, Channels, Cohorts:** AOV, CPA, LTV, payback and retention.
- **Checks:** 45 live reconciliations; cell B3 shows whether all of them pass.

## Caveats

The data is synthetic, so the results show the method, not market facts. Lever effects are assumptions that would need testing. The 12-month LTV is extrapolated from at most 9 months of observed data.
