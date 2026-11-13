"""
Calibration backtest for the quant layer's probabilities (LIVE: needs network; not run in CI).

For each ticker and each past as-of date, build the quant report using only data available on
that date, record the stated probabilities, then compare with what happened `horizon` trading
days later. Writes:
  <out>/pairs.csv              ticker, as_of, outcome_date, field, stated_p, outcome
  <out>/calibration_report.json Brier score and reliability table per field

Only probabilities that forecast the same event are scored: "price higher after the horizon".
  forward.prob_up_bootstrap, forward.prob_up_normal, base_rate.share_up
mc_dcf.prob_fair_above_price is NOT scored: it is a model statement about value, not a forecast
of the price rising, so comparing it with price moves would mislabel it.

Look-ahead guard: every input row passed to the report is asserted to be dated on or before the
as-of date (and build_quant_report checks again). Prices are converted to the as-of share basis
(yfinance's closes are adjusted for later splits) before slicing; fundamentals are filtered by
SEC filing date.

Usage:
  python scripts/calibration_backtest.py --tickers AAPL MSFT --start 2018-01-01 --end 2025-06-30
"""

import argparse
import csv
import json
import os
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coordinator.config import Thresholds, load_thresholds
from mcp_server.tools.sec_financials import COMPANY_FACTS_URL, _get_json, lookup_cik, parse_company_facts
from quant import build_quant_report, inputs_from_fetched
from quant.calibration import calibration

SCORED_FIELDS = ("quant:forward.prob_up_bootstrap", "quant:forward.prob_up_normal", "quant:base_rate.share_up")
BENCHMARK_DAYS = 504          # same 2-year window the live debate uses for beta


class LookAhead(AssertionError):
    pass


def as_of_basis(closes: Sequence[Tuple[date, float]], splits: Sequence[Tuple[date, float]],
                as_of: date) -> List[Tuple[date, float]]:
    """Closes up to as_of, on the share basis of that date: undo yfinance's adjustment for
    splits that happened after as_of."""
    factor = 1.0
    for d, ratio in splits:
        if d > as_of and ratio > 0:
            factor *= ratio
    return [(d, c * factor) for d, c in closes if d <= as_of]


def assert_no_lookahead(as_of: date, **series) -> None:
    for name, rows in series.items():
        late = [r for r in rows if r[0] > as_of]
        if late:
            raise LookAhead(f"{name}: {len(late)} row(s) after as_of {as_of}, first {late[0][0]}")


def backtest_ticker(ticker: str, closes: Sequence[Tuple[date, float]], splits: Sequence[Tuple[date, float]],
                    benchmark: Sequence[Tuple[date, float]], risk_free: Sequence[Tuple[date, float]],
                    company_facts: Dict, cik: str, as_of_dates: Sequence[date], cfg: Thresholds) -> List[Dict]:
    """Pure core: data already fetched (full history), returns forecast/outcome rows."""
    horizon = cfg.quant.forward.horizon_days
    dates = [d for d, _ in closes]
    rows = []
    for as_of in as_of_dates:
        i = bisect_right(dates, as_of) - 1            # last trading day on or before as_of
        if i < 0 or i + horizon >= len(closes):
            continue                                  # outcome not yet known
        stock = as_of_basis(closes, splits, as_of)
        bench = [r for r in benchmark if r[0] <= as_of][-(BENCHMARK_DAYS + 1):]
        known_splits = [s for s in splits if s[0] <= as_of]
        rf_rows = [r for r in risk_free if r[0] <= as_of]
        fin = parse_company_facts(company_facts, cik, as_of=as_of.isoformat())
        fin = {"success": True, **fin}
        eps_rows = [(date.fromisoformat(e["filed"]), e["value"]) for e in fin["eps_history"]]
        filed_rows = [(date.fromisoformat(r["filed"]), r["value"]) for r in fin["records"] if r.get("filed")]
        assert_no_lookahead(as_of, closes=stock, benchmark=bench, splits=known_splits, risk_free=rf_rows,
                            eps=eps_rows, fundamentals=filed_rows)

        market = {
            "success": True,
            "history_long": [{"date": d.isoformat(), "close": c} for d, c in stock],
            "benchmark_history": [{"date": d.isoformat(), "close": c} for d, c in bench],
            "price": stock[-1][1],
            "risk_free_rate": rf_rows[-1][1] / 100 if rf_rows else None,
            "splits": [{"date": d.isoformat(), "ratio": r} for d, r in known_splits],
        }
        report = build_quant_report(inputs_from_fetched(fin, market), as_of, cfg.quant, cfg.capm,
                                    calibration_pairs=[])    # never score with future outcomes

        outcome_date, start, end = closes[i + horizon][0], closes[i][1], closes[i + horizon][1]
        outcome = int(end > start)                    # both on yfinance's adjusted basis
        for field in SCORED_FIELDS:
            p = report.value(field)
            if p is not None:
                rows.append({"ticker": ticker, "as_of": as_of.isoformat(), "outcome_date": outcome_date.isoformat(),
                             "field": field.removeprefix("quant:"), "stated_p": round(p, 6), "outcome": outcome})
    return rows


