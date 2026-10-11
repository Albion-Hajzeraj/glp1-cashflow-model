"""Generate the synthetic GLP-1 telehealth dataset from assumptions.yaml.

Each customer is simulated chronologically, one billing cycle at a time, from signup to
the end of the period: charge -> (dunning) -> shipments -> refund/chargeback draw ->
churn / pause decision -> win-back. Every parameter comes from assumptions.yaml.

Usage:  python src/generate_data.py
"""
from __future__ import annotations

import argparse
import calendar
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from unit_economics import ROOT, by_tenure, load_assumptions, shipment_unit_costs

DATA = ROOT / "data"


# ----------------------------------------------------------------------------- dates
def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def month_start(key: str) -> date:
    y, m = map(int, key.split("-"))
    return date(y, m, 1)


def month_end(key: str) -> date:
    y, m = map(int, key.split("-"))
    return date(y, m, calendar.monthrange(y, m)[1])


def add_months(d: date, k: int) -> date:
    """Shift by k months; safe because anchor days are <= 28."""
    y, m = divmod(d.month - 1 + k, 12)
    return date(d.year + y, m + 1, d.day)


def end_of_month_after(key: str, k: int) -> date:
    return month_end(month_key(add_months(month_start(key), k)))


# ----------------------------------------------------------------------------- simulator
class Simulator:
    def __init__(self, a: dict):
        self.a = a
        self.sim = a["simulation"]
        self.rng = np.random.default_rng(a["period"]["random_seed"])
        self.months = [str(m) for m in a["period"]["months"]]
        self.period_start = month_start(self.months[0])
        self.period_end = month_end(self.months[-1])
        self.med_cost = dict(zip(self.months, a["medication"]["cost_per_month_supply"]))
        self.involuntary_p = 1 - a["retention"]["failed_payment_recovery"]
        reasons = self.sim["cancel_reasons"]
        self.reason_names = list(reasons)
        self.reason_p = np.array(list(reasons.values())) / sum(reasons.values())

        self.customers, self.orders, self.payments, self.refunds = [], [], [], []
        self.events, self.shipments, self.served = [], [], []
        self.n_order = self.n_payment = self.n_refund = 0

    # -- helpers
    def rand_day_in(self, key: str) -> date:
        return month_start(key) + timedelta(int(self.rng.integers(0, self.sim["max_billing_day"])))

    def between(self, lo: date, hi: date) -> date:
        """Random date strictly after lo and strictly before hi (falls back to lo + 1)."""
        span = (hi - lo).days - 1
        return lo + timedelta(int(self.rng.integers(1, span + 1)) if span >= 1 else 1)

    def med_unit(self, d: date) -> float:
        return self.med_cost.get(month_key(d), self.med_cost[self.months[-1]])

    def event(self, cid, d, etype, reason):
        if d <= self.period_end:
            self.events.append((cid, d, etype, reason))

    # -- acquisition
    def new_customer_counts(self) -> dict[tuple[str, str], int]:
        sd = self.sim["new_customer_noise_sd"]
        counts = {}
        for ch, cfg in self.a["channels"].items():
            for i, m in enumerate(self.months):
                expected = cfg["spend"][i] / cfg["expected_cpa"]
                counts[(m, ch)] = max(0, int(round(expected * (1 + self.rng.normal(0, sd)))))
        return counts

    def run(self) -> None:
        plans = self.a["plans"]
        plan_names = list(plans)
        plan_p = np.array([plans[p]["mix"] for p in plan_names])
        promo = self.a["promo"]
        n_cust = 0
        for (m, ch), n in self.new_customer_counts().items():
            for _ in range(n):
                n_cust += 1
                cid = f"C{n_cust:06d}"
                plan = plan_names[self.rng.choice(len(plan_names), p=plan_p)]
                promo_applied = plan == "monthly" and ch in promo["channels"]
                signup = self.rand_day_in(m)
                self.customers.append((cid, signup, ch, plan, promo_applied))
                self.simulate_customer(cid, signup, ch, plan, promo_applied)

    # -- one customer's life
    def simulate_customer(self, cid, signup, channel, plan, promo_applied) -> None:
        a, rng, ret = self.a, self.rng, self.a["retention"]
        p, promo = a["plans"][plan], a["promo"]
        term = p["term_months"]
        churn_mult = a["channels"][channel]["churn_multiplier"] * p.get("churn_multiplier", 1.0)
        pause_p = 1 - (1 - ret["monthly_pause_rate"]) ** term

        self.event(cid, signup, "start", "new_signup")
        bill, tenure, order_no, block_date = signup, 0, 0, None

        while bill <= self.period_end:
            if block_date and block_date <= bill:           # chargeback -> account blocked
                self.event(cid, block_date, "cancel", "chargeback")
                return
            order_no += 1
            first = order_no == 1
            next_bill = add_months(bill, term)

            # ---- charge + dunning
            gross = p["price"]
            discount = round(gross * promo["first_month_discount"], 2) if first and promo_applied else 0.0
            net = round(gross - discount, 2)
            self.n_order += 1
            oid = f"O{self.n_order:07d}"
            paid_on = self.charge(oid, bill, net, can_fail=not first)
            if paid_on is None:                             # involuntary churn
                self.orders.append([oid, cid, bill, order_no, plan, gross, discount, net, "failed"])
                last_try = bill + timedelta(self.sim["dunning_retry_days"][-1])
                self.event(cid, last_try, "cancel", "payment_failed")
                bill = self.winback(cid, last_try, block_date)
                if bill is None:
                    return
                continue

            # ---- service: one shipment per covered month
            covered = list(range(tenure + 1, tenure + term + 1))
            for k, t in enumerate(covered):
                ship = paid_on if k == 0 else add_months(bill, k)
                self.ship(oid, cid, ship, t)
                self.served.append((cid, month_key(add_months(bill, k))))
            tenure += term

            # ---- refund / chargeback
            status = "completed"
            refunded = self.refund_or_chargeback(oid, bill, net, first)
            if refunded == "chargeback" and self.sim["chargeback_blocks_customer"]:
                cb_date = self.refunds[-1][2]
                block_date = min(block_date or cb_date, cb_date)
            if refunded:
                status = "refunded"
            self.orders.append([oid, cid, bill, order_no, plan, gross, discount, net, status])

            # ---- churn / pause decision for the next cycle
            if refunded == "refund" and self.sim["refund_triggers_cancel"]:
                reason = "refund"
            elif any(rng.random() < min(1.0, by_tenure(ret["churn_by_tenure"], t) * churn_mult)
                     for t in covered):
                reason = self.reason_names[rng.choice(len(self.reason_names), p=self.reason_p)]
            else:
                reason = None

            if reason:
                cancel_on = self.between(bill, next_bill)
                if block_date and block_date <= cancel_on:
                    self.event(cid, block_date, "cancel", "chargeback")
                    return
                self.event(cid, cancel_on, "cancel", reason)
                bill = self.winback(cid, cancel_on, block_date, earliest=next_bill)
                if bill is None:
                    return
            elif rng.random() < pause_p:
                if block_date and block_date <= next_bill:
                    bill = next_bill                         # loop top cancels on block date
                    continue
                resume = add_months(next_bill, ret["pause_duration_months"])
                self.event(cid, next_bill, "pause", "customer_request")
                self.event(cid, resume, "reactivate", "pause_ended")
                bill = resume
            else:
                bill = next_bill

        if block_date and block_date <= self.period_end:
            self.event(cid, block_date, "cancel", "chargeback")

    def charge(self, oid, bill, amount, can_fail) -> date | None:
        """Record payment attempts; return the successful payment date or None."""
        rng, ret, lag = self.rng, self.a["retention"], self.a["cash"]["card_payout_lag_days"]

        def pay(d, status):
            self.n_payment += 1
            payout = d + timedelta(lag) if status != "failed" else None
            self.payments.append((f"P{self.n_payment:07d}", oid, d, amount, status, payout))

        if not can_fail or rng.random() >= ret["failed_payment_rate"]:
            pay(bill, "success")
            return bill
        pay(bill, "failed")
        retries = self.sim["dunning_retry_days"]
        recovered_at = int(rng.integers(len(retries))) if rng.random() < ret["failed_payment_recovery"] else None
        for i, days in enumerate(retries):
            d = bill + timedelta(days)
            if i == recovered_at:
                pay(d, "recovered")
                return d
            pay(d, "failed")
        return None

    def ship(self, oid, cid, ship_date, tenure_month) -> None:
        u = shipment_unit_costs(self.a)
        dose = by_tenure(self.a["medication"]["dose_multiplier_by_tenure"], tenure_month)
        self.shipments.append({
            "order_id": oid, "customer_id": cid, "ship_date": ship_date, "tenure_month": tenure_month,
            "medication": round(self.med_unit(ship_date) * dose, 2),
            "pharmacy_dispensing": u["pharmacy_dispensing"],
            "clinical_consult": u["consult_initial"] if tenure_month == 1 else u["consult_follow_up"],
            "shipping": u["shipping"],
            "packaging_fulfillment": u["packaging_fulfillment"],
        })

    def refund_or_chargeback(self, oid, bill, net, first) -> str | None:
        rng, a, sim = self.rng, self.a, self.sim
        ref, cb = a["refunds"], a["chargebacks"]
        r_rate = ref["first_order_rate"] if first else ref["renewal_rate"]
        c_rate = cb["first_order_rate"] if first else cb["renewal_rate"]
        dpm = a["cash"]["days_per_month"]
        u = rng.random()
        if u < r_rate:
            spread = sim["refund_share_spread"]
            share = min(1.0, ref["avg_refund_share"] * rng.uniform(1 - spread, 1 + spread))
            jitter = int(rng.integers(-sim["refund_jitter_days"], sim["refund_jitter_days"] + 1))
            d = bill + timedelta(max(1, ref["lag_months"] * dpm + jitter))
            kind, amount, fee = "refund", round(net * share, 2), 0.0
        elif u < r_rate + c_rate:
            jitter = int(rng.integers(-sim["chargeback_jitter_days"], sim["chargeback_jitter_days"] + 1))
            d = bill + timedelta(max(1, cb["lag_months"] * dpm + jitter))
            kind, amount, fee = "chargeback", net, cb["fee_per_chargeback"]
        else:
            return None
        if d > self.period_end:          # dataset is a snapshot at period end
            return None
        self.n_refund += 1
        self.refunds.append((f"R{self.n_refund:06d}", oid, d, amount, kind, fee))
        return kind

    def winback(self, cid, cancel_on, block_date, earliest: date | None = None) -> date | None:
        """Monthly chance to come back within the window, never before the paid term ends
        (`earliest`); returns the restart billing date."""
        ret = self.a["retention"]
        if block_date:
            return None
        for k in range(1, ret["reactivation_window_months"] + 1):
            d = self.rand_day_in(month_key(add_months(cancel_on.replace(day=1), k)))
            if d > self.period_end:
                return None
            if earliest and d < earliest:
                continue
            if self.rng.random() < ret["reactivation_rate"]:
                self.event(cid, d, "reactivate", "winback")
                return d
        return None

    # ------------------------------------------------------------------------- tables
    def tables(self) -> dict[str, pd.DataFrame]:
        a, cash = self.a, self.a["cash"]
        vc_cols = ["medication", "pharmacy_dispensing", "clinical_consult", "shipping",
                   "packaging_fulfillment"]

        customers = pd.DataFrame(self.customers, columns=[
            "customer_id", "signup_date", "channel", "plan_type", "promo_applied"])
        orders = pd.DataFrame(self.orders, columns=[
            "order_id", "customer_id", "order_date", "order_number", "plan_type",
            "gross_price", "discount", "net_price", "status"]).sort_values("order_id")
        payments = pd.DataFrame(self.payments, columns=[
            "payment_id", "order_id", "attempt_date", "amount", "status", "payout_date"])
        refunds = pd.DataFrame(self.refunds, columns=[
            "refund_id", "order_id", "refund_date", "amount", "type", "fee"])
        events = (pd.DataFrame(self.events, columns=["customer_id", "event_date", "event_type", "reason"])
                  .sort_values(["customer_id", "event_date"], kind="stable"))

        marketing = pd.DataFrame([(m, ch, cfg["spend"][i])
                                  for ch, cfg in a["channels"].items()
                                  for i, m in enumerate(self.months)],
                                 columns=["month", "channel", "spend"])

        dose = a["medication"]["dose_multiplier_by_tenure"]
        med = pd.DataFrame([(m, t, f"{t}+" if t == len(dose) else str(t), self.med_cost[m],
                             dose[t - 1], round(self.med_cost[m] * dose[t - 1], 2))
                            for m in self.months for t in range(1, len(dose) + 1)],
                           columns=["month", "tenure_month", "tenure_label", "base_med_cost",
                                    "dose_multiplier", "unit_med_cost"])

        # order_costs covers every shipment an order pays for, including prepaid months
        # scheduled after period end. shipments.csv and payables are a snapshot at
        # period end, so they only hold shipments that actually happened by then.
        all_ships = pd.DataFrame(self.shipments)
        pp = a["variable_costs"]["payment_processing"]
        done = orders[orders["status"] != "failed"]
        costs = all_ships.groupby("order_id")[vc_cols].sum()
        costs["shipments"] = all_ships.groupby("order_id").size()
        costs = costs.join(done.set_index("order_id")[["net_price"]], how="inner")
        costs["payment_processing"] = (costs["net_price"] * pp["pct_of_charge"]
                                       + pp["fixed_per_charge"]).round(2)
        costs["total_variable_cost"] = costs[vc_cols + ["payment_processing"]].sum(axis=1)
        order_costs = costs.drop(columns="net_price").reset_index().round(2)

        ships = all_ships[all_ships["ship_date"] <= self.period_end].copy()
        ships["month"] = ships["ship_date"].map(month_key)
        shipments = ships.sort_values(["ship_date", "order_id"], kind="stable").reset_index(drop=True)
        shipments.insert(0, "shipment_id", [f"S{i + 1:07d}" for i in range(len(shipments))])
        shipments = shipments[["shipment_id", "order_id", "customer_id", "ship_date", "tenure_month"]]

        fixed = pd.DataFrame([(m, cat, vals[i]) for cat, vals in a["fixed_opex"].items()
                              for i, m in enumerate(self.months)],
                             columns=["month", "category", "amount"])

        # payables: pharmacy invoiced per shipment day (net-30); others by month
        pay_rows = []
        ships["pharmacy"] = ships["medication"] + ships["pharmacy_dispensing"]
        for d, amt in ships.groupby("ship_date")["pharmacy"].sum().items():
            pay_rows.append(("pharmacy", month_key(d), d, amt,
                             d + timedelta(cash["pharmacy_payment_terms_days"])))
        for col in ["clinical_consult", "shipping", "packaging_fulfillment"]:
            for m, amt in ships.groupby("month")[col].sum().items():
                pay_rows.append((col, m, month_end(m), amt, month_end(m)))
        proc = order_costs.merge(orders[["order_id", "order_date"]], on="order_id")
        proc["month"] = proc["order_date"].map(month_key)
        for m, amt in proc.groupby("month")["payment_processing"].sum().items():
            pay_rows.append(("payment_processing", m, month_end(m), amt, month_end(m)))
        for m, amt in marketing.groupby("month")["spend"].sum().items():
            pay_rows.append(("marketing", m, month_end(m), amt,
                             end_of_month_after(m, cash["ad_spend_payment_lag_months"])))
        for m, amt in fixed.groupby("month")["amount"].sum().items():
            pay_rows.append(("fixed_opex", m, month_end(m), amt, month_end(m)))
        payables = pd.DataFrame(pay_rows, columns=[
            "expense_type", "incurred_month", "incurred_date", "amount", "paid_date"]).round(2)

        self._ships, self._served = ships, pd.DataFrame(self.served, columns=["customer_id", "month"])
        return {
            "customers": customers, "marketing_spend": marketing, "orders": orders,
            "payments": payments, "refunds": refunds, "subscription_events": events,
            "med_cost_monthly": med, "order_costs": order_costs, "shipments": shipments,
            "fixed_opex": fixed, "cash_payables": payables,
        }


