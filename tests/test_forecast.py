"""Forecast / plan tests on the LibreOffice-recalculated workbook."""
import numpy as np

NF = 9


def row(ws, label):
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, 1).value == label:
            return r
    raise KeyError(f"{label!r} not on {ws.title}")


def fvals(ws, label):
    r = row(ws, label)
    return np.array([ws.cell(r, 3 + f).value for f in range(1, NF + 1)], dtype=float)


def test_forecast_opening_cash_equals_last_actual_closing(recalculated, recalculated_downside):
    for wb in (recalculated, recalculated_downside):
        cf = wb["Cash_Flow"]
        r = row(cf, "Closing cash")
        last_close = [cf.cell(r, j).value for j in range(2, 11) if cf.cell(r, j).value is not None][-1]
        fc = wb["Forecast"]
        assert abs(fc.cell(row(fc, "Opening cash"), 4).value - last_close) < 0.01
        # cash rolls forward inside the forecast too
        opening, ncf, closing = fvals(fc, "Opening cash"), fvals(fc, "Net operating cash flow"), fvals(fc, "Closing cash")
        assert np.allclose(opening + ncf, closing, atol=0.01)
        assert np.allclose(opening[1:], closing[:-1], atol=0.01)


def test_bridge_bars_sum_exactly_to_total_change(recalculated, recalculated_downside):
    for wb in (recalculated, recalculated_downside):
        b = wb["Doubling_Bridge"]
        baseline, target_cf, total = b["B7"].value, b["B9"].value, b["B10"].value
        start, end = row(b, "Momentum (no levers)"), row(b, "Target-month cash flow")
        bars = [b.cell(r, 2).value for r in range(start, end)]
        assert len(bars) == 9                                    # momentum + 8 levers
        assert abs(sum(bars) - total) < 1e-6
        assert abs(total - (target_cf - baseline)) < 1e-6
        assert abs(b.cell(end, 2).value - target_cf) < 1e-6
        # the last cumulative bridge step is the Forecast itself
        fc = wb["Forecast"]
        assert abs(fvals(fc, "Net operating cash flow")[b["B5"].value - 1] - target_cf) < 1e-6


def test_scenario_switch_changes_outputs(recalculated, recalculated_downside):
    base, down = recalculated["Forecast"], recalculated_downside["Forecast"]
    for label in ["Net revenue", "Contribution profit", "Net operating cash flow", "Closing cash"]:
        assert not np.allclose(fvals(base, label), fvals(down, label)), label
    # the switch selects exactly the matching scenario engine
    for wb, eng in [(recalculated, "Eng_Base"), (recalculated_downside, "Eng_Down")]:
        for label in ["Net operating cash flow", "Active customers (incl. paused)"]:
            assert np.allclose(fvals(wb["Forecast"], label), fvals(wb[eng], label), atol=1e-6), (eng, label)
    # the side-by-side table is independent of the switch
    s1, s2 = recalculated["Scenarios"], recalculated_downside["Scenarios"]
    for r in range(1, s1.max_row + 1):
        for c in range(1, 22):
            v1, v2 = s1.cell(r, c).value, s2.cell(r, c).value
            if isinstance(v1, float):
                assert abs(v1 - v2) < 1e-6, (r, c)


def test_no_borrowing_in_forecast_operating_cash_flow(recalculated):
    fc = recalculated["Forecast"]
    receipts, payments = fvals(fc, "Cash receipts"), fvals(fc, "Cash payments")
    assert np.allclose(receipts - payments, fvals(fc, "Net operating cash flow"), atol=0.01)
    parts = sum(fvals(fc, x) for x in ["Pharmacy paid (net-30)", "Marketing paid (lagged)",
                                       "Other variable paid", "Fixed opex paid"])
    assert np.allclose(parts, payments, atol=0.01)


def test_workbook_checks_pass_both_scenarios(recalculated, recalculated_downside):
    for wb in (recalculated, recalculated_downside):
        assert wb["Checks"]["B3"].value is True
