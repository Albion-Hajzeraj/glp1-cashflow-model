"""Reconciliation tests: prove the dataset and the metrics are internally consistent.

Every expected value is recomputed independently from data/*.csv (pandas) and compared
with output/metrics/*.csv (DuckDB SQL). Where totals legitimately differ, the reconciling
item is computed explicitly and named. Examples: cash dated after the period, revenue
deferred past period end, prepaid shipments scheduled after period end.
"""
import hashlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from conftest import ROOT

CENT = 0.01
VAR_COLS = ["medication", "pharmacy_dispensing", "clinical_consult", "shipping",
            "packaging_fulfillment", "payment_processing", "chargeback_fees"]
SHIP_COLS = ["medication", "pharmacy_dispensing", "clinical_consult", "shipping",
             "packaging_fulfillment"]


# ----------------------------------------------------------------------------- helpers
def paid(orders):
    return orders[orders.status != "failed"]


def month_of(s):
    return s.dt.strftime("%Y-%m")


def unit_costs(a):
    vc = a["variable_costs"]
    c = vc["clinical_consult"]
    return {
        "dispensing": vc["pharmacy_dispensing_per_shipment"],
        "shipping": vc["shipping_per_shipment"],
        "packaging": vc["packaging_fulfillment_per_shipment"],
        "consult_initial": round(c["initial"] / (1 - c["intake_decline_rate"]), 2),  # money is in cents
        "consult_follow_up": c["follow_up"],
    }


def dose(a, t):
    d = a["medication"]["dose_multiplier_by_tenure"]
    return d[min(t, len(d)) - 1]


def recognized_revenue(data, a):
    """Net price of each paid order spread evenly over its service months."""
    o = paid(data["orders"])
    term = o.plan_type.map({p: v["term_months"] for p, v in a["plans"].items()})
    rep = o.loc[o.index.repeat(term)].copy()
    rep["k"] = rep.groupby(level=0).cumcount()
    rep["month"] = (rep.order_date.dt.to_period("M") + rep.k).astype(str)
    rep["amount"] = rep.net_price / term.loc[rep.index]
    return rep


def post_period_shipments(data, a, period):
    """Shipments that order_costs includes but that fall after period end (prepaid
    terms running past the period). Costed independently from the assumptions."""
    oc = data["order_costs"].merge(data["orders"][["order_id", "customer_id", "order_number"]])
    sh = data["shipments"]
    oc["in_period"] = sh.groupby("order_id").size().reindex(oc.order_id).fillna(0).astype(int).values
    last_t = sh.groupby("order_id").tenure_month.max()
    cust_t = sh.groupby("customer_id").tenure_month.max()
    base_last = a["medication"]["cost_per_month_supply"][-1]
    u = unit_costs(a)
    rows = []
    for r in oc[oc.shipments > oc.in_period].itertuples():
        if r.in_period:
            t0 = last_t[r.order_id] + 1
        else:  # whole order ships after period end (e.g. dunning recovered in October)
            t0 = cust_t.get(r.customer_id, 0) + 1
        for t in range(t0, t0 + r.shipments - r.in_period):
            rows.append({"order_id": r.order_id, "tenure_month": t,
                         "medication": base_last * dose(a, t),
                         "pharmacy_dispensing": u["dispensing"],
                         "clinical_consult": u["consult_initial"] if t == 1 else u["consult_follow_up"],
                         "shipping": u["shipping"], "packaging_fulfillment": u["packaging"]})
    return pd.DataFrame(rows, columns=["order_id", "tenure_month"] + SHIP_COLS)


# ============================================================================= cash
def test_cash_opening_plus_flow_equals_closing(metrics):
    cf = metrics["cash_flow"]
    diff = (cf.opening_cash + cf.net_operating_cash_flow - cf.closing_cash).abs()
    assert (diff < CENT).all(), cf[diff >= CENT]


