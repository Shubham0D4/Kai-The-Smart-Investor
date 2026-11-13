"""
Offline checks of scripts/calibration_backtest.py's pure core on the recorded AAPL fixtures.
The script's live fetching is not exercised here (it needs the network).
"""

import json
from datetime import date
from pathlib import Path

import pytest

from coordinator.config import load_thresholds
from scripts.calibration_backtest import LookAhead, as_of_basis, assert_no_lookahead, backtest_ticker, summarise

FIX = Path(__file__).parent.parent / "fixtures"
CFG = load_thresholds()


@pytest.fixture(scope="module")
def data():
    yf = json.loads((FIX / "yfinance.json").read_text())
    d = lambda s: date.fromisoformat(s)
    closes = [(d(r["date"]), r["close"]) for r in yf["history"]["AAPL"]["10y"]]
    bench = [(d(r["date"]), r["close"]) for r in yf["history"]["^GSPC"]["2y"]]
    splits = [(d(s["date"]), s["ratio"]) for s in yf["splits"]["AAPL"]]
    rf = [(c[0], 4.5) for c in closes]                         # flat 4.5% yield series
    facts = json.loads((FIX / "sec_companyfacts_aapl.json").read_text())
    return closes, splits, bench, rf, facts


def test_as_of_basis_undoes_later_splits():
    closes = [(date(2020, 8, 28), 125.0), (date(2020, 9, 1), 134.0)]   # adjusted for the 4:1 split
    splits = [(date(2020, 8, 31), 4.0)]
    assert as_of_basis(closes, splits, date(2020, 8, 28)) == [(date(2020, 8, 28), 500.0)]
    assert as_of_basis(closes, splits, date(2020, 9, 1))[-1] == (date(2020, 9, 1), 134.0)


def test_lookahead_assertion():
    with pytest.raises(LookAhead):
        assert_no_lookahead(date(2024, 1, 1), closes=[(date(2024, 1, 2), 1.0)])
    assert_no_lookahead(date(2024, 1, 1), closes=[(date(2024, 1, 1), 1.0)])


def test_backtest_core_on_fixtures(data):
    closes, splits, bench, rf, facts = data
    as_of = [date(2025, 6, 2), date(2025, 12, 1), date(2026, 10, 1)]      # last one: outcome unknown
    rows = backtest_ticker("AAPL", closes, splits, bench, rf, facts, "0000320193", as_of, CFG)
    by_date = {r["as_of"] for r in rows}
    assert by_date == {"2025-06-02", "2025-12-01"}
    for r in rows:
        assert r["outcome_date"] > r["as_of"] and r["outcome"] in (0, 1)
        assert 0 <= r["stated_p"] <= 1
        assert r["field"] != "mc_dcf.prob_fair_above_price"            # not a price-rise forecast
    # outcome matches the price move over the horizon
    i = next(k for k, (dd, _) in enumerate(closes) if dd >= date(2025, 6, 2))
    expected = int(closes[i + 63][1] > closes[i][1])
    assert {r["outcome"] for r in rows if r["as_of"] == "2025-06-02"} == {expected}
    s = summarise(rows)
    assert all(v["n"] >= 1 and 0 <= v["brier"] <= 1 for v in s.values())
