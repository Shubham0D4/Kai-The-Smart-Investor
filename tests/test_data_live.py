"""
Live data: SEC EDGAR, Yahoo Finance and news feeds, fetched for real. Every derived number
(TTM, split adjustment, return windows, catalyst ratios) is recomputed here independently from
the raw provider data and compared with what Kai sends to the agents.
"""

from datetime import date

import pytest

from mcp_server.tools.catalysts import fetch_catalysts
from mcp_server.tools.market_data import RETURN_WINDOWS, fetch_market_data
from mcp_server.tools.news_stream import get_news_stream
from mcp_server.tools.sec_financials import fetch_financial_statements, series


@pytest.fixture(scope="module")
def nvda_market():
    m = fetch_market_data("NVDA")
    assert m["success"], m.get("error")
    return m


@pytest.fixture(scope="module")
def nvda_financials(nvda_market):
    f = fetch_financial_statements("NVDA", splits=nvda_market["splits"])
    assert f["success"], f.get("error")
    return f


def _rec(records, id_):
    return next((r for r in records if r["id"] == id_), None)


def test_market_snapshot_has_a_fetch_time_and_matching_price(nvda_market, record):
    """The market snapshot records when it was fetched and the last close it belongs to."""
    m = nvda_market
    assert m["fetched_at"].endswith("Z") and m["market_date"] == m["history"][-1]["date"]
    assert _rec(m["records"], "mkt:price")["value"] == pytest.approx(m["price"], rel=1e-4)
    record("data", "nvda_snapshot", {"price": round(m["price"], 2), "fetched_at": m["fetched_at"],
                                     "market_date": m["market_date"]})


def test_return_windows_are_dated_and_recompute_from_closes(nvda_market, record):
    """Every price return carries its exact date range and equals last close / start close - 1."""
    hist = nvda_market["history"]
    by_date = {p["date"]: p["close"] for p in hist}
    out = {}
    for key, _, n in RETURN_WINDOWS:
        r = _rec(nvda_market["records"], f"mkt:return_{key}")
        start, end = r["period"].split("..")
        assert end == hist[-1]["date"] and start == hist[-1 - n]["date"]
        assert r["value"] == pytest.approx(by_date[end] / by_date[start] - 1, abs=1e-6)
        out[key] = {"value": r["value"], "period": r["period"]}
    dd = _rec(nvda_market["records"], "mkt:drawdown_from_52w_high")
    assert dd["value"] <= 0
    out["drawdown_from_52w_high"] = {"value": dd["value"], "period": dd["period"]}
    record("data", "nvda_returns", out)


def test_sec_eps_is_split_adjusted_for_filings_before_a_split(nvda_market, nvda_financials, record):
    """NVDA split 10-for-1 in June 2024: EPS from filings made before it must be divided by 10."""
    splits = [s for s in nvda_market["splits"] if s["date"] >= "2024-01-01"]
    assert any(s["ratio"] == 10 for s in splits), splits
    eps = series(nvda_financials["records"], "eps_diluted")
    early = [r for r in eps if r["filed"] < "2024-06-10"]
    assert early, "expected at least one EPS figure filed before the 2024 split"
    for r in early:
        assert "split-adjusted" in r["label"]
    # Adjusted, EPS rises every year in this window: a pre-split figure left unadjusted would
    # show a fall of about 10x (the bug that produced a 6% EPS CAGR in a live debate).
    values = [r["value"] for r in eps]
    cagr = (values[-1] / values[0]) ** (1 / (len(values) - 1)) - 1
    assert cagr > 0.3, (values, cagr)
    record("data", "nvda_eps_series", {r["id"]: round(r["value"], 4) for r in eps})
    record("data", "nvda_eps_cagr", round(cagr, 4))


def test_ttm_eps_is_built_from_the_latest_filings(nvda_financials, record):
    """TTM EPS covers twelve months ending at the latest 10-Q or 10-K."""
    ttm = _rec(nvda_financials["records"], "sec:eps_diluted:TTM")
    assert ttm is not None
    start, end = ttm["period"].split("..")
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    assert 355 <= days <= 375
    latest_fy = series(nvda_financials["records"], "eps_diluted")[-1]
    assert end >= latest_fy["period"].split("..")[1]
    record("data", "nvda_ttm_eps", {"value": ttm["value"], "period": ttm["period"], "how": ttm["source"]})


def test_catalysts_are_priced_off_the_snapshot(nvda_market, record):
    """Earnings date, analyst targets and options are fetched; ratios use the snapshot price."""
    c = fetch_catalysts("NVDA", nvda_market["price"], nvda_market["market_date"])
    assert c["success"], c.get("error")
    ids = {r["id"]: r for r in c["records"]}
    for need in ("cat:days_to_earnings", "cat:target_mean", "cat:implied_vol_atm"):
        assert need in ids, (need, c["warnings"])
    up = ids["cat:target_mean_upside"]["value"]
    assert up == pytest.approx(ids["cat:target_mean"]["value"] / nvda_market["price"] - 1, abs=1e-5)
    if "cat:implied_move" in ids:
        assert 0 < ids["cat:implied_move"]["value"] < 0.6
    assert 0 < ids["cat:implied_vol_atm"]["value"] < 3
    record("data", "nvda_catalysts", {k: {"value": v["value"], "period": v["period"]} for k, v in ids.items()})
    record("data", "nvda_catalyst_warnings", c["warnings"])


@pytest.mark.parametrize("ticker", ["AAPL", "TSLA"])
def test_sec_financials_and_market_data_for_other_tickers(ticker, record):
    """SEC statements (five fiscal years plus TTM) and market data load for each test ticker."""
    m = fetch_market_data(ticker)
    f = fetch_financial_statements(ticker, splits=m.get("splits"))
    assert m["success"] and f["success"], (m.get("error"), f.get("error"))
    assert len(f["fiscal_years"]) == 5
    assert _rec(f["records"], "sec:eps_diluted:TTM") is not None
    record("data", f"{ticker.lower()}_fiscal_years", f["fiscal_years"])


def test_news_comes_from_several_feeds_with_summaries(record):
    """News is merged from several providers; most articles carry a summary, not just a title."""
    n = get_news_stream("NVDA", days=7)
    assert n["success"] and n["count"] > 0, n.get("error")
    with_summary = sum(1 for a in n["articles"] if not a["headline_only"])
    assert with_summary >= n["count"] / 2
    record("data", "nvda_news", {"articles": n["count"], "with_summary": with_summary,
                                 "providers_ok": n["providers_ok"], "providers_failed": n["providers_failed"]})