# ----------------------------------------------------------------------------- reporting
def monthly_summary(t: dict, sim: Simulator) -> pd.DataFrame:
    months = sim.months
    cust = t["customers"].assign(month=lambda d: d["signup_date"].map(month_key))
    new = cust.pivot_table(index="month", columns="channel", values="customer_id",
                           aggfunc="count").reindex(months).fillna(0).astype(int)
    new.columns = [f"new_{c}" for c in new.columns]
    out = new.assign(new_total=new.sum(axis=1))

    out["active_customers"] = sim._served.drop_duplicates().groupby("month").size().reindex(months)

    orders = t["orders"].assign(month=lambda d: d["order_date"].map(month_key))
    done = orders[orders["status"] != "failed"]
    out["completed_orders"] = done.groupby("month").size().reindex(months)

    # net revenue: prepaid recognized ratably over its term, less refunds/chargebacks in month
    term = {p: v["term_months"] for p, v in sim.a["plans"].items()}
    rec = defaultdict(float)
    for d, plan, net in done[["order_date", "plan_type", "net_price"]].itertuples(index=False):
        n = term[plan]
        for k in range(n):
            rec[month_key(add_months(d, k))] += net / n
    refunds = t["refunds"].assign(month=lambda d: d["refund_date"].map(month_key))
    out["net_revenue"] = (pd.Series(rec).reindex(months)
                          - refunds.groupby("month")["amount"].sum().reindex(months).fillna(0))

    ships = sim._ships
    vc = ships.groupby("month")[["medication", "pharmacy_dispensing", "clinical_consult",
                                 "shipping", "packaging_fulfillment"]].sum().sum(axis=1)
    proc = t["order_costs"].merge(orders[["order_id", "month"]], on="order_id")
    vc = vc.add(proc.groupby("month")["payment_processing"].sum(), fill_value=0)
    vc = vc.add(refunds.groupby("month")["fee"].sum(), fill_value=0)
    out["variable_costs"] = vc.reindex(months)
    out["marketing_spend"] = t["marketing_spend"].groupby("month")["spend"].sum().reindex(months)
    return out.round(0)


