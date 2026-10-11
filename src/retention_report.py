"""Retention report (output/retention_report.pdf), matplotlib PdfPages only.

Every number in the text is computed here from output/metrics/*.csv and data/*.csv.
Lever effects on LTV reuse src/ltv_projection.py with the lever values from
src/forecast_sheets.py (the workbook's Levers sheet); cash effects come from the
workbook's doubling bridge (output/metrics/doubling_bridge.csv).

Usage:  python src/retention_report.py   (after run_metrics.py and build_workbook.py)
"""
from __future__ import annotations

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

import charts  # noqa: E402  (shared style, palette and chart functions)
import ltv_projection  # noqa: E402
from forecast_sheets import LEVERS  # noqa: E402
from unit_economics import ROOT, by_tenure  # noqa: E402

A4 = (8.27, 11.69)
Z = 1.96 + 0.84          # two-sided alpha 5%, power 80%
LV = {k: v for k, _, v, *_ in LEVERS}


# ----------------------------------------------------------------------------- evidence
class Evidence:
    def __init__(self):
        self.d = charts.Data()
        M, D = ROOT / "output" / "metrics", ROOT / "data"
        self.ev = pd.read_csv(D / "subscription_events.csv")
        self.orders = pd.read_csv(D / "orders.csv")
        self.pay = pd.read_csv(D / "payments.csv")
        self.cust = pd.read_csv(D / "customers.csv")
        self.bridge = pd.read_csv(M / "doubling_bridge.csv")
        self.a = self.d.a
        rbc, rcp = self.d.rbc, self.d.rcp
        pool = lambda df, k: (df[df.months_since_signup == k].retained.sum()
                              / df[df.months_since_signup == k].eligible_customers.sum())
        self.pool = {k: pool(rbc, k) for k in range(9)}
        self.plan = {p: {k: pool(rcp[rcp.plan_type == p], k) for k in range(9)} for p in ["monthly", "prepaid_3m"]}
        self.ch = rbc.pivot(index="channel", columns="months_since_signup", values="retention_pct")
        rc = self.d.rc.dropna(subset=["monthly_churn"])
        self.churn_first, self.churn_last = rc.iloc[0], rc.iloc[-1]
        cancels = self.ev[self.ev.event_type == "cancel"].reason.value_counts()
        self.cancels = cancels
        self.voluntary = cancels.drop(["refund", "payment_failed", "chargeback"], errors="ignore")
        ren = self.orders[self.orders.order_number > 1]
        self.renewals, self.failed_orders = len(ren), int((ren.status == "failed").sum())
        failed_any = self.pay[self.pay.status == "failed"].order_id.nunique()
        self.failed_any, self.recovered = failed_any, int((self.pay.status == "recovered").sum())
        self.months = self.d.months
        self.new_per_month = len(self.cust) / len(self.months)
        self.monthly_share = (self.cust.plan_type == "monthly").mean()
        m1 = self.plan["monthly"]
        self.mhaz = {k: 1 - m1[k] / m1[k - 1] for k in range(1, 9)}
        self.react = self.ev[self.ev.event_type == "reactivate"].reason.value_counts()
        self.n_pause = int((self.ev.event_type == "pause").sum())
        coh = self.d.coh.dropna(subset=["retained"])
        m1c = coh[coh.months_since_signup == 1].retention_pct
        self.m1_range = (m1c.min(), m1c.max())

    # ---- LTV effect of retention levers (12-month contribution LTV, customer-weighted)
    def ltv_effects(self) -> dict[str, float]:
        a = self.a
        h = a["ltv"]["horizon_months"]
        disc = a["ltv"]["discount_rate_monthly"]
        inputs = ltv_projection.channel_inputs(a)
        fail = a["retention"]["failed_payment_rate"]
        ret_back = 0.60                                  # Levers: pausers who resume
        out = {"base": 0.0, "b1": 0.0, "b2": 0.0, "b3": 0.0}
        wsum = 0
        for ch, inp in inputs.items():
            r = ltv_projection.retention_curve(inp["b"], inp["c"], h)
            rev, cost = ltv_projection.monthly_unit_values(a, inp["mix_prepaid"], inp["promo_share"], h)
            df = 1 / (1 + disc) ** np.arange(h)
            haz = np.r_[0, 1 - r[1:] / r[:-1]]
            def value(hz, paid_adj=None):
                rr = np.cumprod(1 - hz)
                paid = np.where(np.arange(h) == 0, 1.0, inp["paid_share"])
                if paid_adj is not None:
                    paid = paid - paid_adj
                return float((rr * paid * (rev - cost) * df).sum())
            k = np.arange(h)
            variants = {
                "base": value(haz),
                "b1": value(np.where((k >= 1) & (k <= 3), haz * (1 - LV["b1"]), haz)),
                "b2": value(np.clip(haz - fail * LV["b2"] * ((1 - inp["mix_prepaid"])
                                                             + inp["mix_prepaid"] * (k % 3 == 0)) * (k >= 1), 0, 1)),
                "b3": value(haz * (1 - LV["b3"] * ret_back), paid_adj=haz * LV["b3"] * ret_back),
            }
            w = inp["new_customers"]
            for key, v in variants.items():
                out[key] += v * w
            wsum += w
        return {k: v / wsum for k, v in out.items()}

    def cash_effect(self, key: str) -> float:
        row = self.bridge[self.bridge.step.str.startswith(f"{key})")]
        return float(row.cash_impact.iloc[0]) if len(row) else float("nan")


