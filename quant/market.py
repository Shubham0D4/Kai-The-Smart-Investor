"""
(b) Market relationship - OLS of the stock's daily simple returns on the benchmark's
(S&P 500), over consecutive dates present in both series:

    r_stock = alpha + beta x r_bench + e

beta_se is the classical OLS standard error (assumes homoskedastic, uncorrelated residuals).
correlation is Pearson's r between the two return series. All descriptive.
"""

from typing import Dict, Sequence, Tuple

import numpy as np
import statsmodels.api as sm

from quant.models import QuantField, ok, unavailable

_FIELDS = {
    "beta": ("Beta vs S&P 500", "x", "OLS slope of daily stock returns on S&P 500 returns"),
    "beta_se": ("Standard error of beta", "x", "classical OLS standard error of the slope"),
    "correlation": ("Correlation with S&P 500", "ratio", "Pearson correlation of daily returns"),
    "alpha_daily": ("OLS intercept (daily)", "ratio", "OLS intercept of daily returns"),
}


def aligned_returns(stock: Sequence[Tuple[str, float]], bench: Sequence[Tuple[str, float]]):
    """Daily simple returns on consecutive dates common to both (date, close) series."""
    s, b = dict(stock), dict(bench)
    dates = sorted(set(s) & set(b))
    rs, rb = [], []
    for prev, cur in zip(dates, dates[1:]):
        if s[prev] > 0 and b[prev] > 0:
            rs.append(s[cur] / s[prev] - 1)
            rb.append(b[cur] / b[prev] - 1)
    return np.array(rs), np.array(rb)


def market_relationship(stock: Sequence[Tuple[str, float]], bench: Sequence[Tuple[str, float]],
                        min_obs: int = 60) -> Dict[str, QuantField]:
    fid = lambda k: f"quant:market.{k}"
    rs, rb = aligned_returns(stock, bench) if stock and bench else (np.array([]), np.array([]))
    n = len(rs)

    def all_unavailable(reason):
        return {fid(k): unavailable(fid(k), lbl, unit, "observations", "descriptive", m, reason, n)
                for k, (lbl, unit, m) in _FIELDS.items()}

    if not bench:
        return all_unavailable("no benchmark history")
    if n < min_obs:
        return all_unavailable(f"only {n} common daily returns; at least {min_obs} needed")
    if np.std(rb) < 1e-12:
        return all_unavailable("benchmark returns have no variance")

    fit = sm.OLS(rs, sm.add_constant(rb)).fit()
    values = {
        "beta": float(fit.params[1]),
        "beta_se": float(fit.bse[1]),
        "correlation": float(np.corrcoef(rs, rb)[0, 1]),
        "alpha_daily": float(fit.params[0]),
    }
    return {fid(k): ok(fid(k), lbl, values[k], unit, n, "observations", "descriptive", m)
            for k, (lbl, unit, m) in _FIELDS.items()}