def sanity_checks(t: dict, sim: Simulator) -> list[tuple[str, bool, str]]:
    orders, costs, ev = t["orders"], t["order_costs"], t["subscription_events"]
    checks = []

    neg = (orders[["gross_price", "discount", "net_price"]] < 0).any(axis=1).sum()
    checks.append(("no negative prices", neg == 0, f"{neg} rows"))

    # last subscription event on or before each successful order must not be cancel/pause
    done = (orders[orders["status"] != "failed"]
            .assign(order_date=lambda d: pd.to_datetime(d["order_date"])).sort_values("order_date"))
    e = ev.assign(event_date=lambda d: pd.to_datetime(d["event_date"])).sort_values("event_date",
                                                                                    kind="stable")
    j = pd.merge_asof(done, e, left_on="order_date", right_on="event_date",
                      by="customer_id", direction="backward")
    bad = j["event_type"].isin(["cancel", "pause"]).sum()
    checks.append(("no orders after cancel/pause unless reactivated", bad == 0, f"{bad} orders"))

    term_days = {p: v["term_months"] * 28 for p, v in load_assumptions()["plans"].items()}
    s = done.sort_values(["customer_id", "order_date"])
    gap = s.groupby("customer_id")["order_date"].diff().dt.days
    short = (gap < s["plan_type"].map(term_days)).sum()
    checks.append(("no overlapping paid terms (prepaid billed every 3 months)", short == 0,
                   f"{short} orders"))

    n_cost = costs.groupby("order_id").size()
    done_ids = set(done["order_id"])
    one_each = n_cost.reindex(list(done_ids)).eq(1).all() and set(n_cost.index) == done_ids
    checks.append(("every completed order has exactly one cost row", bool(one_each),
                   f"{len(done_ids)} orders / {len(costs)} cost rows"))

    pay = t["cash_payables"]
    shipped = sim._ships[["medication", "pharmacy_dispensing"]].to_numpy().sum()
    diff = abs(pay.loc[pay.expense_type == "pharmacy", "amount"].sum() - shipped)
    checks.append(("pharmacy payables reconcile to shipments in period", diff < 0.01, f"diff ${diff:,.2f}"))
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DATA, help="output folder (default: data/)")
    out = parser.parse_args().out

    a = load_assumptions()
    sim = Simulator(a)
    sim.run()
    tables = sim.tables()

    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out / f"{name}.csv", index=False)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    print("\nRow counts")
    for name, df in tables.items():
        print(f"  {name + '.csv':<26}{len(df):>9,}")

    print("\nMonthly summary (net revenue: prepaid recognized ratably, less refunds & chargebacks)")
    print(monthly_summary(tables, sim).T.to_string())

    print("\nSanity checks")
    checks = sanity_checks(tables, sim)
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
    if not all(ok for _, ok, _ in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
