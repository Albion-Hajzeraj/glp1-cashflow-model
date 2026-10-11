import contextlib
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unit_economics import load_assumptions  # noqa: E402

DATE_COLS = {
    "customers": ["signup_date"],
    "orders": ["order_date"],
    "payments": ["attempt_date", "payout_date"],
    "refunds": ["refund_date"],
    "subscription_events": ["event_date"],
    "shipments": ["ship_date"],
    "cash_payables": ["incurred_date", "paid_date"],
}


@pytest.fixture(scope="session")
def a():
    return load_assumptions()


@pytest.fixture(scope="session")
def period(a):
    months = [str(m) for m in a["period"]["months"]]
    start = pd.Timestamp(f"{months[0]}-01")
    end = pd.Timestamp(f"{months[-1]}-01") + pd.offsets.MonthEnd(0)
    return {"months": months, "start": start, "end": end}


@pytest.fixture(scope="session")
def data():
    return {p.stem: pd.read_csv(p, parse_dates=DATE_COLS.get(p.stem, []))
            for p in (ROOT / "data").glob("*.csv")}


@pytest.fixture(scope="session")
def metrics(a, data):
    """Re-run the SQL + LTV pipeline on the current data, then read the saved CSVs."""
    import ltv_projection
    import run_metrics
    with contextlib.redirect_stdout(io.StringIO()):
        run_metrics.run_sql(run_metrics.connect(a))
        ltv_projection.run(a)
    return {p.stem: pd.read_csv(p) for p in (ROOT / "output" / "metrics").glob("*.csv")}


def _recalc_build(tmp_path_factory, scenario):
    import build_workbook
    from recalc import find_soffice, recalc
    if not find_soffice():
        pytest.skip("LibreOffice not installed: cannot recalculate the workbook")
    src = tmp_path_factory.mktemp(f"wb_s{scenario}") / "omzo_model.xlsx"
    build_workbook.build(src, scenario=scenario)
    return recalc(src, tmp_path_factory.mktemp(f"recalc_s{scenario}"))


@pytest.fixture(scope="session")
def recalculated(metrics, tmp_path_factory):
    """Default workbook (scenario switch = 1), rebuilt and recalculated by LibreOffice."""
    return _recalc_build(tmp_path_factory, 1)


@pytest.fixture(scope="session")
def recalculated_downside(metrics, tmp_path_factory):
    """Same workbook with the scenario switch set to 2 (downside)."""
    return _recalc_build(tmp_path_factory, 2)
