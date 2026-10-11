"""Run every metric query in sql/ with DuckDB and save results to output/metrics/.

Each sql/*.sql file runs in name order. Text before the first `-- name: <x>` marker is
setup (views). Each `-- name: <x>` block is one query: it is saved as
output/metrics/<x>.csv and registered as a view so later queries can build on it.
Then runs the LTV projection and prints a short summary.

Usage:  python src/run_metrics.py
"""
from __future__ import annotations

import re

import duckdb
import pandas as pd

import ltv_projection
from unit_economics import ROOT, load_assumptions, shipment_unit_costs

SQL_DIR = ROOT / "sql"
DATA = ROOT / "data"
OUT = ROOT / "output" / "metrics"
TABLES = ["customers", "orders", "payments", "refunds", "subscription_events",
          "order_costs", "shipments", "med_cost_monthly", "marketing_spend", "fixed_opex",
          "cash_payables"]


def connect(a: dict) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for t in TABLES:
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_csv_auto('{(DATA / f'{t}.csv').as_posix()}')")
    # parameter tables from assumptions.yaml, so no numbers live in the SQL
    params = {
        "p_months": pd.DataFrame({"month": pd.to_datetime([f"{m}-01" for m in a["period"]["months"]]).date}),
        "p_plans": pd.DataFrame([(k, v["term_months"]) for k, v in a["plans"].items()],
                                columns=["plan_type", "term_months"]),
        "p_channels": pd.DataFrame([(k, bool(v["paid"])) for k, v in a["channels"].items()],
                                   columns=["channel", "paid"]),
        "p_cash": pd.DataFrame({"opening_balance": [float(a["cash"]["opening_balance"])]}),
        "p_unit_costs": pd.DataFrame([shipment_unit_costs(a)]),
    }
    for name, df in params.items():
        con.register(f"_{name}", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}")
    return con


def run_sql(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for path in sorted(SQL_DIR.glob("*.sql")):
        parts = re.split(r"^-- name:\s*(\w+)\s*$", path.read_text(encoding="utf-8"), flags=re.M)
        if parts[0].strip():
            con.execute(parts[0])                      # setup statements
        for name, body in zip(parts[1::2], parts[2::2]):
            query = body.strip().rstrip(";")
            if re.search(r"^\s*PIVOT\b", query, flags=re.M | re.I):
                # dynamic PIVOT can't live in a view: run it and register the result
                df = con.execute(query).df()
                con.register(name, df)
            else:
                con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS {query}")
                df = con.execute(f"SELECT * FROM {name}").df()
            df.to_csv(OUT / f"{name}.csv", index=False)
            results[name] = df
    return results


def summary(r: dict[str, pd.DataFrame], ltv: pd.DataFrame) -> None:
    pd.set_option("display.width", 200)
    fmt = lambda x: f"{x:,.0f}"

    cf = r["cash_flow"].set_index("month")
    pnl = r["monthly_pnl"].set_index("month")
    both = pd.DataFrame({"operating_profit": pnl["operating_profit"],
                         "net_operating_cash_flow": cf["net_operating_cash_flow"],
                         "closing_cash": cf["closing_cash"]})
    print("\nOperating profit vs net operating cash flow ($)")
    print(both.to_string(float_format=fmt))

    pos = cf.index[cf["net_operating_cash_flow"] > 0].tolist()
    print(f"\nBaseline month candidates (net operating cash flow > 0): {', '.join(pos) or 'none'}")

    cols = ["actual_cpa", "target_cpa", "break_even_cpa", "max_cpa_4m", "payback_months",
            "ltv12_contribution", "first_order_contribution"]
    t = ltv.set_index("channel")[cols].copy()
    t["vs_target"] = (t["actual_cpa"] / t["target_cpa"] - 1).map(lambda x: f"{x:+.0%}")
    print("\nCPA vs target and payback per channel (12-month projection, base case)")
    print(t.to_string(float_format=lambda x: f"{x:,.1f}"))

    br = r["profit_to_cash_bridge"].set_index("month")
    comps = ["prepaid_deferral", "payout_lag", "ad_payment_lag", "pharmacy_net30",
             "other_variable_timing", "opex_timing"]
    m = br["gap"].abs().idxmax()
    row = br.loc[m]
    top = row[comps].abs().sort_values(ascending=False)
    reasons = {
        "prepaid_deferral": "prepaid plans bill 3 months up front but revenue is recognized 1/3 per month",
        "payout_lag": "card payouts settle T+2, so the last days of each month's charges arrive next month",
        "ad_payment_lag": "ad spend is expensed this month but paid at the end of next month",
        "pharmacy_net30": "pharmacy (medication + dispensing) is paid 30 days after shipment",
        "other_variable_timing": "variable-cost timing (prepaid costs spread in P&L, paid per shipment)",
        "opex_timing": "fixed opex timing",
    }
    print(f"\nBiggest profit-vs-cash gap: {m}: cash flow {fmt(row['net_operating_cash_flow'])} vs "
          f"operating profit {fmt(row['operating_profit'])} (gap {fmt(row['gap'])})")
    for c in top.index[:3]:
        print(f"  {c:<22}{row[c]:>12,.0f}  {reasons[c]}")


def main() -> None:
    a = load_assumptions()
    con = connect(a)
    results = run_sql(con)
    print("Saved to output/metrics/:")
    for name, df in results.items():
        print(f"  {name + '.csv':<32}{len(df):>6} rows")

    ltv, sens = ltv_projection.run(a)
    print(f"  {'ltv_and_cpa.csv':<32}{len(ltv):>6} rows")
    print(f"  {'sensitivity.csv':<32}{len(sens):>6} rows")
    summary(results, ltv)


if __name__ == "__main__":
    main()
