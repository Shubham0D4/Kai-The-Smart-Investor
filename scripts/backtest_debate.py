"""
Stage B backtest: real Kai debates on past as-of dates (Charlie + Gamma only), resumable.

Each (ticker, as-of) is one debate run with only data that existed on the as-of date: SEC facts
filed on or before it, prices up to it (as-of share basis), ^TNX at that date. News and
catalysts are current-only, so they are switched off (their agents drop out, coverage 0.6).
The verdict for all three personas is computed by the deterministic judge (no narrative LLM
call), then compared with the 63-trading-day forward excess return over the S&P 500.

Ledger: data/backtest/ledger.jsonl, one JSON line per finished run. Re-running skips finished
(ticker, as_of, status=done) pairs, so daily batches just continue. Quota spend is logged per line
(tokens, models). Never print keys. Needs keys in the environment (see BACKTEST_RUNBOOK.md).

Usage:
  python scripts/backtest_debate.py --max-runs 8 [--token-budget 150000] [--dry-run]
  python scripts/backtest_debate.py --summary          # results so far, no network
"""

import argparse
import json
import os
import sys
import time
from bisect import bisect_right
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.charlie_fundamental.agent as charlie_mod
from coordinator.config import load_thresholds
from coordinator.server import DebateCoordinator
from judge.sigma_agent import SigmaAgent
from coordinator.transcript import Transcript
from mcp_server.tools.market_data import return_records
from mcp_server.tools.sec_financials import COMPANY_FACTS_URL, _get_json, lookup_cik, parse_company_facts
from scripts.backtest_mechanical import fetch_history
from scripts.calibration_backtest import BENCHMARK_DAYS, as_of_basis, assert_no_lookahead

LEDGER = Path("data/backtest/ledger.jsonl")
TICKERS = ["AAPL", "MSFT", "ADBE", "CRM", "KO", "PEP", "PG", "WMT", "JNJ", "PFE", "UNH", "MRK", "CVX", "CAT",
           "HON", "UPS", "HD", "MCD", "CSCO", "T"]
AS_OF_DATES = [date(2026, 4, 15), date(2026, 5, 29), date(2026, 7, 15)]
PERSONAS = ("zen", "balanced", "alpha")
_CACHE: Dict[str, tuple] = {}


class _NoLLM:
    system_instruction = ""

    def estimate_tokens(self, t):
        return len(t) // 3

    def generate_content(self, *a, **k):
        raise RuntimeError("no LLM (judge narrative disabled for backtest)")


def _stub_filing_link(*a, **k):                    # the filing link is a live-only reference: skip
    return {"success": False, "error": "historical mode: no live filing lookup"}


charlie_mod.fetch_filing_data = _stub_filing_link


def load_ticker(ticker: str):
    if ticker not in _CACHE:
        raw, adj, splits = fetch_history(ticker)
        cik = lookup_cik(ticker)
        _CACHE[ticker] = (raw, adj, splits, _get_json(COMPANY_FACTS_URL.format(cik=cik)), cik)
    return _CACHE[ticker]


