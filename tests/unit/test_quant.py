"""
Quant layer unit tests (phase 4.5): closed-form or hand-computed cases for every function,
seeded reproducibility, and explicit "unavailable" for short or missing inputs.
"""

import inspect
import math
from datetime import date, timedelta

import numpy as np
import pytest
from scipy import stats

from coordinator.config import load_thresholds
from mcp_server.tools.finance_math import calculate_dcf_value
from mcp_server.tools.market_data import fetch_market_data
from mcp_server.tools.sec_financials import fetch_financial_statements
from quant import LookAheadError, QuantInputs, build_quant_report, inputs_from_fetched
from quant.base_rates import Case, base_rate, build_cases, split_adjusted_eps, wilson_interval
from quant.calibration import brier_score, calibration, load_pairs, reliability_table
from quant.forward import forward_probability
from quant.market import market_relationship
from quant.mc_dcf import monte_carlo_dcf
from quant.models import QuantField
from quant.returns import return_distribution

CFG = load_thresholds()
D0 = date(2020, 1, 1)


def prices_from_returns(returns, start=100.0):
    p = [start]
    for r in returns:
        p.append(p[-1] * (1 + r))
    return p


def dated(values, start=D0):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def val(fields, key):
    return fields[key].value


# ---------------------------------------------------------------- (a) return distribution

def test_constant_growth_series():
    p = [100 * 1.001 ** t for t in range(253)]
    f = return_distribution(p)
    assert val(f, "quant:returns.vol_annual") == pytest.approx(0, abs=1e-12)
    assert val(f, "quant:returns.max_drawdown") == pytest.approx(0, abs=1e-12)
    assert val(f, "quant:returns.var95_hist") == pytest.approx(-0.001)       # a gain, not a loss
    assert val(f, "quant:returns.var95_normal") == pytest.approx(-0.001)
    # zero variance: skew/kurtosis are undefined -> explicit unavailable, not a number
    for k in ("skew", "excess_kurtosis"):
        assert f[f"quant:returns.{k}"].status == "unavailable"
        assert "non-finite" in f[f"quant:returns.{k}"].reason


def test_hand_computed_var_and_volatility():
    r = np.linspace(-0.05, 0.05, 100)                 # sorted, evenly spaced daily returns
    f = return_distribution(prices_from_returns(r), window=252)
    # linear-interpolated 5th percentile: position 0.05 x 99 = 4.95 -> -0.05 + 4.95 x 0.1/99 = -0.045
    assert val(f, "quant:returns.var95_hist") == pytest.approx(0.045)
    sd = np.std(r, ddof=1)
    assert val(f, "quant:returns.vol_annual") == pytest.approx(sd * math.sqrt(252))
    assert val(f, "quant:returns.var95_normal") == pytest.approx(-(r.mean() + stats.norm.ppf(0.05) * sd))
    assert val(f, "quant:returns.skew") == pytest.approx(0, abs=1e-9)
    assert f["quant:returns.vol_annual"].n == 100
    assert f["quant:returns.vol_annual"].kind == "descriptive"
    assert f["quant:returns.var95_normal"].kind == "model"


def test_max_drawdown_hand_computed():
    p = [100, 120, 90, 110, 60, 130, 117]          # peak 120 -> trough 60 = -50%
    f = return_distribution(p, min_obs=5)
    assert val(f, "quant:returns.max_drawdown") == pytest.approx(-0.5)


def test_excess_kurtosis_of_alternating_returns():
    r = [0.01, -0.01] * 100                          # two-point distribution: excess kurtosis ~ -2
    f = return_distribution(prices_from_returns(r))
    assert val(f, "quant:returns.excess_kurtosis") == pytest.approx(-2.0, abs=0.05)


@pytest.mark.parametrize("prices, why", [([], "only 0"), ([100] * 30, "only 29"),
                                         ([100, -1] + [100] * 100, "non-positive")])
def test_short_or_bad_history_is_unavailable(prices, why):
    f = return_distribution(prices)
    assert all(x.status == "unavailable" and x.value is None for x in f.values())
    assert all(why in x.reason for x in f.values())


# ---------------------------------------------------------------- (b) market relationship

