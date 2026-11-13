"""
(e) Base rates - how often similar past situations went up over the horizon.
[descriptive of the past; used as a prior]

A case is (date, trailing PE, momentum, forward return). The report builds cases from the
stock's own ~10-year history:
  trailing PE at t  = close_t / latest split-adjusted diluted EPS whose 10-K was FILED on or
                      before t (no look-ahead; EPS divided by the product of stock splits dated
                      after its filing, because prices from yfinance are split-adjusted)
  momentum at t     = close_t / close_{t - momentum_days} - 1
  forward return    = close_{t + horizon} / close_t - 1, only when t + horizon <= as_of
Cases are taken every `horizon` trading days counting back from the last complete window, so
forward windows do not overlap and no case is double-counted.

Similar = |momentum - current momentum| <= momentum_band and |PE / current PE - 1| <= pe_band.
share_up = fraction of similar cases with forward return > 0, Wilson 95% interval.
Below `min_cases` similar cases the field is "insufficient data", never a number.
"""

import math
from bisect import bisect_right
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel

from quant.models import QuantField, ok, unavailable

Z95 = 1.959963984540054


class Case(BaseModel):
    date: date
    trailing_pe: float
    momentum: float
    forward_return: float


def wilson_interval(k: int, n: int, z: float = Z95) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def split_adjusted_eps(eps_history: Sequence[Tuple[date, float]], splits: Sequence[Tuple[date, float]]):
    """[(filed_date, eps)] -> [(filed_date, eps / product of split ratios dated after filing)]."""
    out = []
    for filed, eps in eps_history:
        factor = 1.0
        for d, ratio in splits:
            if d > filed and ratio > 0:
                factor *= ratio
        out.append((filed, eps / factor))
    return sorted(out)


def build_cases(closes: Sequence[Tuple[date, float]], eps_known: Sequence[Tuple[date, float]], *,
                horizon: int, momentum_days: int) -> List[Case]:
    """closes: [(date, split-adjusted close)] oldest first; eps_known: [(filed_date, adjusted eps)]."""
    dates = [d for d, _ in closes]
    px = [c for _, c in closes]
    eps_dates = [d for d, _ in eps_known]
    cases = []
    t = len(closes) - 1 - horizon            # last date whose forward window is complete
    while t >= momentum_days:
        i = bisect_right(eps_dates, dates[t]) - 1
        if i >= 0 and eps_known[i][1] > 0 and px[t] > 0 and px[t - momentum_days] > 0:
            cases.append(Case(date=dates[t], trailing_pe=px[t] / eps_known[i][1],
                              momentum=px[t] / px[t - momentum_days] - 1,
                              forward_return=px[t + horizon] / px[t] - 1))
        t -= horizon
    return list(reversed(cases))


def base_rate(cases: Sequence[Case], current_pe: Optional[float], current_momentum: Optional[float], *,
              momentum_band: float, pe_band: float, min_cases: int, horizon: int) -> Dict[str, QuantField]:
    fid = "quant:base_rate.share_up"
    label = f"Share of similar past cases that rose over {horizon} trading days"
    method = (f"own history, non-overlapping {horizon}-day windows; similar = momentum within "
              f"+/-{momentum_band:.0%} points and trailing PE within +/-{pe_band:.0%}; Wilson 95% interval")
    if current_pe is None or current_pe <= 0:
        return {fid: unavailable(fid, label, "ratio", "cases", "descriptive", method,
                                 "no positive current trailing PE")}
    if current_momentum is None:
        return {fid: unavailable(fid, label, "ratio", "cases", "descriptive", method, "no current momentum")}
    similar = [c for c in cases
               if abs(c.momentum - current_momentum) <= momentum_band
               and abs(c.trailing_pe / current_pe - 1) <= pe_band]
    n = len(similar)
    if n < min_cases:
        return {fid: unavailable(fid, label, "ratio", "cases", "descriptive", method,
                                 f"insufficient data: {n} similar cases out of {len(cases)}; at least {min_cases} needed",
                                 n)}
    k = sum(c.forward_return > 0 for c in similar)
    return {fid: ok(fid, label, k / n, "ratio", n, "cases", "descriptive", method,
                    interval=wilson_interval(k, n), interval_level=0.95)}
