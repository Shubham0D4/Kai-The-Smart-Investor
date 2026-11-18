"""
Mechanical backtest of Kai's valuation and quant signals (NO LLM calls; public data only).

For each ticker and past as-of date it rebuilds, from data that existed on that date, the
numbers behind Kai's Buy gate: Gamma's point DCF and margin of safety (MoS), the Monte Carlo
DCF, volatility, momentum, and the forward-probability fields. It records what the stock did
over the next `horizon` trading days: total return and excess return over the S&P 500.
Writes <out>/mechanical.csv. Analyse with scripts/analyze_mechanical.py.

It does NOT test the agents' stances (they need LLMs), so the score gate S is not covered.

Point-in-time rules (shared with scripts/calibration_backtest.py):
  * fundamentals: SEC facts filed on or before the as-of date
  * prices: closes up to as-of on that date's share basis (later splits undone). These are NOT
    dividend-adjusted, so the DCF is compared with the price a buyer saw
  * forward returns: dividend-adjusted closes (total return) for stock and S&P 500
  * risk-free: ^TNX; every input row is asserted dated <= as-of

Usage:
  python scripts/backtest_mechanical.py --tickers AAPL MSFT --start 2021-01-01 --end 2026-05-31
"""

import argparse
import csv
import os
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.gamma_valuation.agent import GammaAgent
from coordinator.config import load_thresholds
from mcp_server.tools.market_data import return_records
from mcp_server.tools.sec_financials import COMPANY_FACTS_URL, _get_json, lookup_cik, parse_company_facts
from quant import build_quant_report, inputs_from_fetched
from scripts.calibration_backtest import BENCHMARK_DAYS, as_of_basis, assert_no_lookahead

COLUMNS = ["ticker", "as_of", "price", "dcf_value", "mos", "mc_p50", "mc_mos", "p_fair_above_price", "vol_annual",
           "beta", "pe", "momentum_126d", "ret_1y", "p_up_bootstrap", "fwd_return", "bench_return", "excess_return",
           "outcome_date", "note"]


def fetch_history(symbol: str):
    """(raw closes: split-adjusted only, dividend-adjusted closes, splits)."""
    import yfinance as yf
    tk = yf.Ticker(symbol)
    h = tk.history(period="max", interval="1d", auto_adjust=False)
    raw = [(i.date(), float(r["Close"])) for i, r in h.iterrows() if r["Close"] == r["Close"]]
    adj = [(i.date(), float(r["Adj Close"])) for i, r in h.iterrows() if r["Adj Close"] == r["Adj Close"]]
    splits = [(i.date(), float(v)) for i, v in tk.splits.items()]
    return raw, adj, splits


class _NoLLM:
    system_instruction = ""

    def generate_content(self, *a, **k):
        raise RuntimeError("backtest_mechanical makes no LLM calls")