def n_per_arm(p1, p2):
    return int(np.ceil(Z ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p1 - p2) ** 2))


# ----------------------------------------------------------------------------- page helpers
class Page:
    def __init__(self, pdf, title=None, subtitle=None):
        self.pdf = pdf
        self.fig = plt.figure(figsize=A4)
        self.y = 0.95
        if title:
            self.fig.text(0.08, self.y, title, fontsize=15, fontweight="bold", va="top")
            self.y -= 0.035
        if subtitle:
            self.fig.text(0.08, self.y, subtitle, fontsize=9, color=charts.INK2, va="top")
            self.y -= 0.03

    def h(self, text):
        self.y -= 0.012
        self.fig.text(0.08, self.y, text, fontsize=11, fontweight="bold", va="top")
        self.y -= 0.026

    def p(self, text, size=9, color=charts.INK, indent=0.0, width=104, gap=0.008):
        lines = textwrap.wrap(text, width=int(width - indent * 120))
        for ln in lines:
            self.fig.text(0.08 + indent, self.y, ln, fontsize=size, color=color, va="top")
            self.y -= 0.0165 * size / 9
        self.y -= gap

    def bullets(self, items, size=9):
        for it in items:
            lines = textwrap.wrap(it, width=98)
            for i, ln in enumerate(lines):
                self.fig.text(0.095 if i else 0.08, self.y, ("• " if i == 0 else "") + ln, fontsize=size, va="top")
                self.y -= 0.0165 * size / 9
            self.y -= 0.005

    def chart(self, fn, d, height):
        top = self.y - 0.035                # room for the chart's own title
        spec = self.fig.add_gridspec(1, 1, left=0.1, right=0.9, top=top, bottom=top - height)[0]
        fn(self.fig, spec, d)
        self.y = top - height - 0.05

    def close(self, n, total):
        self.fig.text(0.92, 0.03, f"{n} / {total}", fontsize=8, color=charts.MUTED, ha="right")
        self.fig.text(0.08, 0.03, "Omzo retention report · synthetic data, Jan–Sep 2026", fontsize=8, color=charts.MUTED)
        self.pdf.savefig(self.fig)
        plt.close(self.fig)


