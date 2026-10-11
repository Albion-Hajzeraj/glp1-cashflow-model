"""Project 12-month LTV per channel and derive CPA guardrails.

1. Fit a retention curve per channel to the observed cohort curve
   (output/metrics/retention_by_channel.csv): r(k) = exp(-b * k^c), r(0) = 1.
   This is a Weibull survival curve; churn hazard falls with tenure when c < 1.
   It is fitted as a line on ln(-ln r) vs ln k, weighted by eligible customers.
2. Expected value in month k per acquired customer:
       r(k) x paid_share x (revenue per paid month - variable cost per paid month)
   - Revenue uses the channel's observed plan mix and promo share. Prepaid is
     recognized at 1/3 per month, net of expected refund/chargeback losses.
   - Medication = base cost of calendar month (start + k) x dose multiplier for
     tenure k+1. Months after the modeled period use the last known cost.
   - Discounted at the monthly rate in assumptions.yaml.
3. Break-even CPA = 12-month contribution LTV. Target CPA = break-even / 3.
   Max CPA = contribution earned in the first 4 months. Payback is measured at the
   actual period CPA. All on discounted contribution.

Usage:  python src/ltv_projection.py   (needs output/metrics/ from run_metrics.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from unit_economics import ROOT, by_tenure, load_assumptions, payback_months, shipment_unit_costs

METRICS = ROOT / "output" / "metrics"


def fit_retention(curve: pd.DataFrame, min_n: int) -> tuple[float, float]:
    """Return (b, c) for r(k) = exp(-b k^c) from k >= 1 points with enough customers."""
    pts = curve[(curve.months_since_signup >= 1) & (curve.eligible_customers >= min_n)
                & curve.retention_pct.between(0, 1, inclusive="neither")]
    x = np.log(pts.months_since_signup)
    y = np.log(-np.log(pts.retention_pct))
    c, ln_b = np.polyfit(x, y, 1, w=np.sqrt(pts.eligible_customers))
    return float(np.exp(ln_b)), float(c)


def retention_curve(b: float, c: float, horizon: int, drop_pp: float = 0.0) -> np.ndarray:
    k = np.arange(horizon)
    r = np.exp(-b * np.power(k, c, where=k > 0, out=np.zeros(horizon)))
    r[1:] = np.clip(r[1:] - drop_pp, 0, 1)
    return r


def monthly_unit_values(a: dict, mix_prepaid: float, promo_share: float, horizon: int,
                        med_uplift: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Revenue and variable cost per PAID active customer-month, k = 0..horizon-1."""
    plans, vc, ref, cb = a["plans"], a["variable_costs"], a["refunds"], a["chargebacks"]
    pp = vc["payment_processing"]
    mo, pre = plans["monthly"], plans["prepaid_3m"]
    months = [str(m) for m in a["period"]["months"]]
    med_by_month = dict(zip(months, a["medication"]["cost_per_month_supply"]))
    start = pd.Period(a["ltv"]["projection_start_month"], "M")
    dose = a["medication"]["dose_multiplier_by_tenure"]

    rev, cost = np.zeros(horizon), np.zeros(horizon)
    for k in range(horizon):
        first = k == 0
        monthly_charge = mo["price"] * (1 - promo_share * a["promo"]["first_month_discount"] if first else 1)
        prepaid_month = pre["price"] / pre["term_months"]
        gross = (1 - mix_prepaid) * monthly_charge + mix_prepaid * prepaid_month
        r_rate = ref["first_order_rate"] if first else ref["renewal_rate"]
        c_rate = cb["first_order_rate"] if first else cb["renewal_rate"]
        rev[k] = gross * (1 - r_rate * ref["avg_refund_share"] - c_rate)

        cal = str(start + k)
        med = med_by_month.get(cal, med_by_month[months[-1]]) * by_tenure(dose, k + 1) * (1 + med_uplift)
        processing = ((1 - mix_prepaid) * (monthly_charge * pp["pct_of_charge"] + pp["fixed_per_charge"])
                      + mix_prepaid * (pre["price"] * pp["pct_of_charge"] + pp["fixed_per_charge"])
                      / pre["term_months"])
        u = shipment_unit_costs(a)
        consult_cost = u["consult_initial"] if first else u["consult_follow_up"]
        cost[k] = (med + vc["pharmacy_dispensing_per_shipment"] + consult_cost
                   + vc["shipping_per_shipment"] + vc["packaging_fulfillment_per_shipment"]
                   + processing + c_rate * cb["fee_per_chargeback"])
    return rev, cost


