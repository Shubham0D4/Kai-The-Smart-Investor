"""
(d) Forward simulation - probability that the price is above today's after N trading days.
[prediction]

Uses daily LOG returns of the last `window` days.
drift_mode "zero" (default): log returns are de-meaned, so the median path is flat and only the
    shape of the return distribution (volatility, skew, fat tails, short-run dependence) remains.
drift_mode "historical": the past window's average daily log return is carried forward.

Bootstrap: moving-block bootstrap (blocks of `block` consecutive days, start drawn uniformly) to
    build `paths` N-day paths; P(up) = share of paths with total log return > 0.
    Interval: outer bootstrap - resample the history `outer` times (same block scheme), recompute
    P(up) with `outer_paths` paths each, report the 5th-95th percentiles (estimation uncertainty
    from having one finite history).
Normal: log returns ~ Normal(mu, sigma) i.i.d.; P(up) = Phi(sqrt(N) mu / sigma).
    Interval from mu +/- 1.645 sigma/sqrt(n). With drift "zero", mu = 0 exactly, so P(up) = 0.5
    and the interval collapses to a point - by construction, not by estimate.
"""

import math
from typing import Dict, Sequence

import numpy as np
from scipy import stats

from quant.models import QuantField, ok, unavailable

Z90 = stats.norm.ppf(0.95)   # 1.645, two-sided 90%


def log_returns(prices: Sequence[float]) -> np.ndarray:
    p = np.asarray(prices, dtype=float)
    return np.diff(np.log(p))


def block_bootstrap_sums(returns: np.ndarray, horizon: int, paths: int, block: int,
                         rng: np.random.Generator) -> np.ndarray:
    """Total log return of `paths` simulated `horizon`-day paths (moving-block bootstrap)."""
    n = len(returns)
    block = min(block, n)
    n_blocks = math.ceil(horizon / block)
    starts = rng.integers(0, n - block + 1, size=(paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)).reshape(paths, n_blocks * block)[:, :horizon]
    return returns[idx].sum(axis=1)


def block_bootstrap_sums_series(returns: np.ndarray, length: int, block: int, rng) -> np.ndarray:
    """One block-bootstrapped pseudo-history of `length` daily returns."""
    n = len(returns)
    block = min(block, n)
    n_blocks = math.ceil(length / block)
    starts = rng.integers(0, n - block + 1, size=n_blocks)
    return returns[(starts[:, None] + np.arange(block)).ravel()[:length]]


def forward_probability(prices: Sequence[float], *, horizon: int, paths: int, block: int,
                        outer: int, outer_paths: int, drift_mode: str = "zero", window: int = 252,
                        min_obs: int = 120, seed: int) -> Dict[str, QuantField]:
    fid = lambda k: f"quant:forward.{k}"
    p = np.asarray(prices, dtype=float)[-(window + 1):]
    r = log_returns(p) if len(p) >= 2 and np.all(p > 0) else np.array([])
    n = len(r)
    drift_txt = "trend removed (log returns de-meaned)" if drift_mode == "zero" else "past drift carried forward"
    m_boot = f"moving-block bootstrap, block {block}, {paths} paths of {horizon} days, {drift_txt}; seed {seed}"
    m_norm = f"i.i.d. normal log returns over {horizon} days, {drift_txt}"
    labels = {
        "prob_up_bootstrap": (f"P(price above today in {horizon} trading days), bootstrap", "ratio", "simulations", m_boot),
        "prob_up_normal": (f"P(price above today in {horizon} trading days), normal", "ratio", "observations", m_norm),
        "return_p5": (f"{horizon}-day return, 5th percentile (bootstrap)", "ratio", "simulations", m_boot),
        "return_p50": (f"{horizon}-day return, median (bootstrap)", "ratio", "simulations", m_boot),
        "return_p95": (f"{horizon}-day return, 95th percentile (bootstrap)", "ratio", "simulations", m_boot),
    }
    if n < min_obs:
        reason = f"only {n} daily returns; at least {min_obs} needed"
        return {fid(k): unavailable(fid(k), lbl, u, nk, "prediction", m, reason, n)
                for k, (lbl, u, nk, m) in labels.items()}

    if drift_mode == "zero":
        r = r - r.mean()
    rng = np.random.default_rng(seed)

    sums = block_bootstrap_sums(r, horizon, paths, block, rng)
    p_up = float(np.mean(sums > 0))
    outer_probs = []
    for _ in range(outer):
        hist = block_bootstrap_sums_series(r, n, block, rng)
        if drift_mode == "zero":
            hist = hist - hist.mean()
        outer_probs.append(np.mean(block_bootstrap_sums(hist, horizon, outer_paths, block, rng) > 0))
    lo, hi = np.percentile(outer_probs, [5, 95])
    q5, q50, q95 = np.expm1(np.percentile(sums, [5, 50, 95]))

    mu, sigma = float(r.mean()), float(np.std(r, ddof=1))
    if drift_mode == "zero":
        mu, p_norm, norm_iv = 0.0, 0.5, (0.5, 0.5)
    else:
        se = sigma / math.sqrt(n)
        f = lambda m: float(stats.norm.cdf(math.sqrt(horizon) * m / sigma))
        p_norm, norm_iv = f(mu), (f(mu - Z90 * se), f(mu + Z90 * se))

    return {
        fid("prob_up_bootstrap"): ok(fid("prob_up_bootstrap"), labels["prob_up_bootstrap"][0], p_up, "ratio",
                                     paths, "simulations", "prediction",
                                     f"{m_boot}; interval: {outer} resampled histories x {outer_paths} paths",
                                     interval=(lo, hi), interval_level=0.90),
        fid("prob_up_normal"): ok(fid("prob_up_normal"), labels["prob_up_normal"][0], p_norm, "ratio",
                                  n, "observations", "prediction",
                                  f"{m_norm}; interval from mean +/- 1.645 sd/sqrt(n)",
                                  interval=norm_iv, interval_level=0.90),
        fid("return_p5"): ok(fid("return_p5"), labels["return_p5"][0], q5, "ratio", paths, "simulations", "prediction", m_boot),
        fid("return_p50"): ok(fid("return_p50"), labels["return_p50"][0], q50, "ratio", paths, "simulations", "prediction", m_boot),
        fid("return_p95"): ok(fid("return_p95"), labels["return_p95"][0], q95, "ratio", paths, "simulations", "prediction", m_boot),
    }
