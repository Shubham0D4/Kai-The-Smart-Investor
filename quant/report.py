"""
Entry point of the quant layer: build_quant_report(...) -> QuantReport (DESIGN.md §10).

Pure: takes already-fetched data, makes no network or LLM calls. Randomness comes only from
`seed` (Monte Carlo DCF uses seed, the forward simulation seed + 1).

Look-ahead guard: every dated input row (closes, benchmark closes, split dates, filing dates of
fundamentals, price and risk-free dates) must be on or before `as_of`, else LookAheadError.
Prices must be on the as-of share basis (the backtest script converts yfinance's split-adjusted
closes; for a live run as_of is today and no conversion is needed).
"""

from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from coordinator.config import CapmConfig, QuantConfig
from mcp_server.tools.finance_math import capm_discount_rate
from mcp_server.tools.sec_financials import fcf_history, latest, net_debt
from quant.base_rates import base_rate, build_cases, split_adjusted_eps
from quant.calibration import calibration, load_pairs
from quant.forward import forward_probability
from quant.market import market_relationship
from quant.mc_dcf import monte_carlo_dcf
from quant.models import QuantField, QuantReport, ok, unavailable
from quant.returns import return_distribution


class LookAheadError(ValueError):
    """An input row is dated after the report's as-of date."""


class QuantInputs(BaseModel):
    closes: List[Tuple[date, float]] = Field(default_factory=list)       # stock, oldest first
    benchmark: List[Tuple[date, float]] = Field(default_factory=list)    # S&P 500
    price: Optional[float] = None
    price_date: Optional[date] = None
    risk_free: Optional[float] = None
    risk_free_date: Optional[date] = None
    fcf: List[Tuple[str, float, Optional[date]]] = Field(default_factory=list)   # (fy, value, filed)
    net_debt: Optional[float] = None
    net_debt_filed: Optional[date] = None
    shares: Optional[float] = None
    shares_date: Optional[date] = None
    eps_history: List[Tuple[date, float]] = Field(default_factory=list)  # (filed, diluted EPS as reported)
    splits: List[Tuple[date, float]] = Field(default_factory=list)       # (date, ratio)


def check_no_lookahead(inputs: QuantInputs, as_of: date) -> None:
    late = []
    for name, rows in (("closes", inputs.closes), ("benchmark", inputs.benchmark),
                       ("eps_history", inputs.eps_history), ("splits", inputs.splits)):
        late += [f"{name} {d}" for d, _ in rows if d > as_of]
    late += [f"fcf {fy} filed {f}" for fy, _, f in inputs.fcf if f and f > as_of]
    for name in ("price_date", "risk_free_date", "net_debt_filed", "shares_date"):
        d = getattr(inputs, name)
        if d and d > as_of:
            late.append(f"{name} {d}")
    if late:
        raise LookAheadError(f"{len(late)} input row(s) dated after as_of {as_of}: {late[:5]}")


def _span(rows) -> Optional[Tuple[date, date]]:
    ds = [r[0] for r in rows]
    return (min(ds), max(ds)) if ds else None


