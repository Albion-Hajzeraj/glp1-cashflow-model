"""Charts (PNG) and the KPI dashboard (PDF), matplotlib only.

Reads output/metrics/*.csv (SQL metrics, LTV projection, and forecast results exported by
build_workbook.py) plus data/ for a few volume counts. Writes output/charts/*.png
(overwritten each run) and output/dashboard.pdf.

Style: one light theme, validated categorical palette in fixed channel order (see the
dataviz palette: worst adjacent CVD dE 9.1), hairline solid grid, no dual axes (two
measures of different scale = two stacked panels), selective direct labels.

Usage:  python src/charts.py     (run src/build_workbook.py first for forecast CSVs)
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.ticker import FuncFormatter, PercentFormatter  # noqa: E402

from unit_economics import ROOT, load_assumptions  # noqa: E402

METRICS = ROOT / "output" / "metrics"
CHARTS = ROOT / "output" / "charts"

# ---- palette & chrome (light mode reference palette)
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CHANNELS = ["meta", "google", "tiktok", "affiliate", "organic_referral"]
CH_COLOR = dict(zip(CHANNELS, SERIES))          # colour follows the entity, fixed order
CH_LABEL = {"meta": "Meta", "google": "Google", "tiktok": "TikTok", "affiliate": "Affiliate",
            "organic_referral": "Organic / referral"}
POS, NEG, TOTAL = "#2a78d6", "#e34948", "#52514e"   # diverging poles + neutral for totals
SEQ = LinearSegmentedColormap.from_list("blue_seq", ["#cde2fb", "#86b6ef", "#2a78d6", "#1c5cab", "#0d366b"])

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 9, "axes.titlesize": 11,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 10,
    "axes.facecolor": SURFACE, "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK2,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.6, "grid.linestyle": "-",
    "axes.axisbelow": True, "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2,
    "ytick.labelcolor": INK2, "xtick.major.size": 0, "ytick.major.size": 0, "text.color": INK,
    "legend.frameon": False, "legend.fontsize": 8, "lines.linewidth": 2, "lines.markersize": 5,
    "text.parse_math": False,          # "$" is currency, never math mode
})


def usd(x, _=None):
    a = abs(x)
    s = f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:,.0f}"
    return "-" + s if x < 0 else s


USD = FuncFormatter(usd)
USD0 = FuncFormatter(lambda x, _=None: f"${x:,.0f}")
PCT = PercentFormatter(1.0, decimals=0)


def mlabel(m: str) -> str:
    return pd.Period(m, "M").strftime("%b %y")


# ----------------------------------------------------------------------------- data
class Data:
    def __init__(self):
        rd = lambda n: pd.read_csv(METRICS / f"{n}.csv")
        self.a = load_assumptions()
        self.pnl, self.cf, self.aov = rd("monthly_pnl"), rd("cash_flow"), rd("aov")
        self.acq, self.acq_p = rd("acquisition"), rd("acquisition_period").set_index("channel")
        self.ltv = rd("ltv_and_cpa").set_index("channel")
        self.coh = rd("cohort_retention_long")
        self.rbc, self.rcp = rd("retention_by_channel"), rd("retention_by_channel_plan")
        self.rc = rd("renewal_churn")
        try:
            self.fc, self.bridge = rd("forecast_scenarios"), rd("doubling_bridge")
            self.fsum = rd("forecast_summary").iloc[0]
        except FileNotFoundError as e:
            raise SystemExit("Forecast CSVs missing: run `python src/build_workbook.py` first") from e
        ship = pd.read_csv(ROOT / "data" / "shipments.csv", parse_dates=["ship_date"])
        self.shipments = ship.ship_date.dt.strftime("%Y-%m").value_counts().sort_index()
        self.months = self.pnl.month.tolist()
        self.base_month = self.fsum.baseline_month

    def kpis(self) -> list[tuple[str, str, str]]:
        m = self.base_month
        p = self.pnl.set_index("month").loc[m]
        cf = self.cf.set_index("month").loc[m]
        aov = self.aov.set_index("month").loc[m]
        bl = self.acq.query("month == @m and channel == 'ALL_BLENDED'").iloc[0]
        w = self.acq_p.loc[CHANNELS, "new_paying_customers"]
        ltv = (self.ltv.loc[CHANNELS, "ltv12_contribution"] * w).sum() / w.sum()
        payback = (self.ltv.loc[CHANNELS, "payback_months"] * w).sum() / w.sum()
        k3 = self.coh.query("months_since_signup == 3").dropna(subset=["retained"])
        m3 = k3.retained.sum() / k3.cohort_size.sum()
        ml = mlabel(m)
        return [
            ("Net revenue", usd(p.net_revenue), ml),
            ("AOV", f"${aov.aov_overall:,.0f}", f"{ml} · net revenue / order"),
            ("Blended CPA", f"${bl.cpa:,.0f}", ml),
            ("12-mo contribution LTV", f"${ltv:,.0f}", "projected, customer-weighted"),
            ("Payback", f"{payback:.1f} mo", "customer-weighted"),
            ("M3 retention", f"{m3:.0%}", "pooled cohorts Jan–Jun"),
            ("Contribution margin", f"{p.contribution_margin_pct:.1%}", ml),
            ("Net operating cash flow", usd(cf.net_operating_cash_flow), f"{ml} · baseline"),
            ("Closing cash", usd(cf.closing_cash), ml),
        ]


# ----------------------------------------------------------------------------- helpers
def end_label(ax, x, y, text, color=INK2, dx=4, dy=0, ha="left"):
    ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", va="center", ha=ha,
                fontsize=8, color=color)


def bar_labels(ax, bars, fmt, inside=False):
    for b in bars:
        h = b.get_height()
        ax.annotate(fmt(h), (b.get_x() + b.get_width() / 2, h), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=7.5, color=INK2)


def panels(fig, spec, n=2, ratios=None):
    gs = spec.subgridspec(n, 1, height_ratios=ratios or [1] * n, hspace=0.35)
    axs = [fig.add_subplot(gs[i]) for i in range(n)]
    for ax in axs[1:]:
        ax.sharex(axs[0])
    return axs


def xmonths(ax, months, every=1):
    """Compact two-line month ticks: 'Jan' with the year under the first tick and each January."""
    def lab(i, m):
        p = pd.Period(m, "M")
        return p.strftime("%b") + (p.strftime("\n%Y") if i == 0 or p.month == 1 else "")
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels([lab(i, m) if i % every == 0 else "" for i, m in enumerate(months)])


# ----------------------------------------------------------------------------- charts
def revenue_margin(fig, spec, d: Data):
    ax1, ax2 = panels(fig, spec, 2, [3, 2])
    x = np.arange(len(d.months))
    bars = ax1.bar(x, d.pnl.net_revenue, width=0.6, color=SERIES[0])
    ax1.yaxis.set_major_formatter(USD)
    ax1.set_title("Net revenue by month")
    bar_labels(ax1, [bars[0], bars[-1]], usd)
    ax2.plot(x, d.pnl.contribution_margin_pct, color=SERIES[1], marker="o")
    ax2.yaxis.set_major_formatter(PCT)
    ax2.set_ylim(0, max(0.5, d.pnl.contribution_margin_pct.max() * 1.2))
    ax2.set_title("Contribution margin %", fontsize=10)
    i = int(d.pnl.contribution_margin_pct.idxmin())
    end_label(ax2, i, d.pnl.contribution_margin_pct[i], f"low {d.pnl.contribution_margin_pct[i]:.0%} (med. cost peak)",
              dy=-10, ha="center", dx=0)
    end_label(ax2, x[-1], d.pnl.contribution_margin_pct.iloc[-1], f"{d.pnl.contribution_margin_pct.iloc[-1]:.0%}")
    xmonths(ax2, d.months)
    plt.setp(ax1.get_xticklabels(), visible=False)


def aov_chart(fig, spec, d: Data):
    ax = fig.add_subplot(spec)
    x = np.arange(len(d.months))
    for col, lab, c in [("aov_first_order", "First order", SERIES[0]), ("aov_repeat_order", "Repeat order", SERIES[1])]:
        y = d.aov[col]
        ax.plot(x, y, color=c, marker="o", label=lab)
        last = y.last_valid_index()
        end_label(ax, x[last], y[last], f"{lab} ${y[last]:,.0f}", color=INK2)
    ax.yaxis.set_major_formatter(USD0)
    ax.set_title("AOV: first-order vs repeat (net revenue / completed orders)")
    ax.legend(loc="lower left")
    xmonths(ax, d.months)
    ax.set_xlim(-0.4, len(x) + 1.6)


def cpa_chart(fig, spec, d: Data):
    ax = fig.add_subplot(spec)
    x = np.arange(len(CHANNELS))
    cpa = d.acq_p.loc[CHANNELS, "cpa"]
    bars = ax.bar(x, cpa, width=0.55, color=[CH_COLOR[c] for c in CHANNELS])
    bar_labels(ax, bars, lambda v: f"${v:,.0f}")
    tgt, mx = d.ltv.loc[CHANNELS, "target_cpa"], d.ltv.loc[CHANNELS, "max_cpa_4m"]
    ax.hlines(tgt, x - 0.36, x + 0.36, color=INK, linewidth=1.6, label="Target CPA (LTV / 3)")
    ax.hlines(mx, x - 0.36, x + 0.36, color=NEG, linewidth=1.6, label="Max CPA (4-month payback)")
    bl = d.acq_p.loc["ALL_BLENDED", "cpa"]
    ax.axhline(bl, color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
    end_label(ax, len(x) - 0.45, bl, f"Blended ${bl:,.0f}", color=INK2, dy=7, ha="right", dx=0)
    ax.set_xticks(x)
    ax.set_xticklabels([CH_LABEL[c] for c in CHANNELS])
    ax.yaxis.set_major_formatter(USD0)
    ax.set_title("CPA by channel vs blended, target and max CPA (Jan–Sep)")
    ax.legend(loc="upper left")
    ax.set_ylim(0, max(mx.max(), cpa.max()) * 1.18)


def ltv_vs_cpa(fig, spec, d: Data):
    ax = fig.add_subplot(spec)
    x = np.arange(len(CHANNELS))
    ltv, cpa = d.ltv.loc[CHANNELS, "ltv12_contribution"], d.ltv.loc[CHANNELS, "actual_cpa"]
    b1 = ax.bar(x - 0.17, ltv, width=0.32, color=SERIES[0], label="12-mo contribution LTV")
    b2 = ax.bar(x + 0.17, cpa, width=0.32, color=SERIES[1], label="CPA")
    bar_labels(ax, b1, lambda v: f"${v:,.0f}")
    for i, c in enumerate(CHANNELS):
        ax.annotate(f"{ltv[c] / cpa[c]:.1f}x", (x[i] + 0.17, cpa[c]), xytext=(0, 3), textcoords="offset points",
                    ha="center", fontsize=7.5, color=INK, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([CH_LABEL[c] for c in CHANNELS])
    ax.yaxis.set_major_formatter(USD0)
    ax.set_title("Projected 12-month contribution LTV vs CPA (label = LTV / CPA)")
    ax.legend(loc="upper left")
    ax.set_ylim(0, ltv.max() * 1.2)


def payback_chart(fig, spec, d: Data):
    ax = fig.add_subplot(spec)
    pb = d.ltv.loc[CHANNELS, "payback_months"].sort_values()
    y = np.arange(len(pb))
    ax.barh(y, pb, height=0.55, color=[CH_COLOR[c] for c in pb.index])
    for i, v in enumerate(pb):
        ax.annotate(f"{v:.1f} mo", (v, i), xytext=(4, 0), textcoords="offset points", va="center",
                    fontsize=8, color=INK2)
    lim = d.a["ltv"]["max_payback_months"]
    ax.axvline(lim, color=NEG, linewidth=1.2)
    ax.annotate(f"max {lim}-month payback", (lim, len(pb) - 0.5), xytext=(4, 0), textcoords="offset points",
                fontsize=8, color=NEG)
    ax.set_yticks(y)
    ax.set_yticklabels([CH_LABEL[c] for c in pb.index])
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.set_xlabel("months to recover CPA (discounted contribution)")
    ax.set_title("CPA payback by channel")
    ax.set_xlim(0, max(pb.max(), lim) * 1.25)


def cohort_heatmap(fig, spec, d: Data):
    ax = fig.add_subplot(spec)
    tri = d.coh.pivot(index="cohort", columns="months_since_signup", values="retention_pct")
    ax.imshow(np.ma.masked_invalid(tri.values), cmap=SEQ, vmin=0.3, vmax=1, aspect="auto")
    for i in range(tri.shape[0]):
        for j in range(tri.shape[1]):
            v = tri.values[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=7.5,
                        color="white" if v > 0.62 else INK)
    ax.set_xticks(range(tri.shape[1]))
    ax.set_xticklabels([f"M{k}" for k in tri.columns])
    ax.set_yticks(range(tri.shape[0]))
    ax.set_yticklabels([mlabel(c) for c in tri.index])
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Cohort retention (active = paid or paused; blank = not yet observable)")
    ax.set_xlabel("months since signup")


def retention_curves(fig, spec, d: Data):
    ax = fig.add_subplot(spec)
    for c in CHANNELS:
        cv = d.rbc[d.rbc.channel == c]
        ax.plot(cv.months_since_signup, cv.retention_pct, color=CH_COLOR[c], marker="o", markersize=3.5,
                label=CH_LABEL[c])
    last = d.rbc.months_since_signup.max()
    ends = d.rbc[d.rbc.months_since_signup == last].set_index("channel").retention_pct.sort_values()
    ys = list(ends.values)
    for i in range(1, len(ys)):                      # nudge labels apart
        ys[i] = max(ys[i], ys[i - 1] + 0.045)
    for (c, v), y in zip(ends.items(), ys):
        ax.annotate(f"{CH_LABEL[c]} {v:.0%}", (last, v), xytext=(last + 0.25, y), textcoords="data",
                    va="center", fontsize=8, color=INK2)
    ax.yaxis.set_major_formatter(PCT)
    ax.set_ylim(0, 1.05)
    ax.set_xlim(-0.2, last + 2.6)
    ax.set_xticks(range(last + 1))
    ax.set_xticklabels([f"M{k}" for k in range(last + 1)])
    ax.set_title("Retention by acquisition channel")
    ax.legend(loc="lower left", ncols=2)


def medication_chart(fig, spec, d: Data):
    ax1, ax2 = panels(fig, spec, 2)
    x = np.arange(len(d.months))
    base = d.a["medication"]["cost_per_month_supply"]
    per_ship = d.pnl.medication.values / d.shipments.reindex(d.months).values
    ax1.plot(x, base, color=SERIES[0], marker="o", label="Base cost per supply (starting dose)")
    ax1.plot(x, per_ship, color=SERIES[1], marker="o", label="Actual cost per shipment (dose mix)")
    ax1.yaxis.set_major_formatter(USD0)
    ax1.set_title("Medication cost by month")
    ax1.legend(loc="upper left")
    jul = d.months.index("2026-07") if "2026-07" in d.months else None
    if jul is not None:
        end_label(ax1, jul, base[jul], "new contract (Jul)", dy=10, ha="center", dx=0)
    ax1.set_ylim(min(base) * 0.85, max(per_ship) * 1.25)
    cpo = d.pnl.contribution_profit / d.pnl.completed_orders
    ax2.plot(x, cpo, color=SERIES[2], marker="o")
    ax2.yaxis.set_major_formatter(USD0)
    ax2.set_title("Contribution per completed order", fontsize=10)
    i = int(cpo.idxmin())
    ax2.set_ylim(cpo.min() * 0.85, cpo.max() * 1.1)
    end_label(ax2, i, cpo[i], f"low ${cpo[i]:,.0f}", dx=7)
    end_label(ax2, x[-1], cpo.iloc[-1], f"${cpo.iloc[-1]:,.0f}")
    xmonths(ax2, d.months)
    plt.setp(ax1.get_xticklabels(), visible=False)


def profit_vs_cash(fig, spec, d: Data):
    ax1, ax2 = panels(fig, spec, 2, [3, 2])
    x = np.arange(len(d.months))
    ax1.bar(x - 0.19, d.pnl.operating_profit, width=0.36, color=SERIES[0], label="Operating profit")
    ax1.bar(x + 0.19, d.cf.net_operating_cash_flow, width=0.36, color=SERIES[1], label="Net operating cash flow")
    ax1.axhline(0, color=AXIS, linewidth=0.8)
    ax1.yaxis.set_major_formatter(USD)
    ax1.set_title("Operating profit vs net operating cash flow")
    ax1.legend(loc="upper left")
    ax2.plot(x, d.cf.closing_cash, color=SERIES[2], marker="o")
    ax2.yaxis.set_major_formatter(USD)
    ax2.set_title("Closing cash balance", fontsize=10)
    end_label(ax2, x[-1], d.cf.closing_cash.iloc[-1], usd(d.cf.closing_cash.iloc[-1]))
    ax2.set_ylim(0, d.cf.closing_cash.max() * 1.2)
    xmonths(ax2, d.months)
    plt.setp(ax1.get_xticklabels(), visible=False)


def forecast_chart(fig, spec, d: Data):
    ax = fig.add_subplot(spec)
    act = d.cf[["month", "net_operating_cash_flow"]]
    fmonths = d.fc.query("scenario == 'base'").month.tolist()
    allm = act.month.tolist() + fmonths
    xa = np.arange(len(act))
    ax.plot(xa, act.net_operating_cash_flow, color=INK2, marker="o", markersize=3.5, label="Actual")
    xf = np.arange(len(act), len(allm))
    for scen, c, lab in [("base", SERIES[0], "Base case"), ("downside", SERIES[1], "Downside case")]:
        y = d.fc.query("scenario == @scen").net_operating_cash_flow.values
        ax.plot(np.r_[xa[-1], xf], np.r_[act.net_operating_cash_flow.iloc[-1], y], color=c, marker="o",
                markersize=3.5, label=lab)
        end_label(ax, xf[-1], y[-1], usd(y[-1]))
    tgt = d.fsum.target_ncf
    ax.axhline(tgt, color=INK, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.annotate(f"2x target {usd(tgt)}", (0, tgt), xytext=(0, 5), textcoords="offset points", fontsize=8, color=INK)
    bi = allm.index(d.base_month)
    ax.scatter([bi], [d.fsum.baseline_ncf], s=60, color=INK, zorder=5, edgecolor=SURFACE, linewidth=2)
    end_label(ax, bi, d.fsum.baseline_ncf, f"baseline {usd(d.fsum.baseline_ncf)}", dy=12, ha="right", dx=-6)
    ax.axvspan(len(act) - 0.5, len(allm) - 0.5, color=GRID, alpha=0.35, linewidth=0)
    ax.axhline(0, color=AXIS, linewidth=0.8)
    ax.yaxis.set_major_formatter(USD)
    xmonths(ax, allm, every=2)
    ax.set_xlim(-0.5, len(allm) + 1.2)
    reached = d.fsum.base_month_reached
    down = d.fsum.downside_month_reached
    ax.set_title(f"Cash flow forecast: 2x in {mlabel(reached) if reached != 'not reached' else 'n/a'} (base), "
                 f"{'not reached' if down == 'not reached' else mlabel(down)} (downside)")
    ax.legend(loc="upper left")


def waterfall(fig, spec, d: Data):
    ax = fig.add_subplot(spec)
    br = d.bridge
    short = {"Momentum": "Momentum\n(no levers)", "a)": "a) Channel\nmix", "b1)": "b1) Check-\nins",
             "b2)": "b2)\nDunning", "b3)": "b3) Pause\noption", "c)": "c) Plan\nmix (off)", "d)": "d) Price\n+5%",
             "e)": "e) Med.\ncost -8%", "f)": "f) Shipping/\nfulfillment"}
    labels = (["Baseline\n" + mlabel(d.base_month)] + [short[s.split(" ")[0]] for s in br.step]
              + ["Target month\n" + mlabel(d.fsum.bridge_month)])
    vals = br.cash_impact.values
    run = d.fsum.baseline_ncf
    x = np.arange(len(labels))
    ax.bar(0, run, width=0.6, color=TOTAL)
    for i, v in enumerate(vals, start=1):
        bottom = run if v >= 0 else run + v
        color = MUTED if i == 1 else POS if v >= 0 else NEG          # momentum is not a lever
        ax.bar(i, abs(v), bottom=bottom, width=0.6, color=color)
        if abs(v) > 1:
            ax.annotate(("+" if v >= 0 else "") + usd(v), (i, run + max(v, 0)), xytext=(0, 3),
                        textcoords="offset points", ha="center", fontsize=7.5, color=INK2)
        run += v
    ax.bar(len(labels) - 1, run, width=0.6, color=TOTAL)
    ax.annotate(usd(run), (len(labels) - 1, run), xytext=(0, 3), textcoords="offset points", ha="center",
                fontsize=8, color=INK, fontweight="bold")
    ax.annotate(usd(d.fsum.baseline_ncf), (0, d.fsum.baseline_ncf), xytext=(0, 3), textcoords="offset points",
                ha="center", fontsize=8, color=INK, fontweight="bold")
    ax.axhline(d.fsum.target_ncf, color=INK, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.annotate(f"2x target {usd(d.fsum.target_ncf)}", (len(labels) - 1.5, d.fsum.target_ncf), xytext=(0, 4),
                textcoords="offset points", ha="right", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7.5)
    ax.yaxis.set_major_formatter(USD)
    ax.set_title("Doubling bridge: baseline to target-month net operating cash flow (levers added in fixed order)")


CHART_FUNCS = {
    "01_revenue_margin": (revenue_margin, (8, 5.5)),
    "02_aov_first_vs_repeat": (aov_chart, (8, 4.5)),
    "03_cpa_by_channel": (cpa_chart, (8, 4.5)),
    "04_ltv_vs_cpa": (ltv_vs_cpa, (8, 4.5)),
    "05_payback_by_channel": (payback_chart, (8, 4)),
    "06_cohort_retention_heatmap": (cohort_heatmap, (8, 5)),
    "07_retention_curves_by_channel": (retention_curves, (8, 4.8)),
    "08_medication_cost_effect": (medication_chart, (8, 5.5)),
    "09_profit_vs_cash": (profit_vs_cash, (8, 5.5)),
    "10_forecast_base_vs_downside": (forecast_chart, (9, 4.8)),
    "11_doubling_bridge": (waterfall, (11, 5.4)),
}


def save_pngs(d: Data):
    CHARTS.mkdir(parents=True, exist_ok=True)
    for old in CHARTS.glob("*.png"):          # keep the folder lean: no stale versions
        if old.stem not in CHART_FUNCS:
            old.unlink()
    for name, (fn, size) in CHART_FUNCS.items():
        fig = plt.figure(figsize=size)
        fn(fig, fig.add_gridspec(1, 1)[0], d)
        fig.savefig(CHARTS / f"{name}.png", dpi=150, bbox_inches="tight")
        plt.close(fig)


def kpi_tiles(fig, spec, d: Data):
    tiles = d.kpis()
    gs = spec.subgridspec(1, len(tiles), wspace=0.12)
    for i, (lab, val, sub) in enumerate(tiles):
        ax = fig.add_subplot(gs[i])
        ax.set_axis_off()
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, facecolor="#f3f2ee", edgecolor="none"))
        ax.text(0.08, 0.78, lab, fontsize=7.5, color=INK2, transform=ax.transAxes)
        ax.text(0.08, 0.38, val, fontsize=15, color=INK, fontweight="bold", transform=ax.transAxes)
        ax.text(0.08, 0.12, sub, fontsize=6.3, color=MUTED, transform=ax.transAxes)


def dashboard(d: Data, path, preview_dir=None):
    """Two A3-landscape pages; preview_dir also saves each page as PNG (for review)."""
    page = 0
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(16.5, 11.7))          # A3 landscape
        gs = fig.add_gridspec(3, 3, height_ratios=[0.55, 3, 3], hspace=0.42, wspace=0.28,
                              left=0.04, right=0.98, top=0.9, bottom=0.05)
        fig.suptitle(f"Omzo GLP-1 telehealth: dashboard (synthetic data, Jan–Sep 2026; KPIs for baseline month "
                     f"{mlabel(d.base_month)})", x=0.04, ha="left", fontsize=15, fontweight="bold")
        kpi_tiles(fig, gs[0, :], d)
        revenue_margin(fig, gs[1, 0], d)
        profit_vs_cash(fig, gs[1, 1], d)
        forecast_chart(fig, gs[1, 2], d)
        cpa_chart(fig, gs[2, 0], d)
        ltv_vs_cpa(fig, gs[2, 1], d)
        payback_chart(fig, gs[2, 2], d)
        pdf.savefig(fig)
        page += 1
        if preview_dir:
            fig.savefig(f"{preview_dir}/dashboard_p{page}.png", dpi=60)
        plt.close(fig)

        fig = plt.figure(figsize=(16.5, 11.7))
        gs = fig.add_gridspec(2, 3, hspace=0.42, wspace=0.28, left=0.04, right=0.98, top=0.92, bottom=0.1)
        fig.suptitle("Retention, unit costs and the doubling plan", x=0.04, ha="left", fontsize=15, fontweight="bold")
        cohort_heatmap(fig, gs[0, 0], d)
        retention_curves(fig, gs[0, 1], d)
        medication_chart(fig, gs[0, 2], d)
        aov_chart(fig, gs[1, 0], d)
        waterfall(fig, gs[1, 1:], d)
        pdf.savefig(fig)
        page += 1
        if preview_dir:
            fig.savefig(f"{preview_dir}/dashboard_p{page}.png", dpi=60)
        plt.close(fig)


def main():
    d = Data()
    save_pngs(d)
    dashboard(d, ROOT / "output" / "dashboard.pdf")
    print(f"Saved {len(CHART_FUNCS)} charts to output/charts/ and output/dashboard.pdf")


if __name__ == "__main__":
    main()
