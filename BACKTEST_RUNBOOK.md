# Kai backtest runbook (handoff)

State lives in `data/backtest/`: `mechanical_<TICKER>.csv` (Stage A, done) and `ledger.jsonl` (Stage B, one line per finished run).

## Stage A (done, no LLM)
`PYTHONPATH=. python scripts/backtest_mechanical.py --tickers ... --start 2020-06-01 --end 2026-05-31 --step-days 42 --out data/backtest`
`PYTHONPATH=. python scripts/analyze_mechanical.py` prints correlations, MoS buckets and Buy-gate reachability.
Finding: the margin-of-safety gate has ~zero rank correlation with 63d excess return vs SPY; bootstrap P(up) has the only weak signal (rho about +0.08).

## Stage B (daily, resumable)
Plan: 20 tickers x 3 as-of dates (2026-04-15, 2026-05-29, 2026-07-15) = 60 runs, Charlie + Gamma only (news/catalysts are current-only and disabled in historical mode). Verdicts for all personas come from the deterministic judge with no narrative LLM call. Outcome = 63-trading-day forward excess return vs SPY, dividend-adjusted.
1. Keys: use `tools vault write-file --key password --name "<entry>" --return-channel whatsapp` for "Kai: Groq API key", "Kai: Google Gemini API key", "Kai: NVIDIA API key", "Kai: OpenRouter API key" (sandbox-allowed). Files land under /home/sandbox/.vault/kai-*/password. Never print them. Export them as GROQ_API_KEY, GOOGLE_API_KEY, NVIDIA_API_KEY, OPENROUTER_API_KEY inside the command (see wrapper below). Delete .env, *.pem and the vault files when done.
2. Wrapper (/tmp/kai_run.sh, recreate if missing): cd /tmp/kai; export the four env vars from the vault files; `PYTHONPATH=. exec /tmp/kvenv/bin/python scripts/backtest_debate.py "$@"`.
3. Run: `kai_run.sh --max-runs N --only T1 T2 ...`. Finished runs are skipped. A run takes 40-90s and 25-35k tokens (NO_VERDICT runs about 8k). A bash call is capped at about 120s and background jobs are killed when the call returns, so run 3-4 processes in parallel with disjoint `--only` sets, `--max-runs 2` each, inside one call with `timeout 108` and `wait`.
4. Quota: Groq free about 200k tokens/day (about 6-7 runs), Gemini flash-lite limited and often 503, NVIDIA about 40 RPM, OpenRouter free about 50 requests/day. Stop for the day when runs fail with all-models-failed errors (ledger status error/failed) and resume next day. Failed/error lines are retried; done lines (including NO_VERDICT) are not.
5. Read results: `scripts/backtest_debate.py --summary`.
## Analysis to do once most runs are done
Per verdict: mean 63d excess return, hit rate; Spearman of score S and confidence vs excess return; Buy/Sell/Hold shares; effect of making MoS a soft input instead of a hard gate (user direction, Nov 18 2:04pm: the margin gate should be one parameter, not the sole blocker). Fit on older as-of dates, test on the newest. Report bias caveats: survivorship, small effective sample, one regime, 2-agent system, model-knowledge contamination risk for early as-of dates.
## Not done
Nothing is pushed to GitHub. Branch vs main for the reliability changes is the user's pending decision.