def test_cash_closing_rolls_to_next_opening(metrics, a):
    cf = metrics["cash_flow"]
    assert abs(cf.opening_cash.iloc[0] - a["cash"]["opening_balance"]) < CENT
    gap = (cf.closing_cash.shift(1) - cf.opening_cash).iloc[1:].abs()
    assert (gap < CENT).all()


def test_cash_receipts_reconcile_to_payments(metrics, data, period):
    pay, ref = data["payments"], data["refunds"]
    ok = pay[pay.status.isin(["success", "recovered"])]
    # Reconciling items: cash whose date falls after the period.
    late_payouts = ok[ok.payout_date > period["end"]]
    late_refunds = ref[ref.refund_date > period["end"]]
    expected = (ok.amount.sum() - late_payouts.amount.sum()
                - (ref.amount.sum() - late_refunds.amount.sum()))
    actual = metrics["cash_flow"].cash_receipts.sum()
    print(f"\n  reconciling items: {len(late_payouts)} payouts after period end "
          f"(${late_payouts.amount.sum():,.2f}), {len(late_refunds)} refunds/chargebacks after period end")
    assert abs(actual - expected) < CENT
    assert late_payouts.payout_date.min() > period["end"] if len(late_payouts) else True


def test_no_financing_or_investing_in_operating_cash_flow(metrics, data):
    operating_types = {"pharmacy", "clinical_consult", "shipping", "packaging_fulfillment",
                       "payment_processing", "marketing", "fixed_opex"}
    assert set(data["cash_payables"].expense_type) <= operating_types
    banned = ("loan", "debt", "borrow", "equity", "invest", "capex", "financ", "interest", "dividend")
    assert not [c for c in metrics["cash_flow"].columns if any(b in c.lower() for b in banned)]
    cf = metrics["cash_flow"]
    flows = cf.cash_receipts - (cf.pharmacy_paid + cf.marketing_paid
                                + cf.other_variable_paid + cf.fixed_opex_paid)
    assert np.allclose(flows, cf.net_operating_cash_flow, atol=CENT)


# ============================================================================= revenue & orders
def test_net_revenue_matches_recognition_rule(metrics, data, a, period):
    rec = recognized_revenue(data, a)
    ref = data["refunds"].assign(month=lambda d: month_of(d.refund_date)).groupby("month").amount.sum()
    expected = (rec.groupby("month").amount.sum().reindex(period["months"]).fillna(0)
                - ref.reindex(period["months"]).fillna(0))
    pnl = metrics["monthly_pnl"].set_index("month")
    assert np.allclose(pnl.net_revenue, expected, atol=CENT)
    # Total: net charges − refunds = recognized in period + deferred past period end.
    deferred = rec.loc[rec.month > period["months"][-1], "amount"].sum()
    total = paid(data["orders"]).net_price.sum() - data["refunds"].amount.sum()
    assert abs(pnl.net_revenue.sum() + deferred - total) < CENT


def test_aov_times_orders_equals_net_revenue(metrics):
    aov = metrics["aov"].set_index("month")
    pnl = metrics["monthly_pnl"].set_index("month")
    assert np.allclose(aov.aov_overall * aov.completed_orders, pnl.net_revenue, atol=CENT)
    split = (aov.aov_first_order.fillna(0) * aov.first_orders
             + aov.aov_repeat_order.fillna(0) * aov.repeat_orders)
    assert np.allclose(split, pnl.net_revenue, atol=CENT)


def test_first_plus_repeat_orders_equal_completed(metrics, data, period):
    aov = metrics["aov"]
    assert (aov.first_orders + aov.repeat_orders == aov.completed_orders).all()
    pnl = metrics["monthly_pnl"]
    assert (aov.completed_orders.values == pnl.completed_orders.values).all()
    o = paid(data["orders"])
    assert aov.completed_orders.sum() == len(o)


