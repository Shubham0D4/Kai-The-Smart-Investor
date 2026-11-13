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
| Delta | Up to 15 articles: title, publisher, date, URL, summary text, `providers[]` | yfinance news, Yahoo Finance RSS, Nasdaq RSS and Google News RSS, fetched in parallel and merged (below) | `news:7` |
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