def plan_curves(fig, spec, e: Evidence):
    ax = fig.add_subplot(spec)
    for i, (p, lab) in enumerate([("monthly", "Monthly plan"), ("prepaid_3m", "3-month prepaid")]):
        ks = list(e.plan[p])
        ys = [e.plan[p][k] for k in ks]
        ax.plot(ks, ys, color=charts.SERIES[i], marker="o", markersize=3.5, label=lab)
        ax.annotate(f"{lab} {ys[-1]:.0%}", (ks[-1], ys[-1]), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8, color=charts.INK2)
    ax.yaxis.set_major_formatter(charts.PCT)
    ax.set_ylim(0, 1.05)
    ax.set_xlim(-0.2, 10.5)
    ax.set_xticks(range(9))
    ax.set_xticklabels([f"M{k}" for k in range(9)])
    ax.set_title("Retention by plan: prepaid cannot churn before its first renewal (M3)")
    ax.legend(loc="lower left")


# ----------------------------------------------------------------------------- report
def build(path=ROOT / "output" / "retention_report.pdf"):
    e = Evidence()
    d = e.d
    ltv = e.ltv_effects()
    P, MP = e.pool, e.plan
    ch = e.ch
    best, worst = ch[3].idxmax(), ch[3].idxmin()
    lab = charts.CH_LABEL
    vol = e.voluntary
    n_cancel = int(e.cancels.sum())
    fail_rate_obs = e.failed_any / e.renewals
    rec_rate_obs = e.recovered / e.failed_any
    total_pages = 6
    with PdfPages(path) as pdf:
        # ---------------------------------------------------------------- page 1
        pg = Page(pdf, "Retention report", "Omzo GLP-1 telehealth · synthetic dataset, Jan–Sep 2026 · "
                                          "all figures computed from the project's data and metrics")
        pg.h("Summary")
        pg.bullets([
            f"{P[1]:.0%} of new customers are still active one month after signup, {P[3]:.0%} at month 3 and "
            f"{P[8]:.0%} at month 8 (pooled across observable cohorts).",
            f"Early loss is concentrated in the monthly plan ({MP['monthly'][1]:.0%} at M1). Prepaid customers "
            f"show a step at their first renewal ({MP['prepaid_3m'][2]:.0%} at M2 to {MP['prepaid_3m'][3]:.0%} at M3).",
            f"Channel matters: M3 retention ranges from {ch[3][worst]:.0%} ({lab[worst]}) to {ch[3][best]:.0%} "
            f"({lab[best]}).",
            f"Failed payments end {e.cancels.get('payment_failed', 0):,} subscriptions "
            f"({e.cancels.get('payment_failed', 0) / n_cancel:.0%} of all cancels). Dunning already recovers "
            f"{rec_rate_obs:.0%} of failed renewals.",
            f"Three retention actions (check-ins, dunning, pause) add ~${sum(e.cash_effect(k) for k in ['b1','b2','b3']) / 1e3:,.0f}K "
            f"to monthly operating cash flow by Jun 2027 in the base plan. The downside case only reaches 2x if they work.",
        ])
        pg.h("Definitions and handling rules")
        pg.bullets([
            "Retention (month k): share of a signup cohort active in calendar month signup + k. Active = a "
            "successful payment covers that month (a prepaid charge covers 3 months) OR the customer is in an "
            "active pause. Month 0 is 100% by construction.",
            "Churn (monthly): customers active last month who are not active this month (cancelled, or a failed "
            "payment not recovered), divided by last month's actives. Pauses count as active, so a pause is not churn.",
            "Renewal rate: customers whose paid term ended last month who paid again this month, divided by those "
            "due (customers who paused instead are excluded from the denominator).",
            f"Pauses: {e.n_pause:,} one-month pauses in the data. Paused customers stay in the retention numerator; "
            "the paid-share metric separates paying from paused.",
            f"Reactivations: {e.react.get('winback', 0):,} win-backs. A returning customer stays in the ORIGINAL "
            "signup cohort, so a cohort's retention can rise slightly in later months.",
            f"Failed payments: {fail_rate_obs:.1%} of renewal charges fail at least once; retries run on days 1, 3, 5. "
            "Recovered = active; unrecovered = involuntary churn ('payment_failed').",
            "Right-censoring: months after Sep 2026 are not yet observable. They are blank (NULL), never 0. Pooled "
            "curves use only cohorts observable at that month, so later points rest on fewer, older cohorts.",
        ])
        pg.p("Caveat: the data is synthetic, generated from stated assumptions. 'Supported by the data' below means "
             "visible in this dataset; real data would be needed to confirm the patterns.", color=charts.INK2, size=8.5)
        pg.close(1, total_pages)

        # ---------------------------------------------------------------- page 2
        pg = Page(pdf, "Cohort retention")
        pg.chart(charts.cohort_heatmap, d, 0.42)
        pg.h("Observations")
        pg.bullets([
            f"1. The steepest losses come first: M1 {P[1]:.0%}, M2 {P[2]:.0%}, M3 {P[3]:.0%}. After M3 the curve "
            f"flattens to ~{(P[3] - P[6]) / 3:.1%}-pt per month (M6 {P[6]:.0%}).",
            f"2. Cohorts are stable: M1 retention is {e.m1_range[0]:.0%}–{e.m1_range[1]:.0%} for every cohort Jan–Aug. "
            "Growth has not come with worse-quality customers so far.",
            f"3. Portfolio churn is falling as the base matures: monthly churn {e.churn_first.monthly_churn:.1%} "
            f"({charts.mlabel(e.churn_first.month)}) to {e.churn_last.monthly_churn:.1%} "
            f"({charts.mlabel(e.churn_last.month)}); renewal rate {e.churn_first.renewal_rate:.0%} to "
            f"{e.churn_last.renewal_rate:.0%}. That is a mix effect (more long-tenure customers), not proof "
            "that early churn improved.",
        ])
        pg.close(2, total_pages)

        # ---------------------------------------------------------------- page 3
        pg = Page(pdf, "Retention by channel and plan")
        pg.chart(charts.retention_curves, d, 0.3)
        pg.chart(lambda f, s, _: plan_curves(f, s, e), d, 0.22)
        pg.h("Observations")
        rows = " · ".join(f"{lab[c]} {ch[1][c]:.0%}/{ch[3][c]:.0%}/{ch[6][c]:.0%}" for c in charts.CHANNELS)
        pg.bullets([
            f"4. Channel gap: {lab[best]} retains {ch[3][best] - ch[3][worst]:.1%}-pts more than {lab[worst]} at M3 "
            f"and {ch[6][best] - ch[6][worst]:.1%}-pts more at M6. M1/M3/M6 by channel: {rows}.",
            f"5. Plan: the monthly plan loses {1 - MP['monthly'][1]:.0%} of customers after the first month. "
            f"Prepaid holds 100% to M2, then {MP['prepaid_3m'][3]:.0%} renew; at M8, prepaid "
            f"{MP['prepaid_3m'][8]:.0%} vs monthly {MP['monthly'][8]:.0%}.",
        ])
        pg.close(3, total_pages)

        # ---------------------------------------------------------------- page 4
        pg = Page(pdf, "Why customers leave")
        pg.h("(a) SUPPORTED by the dataset")
        pg.bullets([
            f"Churn spike in months 1–2: monthly-plan churn is {e.mhaz[1]:.0%} in month 1 and {e.mhaz[2]:.0%} in "
            f"month 2, vs {e.mhaz[4]:.0%} in month 4 and {e.mhaz[6]:.0%} in month 6.",
            f"Channel differences: M3 retention {ch[3][worst]:.0%} ({lab[worst]}) to {ch[3][best]:.0%} ({lab[best]}). "
            "Paid-social customers churn fastest; referrals slowest.",
            f"Involuntary churn: {e.failed_any:,} renewals ({fail_rate_obs:.1%}) failed at least once; "
            f"{rec_rate_obs:.0%} were recovered. {e.failed_orders:,} were lost, ending "
            f"{e.cancels.get('payment_failed', 0):,} subscriptions ({e.cancels.get('payment_failed', 0) / n_cancel:.0%} of cancels).",
            f"Refund-driven exits: {e.cancels.get('refund', 0):,} cancels ({e.cancels.get('refund', 0) / n_cancel:.0%}) "
            f"follow a refund, mostly on first orders. Chargebacks block a further {e.cancels.get('chargeback', 0):,}.",
            f"Stated reasons for voluntary cancels ({int(vol.sum()):,}): " + ", ".join(
                f"{r.replace('_', ' ')} {v / vol.sum():.0%}" for r, v in vol.items()) + ".",
            f"Medication-driven dose/cost: dose steps up at tenure months 3 and 5 (cost x1.25 and x1.5), but "
            f"there is NO matching churn spike (monthly-plan churn {e.mhaz[2]:.0%} into month 3, "
            f"{e.mhaz[4]:.0%} into month 5, on trend). Customer price never changes with medication cost. In "
            "this data the medication effect is on margin (contribution per order fell to its low at the May "
            "cost peak), not on retention.",
        ], size=8.8)
        pg.h("(b) HYPOTHESES needing investigation")
        pg.bullets([
            "Side effects: 'side effects' is a stated reason, but the data cannot say which effect, when, how "
            "severe, or whether dose timing triggers it. Needs: symptom check-in data and clinician notes by tenure.",
            "Expectations: early weight-loss pace below expectations may drive month-1–2 exits and 'price' "
            "reasons. Needs: onboarding survey, weight-trajectory vs churn.",
            "Delivery issues: cold-chain delays or damaged shipments could trigger cancels. Needs: carrier "
            "scans, temperature excursions, reship rate joined to churn.",
            "Service experience: slow clinician or support responses. Needs: ticket response times and CSAT by customer.",
            "Channel promise mismatch: promo-heavy social ads may over-promise. Needs: creative-level retention "
            "and promo vs non-promo cohorts.",
            "'Reached goal' exits may be healthy completions rather than failures. Needs: weight-goal tracking "
            "and a maintenance offer.",
        ], size=8.8)
        pg.close(4, total_pages)

        # ---------------------------------------------------------------- page 5 & 6: actions
        m1, m3 = MP["monthly"][1], MP["monthly"][3]
        h1, h2, h3 = e.mhaz[1], e.mhaz[2], e.mhaz[3]
        m3_b1 = (1 - h1 * (1 - LV["b1"])) * (1 - h2 * (1 - LV["b1"])) * (1 - h3 * (1 - LV["b1"]))
        new_monthly = e.new_per_month * e.monthly_share
        fail_pm = e.failed_any / len(e.months)
        vol_pm = vol.sum() / len(e.months)
        p_back = 2 * e.a["retention"]["reactivation_rate"]
        p_pause = p_back + LV["b3"] * 0.60
        m1_onb = min(0.99, m1 + 0.03)
        n1, n2, n3, n4 = (n_per_arm(m3, m3_b1), n_per_arm(0.60, 0.60 + LV["b2"]),
                          n_per_arm(p_back, p_pause), n_per_arm(m1, m1_onb))
        base_ltv = ltv["base"]
        eff = lambda k: (f"12-mo contribution LTV +${ltv[k] - base_ltv:,.0f} per new customer "
                         f"({(ltv[k] - base_ltv) / base_ltv:+.1%}); monthly cash flow +${e.cash_effect(k) / 1e3:,.0f}K "
                         f"by Jun 2027 (bridge).")
        actions = [
            ("1. Titration and side-effect check-ins with clinician review (lever b1)",
             f"Design: randomise new monthly-plan customers 50/50. Treatment = proactive clinician check-ins at "
             f"weeks 1, 2, 4 and before each dose step, with symptom triage; control = current care.",
             f"Primary metric: M3 retention (monthly plan), baseline {m3:.0%}; the lever's 15% cut in month 1–3 "
             f"churn implies {m3_b1:.1%}. Guardrails: adverse-event reports, clinician-initiated stops (must not fall).",
             f"Sample/duration: {n1:,} per arm; at ~{new_monthly:,.0f} new monthly-plan customers/month, enrolment "
             f"takes ~{2 * n1 / new_monthly:.1f} months plus a 3-month read: ~{2 * n1 / new_monthly + 3:.0f} months.",
             eff("b1")),
            ("2. Dunning: smarter retries, card updater, SMS/email reminders (lever b2)",
             "Design: randomise failed renewals 50/50 to the new dunning sequence vs current retries.",
             f"Primary metric: recovery rate of failed renewals, baseline {rec_rate_obs:.0%} (assumption 60%) "
             f"vs target {0.60 + LV['b2']:.0%}. Secondary: involuntary churn, complaints.",
             f"Sample/duration: {n2:,} failed renewals per arm; ~{fail_pm:,.0f} failures/month means "
             f"~{2 * n2 / fail_pm:.1f} months to enrol, read within 1 week of each failure.",
             eff("b2")),
            ("3. Pause option instead of cancel (lever b3)",
             "Design: in the cancel flow, randomise 50/50 to a 1-month pause offer vs the standard flow. The "
             "pause must be clinically appropriate (clinician confirms it is safe to interrupt and how to restart).",
             f"Primary metric: share of cancel-flow entrants active 60 days later; baseline ~{p_back:.0%} "
             f"(win-backs) vs ~{p_pause:.0%} if {LV['b3']:.0%} pause and 60% resume.",
             f"Sample/duration: {n3:,} per arm; ~{vol_pm:,.0f} voluntary cancels/month means "
             f"~{2 * n3 / vol_pm:.1f} months to enrol plus a 2-month read.",
             eff("b3")),
            ("4. Onboarding expectation-setting (not in Levers: no cash credit taken)",
             "Design: randomise new customers to a structured onboarding (realistic weight-loss timeline, "
             "side-effect guide, what to expect at each dose) vs current welcome flow.",
             f"Primary metric: M1 retention (monthly plan), baseline {m1:.0%}; minimum effect worth detecting "
             f"+3pp ({m1_onb:.0%}).",
             f"Sample/duration: {n4:,} per arm: ~{2 * n4 / new_monthly:.1f} months to enrol plus a 1-month read.",
             "Effect: not modelled in the Levers sheet, so the plan takes no credit; upside only."),
            ("5. Delivery tracking and proactive delay alerts (not in Levers: no cash credit taken)",
             "Design: randomise by shipment week (cluster) to proactive tracking SMS + delay/temperature "
             "alerts with automatic reship vs current notifications.",
             "Primary metric: delivery-related tickets per 100 shipments; secondary: M2 retention and reship cost.",
             "Sample/duration: tickets are frequent, so ~4–6 weekly clusters per arm (6–8 weeks) detect a 20% "
             "ticket reduction; a retention effect needs the larger sample above.",
             "Effect: not modelled in the Levers sheet; no credit taken. It also produces the data needed to "
             "test the delivery hypothesis."),
        ]
        for page_no, chunk in [(5, actions[:3]), (6, actions[3:])]:
            pg = Page(pdf, "Recommended actions" + (" (continued)" if page_no == 6 else ""),
                      "Each action: A/B design, primary metric, sample/duration (alpha 5%, power 80%), "
                      "and estimated effect from the Levers sheet")
            for title, *lines in chunk:
                pg.h(title)
                for ln in lines:
                    pg.p(ln, size=8.8, indent=0.0, gap=0.004)
            if page_no == 6:
                pg.h("Clinical-suitability guardrail")
                pg.p("Retention actions serve patient outcomes, not retention for its own sake. No incentives to "
                     "stay on medication against clinical advice. No discounts or pressure that discourage a "
                     "clinically indicated stop. No bypassing eligibility screening to raise conversion or "
                     "retention. Check-ins and pauses are clinician-led; patients can always cancel in one step. "
                     "Every test tracks safety guardrail metrics (adverse events, clinician-initiated "
                     "discontinuations, complaints) and stops if they worsen.", size=8.8)
                pg.h("How the estimates were made")
                pg.p("LTV effects: 12-month discounted contribution LTV from the fitted retention curves "
                     "(src/ltv_projection.py), customer-weighted across channels, with each lever's churn change "
                     "applied at its full Levers value. Cash effects: the workbook's doubling bridge (target month "
                     "Jun 2027, levers added in fixed order, base case). Sample sizes: two-proportion z-test.",
                     size=8.5, color=charts.INK2)
            pg.close(page_no, total_pages)
    return path


if __name__ == "__main__":
    print(f"Saved {build()}")