def build_quant_report(inputs: QuantInputs, as_of: date, cfg: QuantConfig, capm: CapmConfig,
                       seed: Optional[int] = None,
                       calibration_pairs: Optional[Sequence[Tuple[float, int]]] = None) -> QuantReport:
    check_no_lookahead(inputs, as_of)
    seed = cfg.seed if seed is None else seed
    closes = sorted(inputs.closes)
    prices = [c for _, c in closes]
    fields: Dict[str, QuantField] = {}
    notes: List[str] = []

    # (a) return distribution
    fields.update(return_distribution(prices, cfg.returns.window_days, cfg.returns.min_obs))

    # (b) market relationship
    market = market_relationship(closes, sorted(inputs.benchmark), cfg.market.min_obs)
    fields.update(market)

    # CAPM discount rate from the report's own beta (persona-free)
    beta = market["quant:market.beta"]
    cid, clabel = "quant:capm.discount_rate", "CAPM discount rate"
    cmethod = (f"risk-free + clamp(beta, {list(capm.beta_clamp)}) x {capm.equity_risk_premium:.0%} ERP, "
               f"clamped to {list(capm.rate_clamp)}")
    if beta.status != "ok":
        fields[cid] = unavailable(cid, clabel, "ratio", "observations", "model", cmethod, f"beta unavailable: {beta.reason}")
    elif inputs.risk_free is None:
        fields[cid] = unavailable(cid, clabel, "ratio", "observations", "model", cmethod, "no risk-free rate")
    else:
        rate = capm_discount_rate(inputs.risk_free, beta.value, capm.equity_risk_premium, capm.beta_clamp, capm.rate_clamp)
        fields[cid] = ok(cid, clabel, rate, "ratio", beta.n, "observations", "model", cmethod)
    discount = fields[cid].value

    # (c) Monte Carlo DCF
    mc = cfg.mc_dcf
    fields.update(monte_carlo_dcf(
        [v for _, v, _ in inputs.fcf], inputs.net_debt, inputs.shares, inputs.price, discount,
        simulations=mc.simulations, growth_clamp=mc.growth_clamp, growth_sd_floor=mc.growth_sd_floor,
        growth_clip=mc.growth_clip, discount_sd=mc.discount_sd, discount_clip=capm.rate_clamp,
        terminal_range=mc.terminal_range, min_spread=mc.min_spread, seed=seed))

    # (d) forward simulation
    fw = cfg.forward
    fields.update(forward_probability(
        prices, horizon=fw.horizon_days, paths=fw.paths, block=fw.block_length, outer=fw.outer_resamples,
        outer_paths=fw.outer_paths, drift_mode=fw.drift_mode, window=cfg.returns.window_days,
        min_obs=fw.min_obs, seed=seed + 1))

    # (e) base rates from own history
    br = cfg.base_rate
    history = closes[-(br.history_years * 252 + 1):]
    eps_known = split_adjusted_eps(inputs.eps_history, inputs.splits)
    cases = build_cases(history, eps_known, horizon=br.horizon_days, momentum_days=br.momentum_days)
    known_now = [e for d, e in eps_known if d <= as_of]
    current_pe = prices[-1] / known_now[-1] if prices and known_now and known_now[-1] > 0 else None
    current_mom = (prices[-1] / prices[-1 - br.momentum_days] - 1) if len(prices) > br.momentum_days else None
    fields.update(base_rate(cases, current_pe, current_mom, momentum_band=br.momentum_band, pe_band=br.pe_band,
                            min_cases=br.min_cases, horizon=br.horizon_days))
    notes.append(f"base rates: {len(cases)} non-overlapping {br.horizon_days}-day cases from "
                 f"{len(history)} closes; EPS split-adjusted for splits after each filing")

    # (f) calibration of our own past forecasts
    cal = cfg.calibration
    pairs = list(calibration_pairs) if calibration_pairs is not None else load_pairs(cal.path, cal.field, as_of)
    cal_fields, reliability = calibration(pairs, bins=cal.bins, min_pairs=cal.min_pairs, field=cal.field)
    fields.update(cal_fields)

    return QuantReport(
        as_of=as_of, seed=seed, drift_mode=fw.drift_mode, horizon_days=fw.horizon_days,
        input_dates={"closes": _span(closes), "benchmark": _span(sorted(inputs.benchmark)),
                     "eps_history": _span(sorted(inputs.eps_history))},
        fields=fields, reliability=reliability, notes=notes,
    )


def _d(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def inputs_from_fetched(financials: Optional[Dict], market: Optional[Dict]) -> QuantInputs:
    """Adapter from the coordinator's fetched data (phase 2 tools) to QuantInputs. Missing or
    failed sources leave the corresponding inputs empty, so dependent fields are unavailable."""
    q = QuantInputs()
    if market and market.get("success"):
        long = market.get("history_long") or market.get("history") or []
        q.closes = [(_d(p["date"]), p["close"]) for p in long]
        q.benchmark = [(_d(p["date"]), p["close"]) for p in market.get("benchmark_history") or []]
        q.price = market.get("price")
        q.price_date = q.closes[-1][0] if q.closes else None
        q.risk_free = market.get("risk_free_rate")
        q.risk_free_date = q.price_date if q.risk_free is not None else None
        q.splits = [(_d(s["date"]), s["ratio"]) for s in market.get("splits") or []]
        if market.get("shares_outstanding"):
            q.shares, q.shares_date = market["shares_outstanding"], q.price_date
    if financials and financials.get("success"):
        recs = financials["records"]
        q.fcf = [(f["fy"], f["value"], _d(f["filed"])) for f in fcf_history(recs)]
        nd = net_debt(recs)
        q.net_debt, q.net_debt_filed = nd["value"], _d(nd["filed"])
        sh = latest(recs, "shares_outstanding")
        if sh:                                  # SEC cover-page count preferred over yfinance
            q.shares, q.shares_date = sh["value"], _d(sh.get("filed"))
        q.eps_history = [(_d(e["filed"]), e["value"]) for e in financials.get("eps_history", []) if e.get("filed")]
    return q