def test_beta_of_series_against_itself_is_one():
    rng = np.random.default_rng(0)
    s = dated(prices_from_returns(rng.normal(0, 0.01, 300)))
    f = market_relationship(s, s)
    assert val(f, "quant:market.beta") == pytest.approx(1.0)
    assert val(f, "quant:market.correlation") == pytest.approx(1.0)
    assert val(f, "quant:market.beta_se") == pytest.approx(0, abs=1e-9)
    assert val(f, "quant:market.alpha_daily") == pytest.approx(0, abs=1e-12)


def test_exact_linear_relationship():
    rng = np.random.default_rng(1)
    rb = rng.normal(0, 0.01, 300)
    bench = dated(prices_from_returns(rb))
    stock = dated(prices_from_returns(2 * rb + 0.0001))
    f = market_relationship(stock, bench)
    assert val(f, "quant:market.beta") == pytest.approx(2.0)
    assert val(f, "quant:market.alpha_daily") == pytest.approx(0.0001)


def test_beta_standard_error_matches_closed_form():
    rng = np.random.default_rng(2)
    rb, e = rng.normal(0, 0.01, 500), rng.normal(0, 0.005, 500)
    f = market_relationship(dated(prices_from_returns(1.5 * rb + e)), dated(prices_from_returns(rb)))
    beta, se = val(f, "quant:market.beta"), val(f, "quant:market.beta_se")
    resid = 1.5 * rb + e - (val(f, "quant:market.alpha_daily") + beta * rb)
    expected_se = math.sqrt(np.sum(resid ** 2) / (500 - 2) / np.sum((rb - rb.mean()) ** 2))
    assert se == pytest.approx(expected_se, rel=1e-9)
    assert abs(beta - 1.5) < 3 * se


def test_market_unavailable_without_enough_data():
    s = dated([100 + i for i in range(30)])
    assert all(x.status == "unavailable" for x in market_relationship(s, s).values())
    assert "no benchmark" in market_relationship(s, []).popitem()[1].reason


# ---------------------------------------------------------------- (c) Monte Carlo DCF

MC = dict(simulations=5000, growth_clamp=(-0.05, 0.15), growth_sd_floor=0.02, growth_clip=(-0.2, 0.3),
          discount_sd=0.01, discount_clip=(0.06, 0.15), terminal_range=(0.015, 0.03), min_spread=0.02)


def mc(seed=7, **kw):
    args = dict(fcf_history=[80, 90, 100], net_debt=50.0, shares=10.0, price=100.0, discount_centre=0.10)
    args.update(kw)
    return monte_carlo_dcf(args.pop("fcf_history"), args.pop("net_debt"), args.pop("shares"), args.pop("price"),
                           args.pop("discount_centre"), **{**MC, **args}, seed=seed)


def test_mc_dcf_degenerate_distribution_equals_point_dcf():
    f = mc(fcf_history=[100, 100, 100], growth_sd_floor=0.0, discount_sd=0.0, terminal_range=(0.02, 0.02))
    point = calculate_dcf_value([100.0] * 5, 0.10, 0.02, 10.0, 50.0)["value_per_share"]
    for k in ("p10", "p50", "p90"):
        assert val(f, f"quant:mc_dcf.fair_value_{k}") == pytest.approx(point, abs=0.01)
    assert val(f, "quant:mc_dcf.prob_fair_above_price") == 1.0          # point ~ 125 > price 100
    f2 = mc(fcf_history=[100, 100, 100], growth_sd_floor=0.0, discount_sd=0.0, terminal_range=(0.02, 0.02),
            price=200.0)
    assert val(f2, "quant:mc_dcf.prob_fair_above_price") == 0.0


def test_mc_dcf_is_reproducible_with_seed():
    a, b, c = mc(seed=11), mc(seed=11), mc(seed=12)
    assert {k: v.value for k, v in a.items()} == {k: v.value for k, v in b.items()}
    assert a["quant:mc_dcf.fair_value_p50"].value != c["quant:mc_dcf.fair_value_p50"].value
    p10, p50, p90 = (val(a, f"quant:mc_dcf.fair_value_{k}") for k in ("p10", "p50", "p90"))
    assert p10 < p50 < p90
    assert a["quant:mc_dcf.fair_value_p50"].n == 5000 and a["quant:mc_dcf.fair_value_p50"].n_kind == "simulations"