def rows_for_ticker(ticker, raw, adj, splits, bench_raw, bench_adj, risk_free, facts, cik,
                    as_of_dates: Sequence[date], cfg, horizon: int) -> List[Dict]:
    gamma = GammaAgent(llm_client=_NoLLM(), thresholds=cfg)
    dates = [d for d, _ in raw]
    adj_by_date = dict(adj)
    bench_by_date = dict(bench_adj)
    bench_dates = [d for d, _ in bench_adj]
    out = []
    for as_of in as_of_dates:
        i = bisect_right(dates, as_of) - 1
        if i < 252 or i + horizon >= len(raw):
            continue
        row = {c: None for c in COLUMNS}
        row.update(ticker=ticker, as_of=as_of.isoformat())
        try:
            stock = as_of_basis(raw, splits, as_of)
            bench = [r for r in bench_raw if r[0] <= as_of][-(BENCHMARK_DAYS + 1):]
            known_splits = [s for s in splits if s[0] <= as_of]
            rf_rows = [r for r in risk_free if r[0] <= as_of]
            fin = {"success": True, **parse_company_facts(facts, cik, as_of=as_of.isoformat())}
            eps_rows = [(date.fromisoformat(e["filed"]), e["value"]) for e in fin["eps_history"]]
            filed_rows = [(date.fromisoformat(r["filed"]), r["value"]) for r in fin["records"] if r.get("filed")]
            assert_no_lookahead(as_of, closes=stock, benchmark=bench, splits=known_splits, risk_free=rf_rows,
                                eps=eps_rows, fundamentals=filed_rows)
            history = [{"date": d.isoformat(), "close": c} for d, c in stock]
            market = {
                "success": True, "history_long": history, "history": history[-504:],
                "benchmark_history": [{"date": d.isoformat(), "close": c} for d, c in bench],
                "price": stock[-1][1], "shares_outstanding": None,
                "risk_free_rate": rf_rows[-1][1] / 100 if rf_rows else None,
                "splits": [{"date": d.isoformat(), "ratio": r} for d, r in known_splits],
                "records": return_records(history[-504:], ticker), "warnings": [],
            }
            report = build_quant_report(inputs_from_fetched(fin, market), as_of, cfg.quant, cfg.capm,
                                        calibration_pairs=[])
            comp = gamma.compute(fin, market, report)
            val = comp["valuation_metrics"]
            dcf = val.get("dcf_analysis") or {}
            price = market["price"]
            p50 = report.value("quant:mc_dcf.fair_value_p50")
            row.update(
                price=round(price, 4), dcf_value=dcf.get("value_per_share"), mos=val.get("margin_of_safety"),
                mc_p50=p50, mc_mos=((p50 - price) / p50 if p50 and p50 > 0 else None),
                p_fair_above_price=report.value("quant:mc_dcf.prob_fair_above_price"),
                vol_annual=report.value("quant:returns.vol_annual"), beta=report.value("quant:market.beta"),
                pe=val.get("pe_ratio"),
                momentum_126d=(stock[-1][1] / stock[-127][1] - 1) if len(stock) > 127 else None,
                ret_1y=(stock[-1][1] / stock[-253][1] - 1) if len(stock) > 253 else None,
                p_up_bootstrap=report.value("quant:forward.prob_up_bootstrap"))
            if not dcf:
                row["note"] = "no DCF: " + "; ".join(w for w in comp.get("warnings", []) if "DCF" in w or "FCF" in w)[:150] \
                    if isinstance(comp.get("warnings"), list) else "no DCF"
        except Exception as e:           # data gaps are results too: keep the row and the reason
            row["note"] = f"{type(e).__name__}: {str(e)[:120]}"
        out_date = raw[i + horizon][0]
        a0, a1 = adj_by_date.get(dates[i]), adj_by_date.get(out_date)
        j0 = bisect_right(bench_dates, dates[i]) - 1
        j1 = bisect_right(bench_dates, out_date) - 1
        if a0 and a1 and j0 >= 0 and j1 > j0:
            f = a1 / a0 - 1
            b = bench_by_date[bench_dates[j1]] / bench_by_date[bench_dates[j0]] - 1
            row.update(fwd_return=round(f, 6), bench_return=round(b, 6), excess_return=round(f - b, 6),
                       outcome_date=out_date.isoformat())
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--step-days", type=int, default=21, help="trading days between as-of dates")
    ap.add_argument("--out", default="data/backtest")
    args = ap.parse_args()

    cfg = load_thresholds()
    horizon = cfg.quant.forward.horizon_days
    bench_raw, bench_adj, _ = fetch_history("^GSPC")
    tnx, _, _ = fetch_history("^TNX")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for ticker in args.tickers:                      # one file per ticker: resumable, parallel-safe
        path = out / f"mechanical_{ticker}.csv"
        if path.exists():
            print(f"[backtest] {ticker}: already done, skipping", flush=True)
            continue
        try:
            raw, adj, splits = fetch_history(ticker)
            cik = lookup_cik(ticker)
            facts = _get_json(COMPANY_FACTS_URL.format(cik=cik))
        except Exception as e:
            print(f"[backtest] {ticker}: skipped ({type(e).__name__}: {str(e)[:100]})", flush=True)
            continue
        trading = [d for d, _ in raw if args.start <= d <= args.end]
        rows = rows_for_ticker(ticker, raw, adj, splits, bench_raw, bench_adj, tnx, facts, cik,
                               trading[::args.step_days], cfg, horizon)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print(f"[backtest] {ticker}: {len(rows)} as-of dates, {sum(1 for r in rows if r['mos'] is not None)} with a DCF",
              flush=True)


if __name__ == "__main__":
    main()
