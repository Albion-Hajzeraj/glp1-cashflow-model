"""One-customer unit-economics check per acquisition channel.

Cash basis, per charge event: each charge carries the variable costs of the months
it covers (prepaid = 3 months of medication, shipments and consults up front).
Pauses and reactivations are ignored here; they are modeled in the full simulation.

Usage:  python src/unit_economics.py [--month 2026-05]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_assumptions(path: Path = ROOT / "assumptions.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def shipment_unit_costs(a: dict) -> dict[str, float]:
    """Per-shipment non-medication costs in cents. The initial consult includes the
    allocation for declined applicants. Shared by the generator and the P&L SQL."""
    vc = a["variable_costs"]
    c = vc["clinical_consult"]
    return {
        "pharmacy_dispensing": vc["pharmacy_dispensing_per_shipment"],
        "shipping": vc["shipping_per_shipment"],
        "packaging_fulfillment": vc["packaging_fulfillment_per_shipment"],
        "consult_initial": round(c["initial"] / (1 - c["intake_decline_rate"]), 2),
        "consult_follow_up": c["follow_up"],
    }


def by_tenure(curve: list[float], t: int) -> float:
    """Value for tenure month t (1-indexed); the last value applies to later months."""
    return curve[min(t, len(curve)) - 1]


def charge_economics(a: dict, price: float, tenure_months: list[int], med_unit_cost: float,
                     first_order: bool) -> dict:
    """Revenue, contra-revenue and variable costs for one charge covering `tenure_months`."""
    vc, ref, cb = a["variable_costs"], a["refunds"], a["chargebacks"]
    consult = vc["clinical_consult"]
    n = len(tenure_months)
    dose = a["medication"]["dose_multiplier_by_tenure"]

    refund_rate = ref["first_order_rate"] if first_order else ref["renewal_rate"]
    cb_rate = cb["first_order_rate"] if first_order else cb["renewal_rate"]

    consult_cost = 0.0
    for t in tenure_months:
        if t == 1:
            d = consult["intake_decline_rate"]
            consult_cost += consult["initial"] / (1 - d)   # own consult + declined applicants' share
        else:
            consult_cost += consult["follow_up"]

    refunds = price * refund_rate * ref["avg_refund_share"]
    chargebacks = price * cb_rate
    net_revenue = price - refunds - chargebacks

    costs = {
        "medication": med_unit_cost * sum(by_tenure(dose, t) for t in tenure_months),
        "pharmacy_dispensing": vc["pharmacy_dispensing_per_shipment"] * n,
        "clinical_consult": consult_cost,
        "shipping": vc["shipping_per_shipment"] * n,
        "packaging_fulfillment": vc["packaging_fulfillment_per_shipment"] * n,
        "payment_processing": price * vc["payment_processing"]["pct_of_charge"]
                              + vc["payment_processing"]["fixed_per_charge"],
        "chargeback_fees": cb_rate * cb["fee_per_chargeback"],
    }
    return {
        "gross_charge": price,
        "refunds": -refunds,
        "chargebacks": -chargebacks,
        "net_revenue": net_revenue,
        **{k: -v for k, v in costs.items()},
        "contribution": net_revenue - sum(costs.values()),
    }


def contribution_curve(a: dict, plan: str, channel: str, med_unit_cost: float,
                       horizon: int) -> list[float]:
    """Expected contribution per month (index 0 = month 1) for one new customer on `plan`."""
    p = a["plans"][plan]
    ret = a["retention"]
    term = p["term_months"]
    churn_mult = a["channels"][channel]["churn_multiplier"] * p.get("churn_multiplier", 1.0)
    involuntary = ret["failed_payment_rate"] * (1 - ret["failed_payment_recovery"])

    curve = [0.0] * horizon
    alive = 1.0
    for start in range(1, horizon + 1, term):
        price = p["price"]
        if start == 1 and plan == "monthly" and channel in a["promo"]["channels"]:
            price *= 1 - a["promo"]["first_month_discount"]
        months = list(range(start, start + term))
        econ = charge_economics(a, price, months, med_unit_cost, first_order=(start == 1))
        curve[start - 1] = alive * econ["contribution"]
        for t in months:  # survive each covered month to reach the next charge
            alive *= 1 - min(1.0, by_tenure(ret["churn_by_tenure"], t) * churn_mult)
        alive *= 1 - involuntary
    return curve


def payback_months(cum: list[float], cpa: float) -> float | None:
    """Fractional month in which cumulative contribution first covers CPA."""
    prev = 0.0
    for i, c in enumerate(cum, start=1):
        if c >= cpa:
            step = c - prev
            return i - 1 + ((cpa - prev) / step if step > 0 else 1.0)
        prev = c
    return None


def channel_table(a: dict, month: str) -> pd.DataFrame:
    med_cost = a["medication"]["cost_per_month_supply"][a["period"]["months"].index(month)]
    horizon = a["unit_check"]["payback_horizon_months"]
    rows = {}
    for ch, cfg in a["channels"].items():
        blended: dict[str, float] = {}
        cum = [0.0] * horizon
        for plan, p in a["plans"].items():
            price = p["price"]
            if plan == "monthly" and ch in a["promo"]["channels"]:
                price *= 1 - a["promo"]["first_month_discount"]
            econ = charge_economics(a, price, list(range(1, p["term_months"] + 1)), med_cost,
                                    first_order=True)
            for k, v in econ.items():
                blended[k] = blended.get(k, 0.0) + p["mix"] * v
            running = 0.0
            for i, c in enumerate(contribution_curve(a, plan, ch, med_cost, horizon)):
                running += c
                cum[i] += p["mix"] * running
        blended["cpa"] = cfg["expected_cpa"]
        blended["first_order_contrib_less_cpa"] = blended["contribution"] - cfg["expected_cpa"]
        blended[f"ltv_{horizon}m"] = cum[-1]
        blended["ltv_to_cpa"] = cum[-1] / cfg["expected_cpa"]
        pb = payback_months(cum, cfg["expected_cpa"])
        blended["payback_months"] = pb if pb is not None else float("nan")
        rows[ch] = blended
    return pd.DataFrame(rows)


def main() -> None:
    a = load_assumptions()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", default=a["unit_check"]["reference_month"],
                        help="medication cost month, e.g. 2026-05")
    month = parser.parse_args().month

    mix = ", ".join(f"{k} {v['mix']:.0%}" for k, v in a["plans"].items())
    print(f"\nOne-customer unit economics, first order, blended plan mix ({mix})")
    print(f"Medication cost month: {month}  |  cash basis; prepaid first order covers 3 months\n")
    pd.set_option("display.width", 140)
    print(channel_table(a, month).round(2).to_string())

    print("\nPayback sensitivity to medication cost month (months):")
    sens = pd.DataFrame({m: channel_table(a, m).loc["payback_months"]
                         for m in ["2026-01", "2026-05", "2026-07"]}).T
    print(sens.round(1).to_string())


if __name__ == "__main__":
    main()