def test_mc_dcf_rejects_draws_too_close_to_terminal_growth():
    f = mc(discount_centre=0.06, discount_sd=0.0, terminal_range=(0.03, 0.05))   # spread 1-3% vs min 2%
    n = f["quant:mc_dcf.fair_value_p50"].n
    assert 0 < n < 5000 and "rejected" in f["quant:mc_dcf.fair_value_p50"].method


@pytest.mark.parametrize("kw, reason", [
    (dict(fcf_history=[100]), "at least 2 needed"),
    (dict(fcf_history=[100, -5]), "not positive"),
    (dict(shares=None), "no share count"),
    (dict(discount_centre=None), "no CAPM discount rate"),
])
def test_mc_dcf_unavailable(kw, reason):
    f = mc(**kw)
    assert all(x.status == "unavailable" and reason in x.reason for x in f.values())


def test_mc_dcf_has_no_persona_input():
    assert "persona" not in inspect.signature(monte_carlo_dcf).parameters


# ---------------------------------------------------------------- (d) forward simulation

FW = dict(horizon=63, paths=4000, block=5, outer=50, outer_paths=500)


def test_forward_zero_drift_normal_is_exactly_half():
    rng = np.random.default_rng(3)
    p = prices_from_returns(rng.normal(0.002, 0.01, 252))      # strong past uptrend
    f = forward_probability(p, **FW, drift_mode="zero", seed=1)
    assert val(f, "quant:forward.prob_up_normal") == 0.5
    assert f["quant:forward.prob_up_normal"].interval == (0.5, 0.5)
    assert abs(val(f, "quant:forward.prob_up_bootstrap") - 0.5) < 0.05   # trend removed


def test_forward_historical_drift_normal_closed_form():
    rng = np.random.default_rng(4)
    p = prices_from_returns(rng.normal(0.001, 0.01, 252))
    f = forward_probability(p, **FW, drift_mode="historical", seed=1)
    lr = np.diff(np.log(p))
    mu, sd = lr.mean(), lr.std(ddof=1)
    assert val(f, "quant:forward.prob_up_normal") == pytest.approx(stats.norm.cdf(math.sqrt(63) * mu / sd))
    se = sd / math.sqrt(len(lr))
    lo, hi = f["quant:forward.prob_up_normal"].interval
    assert lo == pytest.approx(stats.norm.cdf(math.sqrt(63) * (mu - 1.6448536 * se) / sd), rel=1e-6)
    assert lo < val(f, "quant:forward.prob_up_normal") < hi


def test_forward_all_positive_returns_with_historical_drift():
    p = prices_from_returns([0.002 + 0.001 * (i % 3) for i in range(252)])
    f = forward_probability(p, **FW, drift_mode="historical", seed=2)
    assert val(f, "quant:forward.prob_up_bootstrap") == 1.0
    assert f["quant:forward.prob_up_bootstrap"].interval == (1.0, 1.0)


def test_forward_symmetric_returns_near_half():
    p = prices_from_returns([0.01, -0.01] * 126)
    f = forward_probability(p, **FW, drift_mode="zero", seed=5)
    assert abs(val(f, "quant:forward.prob_up_bootstrap") - 0.5) < 0.05
    lo, hi = f["quant:forward.prob_up_bootstrap"].interval
    assert lo <= val(f, "quant:forward.prob_up_bootstrap") <= hi


def test_forward_is_reproducible_with_seed():
    rng = np.random.default_rng(6)
    p = prices_from_returns(rng.normal(0, 0.015, 252))
    a = forward_probability(p, **FW, seed=9)
    b = forward_probability(p, **FW, seed=9)
    assert {k: (v.value, v.interval) for k, v in a.items()} == {k: (v.value, v.interval) for k, v in b.items()}


def test_forward_short_history_unavailable():
    f = forward_probability(prices_from_returns([0.01] * 50), **FW, seed=1)
    assert all(x.status == "unavailable" and "at least 120" in x.reason for x in f.values())


# ---------------------------------------------------------------- (e) base rates

