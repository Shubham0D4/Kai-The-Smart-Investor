"""
PHASE4_PLAN.md §9: beta, CAPM discount rate and Gamma's fallback (FIXES.md C7).
"""

import math

import pytest

from agents.gamma_valuation.agent import GammaAgent
from mcp_server.tools.finance_math import calculate_beta, capm_discount_rate
from mcp_server.tools.market_data import fetch_market_data
from mcp_server.tools.sec_financials import fetch_financial_statements


def _series(returns, start=100.0):
    prices, p = [], start
    for i, r in enumerate(returns):
        p *= 1 + r
        prices.append((f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", p))
    return prices


BENCH_RETURNS = [0.01 * math.sin(i / 3) + 0.002 * ((i % 5) - 2) for i in range(120)]


def test_beta_one_when_identical():
    s = _series(BENCH_RETURNS)
    assert calculate_beta(s, s) == pytest.approx(1.0)


def test_beta_two_when_moving_twice_as_much():
    stock = _series([2 * r for r in BENCH_RETURNS])
    assert calculate_beta(stock, _series(BENCH_RETURNS)) == pytest.approx(2.0, abs=1e-6)


def test_beta_needs_60_common_days():
    s = _series(BENCH_RETURNS[:59])
    assert calculate_beta(s, s) is None


def test_capm_formula_and_clamps():
    clamp_b, clamp_r = (0.5, 2.0), (0.06, 0.15)
    assert capm_discount_rate(0.04, 1.2, 0.05, clamp_b, clamp_r) == pytest.approx(0.10)
    assert capm_discount_rate(0.04, 3.0, 0.05, clamp_b, clamp_r) == pytest.approx(0.14)   # beta -> 2.0
    assert capm_discount_rate(0.12, 2.0, 0.05, clamp_b, clamp_r) == pytest.approx(0.15)   # rate cap
    assert capm_discount_rate(0.00, 0.5, 0.05, clamp_b, clamp_r) == pytest.approx(0.06)   # rate floor


def test_gamma_uses_capm_from_fixtures():
    computed = GammaAgent(llm_client=object()).compute(fetch_financial_statements("AAPL"), fetch_market_data("AAPL"))
    ev = {e.id: e for e in computed["evidence"]}
    beta, rf = ev["calc:beta"].value, ev["mkt:risk_free_rate"].value
    assert rf == pytest.approx(0.05103, abs=1e-5)
    assert ev["assumption:discount_rate"].value == pytest.approx(rf + beta * 0.05, abs=1e-6)
    assert computed["parameters"]["discount_rate"] == ev["assumption:discount_rate"].value
    assert computed["parameters"]["terminal_growth"] == 0.025


@pytest.mark.parametrize("source", ["benchmark", "risk_free"])
def test_gamma_falls_back_to_10pct_with_warning(failing_sources, source):
    failing_sources.add(source)
    computed = GammaAgent(llm_client=object()).compute(fetch_financial_statements("AAPL"), fetch_market_data("AAPL"))
    assert computed["parameters"]["discount_rate"] == 0.10
    assert any("discount rate set to fallback 10%" in w for w in computed["warnings"])
    assert any(source.replace("_", "-") in w or "^GSPC" in w or "^TNX" in w for w in computed["warnings"])
