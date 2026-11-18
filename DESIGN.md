# Kai design: evidence-grounded debate

Scope: fixes C1–C8 in FIXES.md. Principle: **code fetches and checks, the LLM only argues.**
Everything that can be computed deterministically is, and every number an agent states must
point at a fetched record.

## 1. Data contract

The coordinator builds one `EvidencePack` before round 1, saves it in the debate log, and hands
each agent its slice. Every record has a stable ID, value, unit, period and source URL.

| Agent | Receives | Source | Example ID |
|---|---|---|---|
| Charlie | Last 5 fiscal years + latest quarter: revenue, gross profit, operating income, net income, diluted EPS, operating cash flow, capex, cash, total debt, equity, shares | SEC XBRL `companyfacts` API (same host and User-Agent as today's `sec_filings.py`), each fact tagged with its accession number | `sec:revenue:FY2025` |
| Delta | Up to 15 articles: title, publisher, date, URL, summary text, `providers[]` | Finnhub company news (free key), Yahoo Finance RSS and Google News RSS, fetched in parallel and merged (below); yfinance news only as a last resort | `news:7` |
| Gamma | Price, 2y daily closes for the stock and the S&P 500, 10-year Treasury yield, shares outstanding (yfinance); EPS, net debt and 5y free cash flow = OCF − capex (from Charlie's SEC facts); computed PE, PEG, beta, DCF, annualised volatility, Sharpe (`finance_math.py`) | yfinance + SEC + computed | `mkt:price`, `calc:dcf.value_per_share` |

**News merge.** All providers are called in parallel; any that fail are skipped and named in
`warnings`. Articles are deduplicated by URL, then by normalised title. Where providers
disagree on a field of the same article (date, publisher, summary), the majority value wins;
ties go to a fixed provider order. An article with no summary from any provider is flagged
`headline_only`. No provider needs an API key.

**DCF inputs** are computed the same way for every persona: FCF projected 5 years at historical
FCF CAGR clamped to [−5%, 15%]; discount rate from CAPM = risk-free rate + beta × equity risk
premium, with beta from 2y of daily returns against the S&P 500 clamped to [0.5, 2.0], the rate
clamped to [6%, 15%], and the premium (5%) and terminal growth (2.5%) in config; net debt
subtracted; a ±2% sensitivity grid in the pack. `price`, `eps`, `growth_rate` are removed from
the API request.

- *Rejected: put the 10-K text in Charlie's prompt.* Tens of thousands of tokens, and nothing a
  program can check a claim against.
- *Rejected: scrape full article bodies.* Paywalls and Google redirect URLs make it flaky; a
  summary is enough to verify a quote.
- *Rejected: agents call tools themselves.* Fetch-once-in-code is reproducible, cacheable and
  testable with the Phase 0 fixtures.
- *Rejected: a fixed 10% discount rate.* Simpler, but it values a utility and a chip maker alike.
- *Rejected: one news provider with a fallback.* A single unofficial source failing would
  silence Delta.

## 2. Agent output schema

Every agent turn returns JSON validated by a pydantic model; one retry on invalid output, then
the agent is marked failed.

```json
{
  "agent": "charlie", "round": 1,
  "stance": "bullish | neutral | bearish",
  "confidence": 72,
  "claims": [
    {"id": "C1", "kind": "numeric", "text": "Revenue grew 6% in FY2025",
     "value": 0.06, "unit": "ratio", "op": "growth",
     "evidence": ["sec:revenue:FY2025", "sec:revenue:FY2024"]},
    {"id": "C2", "kind": "quote", "text": "Analysts flag weak China demand",
     "quote": "weak demand in China", "evidence": ["news:7"]},
    {"id": "C3", "kind": "inference", "text": "Margins are durable", "based_on": ["C1"]}
  ],
  "risks": ["..."]
}
```

`numeric` claims carry a value and an `op` (`equals`, `growth`, `ratio`, `diff`) over one or two
evidence IDs. `quote` claims carry a verbatim span. `inference` claims must rest on other claims.

- *Rejected: free text plus regex/keyword parsing* (today's C6). It cannot be verified and breaks
  on phrasing.

## 3. Debate protocol

Calls stay stateless; the coordinator passes full structured state, never truncated.

| Round | Receives | Must produce |
|---|---|---|
| 1 Analysis | Own evidence slice | Output per §2, at least 3 checkable claims |
| 2 Critique | All round-1 outputs, the verifier's pass/fail per claim, full evidence index | `critiques[]`: `{id, target: "charlie.C1", verdict: dispute / qualify / support, argument, evidence[]}`. At least one per other agent; `dispute` needs an evidence ID |
| 3 Revision | Own round-1 output, every critique of its claims, verifier report | Revised output plus `changes[]`: `{claim, action: kept / revised / withdrawn, reason, in_response_to}` for every critique received, and `stance_change {from, to, reason}` |

- *Rejected: multi-turn chat sessions per agent (the literal fix for C3).* Provider-specific,
  non-reproducible, and still would not force engagement with a specific claim.
- *Rejected: today's fixed pairing (Delta critiques Charlie only).* Gamma's numbers go unchallenged.

## 4. Judge (Sigma)

**Faithfulness** = verified checkable claims ÷ checkable claims, per agent and overall, on final
outputs. A claim verifies when its evidence IDs exist and: numeric — recomputing `op` on the
evidence matches `value` within 1%; quote — the span occurs in the cited article; inference —
all `based_on` claims verified.
*Rejected: LLM-as-judge entailment* — non-deterministic and untestable offline.

**Consistency is replaced by debate integrity** = share of received critiques answered in round
3, minus penalties for a stance change with no stated reason and for keeping a claim that failed
verification. Changing position for a stated reason costs nothing.
*Rejected: patching the keyword counter* — it measures vocabulary, not reasoning.

**Verdict** is computed, not generated. Agent score = stance (+1/0/−1) × confidence × that
agent's faithfulness. `S` = equal-weight mean over valid agents. Buy / Hold / Sell comes from
`S`, the DCF margin of safety and volatility against persona thresholds (§5). Each gate names
the data it requires: with no DCF (no usable SEC cash flows) a Buy cannot pass and the best
verdict is Hold. **Confidence** = 100 × |S| × integrity × (valid agents ÷ 3). The LLM runs
once, to write the narrative; `key_risks` are the agents' `risks` fields.

**Degraded runs.** A debate runs with as few as two valid agents. The result carries
`agents_valid`, `agents_missing` and `warnings[]`, and the UI shows them as a banner on the
verdict.
*Rejected: LLM picks the verdict via structured output* — not reproducible, and thresholds
could not be enforced.

**Circuit breaker** checks, in code, with no LLM call: (a) before round 1, required evidence
present — otherwise stop before spending tokens; (b) after rounds 1 and 2, any agent under 60%
faithfulness is dropped with a warning, and fewer than two valid agents ends the debate; (c) token budget
counted across all rounds. A trip returns `NO_VERDICT` with the reason, never a Hold.

## 5. Persona

Persona is a row of thresholds applied to the judge's output only. Agents get persona-free
prompts, so one debate can yield all three verdicts. The rows live in `config/thresholds.yaml`
with the circuit-breaker and CAPM constants, so they can be edited by hand without touching code.

| | Buy if S ≥ | and margin of safety ≥ | and volatility ≤ | Sell if S ≤ |
|---|---|---|---|---|
| Zen | 0.50 | 25% | 35% | −0.20 |
| Balanced | 0.35 | 15% | 50% | −0.30 |
| Alpha | 0.25 | 0% | none | −0.40 |

*Rejected: keep persona wording in agent prompts as "tone".* It leaks into stance and confidence,
which makes the analysis itself persona-dependent.

## 6. C8: drop A2A and ADK, keep MCP as an optional adapter

Drop the A2A and ADK claims; describe MCP honestly as "the data tools are also exposed through
an MCP server". Kai is one process in which the coordinator fetches data deterministically and
calls three prompt wrappers. A2A only earns its keep when agents are separate services that
discover and call each other over a network; here the "handshake" is reading three JSON files
from disk. Routing in-process calls through an stdio MCP server would add a subprocess,
latency and new failure modes while removing the fetch-once guarantee that §1 depends on.
The agent cards can stay as plain documentation of each agent's schema. The debate itself is
unaffected: three agents, three rounds, in one process.

## 7. Where I disagree with FIXES.md, and additions

- **C8** says MCP is "not implemented". `mcp_server/main.py` is a working MCP server; the agents
  just bypass it. It also imports `mcp` while `requirements.txt` pins `fastmcp`.
- **C7** calls the discount rate a "fact". It is an assumption; it should still be
  persona-independent. C7 also misses that persona is injected into every agent's system prompt.
- **C3** frames the problem as missing history. The fix is structured state, not memory.
- **C2**: Delta's headlines-only problem cannot be fixed in the prompt; the RSS feed carries no
  body text, so a new source is needed.
- **C4/C5** should be replaced, not repaired. **B7**'s double synthesis disappears with §4.
- New: `calculate_dcf_value` divides enterprise value by shares without subtracting net debt.
- New: the MCP semantic cache returns results at ≥0.90 embedding similarity, so one ticker's
  data can be served for another. Use an exact-key cache.
- New: volatility is reported per day, not annualised.

## 8. Decisions taken (owner's answers)

1. Threshold values in §5 stand, kept in `config/thresholds.yaml`.
2. Equal agent weights, over valid agents; each verdict gate states the data it requires.
3. Discount rate from CAPM.
4. Unsupported tickers run with fewer agents and a UI warning.
5. yfinance plus free no-key feeds in parallel; majority wins on conflicts.
6. Breaking schema changes are acceptable, including `consistency` → `integrity`.
7. No external requirement for A2A/MCP/ADK; §6 stands and the debate stays.
8. 9 agent calls plus 1 narrative call per debate is acceptable.

## 9. Still open

- With no DCF the best verdict is Hold, even for Alpha. Is that the intended reading of
  "count valid and required"?
- Is "majority wins" meant for conflicting article metadata, as written, or for sentiment?

## 10. Quant layer (phase 4.5)

A deterministic statistics module, `quant/`, built from data already fetched (two years of
daily closes for the stock and the S&P 500, SEC fundamentals, price, CAPM inputs). Pure
functions: numpy, scipy, statsmodels; no network and no LLM. Every random draw takes a seed;
the seed is recorded in the report. No LLM computes any number in it.

### 10.1 Output schema

`build_quant_report(inputs, as_of, cfg, seed) -> QuantReport` (pydantic). Every field is a
`QuantField`:

| Key | Meaning |
|---|---|
| `id` | Evidence ID, e.g. `quant:forward.prob_up_bootstrap` |
| `status` | `ok` or `unavailable` |
| `value`, `unit` | Number and unit (`ratio`, `USD`, `x`); null when unavailable |
| `interval` | `[low, high]` and its level (e.g. 0.90) where one exists |
| `n`, `n_kind` | Sample size and what it counts: `observations`, `simulations` or `cases` |
| `kind` | `descriptive` (summarises the past), `model` (follows from stated assumptions) or `prediction` (a probability about the future) |
| `method` | One line, e.g. "moving-block bootstrap, block 5, 10,000 paths" |
| `reason` | Why it is unavailable; never a default number in its place |

The report also records `as_of`, `seed`, the input windows and the first and last input dates.

| Group | Fields | Kind | Unavailable when |
|---|---|---|---|
| a. returns (last 252 days) | `vol_annual` (sample std × √252), `skew`, `excess_kurtosis`, `max_drawdown`, `var95_hist` (1-day), `var95_normal` (1-day) | descriptive; `var95_normal` model | < 60 returns |
| b. market (2y, common dates) | `beta`, `beta_se`, `correlation`, `alpha_daily` (OLS of stock on S&P 500 daily returns) | descriptive | < 60 common days or no benchmark |
| c. mc_dcf | `fair_value_p10/p50/p90`, `prob_fair_above_price` | model | < 2 FCF years, latest FCF ≤ 0, no shares, no CAPM rate |
| d. forward (horizon 63 days) | `prob_up_bootstrap` with 90% interval; `prob_up_normal` with 90% interval; `return_p5/p50/p95` | prediction | < 120 returns |
| e. base_rate | `share_up` with Wilson 95% interval, `n` | descriptive of past, used as a prior | fewer than 30 similar cases |
| f. calibration | `brier`, `reliability` table (10 bins: n, mean stated, observed share) | descriptive of our past forecasts | fewer than 20 recorded pairs |

Method details
- **Returns:** simple daily returns. VaR is reported as a positive loss fraction. Historical VaR
  = −5th percentile; normal VaR = −(μ − 1.645σ). Max drawdown = min over t of
  P_t / max_{s≤t} P_s − 1.
- **Market:** beta and its classical OLS standard error come from statsmodels.
  This beta replaces the cov/var beta Gamma computes today, so there is one source.
- **MC DCF:** 10,000 draws, each run through the same 5-year DCF as Gamma, with net debt
  subtracted.
  - Growth ~ Normal(historical FCF CAGR clamped to [−5%, 15%], sd of annual FCF growth with a
    2% floor), clipped to [−20%, 30%].
  - Discount rate ~ Normal(CAPM rate, 1%), clipped to [6%, 15%]. It never depends on persona.
  - Terminal growth ~ Uniform(1.5%, 3.0%).
  - Draws with discount − terminal < 2% are rejected; `n` reports the draws kept.
- **Forward, bootstrap:** moving-block bootstrap of the last 252 daily returns (block 5, so
  short runs of dependence are kept), 10,000 paths of 63 days. P(up) = share of paths ending
  above today. The 90% interval comes from an outer bootstrap: 200 resamples of the history,
  2,000 paths each. It reflects the uncertainty from having only one year of data.
- **Forward, normal:** log returns, P(up) = Φ(√N · μ̂ / σ̂). The interval comes from
  μ̂ ± 1.645 σ̂/√n. Both forward methods carry the past year's drift forward, which is the main
  assumption in this group.
- **Base rates:** the function takes any case table `{date, trailing_pe, momentum_126d,
  forward_return}`. A case is "similar" when its momentum is within ±10 points and its PE
  within ±25% of today's. The report builds the table from the stock's own history using
  non-overlapping 63-day windows, so cases are not double-counted. With two years of data this
  gives roughly six cases, so the field will normally say "insufficient data" (see 10.6).
- **Calibration:** reads `data/calibration/pairs.csv` written by
  `scripts/calibration_backtest.py`. Brier = mean((p − outcome)²); 0.25 is a coin flip.

### 10.2 How Gamma cites it

The coordinator builds the report once, after fetching data and before round 1, and stores it
in the debate log under `quant_report`, separate from `transcript`. Gamma's evidence list gains
one record per `ok` field (ID `quant:…`, value, unit, `n` and method in the source line).
Unavailable fields are listed by name and reason only, with no value, so they cannot be cited
as numbers. The volatility and beta Gamma computes today are removed: Gamma cites
`quant:returns.vol_annual` and `quant:market.beta`, and its CAPM rate uses the report's beta.

The verifier resolves every `quant:` ID against the stored report, not against Gamma's copy.
- An ID that is not in the report fails `unknown_evidence`.
- An ID that exists but is unavailable fails `no_value`.
- A stated number that differs fails `value_mismatch`.
- The wording check applies as before.
So a Gamma claim with a number the report does not contain fails, and lowers its faithfulness.

### 10.3 How Sigma receives it

`SigmaAgent.evaluate_debate(log_path, persona, quant_report=None)`. The gateway passes the
report as a `QuantReport`; when it is omitted, Sigma loads `quant_report` from the log and
validates it. Sigma reads it as structured input, never through agent text.

Gates
- **Volatility:** reads `quant:returns.vol_annual` instead of Gamma's figure.
- **Margin of safety:** stays on Gamma's point-estimate DCF.

The report never changes the debate score S or Buy/Hold/Sell; it only lowers confidence and
adds flags. Model outputs rest on strong assumptions, so they temper the agents' verdict rather
than drive it.

### 10.4 Effect on confidence

Phase 4: `confidence = 100 × |S| × integrity × coverage` (Buy/Sell only). Phase 4.5 multiplies
in two factors, each between 0.5 and 1:

    confidence = 100 × |S| × integrity × coverage × Q × C

**Q, agreement with the report.** Signals: `prob_up_bootstrap`, `prob_fair_above_price`, and
`share_up` when available. For each available signal p, with d = +1 for Buy and −1 for Sell:

    a = d × 2 × (p − 0.5)                 # −1 = fully against the verdict, +1 = fully for it
    Q = 1 − 0.5 × mean(max(−a, 0))        # only disagreement counts, agreement never boosts

No signals available → Q = 1, with a warning. If calibration is available and its Brier score
is above 0.25 (worse than a coin flip), the forward signal is left out of Q, with a warning.

**C, agent contradictions.** For each valid agent, a contradiction is:
- bullish with any signal p < 0.45, or
- bearish with any signal p > 0.55.

The 0.05 margin avoids flagging 49% against 51%. Every contradiction is listed in the verdict
as `{agent, stance, field, value}`, for Hold as well.

    C = 1 − 0.5 × (valid agents with at least one contradiction ÷ valid agents)

Example: Buy at S = 0.80 with full integrity and coverage gives 80. If `prob_up_bootstrap` is
0.40 and `prob_fair_above_price` is 0.30, then a = −0.2 and −0.4, so Q = 1 − 0.5 × 0.3 = 0.85.
If all three agents are bullish, all contradict, so C = 0.5. Confidence = 80 × 0.85 × 0.5 = 34,
which falls below the 60 approval line, so HITL sends a low-confidence warning.

All constants live in `config/thresholds.yaml` under `quant`: seed, simulation counts, horizon,
block length, minimum samples, DCF distribution parameters, contradiction margin and penalties.

### 10.5 Alpha packet and look-ahead

- **Alpha packet:** carries the full report under `quant_report`, inside the signed payload.
- **Look-ahead:** `build_quant_report` takes `as_of` and raises if any input row is dated after
  it. The backtest script uses the same check.

### 10.6 Decisions taken (owner's answers) and details settled while building

1. **Ten years of closes are fetched** for base rates. On the AAPL fixtures this still gives only
   7 similar cases out of 37 non-overlapping windows, so the field reports "insufficient data"
   (minimum 30). That is the honest answer for one stock's own history.
2. **Trend removed by default** (`quant.forward.drift_mode: zero`): log returns are de-meaned,
   so the median path is flat. With this setting the normal-model probability is exactly 0.5 by
   construction, and the bootstrap probability differs from 0.5 only through skew, fat tails
   and short-run dependence. `drift_mode: historical` switches both methods to carrying the
   past year's drift forward.
3. **Floors approved:** Q and C each have a floor of 0.5, and the contradiction margin is 0.05.
4. **Split adjustment.** yfinance closes are split-adjusted but SEC EPS is as first reported, so
   each EPS is divided by the stock splits dated after its filing (AAPL FY2019: $11.89 / 4).
   EPS is taken as first reported, dated by its filing date, so trailing P/E uses only what
   was known at each date.
5. **One source per number.** Beta, the CAPM rate and annualised volatility now exist only in
   the report. Gamma's point DCF uses `quant:capm.discount_rate`; Sigma's volatility gate uses
   `quant:returns.vol_annual`.
6. **The backtest scores only price-rise forecasts:** `forward.prob_up_bootstrap`,
   `forward.prob_up_normal` and `base_rate.share_up`. `mc_dcf.prob_fair_above_price` is a
   statement about value under assumptions, not a forecast of the price, so it is not scored
   against price moves.