def test_wilson_interval_known_value():
    lo, hi = wilson_interval(5, 10)
    assert (lo, hi) == (pytest.approx(0.2366, abs=1e-4), pytest.approx(0.7634, abs=1e-4))


def _cases(n_up, n_down, pe=20.0, mom=0.10):
    return ([Case(date=D0, trailing_pe=pe, momentum=mom, forward_return=0.05)] * n_up
            + [Case(date=D0, trailing_pe=pe, momentum=mom, forward_return=-0.05)] * n_down)


def test_base_rate_share_and_interval():
    f = base_rate(_cases(30, 10), 20.0, 0.10, momentum_band=0.10, pe_band=0.25, min_cases=30, horizon=63)
    fld = f["quant:base_rate.share_up"]
    assert fld.value == 0.75 and fld.n == 40 and fld.n_kind == "cases"
    assert fld.interval == pytest.approx(wilson_interval(30, 40))


def test_base_rate_similarity_filter():
    cases = (_cases(30, 0) + _cases(0, 50, pe=40.0)     # PE twice today's: not similar
             + _cases(0, 50, mom=0.50))                 # momentum 40 points away: not similar
    f = base_rate(cases, 20.0, 0.10, momentum_band=0.10, pe_band=0.25, min_cases=30, horizon=63)
    assert f["quant:base_rate.share_up"].value == 1.0 and f["quant:base_rate.share_up"].n == 30


def test_base_rate_insufficient_data():
    f = base_rate(_cases(20, 9), 20.0, 0.10, momentum_band=0.10, pe_band=0.25, min_cases=30, horizon=63)
    fld = f["quant:base_rate.share_up"]
    assert fld.status == "unavailable" and fld.value is None and fld.n == 29
    assert fld.reason.startswith("insufficient data")


def test_build_cases_constant_growth_and_no_lookahead():
    closes = dated([100 * 1.001 ** t for t in range(600)])
    eps = [(D0 + timedelta(days=200), 5.0)]                   # EPS first known on day 200
    cases = build_cases(closes, eps, horizon=63, momentum_days=126)
    assert cases and all(c.date >= D0 + timedelta(days=200) for c in cases)
    assert all(c.forward_return == pytest.approx(1.001 ** 63 - 1) for c in cases)
    assert all(c.momentum == pytest.approx(1.001 ** 126 - 1) for c in cases)
    gaps = {(b.date - a.date).days for a, b in zip(cases, cases[1:])}
    assert gaps == {63}                                       # non-overlapping windows
    assert max(c.date for c in cases) <= closes[-1 - 63][0]   # forward window inside the data


def test_split_adjusted_eps():
    eps = [(date(2019, 10, 31), 11.89), (date(2020, 10, 30), 3.28)]
    adj = split_adjusted_eps(eps, [(date(2014, 6, 9), 7.0), (date(2020, 8, 31), 4.0)])
    assert adj == [(date(2019, 10, 31), pytest.approx(11.89 / 4)), (date(2020, 10, 30), 3.28)]


# ---------------------------------------------------------------- (f) calibration

def test_brier_hand_computed():
    assert brier_score([(1.0, 1), (0.0, 0)]) == 0.0
    assert brier_score([(0.5, 1), (0.5, 0)]) == 0.25
    assert brier_score([(0.8, 1), (0.3, 0), (0.6, 0)]) == pytest.approx((0.04 + 0.09 + 0.36) / 3)


def test_reliability_table():
    rows = reliability_table([(0.05, 0), (0.15, 1), (0.15, 0), (1.0, 1)], bins=10)
    assert len(rows) == 10
    assert (rows[0].n, rows[1].n, rows[9].n) == (1, 2, 1)       # p = 1.0 lands in the last bin
    assert rows[1].mean_stated == pytest.approx(0.15) and rows[1].observed_share == 0.5
    assert rows[5].n == 0 and rows[5].mean_stated is None


def test_calibration_min_pairs_and_validation():
    f, table = calibration([(0.6, 1)] * 5, min_pairs=20)
    assert f["quant:calibration.brier"].status == "unavailable" and table is None
    f, table = calibration([(0.6, 1)] * 10 + [(0.6, 0)] * 10, min_pairs=20)
    assert f["quant:calibration.brier"].value == pytest.approx(0.5 * 0.16 + 0.5 * 0.36)
    with pytest.raises(ValueError):
        calibration([(1.2, 1)] * 20)