def historical_market_and_financials(ticker: str, as_of: date):
    raw, adj, splits, facts, cik = load_ticker(ticker)
    if "_bench" not in _CACHE:
        _CACHE["_bench"] = (fetch_history("^GSPC"), fetch_history("^TNX"))
    (bench_raw, bench_adj, _), (tnx, _, _) = _CACHE["_bench"]
    stock = as_of_basis(raw, splits, as_of)
    if len(stock) < 300:
        raise ValueError("not enough price history")
    bench = [r for r in bench_raw if r[0] <= as_of][-(BENCHMARK_DAYS + 1):]
    known_splits = [s for s in splits if s[0] <= as_of]
    rf_rows = [r for r in tnx if r[0] <= as_of]
    fin = {"success": True, **parse_company_facts(facts, cik, as_of=as_of.isoformat(),
                                                   splits=[{"date": d.isoformat(), "ratio": r} for d, r in known_splits])}
    assert_no_lookahead(as_of, closes=stock, benchmark=bench, splits=known_splits, risk_free=rf_rows,
                        eps=[(date.fromisoformat(e["filed"]), 0) for e in fin["eps_history"]],
                        fundamentals=[(date.fromisoformat(r["filed"]), 0) for r in fin["records"] if r.get("filed")])
    hist = [{"date": d.isoformat(), "close": c} for d, c in stock]
    h2 = hist[-504:]
    rf = rf_rows[-1][1] / 100 if rf_rows else None
    price = stock[-1][1]
    records = [{"id": "mkt:price", "label": "Last price", "value": round(price, 4), "unit": "USD",
                "period": hist[-1]["date"], "source": "historical close on the as-of date",
                "url": f"https://finance.yahoo.com/quote/{ticker}"},
               {"id": "mkt:price_1y_ago", "label": "Close one year ago (start of 1y window)",
                "value": round(h2[-252]["close"], 4), "unit": "USD", "period": h2[-252]["date"],
                "source": "yfinance history", "url": f"https://finance.yahoo.com/quote/{ticker}/history"}]
    if rf is not None:
        records.append({"id": "mkt:risk_free_rate", "label": "10-year Treasury yield", "value": round(rf, 6),
                        "unit": "ratio", "period": hist[-1]["date"], "source": "yfinance ^TNX / 100",
                        "url": "https://finance.yahoo.com/quote/%5ETNX"})
    records += return_records(h2, ticker)
    market = {"success": True, "ticker": ticker, "fetched_at": f"{as_of.isoformat()}T00:00:00Z",
              "market_date": hist[-1]["date"], "price": price, "previous_close": stock[-2][1],
              "shares_outstanding": None, "history": h2, "history_1y": h2[-252:], "history_long": hist,
              "splits": [{"date": d.isoformat(), "ratio": r} for d, r in known_splits],
              "benchmark_history": [{"date": d.isoformat(), "close": c} for d, c in bench],
              "risk_free_rate": rf, "records": records, "warnings": []}
    return fin, market


class HistoricalCoordinator(DebateCoordinator):
    def __init__(self, *a, as_of: date, **k):
        self.as_of = as_of
        super().__init__(*a, **k)

    def _fetch_evidence(self, delta_days):
        fin, market = historical_market_and_financials(self.ticker, self.as_of)
        self.logger.log_system_message(f"HISTORICAL MODE as of {self.as_of}: news and catalysts disabled")
        self.logger.debate_log["market_snapshot"] = {"market_date": market["market_date"], "price": market["price"],
                                                     "historical_as_of": self.as_of.isoformat()}
        off = {"success": False, "error": f"historical mode (as of {self.as_of}): current-only source disabled"}
        return {"financials": fin, "market": market, "news": dict(off), "catalysts": dict(off)}

    def _build_quant_report(self, fetched, as_of=None):
        return super()._build_quant_report(fetched, as_of=self.as_of)


def forward_outcome(ticker: str, as_of: date, horizon: int = 63) -> Optional[Dict]:
    raw, adj, _, _, _ = load_ticker(ticker)
    dates = [d for d, _ in raw]
    i = bisect_right(dates, as_of) - 1
    if i < 0 or i + horizon >= len(raw):
        return None
    adj_by, (b_raw, b_adj, _) = dict(adj), _CACHE["_bench"][0]
    bd = [d for d, _ in b_adj]
    bm = dict(b_adj)
    out_d = raw[i + horizon][0]
    j0, j1 = bisect_right(bd, dates[i]) - 1, bisect_right(bd, out_d) - 1
    f = adj_by[out_d] / adj_by[dates[i]] - 1
    b = bm[bd[j1]] / bm[bd[j0]] - 1
    return {"outcome_date": out_d.isoformat(), "fwd_return": round(f, 6), "bench_return": round(b, 6),
            "excess_return": round(f - b, 6)}


def done_keys() -> set:
    if not LEDGER.exists():
        return set()
    return {(r["ticker"], r["as_of"]) for r in map(json.loads, LEDGER.read_text().splitlines()) if r.get("status") == "done"}