def summarise(rows: List[Dict], bins: int = 10) -> Dict:
    out = {}
    for field in sorted({r["field"] for r in rows}):
        pairs = [(r["stated_p"], r["outcome"]) for r in rows if r["field"] == field]
        fields, table = calibration(pairs, bins=bins, min_pairs=1, field=field)
        brier = fields["quant:calibration.brier"]
        out[field] = {"n": len(pairs), "brier": brier.value,
                      "base_rate_up": sum(o for _, o in pairs) / len(pairs),
                      "reliability": [b.model_dump() for b in table or []]}
    return out


# ---------------------------------------------------------------------- live fetching

def _yf_closes(symbol: str, period: str = "max"):
    import yfinance as yf
    h = yf.Ticker(symbol).history(period=period, interval="1d")
    return [(i.date(), float(r["Close"])) for i, r in h.iterrows() if r["Close"] == r["Close"]]


def fetch_live(ticker: str):
    import yfinance as yf
    tk = yf.Ticker(ticker)
    closes = _yf_closes(ticker)
    splits = [(i.date(), float(v)) for i, v in tk.splits.items()]
    benchmark = _yf_closes("^GSPC")
    risk_free = _yf_closes("^TNX")
    cik = lookup_cik(ticker)
    facts = _get_json(COMPANY_FACTS_URL.format(cik=cik))
    return closes, splits, benchmark, risk_free, facts, cik


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--step-days", type=int, default=63, help="trading days between as-of dates")
    ap.add_argument("--out", default="data/calibration")
    args = ap.parse_args()

    cfg = load_thresholds()
    rows: List[Dict] = []
    for ticker in args.tickers:
        print(f"[backtest] fetching {ticker}...")
        closes, splits, bench, rf, facts, cik = fetch_live(ticker)
        trading = [d for d, _ in closes if args.start <= d <= args.end]
        as_of_dates = trading[::args.step_days]
        got = backtest_ticker(ticker, closes, splits, bench, rf, facts, cik, as_of_dates, cfg)
        print(f"[backtest] {ticker}: {len(as_of_dates)} as-of dates, {len(got)} forecast/outcome pairs")
        rows.extend(got)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "pairs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "as_of", "outcome_date", "field", "stated_p", "outcome"])
        w.writeheader()
        w.writerows(rows)
    summary = summarise(rows, cfg.quant.calibration.bins)
    (out / "calibration_report.json").write_text(json.dumps(summary, indent=2))
    for field, s in summary.items():
        print(f"{field}: n={s['n']}  Brier={s['brier']:.4f}  observed up-rate={s['base_rate_up']:.2f}")
    print(f"[backtest] wrote {out / 'pairs.csv'} and {out / 'calibration_report.json'}")


if __name__ == "__main__":
    main()
