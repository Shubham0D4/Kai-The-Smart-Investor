"""
(c) Monte Carlo DCF - a fair-value distribution under stated input distributions. [model]

Each simulation draws
    g  ~ Normal(centre_g, sd_g), clipped to growth_clip       5-year FCF growth
    r  ~ Normal(discount_centre, discount_sd), clipped         discount rate (CAPM; persona-free)
    tg ~ Uniform(terminal_range)                               terminal growth
and rejects draws with r - tg < min_spread. Each kept draw runs the same 5-year DCF as Gamma
(mcp_server/tools/finance_math.calculate_dcf_value):
    FCF_k = FCF_0 (1+g)^k,  EV = sum FCF_k/(1+r)^k + FCF_5 (1+tg)/(r-tg)/(1+r)^5
    fair value per share = (EV - net_debt) / shares
centre_g = historical FCF CAGR clamped to growth_clamp (0 if the CAGR is undefined);
sd_g = sample sd of year-on-year FCF growth (years with a positive base), floored at growth_sd_floor.
"""

import math
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from quant.models import QuantField, ok, unavailable

PROJECTION_YEARS = 5

_FIELDS = {
    "fair_value_p10": ("DCF fair value per share, 10th percentile", "USD"),
    "fair_value_p50": ("DCF fair value per share, median", "USD"),
    "fair_value_p90": ("DCF fair value per share, 90th percentile", "USD"),
    "prob_fair_above_price": ("Share of simulations with fair value above price", "ratio"),
}


def growth_parameters(fcf: Sequence[float], clamp: Tuple[float, float], sd_floor: float) -> Tuple[float, float]:
    first, last, years = fcf[0], fcf[-1], len(fcf) - 1
    cagr = (last / first) ** (1 / years) - 1 if first > 0 and last > 0 and years >= 1 else 0.0
    centre = min(max(cagr, clamp[0]), clamp[1])
    yoy = [b / a - 1 for a, b in zip(fcf, fcf[1:]) if a > 0]
    sd = float(np.std(yoy, ddof=1)) if len(yoy) >= 2 else 0.0
    return centre, max(sd, sd_floor)


def dcf_per_share(base_fcf, g, r, tg, net_debt: float, shares: float) -> np.ndarray:
    """Vectorised 5-year DCF; g, r, tg are arrays of equal length."""
    k = np.arange(1, PROJECTION_YEARS + 1)
    fcfs = base_fcf * (1 + g[:, None]) ** k
    pv = (fcfs / (1 + r[:, None]) ** k).sum(axis=1)
    tv = fcfs[:, -1] * (1 + tg) / (r - tg)
    ev = pv + tv / (1 + r) ** PROJECTION_YEARS
    return (ev - net_debt) / shares


def monte_carlo_dcf(fcf_history: Sequence[float], net_debt: Optional[float], shares: Optional[float],
                    price: Optional[float], discount_centre: Optional[float], *, simulations: int,
                    growth_clamp, growth_sd_floor: float, growth_clip, discount_sd: float,
                    discount_clip: Tuple[float, float], terminal_range, min_spread: float,
                    seed: int) -> Dict[str, QuantField]:
    fid = lambda k: f"quant:mc_dcf.{k}"
    method = (f"{simulations} draws: FCF growth ~ Normal(hist. CAGR, hist. sd), discount ~ Normal(CAPM, "
              f"{discount_sd:.0%}), terminal ~ Uniform{tuple(terminal_range)}; 5y DCF less net debt")

    def all_unavailable(reason):
        return {fid(k): unavailable(fid(k), lbl, unit, "simulations", "model", method, reason)
                for k, (lbl, unit) in _FIELDS.items()}

    fcf = [float(x) for x in fcf_history]
    if len(fcf) < 2:
        return all_unavailable(f"only {len(fcf)} year(s) of free cash flow; at least 2 needed")
    if fcf[-1] <= 0:
        return all_unavailable("latest free cash flow is not positive")
    if not shares or shares <= 0:
        return all_unavailable("no share count")
    if discount_centre is None:
        return all_unavailable("no CAPM discount rate (beta or risk-free rate unavailable)")
    if price is None or price <= 0:
        return all_unavailable("no current price")
    if net_debt is None:
        return all_unavailable("no net debt figure")

    rng = np.random.default_rng(seed)
    g_c, g_sd = growth_parameters(fcf, growth_clamp, growth_sd_floor)
    g = np.clip(rng.normal(g_c, g_sd, simulations), *growth_clip)
    r = np.clip(rng.normal(discount_centre, discount_sd, simulations), *discount_clip)
    tg = rng.uniform(terminal_range[0], terminal_range[1], simulations)
    keep = (r - tg) >= min_spread
    n = int(keep.sum())
    if n < max(100, simulations // 10):
        return all_unavailable(f"only {n} of {simulations} draws had discount - terminal >= {min_spread}")

    fair = dcf_per_share(fcf[-1], g[keep], r[keep], tg[keep], net_debt, shares)
    p10, p50, p90 = np.percentile(fair, [10, 50, 90])
    detail = f"{method}; growth centre {g_c:.4f}, sd {g_sd:.4f}; seed {seed}; {simulations - n} draws rejected"
    out = {
        fid("fair_value_p10"): ok(fid("fair_value_p10"), _FIELDS["fair_value_p10"][0], p10, "USD", n, "simulations", "model", detail),
        fid("fair_value_p50"): ok(fid("fair_value_p50"), _FIELDS["fair_value_p50"][0], p50, "USD", n, "simulations", "model", detail),
        fid("fair_value_p90"): ok(fid("fair_value_p90"), _FIELDS["fair_value_p90"][0], p90, "USD", n, "simulations", "model", detail),
        fid("prob_fair_above_price"): ok(fid("prob_fair_above_price"), _FIELDS["prob_fair_above_price"][0],
                                         float(np.mean(fair > price)), "ratio", n, "simulations", "model", detail),
    }
    return out