def run_one(ticker: str, as_of: date, rounds: int, dry: bool) -> Dict:
    cfg = load_thresholds()
    llm = _NoLLM() if dry else None      # dry run: no LLM, debate ends at the first model call
    t0 = time.time()
    coord = HistoricalCoordinator(ticker, "balanced", llm_client=llm, thresholds=cfg, as_of=as_of)
    res = coord.run_debate(rounds=rounds)
    rec = {"ticker": ticker, "as_of": as_of.isoformat(), "ran_at": datetime.now().isoformat(timespec="seconds"),
           "debate_status": res["status"], "reason": res.get("reason"), "rounds_completed": res["rounds_completed"],
           "tokens": res["total_tokens_used"], "log_file": res["log_file"], "seconds": round(time.time() - t0)}
    if res["status"] == "COMPLETED":
        ev = SigmaAgent(llm_client=_NoLLM(), thresholds=cfg).evaluate_debate(res["log_file"], persona="balanced")
        fr = ev["final_recommendation"]
        rec.update(verdict=fr["recommendation"], confidence=fr["confidence"], score=fr["score"],
                   hold_conviction=fr.get("hold_conviction"), coverage=fr.get("coverage"),
                   agents_valid=fr["agents_valid"], faithfulness=ev["evaluation"]["faithfulness_score"],
                   gates=[{"passed": g["passed"], "note": g["note"]} for g in fr["gates"]],
                   verdicts_by_persona={p: v["recommendation"] for p, v in ev["verdicts_by_persona"].items()},
                   mos=SigmaAgent.gate_inputs(Transcript.from_dict(
                       json.load(open(res["log_file"])).get("transcript") or {}, ticker))[0])
    rec.update(forward_outcome(ticker, as_of) or {})
    return rec


def summary() -> None:
    rows = [json.loads(l) for l in LEDGER.read_text().splitlines()] if LEDGER.exists() else []
    done = [r for r in rows if r.get("status") == "done"]
    print(f"{len(done)} done of {len(TICKERS) * len(AS_OF_DATES)} planned; tokens spent {sum(r.get('tokens', 0) for r in done):,}")
    for r in done:
        print(f"{r['ticker']:5s} {r['as_of']} {r.get('verdict','-'):8s} conf={r.get('confidence')} S={r.get('score')} "
              f"excess63={r.get('excess_return')} personas={r.get('verdicts_by_persona')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-runs", type=int, default=4)
    ap.add_argument("--token-budget", type=int, default=120000, help="stop starting new runs once this many tokens were spent in this invocation")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--only", nargs="*", help="restrict to these tickers")
    args = ap.parse_args()
    if args.summary:
        return summary()
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    finished = done_keys()
    plan = [(t, d) for d in AS_OF_DATES for t in TICKERS if (not args.only or t in args.only)]
    spent = runs = 0
    for ticker, as_of in plan:
        if (ticker, as_of.isoformat()) in finished:
            continue
        if runs >= args.max_runs or spent >= args.token_budget:
            break
        print(f"\n=== {ticker} as of {as_of} (run {runs + 1}, tokens so far {spent:,}) ===", flush=True)
        try:
            rec = run_one(ticker, as_of, args.rounds, args.dry_run)
            rec["status"] = "done" if rec["debate_status"] in ("COMPLETED", "NO_VERDICT") else "failed"   # NO_VERDICT is a real live outcome, kept
        except Exception as e:
            rec = {"ticker": ticker, "as_of": as_of.isoformat(), "status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}",
                   "ran_at": datetime.now().isoformat(timespec="seconds"), "tokens": 0}
        with open(LEDGER, "a") as f:
            f.write(json.dumps(rec) + "\n")
        runs += 1
        spent += rec.get("tokens", 0)
        print(f"[ledger] {ticker} {as_of}: {rec['status']} {rec.get('verdict', rec.get('error', rec.get('reason')))} tokens={rec.get('tokens')}", flush=True)
    print(f"\n[backtest_debate] {runs} runs, {spent:,} tokens this invocation")


if __name__ == "__main__":
    main()
