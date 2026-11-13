"""
(a) Return distribution - descriptive statistics of the last `window` daily returns.

Simple daily returns r_t = P_t / P_{t-1} - 1.
  vol_annual      sample standard deviation (ddof=1) x sqrt(252)
  skew            adjusted Fisher-Pearson sample skewness (scipy, bias=False)
  excess_kurtosis sample excess kurtosis (scipy, fisher=True, bias=False); normal = 0
  max_drawdown    min_t P_t / max_{s<=t} P_s - 1 over the window (a negative fraction)
  var95_hist      1-day 95% Value at Risk, historical: -(5th percentile of r), linear interpolation
  var95_normal    1-day 95% VaR assuming normal returns: -(mean + z_0.05 x sd)   [model]
VaR is a positive loss fraction (0.03 = a 3% one-day loss).
"""

import math
from typing import Dict, Sequence

import numpy as np
from scipy import stats

from quant.models import QuantField, ok, unavailable

TRADING_DAYS = 252
Z_05 = stats.norm.ppf(0.05)


def simple_returns(prices: Sequence[float]) -> np.ndarray:
    p = np.asarray(prices, dtype=float)
    return p[1:] / p[:-1] - 1


def max_drawdown(prices: Sequence[float]) -> float:
    p = np.asarray(prices, dtype=float)
    return float(np.min(p / np.maximum.accumulate(p) - 1))


_FIELDS = {
    "vol_annual": ("Annualised volatility of daily returns", "ratio", "descriptive",
                   "sample sd of daily simple returns x sqrt(252)"),
    "skew": ("Skewness of daily returns", "ratio", "descriptive", "adjusted sample skewness"),
    "excess_kurtosis": ("Excess kurtosis of daily returns", "ratio", "descriptive",
                        "adjusted sample excess kurtosis (normal = 0)"),
    "max_drawdown": ("Maximum drawdown", "ratio", "descriptive", "min of price / running peak - 1"),
    "var95_hist": ("1-day 95% Value at Risk (historical)", "ratio", "descriptive",
                   "-(5th percentile of daily returns)"),
    "var95_normal": ("1-day 95% Value at Risk (normal)", "ratio", "model",
                     "-(mean + z_0.05 x sd), assumes normally distributed returns"),
}


def return_distribution(prices: Sequence[float], window: int = 252, min_obs: int = 60) -> Dict[str, QuantField]:
    """Statistics of the last `window` daily returns of `prices` (oldest first)."""
    p = np.asarray(prices, dtype=float)[-(window + 1):]
    r = simple_returns(p) if len(p) >= 2 else np.array([])
    n = len(r)
    fid = lambda k: f"quant:returns.{k}"

    bad = None
    if n < min_obs:
        bad = f"only {n} daily returns; at least {min_obs} needed"
    elif not np.all(np.isfinite(p)) or np.any(p <= 0):
        bad = "price history contains non-positive or missing values"
    if bad:
        return {fid(k): unavailable(fid(k), lbl, unit, "observations", kind, m, bad, n)
                for k, (lbl, unit, kind, m) in _FIELDS.items()}

    sd = float(np.std(r, ddof=1))
    flat = sd < 1e-10 * max(1.0, float(np.mean(np.abs(r))))     # numerically constant returns
    values = {
        "vol_annual": sd * math.sqrt(TRADING_DAYS),
        "skew": float("nan") if flat else float(stats.skew(r, bias=False)),
        "excess_kurtosis": float("nan") if flat else float(stats.kurtosis(r, fisher=True, bias=False)),
        "max_drawdown": max_drawdown(p),
        "var95_hist": float(-np.quantile(r, 0.05)),
        "var95_normal": float(-(np.mean(r) + Z_05 * sd)),
    }
    # ok() turns a non-finite value into "unavailable" with the reason, never a number
    return {fid(k): ok(fid(k), lbl, values[k], unit, n, "observations", kind, m)
            for k, (lbl, unit, kind, m) in _FIELDS.items()}