def project(a: dict, ch: str, inputs: dict, med_uplift=0.0, drop_pp=0.0) -> dict:
    L = a["ltv"]
    h, disc = L["horizon_months"], L["discount_rate_monthly"]
    r = retention_curve(inputs["b"], inputs["c"], h, drop_pp)
    paid = np.where(np.arange(h) == 0, 1.0, inputs["paid_share"])
    rev, cost = monthly_unit_values(a, inputs["mix_prepaid"], inputs["promo_share"], h, med_uplift)
    df = 1 / (1 + disc) ** np.arange(h)
    rev_k = r * paid * rev * df
    con_k = r * paid * (rev - cost) * df
    cum = np.cumsum(con_k)
    ltv_c = float(cum[-1])
    pb = payback_months(list(cum), inputs["actual_cpa"])
    return {
        "channel": ch,
        "ltv12_revenue": float(rev_k.sum()),
        "ltv12_contribution": ltv_c,
        "break_even_cpa": ltv_c,
        "target_cpa": ltv_c / L["target_ltv_to_cpa"],
        "max_cpa_4m": float(cum[L["max_payback_months"] - 1]),
        "actual_cpa": inputs["actual_cpa"],
        "payback_months": pb if pb is not None else np.nan,
        "retained_m12": float(r[-1]),
    }


def channel_inputs(a: dict) -> dict[str, dict]:
    ret = pd.read_csv(METRICS / "retention_by_channel.csv")
    acq = pd.read_csv(METRICS / "acquisition_period.csv").set_index("channel")
    cust = pd.read_csv(ROOT / "data" / "customers.csv")
    out = {}
    for ch in a["channels"]:
        cv = ret[ret.channel == ch]
        b, c = fit_retention(cv, a["ltv"]["min_customers_for_fit"])
        later = cv[cv.months_since_signup >= 1]
        cc = cust[cust.channel == ch]
        out[ch] = {
            "b": b, "c": c,
            "paid_share": later.retained_paid.sum() / later.retained.sum(),
            "mix_prepaid": (cc.plan_type == "prepaid_3m").mean(),
            "promo_share": cc.loc[cc.plan_type == "monthly", "promo_applied"].mean(),
            "actual_cpa": float(acq.loc[ch, "cpa"]),
            "new_customers": int(acq.loc[ch, "new_paying_customers"]),
        }
    return out


def run(a: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    s = a["ltv"]["sensitivity"]
    inputs = channel_inputs(a)
    foc = pd.read_csv(METRICS / "first_order_contribution.csv").set_index("channel")

    base = pd.DataFrame([project(a, ch, inp) for ch, inp in inputs.items()])
    base["first_order_contribution"] = base["channel"].map(foc["first_order_contribution"])
    base["ltv_to_cpa"] = base["ltv12_contribution"] / base["actual_cpa"]
    base["fit_b"] = base["channel"].map({k: v["b"] for k, v in inputs.items()})
    base["fit_c"] = base["channel"].map({k: v["c"] for k, v in inputs.items()})
    base["paid_share"] = base["channel"].map({k: v["paid_share"] for k, v in inputs.items()})
    base.to_csv(METRICS / "ltv_and_cpa.csv", index=False)

    scenarios = {
        "base": (0.0, 0.0),
        f"medication +{s['medication_uplift']:.0%}": (s["medication_uplift"], 0.0),
        f"retention -{s['retention_drop_pp'] * 100:.0f}pp": (0.0, s["retention_drop_pp"]),
        "both": (s["medication_uplift"], s["retention_drop_pp"]),
    }
    rows = []
    for name, (med, drop) in scenarios.items():
        for ch, inp in inputs.items():
            rows.append({"scenario": name, **project(a, ch, inp, med, drop)})
    sens = pd.DataFrame(rows)
    sens["cpa_vs_target"] = sens["actual_cpa"] / sens["target_cpa"] - 1
    sens.to_csv(METRICS / "sensitivity.csv", index=False)
    return base, sens


if __name__ == "__main__":
    ltv, sens = run(load_assumptions())
    pd.set_option("display.width", 200)
    print(ltv.round(2).to_string(index=False))
    print(sens.pivot(index="channel", columns="scenario", values="ltv12_contribution").round(0))