# ============================================================================= costs
def test_one_cost_row_per_completed_order_and_none_for_failed(data):
    o, oc = data["orders"], data["order_costs"]
    assert oc.order_id.is_unique
    assert set(oc.order_id) == set(paid(o).order_id)
    assert not set(oc.order_id) & set(o.loc[o.status == "failed", "order_id"])


def test_medication_only_in_medication_column(metrics, data, a, period):
    u = unit_costs(a)
    oc = data["order_costs"].merge(data["orders"][["order_id", "order_number"]])
    n = oc.shipments
    # Non-medication columns are exactly their per-shipment rates: no drug cost hidden in them.
    assert np.allclose(oc.pharmacy_dispensing, n * u["dispensing"], atol=CENT)
    assert np.allclose(oc.shipping, n * u["shipping"], atol=CENT)
    assert np.allclose(oc.packaging_fulfillment, n * u["packaging"], atol=CENT)
    consult = np.where(oc.order_number == 1, u["consult_initial"] + (n - 1) * u["consult_follow_up"],
                       n * u["consult_follow_up"])
    assert np.allclose(oc.clinical_consult, consult, atol=CENT)
    comp = ["medication", "pharmacy_dispensing", "clinical_consult", "shipping",
            "packaging_fulfillment", "payment_processing"]
    assert np.allclose(oc.total_variable_cost, oc[comp].sum(axis=1), atol=CENT)

    # Medication per order = month base cost x dose, shipment by shipment.
    med = data["med_cost_monthly"]
    sh = data["shipments"].assign(month=lambda d: month_of(d.ship_date),
                                  t=lambda d: d.tenure_month.clip(upper=med.tenure_month.max()))
    sh = sh.merge(med, left_on=["month", "t"], right_on=["month", "tenure_month"], suffixes=("", "_m"))
    sh["med"] = sh.base_med_cost * sh.dose_multiplier
    post = post_period_shipments(data, a, period)
    expected = (sh.groupby("order_id").med.sum().reindex(oc.order_id).fillna(0).values
                + post.groupby("order_id").medication.sum().reindex(oc.order_id).fillna(0).values)
    assert np.allclose(oc.medication, expected, atol=CENT)

    # P&L medication = order_costs medication − medication shipped after period end.
    pnl_med = metrics["monthly_pnl"].medication.sum()
    assert abs(pnl_med - (oc.medication.sum() - post.medication.sum())) < CENT


def test_pnl_costs_equal_order_costs_plus_marketing_and_opex(metrics, data, a, period):
    pnl = metrics["monthly_pnl"]
    pnl_total = pnl[VAR_COLS + ["marketing_spend", "fixed_opex"]].to_numpy().sum()
    post = post_period_shipments(data, a, period)
    expected = (data["order_costs"].total_variable_cost.sum()
                - post[SHIP_COLS].to_numpy().sum()        # reconciling: shipped after period end
                + data["refunds"].fee.sum()               # reconciling: chargeback fees (not order-level)
                + data["marketing_spend"].spend.sum()
                + data["fixed_opex"].amount.sum())
    assert abs(pnl_total - expected) < CENT
    assert np.allclose(pnl.total_variable_cost, pnl[VAR_COLS].sum(axis=1), atol=CENT)


def test_pharmacy_accrued_equals_paid_plus_payable(metrics, data, period):
    pnl = metrics["monthly_pnl"].set_index("month")
    accrued = pnl.medication + pnl.pharmacy_dispensing
    ph = data["cash_payables"].query("expense_type == 'pharmacy'")
    assert ph.incurred_date.max() <= period["end"]
    paid_in = ph.loc[ph.paid_date <= period["end"], "amount"].sum()
    payable = ph.loc[ph.paid_date > period["end"], "amount"].sum()
    assert abs(accrued.sum() - (paid_in + payable)) < CENT
    by_month = ph.groupby("incurred_month").amount.sum().reindex(pnl.index).fillna(0)
    assert np.allclose(accrued, by_month, atol=CENT)
    cash_paid = metrics["cash_flow"].pharmacy_paid.sum()
    assert abs(cash_paid - paid_in) < CENT


