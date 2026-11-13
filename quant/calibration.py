"""
(f) Calibration - how well past stated probabilities matched what happened. [descriptive]

pairs: (stated probability p in [0, 1], outcome in {0, 1}).
  Brier score = mean((p - outcome)^2); 0 is perfect, 0.25 is always saying 0.5.
  Reliability table: `bins` equal-width bins of p; per bin n, mean stated p, observed share.
Pairs are written by scripts/calibration_backtest.py as CSV with columns
ticker, as_of, outcome_date, field, stated_p, outcome. Only pairs whose outcome_date is on or
before the report's as_of are used (no look-ahead).
"""

import csv
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from quant.models import QuantField, ReliabilityBin, ok, unavailable


def reliability_table(pairs: Sequence[Tuple[float, int]], bins: int = 10) -> List[ReliabilityBin]:
    p = np.array([x for x, _ in pairs], dtype=float)
    o = np.array([y for _, y in pairs], dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, bins - 1) if len(p) else np.array([], int)
    rows = []
    for b in range(bins):
        m = idx == b
        k = int(m.sum())
        rows.append(ReliabilityBin(low=float(edges[b]), high=float(edges[b + 1]), n=k,
                                   mean_stated=float(p[m].mean()) if k else None,
                                   observed_share=float(o[m].mean()) if k else None))
    return rows


def brier_score(pairs: Sequence[Tuple[float, int]]) -> float:
    p = np.array([x for x, _ in pairs], dtype=float)
    o = np.array([y for _, y in pairs], dtype=float)
    return float(np.mean((p - o) ** 2))


def calibration(pairs: Sequence[Tuple[float, int]], *, bins: int = 10, min_pairs: int = 20,
                field: str = "") -> Tuple[Dict[str, QuantField], Optional[List[ReliabilityBin]]]:
    fid = "quant:calibration.brier"
    label = f"Brier score of past {field or 'probability'} forecasts"
    method = "mean squared difference between stated probability and outcome (0 best, 0.25 = coin flip)"
    bad = [x for x, y in pairs if not 0 <= x <= 1 or y not in (0, 1)]
    if bad:
        raise ValueError(f"calibration pairs must have p in [0, 1] and outcome 0/1; bad: {bad[:3]}")
    n = len(pairs)
    if n < min_pairs:
        return ({fid: unavailable(fid, label, "ratio", "pairs", "descriptive", method,
                                  f"only {n} recorded forecast/outcome pairs; at least {min_pairs} needed", n)},
                None)
    return ({fid: ok(fid, label, brier_score(pairs), "ratio", n, "pairs", "descriptive", method)},
            reliability_table(pairs, bins))


def load_pairs(path: str, field: str, as_of: date) -> List[Tuple[float, int]]:
    """Pairs for `field` whose outcome was known by `as_of`. Missing file -> no pairs."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with open(p, newline="") as f:
        for row in csv.DictReader(f):
            if row["field"] == field and date.fromisoformat(row["outcome_date"]) <= as_of:
                out.append((float(row["stated_p"]), int(row["outcome"])))
    return out