def test_load_pairs_respects_as_of_and_field(tmp_path):
    p = tmp_path / "pairs.csv"
    p.write_text("ticker,as_of,outcome_date,field,stated_p,outcome\n"
                 "AAPL,2024-01-02,2024-04-02,forward.prob_up_bootstrap,0.6,1\n"
                 "AAPL,2024-06-03,2024-09-03,forward.prob_up_bootstrap,0.4,0\n"
                 "AAPL,2024-01-02,2024-04-02,mc_dcf.prob_fair_above_price,0.2,1\n")
    assert load_pairs(str(p), "forward.prob_up_bootstrap", date(2024, 5, 1)) == [(0.6, 1)]
    assert load_pairs(str(tmp_path / "missing.csv"), "x", date(2024, 5, 1)) == []


# ---------------------------------------------------------------- report

def _fixture_inputs():
    return inputs_from_fetched(fetch_financial_statements("AAPL"), fetch_market_data("AAPL"))


AS_OF = date(2026, 11, 13)


def test_report_from_fixtures_is_complete_and_reproducible():
    a = build_quant_report(_fixture_inputs(), AS_OF, CFG.quant, CFG.capm)
    b = build_quant_report(_fixture_inputs(), AS_OF, CFG.quant, CFG.capm)
    assert a.model_dump() == b.model_dump()
    assert a.seed == CFG.quant.seed and a.drift_mode == "zero"
    for f in a.fields.values():
        assert f.id.startswith("quant:") and f.unit and f.n_kind and f.kind
        if f.status == "ok":
            assert f.value is not None and f.n is not None and f.n > 0
        else:
            assert f.value is None and f.reason
    assert a.value("quant:market.beta") == pytest.approx(1.07136, abs=1e-4)
    assert a.get("quant:base_rate.share_up").reason.startswith("insufficient data")
    assert a.get("quant:calibration.brier").status == "unavailable"


def test_report_seed_changes_only_random_fields():
    a = build_quant_report(_fixture_inputs(), AS_OF, CFG.quant, CFG.capm, seed=1)
    b = build_quant_report(_fixture_inputs(), AS_OF, CFG.quant, CFG.capm, seed=2)
    assert a.value("quant:returns.vol_annual") == b.value("quant:returns.vol_annual")
    assert a.value("quant:market.beta") == b.value("quant:market.beta")
    assert a.value("quant:mc_dcf.fair_value_p50") != b.value("quant:mc_dcf.fair_value_p50")


def test_report_with_no_inputs_is_all_unavailable():
    r = build_quant_report(QuantInputs(), AS_OF, CFG.quant, CFG.capm, calibration_pairs=[])
    assert all(f.status == "unavailable" and f.value is None for f in r.fields.values())
    assert r.evidence_records() == []


def test_report_rejects_look_ahead():
    q = _fixture_inputs()
    with pytest.raises(LookAheadError, match="closes"):
        build_quant_report(q, date(2026, 1, 2), CFG.quant, CFG.capm)
    q2 = QuantInputs(fcf=[("FY2030", 1.0, date(2030, 11, 1))])
    with pytest.raises(LookAheadError, match="fcf"):
        build_quant_report(q2, AS_OF, CFG.quant, CFG.capm)


def test_drift_toggle_is_used(tmp_path):
    cfg = CFG.quant.model_copy(update={"forward": CFG.quant.forward.model_copy(update={"drift_mode": "historical"})})
    r = build_quant_report(_fixture_inputs(), AS_OF, cfg, CFG.capm)
    assert r.drift_mode == "historical"
    assert r.value("quant:forward.prob_up_normal") != 0.5


def test_quant_field_rejects_inconsistent_states():
    with pytest.raises(ValueError):
        QuantField(id="quant:x", label="x", status="ok", value=None, unit="ratio", n_kind="cases",
                   kind="descriptive", method="m")
    with pytest.raises(ValueError):
        QuantField(id="quant:x", label="x", status="unavailable", value=0.5, unit="ratio", n_kind="cases",
                   kind="descriptive", method="m", reason="r")