# ============================================================================= acquisition
def test_new_customers_by_channel_sum_to_total(metrics, data):
    acq = metrics["acquisition"]
    ch = acq[~acq.channel.isin(["ALL_BLENDED", "PAID_ONLY"])]
    total = acq[acq.channel == "ALL_BLENDED"].set_index("month").new_paying_customers
    assert (ch.groupby("month").new_paying_customers.sum() == total).all()
    by_month = month_of(data["customers"].signup_date).value_counts().sort_index()
    assert (by_month.reindex(total.index).values == total.values).all()


def test_blended_cpa_recomputed_independently(metrics, data, a):
    spend = data["marketing_spend"].groupby("month").spend.sum()
    new = month_of(data["customers"].signup_date).value_counts()
    acq = metrics["acquisition"].set_index(["month", "channel"])
    for m in spend.index:
        assert abs(acq.loc[(m, "ALL_BLENDED"), "cpa"] - spend[m] / new[m]) < 1e-6
    paid_ch = [c for c, v in a["channels"].items() if v["paid"]]
    ms, cu = data["marketing_spend"], data["customers"]
    paid_only = ms[ms.channel.isin(paid_ch)].spend.sum() / cu.channel.isin(paid_ch).sum()
    per = metrics["acquisition_period"].set_index("channel")
    assert abs(per.loc["PAID_ONLY", "cpa"] - paid_only) < 1e-6
    assert abs(per.loc["ALL_BLENDED", "cpa"] - ms.spend.sum() / len(cu)) < 1e-6


def test_first_order_in_signup_month_and_renewals_not_acquisitions(metrics, data):
    o, c = data["orders"], data["customers"]
    first = o[o.order_number == 1]
    assert first.customer_id.is_unique and len(first) == len(c)
    j = first.merge(c, on="customer_id")
    assert (month_of(j.order_date) == month_of(j.signup_date)).all()
    assert (j.status != "failed").all()
    # acquisitions = customers, not orders (renewals and win-backs have order_number > 1)
    assert metrics["acquisition_period"].set_index("channel").loc["ALL_BLENDED", "new_paying_customers"] == len(c)
    assert o.groupby("customer_id").order_number.apply(lambda s: s.is_monotonic_increasing).all()


# ============================================================================= retention & LTV
def test_month0_is_100pct_and_never_above_100(metrics):
    long = metrics["cohort_retention_long"]
    assert np.allclose(long.loc[long.months_since_signup == 0, "retention_pct"], 1.0)
    assert (long.retention_pct.dropna() <= 1.0 + 1e-12).all()
    rbc = metrics["retention_by_channel"]
    assert np.allclose(rbc.loc[rbc.months_since_signup == 0, "retention_pct"], 1.0)
    assert (rbc.retention_pct <= 1.0 + 1e-12).all()


def test_right_censored_cells_are_null_not_zero(metrics, period):
    long = metrics["cohort_retention_long"]
    last = pd.Period(period["months"][-1], "M")
    censored = (pd.PeriodIndex(long.cohort, freq="M") + long.months_since_signup.values) > last
    assert long.loc[censored, ["retained", "retention_pct"]].isna().all().all()
    assert long.loc[~censored, ["retained", "retention_pct"]].notna().all().all()
    tri = metrics["cohort_retention"].set_index("cohort")
    for i, (cohort, row) in enumerate(tri.iterrows()):
        ks = [c for c in tri.columns if c != "cohort_size"]
        observable = len(period["months"]) - i
        assert row[ks[:observable]].notna().all() and row[ks[observable:]].isna().all()


def test_cohort_sizes_match_customers(metrics, data):
    sizes = month_of(data["customers"].signup_date).value_counts()
    tri = metrics["cohort_retention"].set_index("cohort").cohort_size
    assert (tri == sizes.reindex(tri.index)).all()


