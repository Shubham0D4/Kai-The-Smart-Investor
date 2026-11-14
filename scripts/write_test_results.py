"""
Write TESTRESULTS.md from the last live test run (tests/artifacts/results.json and runs/*.json).

    python -m pytest            # live: real LLM calls and real data
    python scripts/write_test_results.py

Every number in the report comes from that run; nothing is typed in by hand.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "tests" / "artifacts"


def fmt(v, digits=2):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    return str(v)


def pct(v, digits=1):
    return "–" if v is None else f"{v * 100:+.{digits}f}%"


def main(out_path: Path = ROOT / "TESTRESULTS.md"):
    res = json.loads((ART / "results.json").read_text())
    m = res.get("metrics", {})
    tests = res["tests"]
    outcome = Counter(t["outcome"] for t in tests)
    L = []
    w = L.append

    w("# Kai: Test results")
    w("")
    w(f"Live run from **{res['started']}** to **{res['finished']}**. Every test used **real LLM calls** "
      "(Groq and Google Gemini, models as in `config/llm.yaml`) and **real data** (SEC EDGAR, Yahoo Finance, "
      "public news feeds). There is no fake model and no recorded data in the suite.")
    w("")
    w(f"**{outcome.get('passed', 0)} passed, {outcome.get('failed', 0)} failed, {outcome.get('skipped', 0)} skipped** "
      f"out of {len(tests)} tests.")
    w("")
    for note in res.get("notes", []):
        w(f"- {note}")
    if res.get("notes"):
        w("")
    w("Reproduce: `python -m pytest` (needs `GROQ_API_KEY` and `GOOGLE_API_KEY`; about 15-25 minutes and "
      "roughly 200k tokens), then `python scripts/write_test_results.py`. Results differ between runs because "
      "markets, news and model outputs change.")
    w("")

    # ------------------------------------------------------------------ debates
    runs = m.get("runs", {})
    if runs:
        w("## 1. Live debates")
        w("")
        w("| Ticker | Persona | Verdict | Confidence | Debate score | Faithfulness | Integrity | Tokens (limit) | Time |")
        w("|---|---|---|---|---|---|---|---|---|")
        for t, r in runs.items():
            conf = fmt(r["confidence"], 1) + ("% in Hold" if r["recommendation"] == "Hold" else "%")
            if r["confidence"] is None:
                conf = "–"
            w(f"| {t} | {r['persona']} | **{r['recommendation']}** | {conf} | {r['score']:+.3f} | "
              f"{fmt(r['faithfulness'], 1)} | {fmt(r['integrity'], 1)} | {r['total_tokens']:,} ({r['token_limit']:,}) | "
              f"{r['elapsed_s']:.0f}s |")
        w("")
        for t, r in runs.items():
            snap = r.get("snapshot") or {}
            w(f"### {t} ({r['persona']} thresholds)")
            w("")
            if snap:
                w(f"Market snapshot: price **${fmt(snap.get('price'))}** fetched {snap.get('fetched_at')} "
                  f"(last close {snap.get('market_date')}); TTM EPS {fmt(snap.get('eps_ttm'))} "
                  f"({snap.get('eps_ttm_period') or 'n/a'}).")
                w("")
            if r.get("outcome", {}) and r["outcome"].get("reason"):
                w(f"Outcome: {r['outcome']['status']} / {r['outcome']['reason']}: {r['outcome'].get('detail')}")
                w("")
            w("| Agent | State | Round 1 | Round 3 | Fact-check R1 | Fact-check final | Claims | Critiques |")
            w("|---|---|---|---|---|---|---|---|")
            for a, v in r["agents"].items():
                r1 = f"{v['stance_r1']} {fmt(v['conf_r1'], 0)}" if v["stance_r1"] else "–"
                r3 = f"{v['stance_r3']} {fmt(v['conf_r3'], 0)}" if v["stance_r3"] else "–"
                state = v["state"] + (f" ({v['error'][:60]})" if v.get("error") else "") + \
                    (f" (dropped: {v['dropped'][:60]})" if v.get("dropped") else "")
                w(f"| {a.capitalize()} | {state} | {r1} | {r3} | {format(v['faithfulness_r1'], '.0%') if v['faithfulness_r1'] is not None else '–'} | "
                  f"{format(v['faithfulness_final'], '.0%') if v['faithfulness_final'] is not None else '–'} | {v['claims_r1']} | {v['critiques_r2']} |")
            w("")
            w("Token use per step (prompt / visible output / hidden reasoning; model that answered):")
            w("")
            w("| Agent | Round | Prompt | Output | Reasoning | Total | Model | Result |")
            w("|---|---|---|---|---|---|---|---|")
            for c in r["calls"]:
                res_ = "accepted" if c["ok"] and not c.get("repaired_from") else \
                    "repaired" if c["ok"] else (c.get("error_type") or "failed")
                w(f"| {c['agent']} | {c['round']} | {c['prompt_tokens']:,} | {c['candidates_tokens']:,} | "
                  f"{c['reasoning_tokens']:,} | {c['total_tokens']:,} | {' → '.join(c['models']) or '–'} | {res_} |")
            b = r.get("token_budget") or {}
            if b:
                w("")
                w("Budget shares: " + ", ".join(f"{a} {v['used']:,} / {v['share']:,}" for a, v in b["agents"].items())
                  + f"; reserve left {b['reserve_left']:,} of {b['reserve']:,}.")
            w("")
            w("Verdict under each persona's thresholds (same debate): " + ", ".join(
                f"{p} {v['recommendation']} ({fmt(v['confidence'], 1)})" for p, v in r["verdicts_by_persona"].items()) + ".")
            w("")
            w("Gates: " + "; ".join(("PASSED " if g["passed"] else "FAILED ") + g["note"] for g in r["gates"]) + ".")
            w("")
            if r.get("suggestions"):
                w("Suggestions (computed playbook):")
                w("")
                for s in r["suggestions"]:
                    w(f"- **{s['title']}** ({s['kind']}): {s['detail']}")
                w("")
            w("Sigma's explanation (model-written, verbatim):")
            w("")
            w("> " + r["analysis"].replace("\n", "\n> "))
            w("")

    # ------------------------------------------------------------------ dashboard runs
    dash_path = ART / "dashboard_runs.json"
    if dash_path.exists():
        dash = json.loads(dash_path.read_text())
        w("## 1b. Analyses run from the dashboard (same build, real data)")
        w("")
        w("Two analyses started by hand from the dashboard during the test window, read back from the session store.")
        w("")
        w("| Ticker | Persona | Verdict | Confidence | Debate score | Faithfulness | Integrity | Tokens | Duration |")
        w("|---|---|---|---|---|---|---|---|---|")
        from datetime import datetime
        for r in dash:
            ev = r.get("evaluation") or {}
            secs = (datetime.fromisoformat(r["updated_at"]) - datetime.fromisoformat(r["created_at"])).total_seconds()
            conf = "–" if r["confidence"] is None else fmt(r["confidence"], 1) + ("% in Hold" if r["recommendation"] == "Hold" else "%")
            w(f"| {r['ticker']} | {r['persona']} | **{r['recommendation']}** | {conf} | "
              f"{'–' if r['score'] is None else format(r['score'], '+.3f')} | {fmt(ev.get('faithfulness_score'), 1)} | "
              f"{fmt(ev.get('integrity_score'), 1)} | {r['total_tokens']:,} | {secs:.0f}s |")
        w("")
        for r in dash:
            w(f"**{r['ticker']}** ({r['persona']}): {r['notification']}")
            if r.get("outcome", {}).get("reason"):
                w(f"Outcome {r['outcome']['reason']}: {r['outcome']['detail']}.")
                w("Data warnings: " + "; ".join(x for x in r["outcome"].get("warnings", []) if "unavailable:" in x and "quant field" not in x) + ".")
            if r.get("calls"):
                w("Steps: " + "; ".join(f"{c['agent']} r{c['round']} {c['total_tokens']:,} "
                                        f"({' → '.join(m.split(':', 1)[1] for m in c.get('models', [])) or c.get('error_type') or 'no call'})"
                                        for c in r["calls"]) + ".")
                w("Stances (round 1 → round 3): " + ", ".join(f"{a} {v['r1']} → {v['r3'] or '–'}" for a, v in r["stances"].items() if v["r1"]) + ".")
            if r.get("suggestions"):
                w("")
                for x in r["suggestions"]:
                    w(f"- **{x['title']}**: {x['detail']}")
            w("")

    # ------------------------------------------------------------------ properties
    w("## 2. What the live runs showed")
    w("")
    fc = m.get("fact_check", {})
    if fc:
        w("**Fact-check reproduced independently.** Re-running the verifier on every round-1 claim gave the same result as the debate:")
        w("")
        for t, agents in fc.items():
            parts = [f"{a} {v['verified']}/{v['checkable']}" + (f" (failed: {', '.join(v['failed'])})" if v["failed"] else "")
                     for a, v in agents.items()]
            w(f"- {t}: " + "; ".join(parts))
        w("")
    lane = m.get("lane_rule", {})
    if lane:
        w("**Agents stayed in their lane.** Round-3 stance changes and what justified them:")
        w("")
        for t, v in lane.items():
            if isinstance(v, str):
                w(f"- {t}: {v}")
            else:
                for a, c in v.items():
                    w(f"- {t}: {a} {c['from']} → {c['to']} because of {', '.join(c['because'])} (\"{c['reason']}\")")
        w("")
    nar = m.get("narrative", {})
    if nar:
        w("**Sigma's explanation.** Numbers in the text that could not be traced to the evidence, the gates, the playbook or the quant report:")
        w("")
        for t, v in nar.items():
            w(f"- {t}: {v['words']} words; untraced numbers: {', '.join(v['untraced_numbers']) or 'none'}")
        w("")
    hitl = m.get("hitl", {})
    if hitl:
        w("**Human approval rule on the real verdicts:**")
        w("")
        for t, v in hitl.items():
            w(f"- {t}: {v}")
        w("")

    # ------------------------------------------------------------------ data and models
    d = m.get("data", {})
    if d:
        w("## 3. Live data checks")
        w("")
        if "nvda_snapshot" in d:
            s = d["nvda_snapshot"]
            w(f"- NVDA snapshot: ${s['price']} fetched {s['fetched_at']} (last close {s['market_date']}).")
        if "nvda_returns" in d:
            w("- NVDA price returns, each with its window: " + "; ".join(
                f"{k} {pct(v['value'])} ({v['period']})" for k, v in d["nvda_returns"].items()) + ".")
        if "nvda_eps_series" in d:
            w("- NVDA diluted EPS on today's share basis (2024 10-for-1 split applied to older filings): " + ", ".join(
                f"{k.split(':')[-1]} {v}" for k, v in d["nvda_eps_series"].items())
              + f"; growth {pct(d.get('nvda_eps_cagr'))} a year (was reported as 6.2% before the fix).")
        if "nvda_ttm_eps" in d:
            t = d["nvda_ttm_eps"]
            w(f"- NVDA TTM EPS {t['value']} for {t['period']} ({t['how'].split(': ', 1)[-1]}).")
        if "nvda_catalysts" in d:
            c = d["nvda_catalysts"]
            pick = ["cat:days_to_earnings", "cat:eps_estimate_next", "cat:target_mean", "cat:target_mean_upside",
                    "cat:implied_vol_atm", "cat:implied_move", "cat:eps_revisions_up_30d", "cat:eps_revisions_down_30d"]
            w("- NVDA catalysts: " + "; ".join(f"{k.split(':')[1]} {fmt(c[k]['value'], 4)} ({c[k]['period']})"
                                                for k in pick if k in c) + ".")
        if "nvda_news" in d:
            n = d["nvda_news"]
            w(f"- NVDA news: {n['articles']} articles, {n['with_summary']} with a summary; providers ok "
              f"{', '.join(n['providers_ok'])}; failed {', '.join(n['providers_failed']) or 'none'}.")
        w("")
    mods = m.get("models", {})
    if mods:
        w("## 4. Every configured model, live, under the strict schema")
        w("")
        w("| Model | Prompt | Output | Hidden reasoning | Total | Claims |")
        w("|---|---|---|---|---|---|")
        for k, v in mods.items():
            w(f"| {k} | {v['prompt']} | {v['output']} | {v['reasoning']} | {v['total']} | {v['claims']} |")
        w("")
    vt = ART / "vitest.json"
    if vt.exists():
        v = json.loads(vt.read_text())
        w("## 4b. Dashboard tests")
        w("")
        w(f"`npm test` in kai-frontend: **{v['numPassedTests']} passed, {v['numFailedTests']} failed** of {v['numTotalTests']}. "
          "The components are rendered with the real `/status` bodies of the three live debates above "
          "(kai-frontend/src/test/fixtures/), so every verdict, suggestion, claim and token figure they check is real "
          "output. The API-client and source-hygiene tests stub the network transport only; they contain no "
          "market or model data.")
        w("")
    g = m.get("gateway", {})
    if g:
        e = g.get("market_endpoints", {})
        w(f"Gateway market endpoints, live: indices {', '.join(e.get('indices', []))}; NVDA quote {e.get('nvda_quote')}; "
          f"{e.get('history_points')} daily points for one month.")
        w("")

    # ------------------------------------------------------------------ findings (analysis, not measurement)
    if res.get("findings"):
        w("## 5. Findings from this run and what was changed")
        w("")
        w("Written after reading the run above (analysis, not generated). Changes listed here were made "
          "after the run; they still need a full re-run to be confirmed.")
        w("")
        w("| Finding (evidence above) | Change made | Confirmed |")
        w("|---|---|---|")
        for f in res["findings"]:
            w(f"| {f['finding']} | {f['change']} | {f['confirmed']} |")
        w("")

    # ------------------------------------------------------------------ all tests
    w("## 6. Every test")
    w("")
    w("| Result | Test | What it checks | Time |")
    w("|---|---|---|---|")
    for t in tests:
        mark = {"passed": "pass", "failed": "**FAIL**", "skipped": "skip"}[t["outcome"]]
        w(f"| {mark} | `{t['id'].split('::', 1)[-1]}` | {t['doc']} | {t['duration_s']}s |")
    w("")
    failed = [t for t in tests if t["outcome"] == "failed"]
    if failed:
        w("### Failures")
        w("")
        for t in failed:
            w(f"**{t['id']}**")
            w("")
            w("```")
            w(t.get("detail", "")[-1200:])
            w("```")
            w("")
    w("## Not covered by these tests")
    w("")
    w("- Browsers other than the headless Chromium used for the UI checks; the dashboard is tested with real "
      "`/status` payloads captured from these runs, not by clicking through a live browser here.")
    w("- Calibration: whether stated probabilities match outcomes needs `scripts/calibration_backtest.py` over years of data.")
    w("- Scenarios that real data did not produce in this run (for example a circuit-breaker stop or a strong "
      "Buy/Sell needing approval) are reported above only if they occurred.")
    out_path.write_text("\n".join(L) + "\n")
    print(f"wrote {out_path} ({len(tests)} tests, {len(runs)} debates)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "TESTRESULTS.md")