def test_observed_ltv_times_cohort_size_equals_cohort_revenue(metrics, data):
    o = paid(data["orders"]).merge(data["customers"][["customer_id", "signup_date"]])
    o["cohort"] = month_of(o.signup_date)
    ref = data["refunds"].merge(o[["order_id", "cohort"]], on="order_id")
    rev = o.groupby("cohort").net_price.sum() - ref.groupby("cohort").amount.sum().reindex(
        o.cohort.unique()).fillna(0)
    ltv = metrics["ltv_observed"].query("channel == 'ALL' and cohort != 'ALL'").set_index("cohort")
    assert np.allclose(ltv.revenue_ltv_to_date * ltv.customers_acquired, rev.reindex(ltv.index), atol=CENT)


# ============================================================================= data integrity
def test_no_orders_after_cancel_without_reactivate(data):
    o = paid(data["orders"]).sort_values("order_date")
    ev = data["subscription_events"].sort_values("event_date")
    last_cancel = pd.merge_asof(o, ev[ev.event_type == "cancel"][["customer_id", "event_date"]]
                                .rename(columns={"event_date": "cancel_date"}),
                                left_on="order_date", right_on="cancel_date", by="customer_id")
    last_react = pd.merge_asof(last_cancel, ev[ev.event_type == "reactivate"][["customer_id", "event_date"]]
                               .rename(columns={"event_date": "react_date"}),
                               left_on="order_date", right_on="react_date", by="customer_id")
    bad = last_react[last_react.cancel_date.notna()
                     & (last_react.react_date.isna() | (last_react.react_date < last_react.cancel_date))]
    assert bad.empty, bad.head()


def test_no_negative_prices_or_costs(data):
    assert (data["orders"][["gross_price", "discount", "net_price"]] >= 0).all().all()
    assert (data["orders"].discount <= data["orders"].gross_price).all()
    assert (data["order_costs"].drop(columns="order_id") >= 0).all().all()
    assert (data["refunds"][["amount", "fee"]] >= 0).all().all()
    for t, col in [("payments", "amount"), ("cash_payables", "amount"),
                   ("marketing_spend", "spend"), ("fixed_opex", "amount")]:
        assert (data[t][col] >= 0).all(), t


def test_dates_within_period_except_lagged_cash(data, a, period):
    s, e = period["start"], period["end"]
    business = [("customers", "signup_date"), ("orders", "order_date"),
                ("subscription_events", "event_date"), ("refunds", "refund_date"),
                ("shipments", "ship_date"), ("cash_payables", "incurred_date")]
    for t, col in business:
        assert data[t][col].between(s, e).all(), f"{t}.{col} outside period"
    for t in ["marketing_spend", "fixed_opex"]:
        assert data[t].month.isin(period["months"]).all()
    # lagged cash dates: allowed after period end, but only by the stated lags
    retry = max(a["simulation"]["dunning_retry_days"])
    lag = a["cash"]["card_payout_lag_days"]
    p = data["payments"]
    assert p.attempt_date.between(s, e + pd.Timedelta(days=retry)).all()
    assert p.payout_date.dropna().between(s, e + pd.Timedelta(days=retry + lag)).all()
    latest_pay = max(e + pd.Timedelta(days=a["cash"]["pharmacy_payment_terms_days"]),
                     e + pd.offsets.MonthEnd(a["cash"]["ad_spend_payment_lag_months"]))
    assert data["cash_payables"].paid_date.between(s, latest_pay).all()


@pytest.mark.slow
def test_generator_is_deterministic(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "src" / "generate_data.py"), "--out", str(tmp_path)],
                   check=True, capture_output=True)
    md5 = lambda p: hashlib.md5(p.read_bytes()).hexdigest()
    committed = sorted((ROOT / "data").glob("*.csv"))
    assert committed
    for f in committed:
        assert md5(f) == md5(tmp_path / f.name), f"{f.name} differs on re-run"
