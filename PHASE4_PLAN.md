# Phase 4 plan: deterministic judge and persona thresholds (C4, C5, C6, C7)

Branch `phase-4-judge`, based on `phase-3-debate`. Implements DESIGN.md §4 and §5.
Rule for the whole phase: **every score and the verdict are computed in code from the
transcript; the LLM writes only the narrative.**

## 1. Decisions already taken

| Question | Decision |
|---|---|
| No DCF available | Zen and Balanced cannot Buy; Alpha may Buy on score alone (`buy_requires_dcf: false`) |
| Confidence on Hold | None. Only Buy and Sell carry a confidence |
| Discount rate | CAPM, same for every persona; configured 10% fallback with a warning |
| Human approval | Buy/Sell with confidence ≥ 60; value lives in config |

## 2. Files

| File | Change |
|---|---|
| `config/thresholds.yaml` | new: all tunable numbers |
| `coordinator/config.py` | new: load and validate the YAML |
| `judge/verifier.py` | new: claim verification and faithfulness (C4) |
| `judge/scoring.py` | new: integrity (C5), verdict and confidence (C6) |
| `judge/sigma_agent.py` | replace scoring and synthesis; narrative only from the LLM |
| `coordinator/transcript.py` | serialise evidence, drop agents, show verifier report in prompts |
| `coordinator/server.py` | pre-fetch data, real circuit breaker, `NO_VERDICT` results |
| `coordinator/debate_logger.py` | log verification reports; real cited IDs as grounding sources |
| `coordinator/personas.py`, `agents/*/agent.py` | persona removed from prompts and constructors (C7) |
| `mcp_server/tools/finance_math.py`, `market_data.py` | beta, CAPM, benchmark and risk-free fetch |
| `judge/ap2_handler.py`, `gateway/schema.py`, `gateway/app.py`, `gateway/hitl_manager.py` | new evaluation shape, `NO_VERDICT`, config thresholds |
| `kai-frontend` (`CommitteeContext.jsx`, `SigmaCard.jsx`) | `integrity` instead of `consistency`, warning banner |
| `requirements.txt` | add `PyYAML` |

## 3. Config (C7)

`config/thresholds.yaml`, overridable with env `KAI_THRESHOLDS_PATH`:

```yaml
personas:
  zen:      {buy_score: 0.50, min_margin_of_safety: 0.25, max_volatility: 0.35, sell_score: -0.20, buy_requires_dcf: true}
  balanced: {buy_score: 0.35, min_margin_of_safety: 0.15, max_volatility: 0.50, sell_score: -0.30, buy_requires_dcf: true}
  alpha:    {buy_score: 0.25, min_margin_of_safety: 0.00, max_volatility: null, sell_score: -0.40, buy_requires_dcf: false}
verification: {numeric_tolerance: 0.01, min_checkable_claims: 3, min_quote_chars: 15}
circuit_breaker: {min_agent_faithfulness: 0.60, min_valid_agents: 2, token_limit: 50000}
integrity: {unexplained_stance_penalty: 0.25, retained_failed_claim_penalty: 0.50}
capm: {equity_risk_premium: 0.05, beta_clamp: [0.5, 2.0], rate_clamp: [0.06, 0.15], fallback_rate: 0.10, terminal_growth: 0.025}
hitl: {approval_confidence: 60, low_confidence: 50, approval_timeout_minutes: 5}
```

`coordinator/config.py`
- `class PersonaThresholds(BaseModel)`, `class Thresholds(BaseModel)` mirroring the YAML.
- `load_thresholds(path: Optional[str] = None) -> Thresholds` — reads the file, validates,
  raises `ValueError` naming the bad key. Cached per path.
- `RiskPersona.SIGMA = "sigma"` is renamed `RiskPersona.BALANCED = "balanced"` so persona
  strings match the config and the gateway's `PersonaType`.

## 4. C4 — faithfulness from claims vs fetched data

`judge/verifier.py` (pure functions, no I/O, no LLM)

```
class ClaimCheck(BaseModel):
    claim_id: str; kind: str; checkable: bool; verified: bool
    reason: Optional[str]      # unknown_evidence | value_mismatch | no_value | quote_not_found |
                               # quote_too_short | ungrounded_number | unsupported_inference | cycle
    expected: Optional[float]; stated: Optional[float]

class AgentVerification(BaseModel):
    agent: str; round: int; checks: List[ClaimCheck]
    checkable: int; verified: int; faithfulness: float      # 0..1

verify_numeric(claim: NumericClaim, evidence: Dict[str, EvidenceRecord], tol: float) -> ClaimCheck
verify_quote(claim: QuoteClaim, evidence: Dict[str, EvidenceRecord], min_chars: int) -> ClaimCheck
verify_inference(claim: InferenceClaim, checks: Dict[str, ClaimCheck]) -> ClaimCheck
ungrounded_numbers(text: str, allowed: List[float], tol: float) -> List[float]
verify_output(output: AgentOutput, evidence: List[EvidenceRecord], cfg: VerificationConfig) -> AgentVerification
overall_faithfulness(verifications: List[AgentVerification]) -> float
```

Rules
- **numeric**: every evidence ID must exist and have a value. Expected value `E`:
  `equals` → e0; `growth` → e0/e1 − 1; `ratio` → e0/e1; `diff` → e0 − e1 (division by zero
  fails). Stated value is scaled by unit first: `percent` or `%` ÷ 100, `USD_mn` × 1e6,
  `USD_bn` × 1e9, anything else × 1. Verified when `|stated − E| ≤ tol × max(|E|, 1e-9)`.
- **quote**: after normalising case, whitespace and curly quotes/dashes, the quote must be a
  substring of the cited article's title + summary, and at least `min_quote_chars` long.
- **numbers in the wording**: `ungrounded_numbers` extracts quantities from a claim's `text`
  — numbers carrying `$`, `%`, `x`, or a scale word (k, million, bn, billion, trillion).
  Bare integers, years and tokens like `FY2026` are ignored. Each quantity must match, within
  tolerance, the claim's own value, a cited evidence value, or (for an inference) a value of a
  claim it is `based_on`. A quantity that matches nothing fails the claim with
  `ungrounded_number`.
- **inference**: verified only if every `based_on` claim is verified and its own text has no
  ungrounded number. Inferences are **not** counted as checkable.
- **faithfulness per agent** `f_a = verified_checkable / max(checkable, min_checkable_claims)`.
  The floor stops an agent scoring 100% on one easy claim.
- **overall** `F = Σ verified / Σ max(checkable, min_checkable_claims)` over valid agents.
- Scored on each agent's latest output: round 3 if it exists, otherwise round 1.

Deleted: `SigmaAgent.evaluate_faithfulness(debate_log)` and the source/tool "bonus" points.

## 5. C5 — debate integrity replaces consistency

`judge/scoring.py`

```
class AgentIntegrity(BaseModel):
    agent: str; received: int; answered: int; failed_round1_claims: int; retained_failed: int
    unexplained_stance_change: bool; integrity: float      # 0..1

agent_integrity(agent: str, transcript: Transcript,
                round1_checks: AgentVerification, cfg: IntegrityConfig) -> AgentIntegrity
overall_integrity(items: List[AgentIntegrity]) -> float     # mean
```

Formula, per agent:
- `answered_ratio` = critiques of its claims answered in round-3 `changes` ÷ critiques
  received; 1 if none received; 0 if it received some and has no valid round-3 output.
- `retained_ratio` = round-1 claims that failed verification and are not marked `revised` or
  `withdrawn` in round 3 ÷ round-1 claims that failed; 0 if none failed.
- `unexplained` = 1 if final stance ≠ round-1 stance and there is no reason or no
  `changed_by` entry, else 0.
- `integrity = clamp(answered_ratio − 0.25 × unexplained − 0.50 × retained_ratio, 0, 1)`.
- A one-round debate has integrity 1 (nothing to answer).

A reasoned change of stance costs nothing. Deleted: `SigmaAgent.evaluate_consistency`.

Supporting transcript changes (`coordinator/transcript.py`)
- `set_verification(agent: str, v: AgentVerification) -> None`; round 2 and 3 prompts gain a
  "Verifier report" section listing each round-1 claim as VERIFIED or FAILED with the reason.
- `validate_round3` additionally requires a `changes` entry for every one of the agent's own
  failed round-1 claims.
- `drop(agent: str, reason: str) -> None`; `participants()` excludes dropped agents, so their
  claims can no longer be targeted.
- `to_dict()` adds `evidence`, `verification`, `dropped`; new
  `Transcript.from_dict(data: Dict) -> Transcript` so Sigma can score a saved log.

## 6. C6 — verdict, confidence, risks

`judge/scoring.py`

```
class Gate(BaseModel):
    name: str; value: Optional[float]; threshold: Optional[float]; passed: bool; note: str

class Verdict(BaseModel):
    recommendation: Literal["Buy", "Hold", "Sell", "No Verdict"]
    confidence: Optional[float]            # None for Hold and No Verdict
    score: float                           # S
    agent_scores: Dict[str, float]
    gates: List[Gate]
    agents_valid: List[str]; agents_missing: List[str]; warnings: List[str]
    reason: Optional[str]                  # set for No Verdict

agent_score(output: AgentOutput, faithfulness: float) -> float
decide_verdict(outputs: Dict[str, AgentOutput], faithfulness: Dict[str, float], integrity: float,
               margin_of_safety: Optional[float], volatility: Optional[float],
               persona: PersonaThresholds, cfg: Thresholds, total_agents: int = 3) -> Verdict
collect_key_risks(outputs: Dict[str, AgentOutput], limit: int = 5) -> List[str]
```

Formulas
- `s_a = sign(stance) × confidence/100 × f_a`, with bullish +1, neutral 0, bearish −1.
- Valid agents: have a final output and `f_a ≥ min_agent_faithfulness`.
  `S = mean(s_a)` over valid agents.
- Fewer than `min_valid_agents` valid → `No Verdict`.
- **Buy** when all gates pass: `S ≥ buy_score`; margin of safety ≥ `min_margin_of_safety`;
  volatility ≤ `max_volatility` (skipped when null). If the margin of safety is missing, the
  gate fails when `buy_requires_dcf` is true and is skipped with a warning when false. A
  missing volatility fails its gate unless `max_volatility` is null.
- **Sell** when `S ≤ sell_score`. Otherwise **Hold**. Every gate is recorded with its value,
  threshold and a note such as "Buy blocked: margin of safety −1.40 < 0.25".
- **Confidence** (Buy/Sell only) `= 100 × |S| × integrity × (valid agents ÷ 3)`, rounded to 2.
- Margin of safety and volatility are read from Gamma's evidence records
  `calc:dcf.margin_of_safety` and `calc:volatility_annual`, even if Gamma's own output was
  dropped; the coordinator stores Gamma's evidence as soon as it is computed.
- `collect_key_risks`: the agents' final `risks` fields, de-duplicated case-insensitively,
  each prefixed with the agent name.

`judge/sigma_agent.py`
- `__init__(self, llm_client=None, thresholds: Optional[Thresholds] = None)`
- `score_debate(self, transcript: Transcript) -> Dict` — verifications, `F`, integrity; no LLM.
  Replaces the Phase 3 `score_debate(debate_log)`.
- `evaluate_debate(self, debate_log_path: str, persona: Optional[str] = None) -> Dict` — loads
  the transcript from the log, scores, calls `decide_verdict` for the requested persona and
  for all three (`verdicts_by_persona`), then `write_narrative`. Return shape:
  `evaluation {faithfulness_score, integrity_score, overall_quality}` (0–100, overall = mean),
  `final_recommendation {recommendation, confidence, score, gates, analysis, key_risks,
  agents_valid, agents_missing, warnings}`, `verification`, `verdicts_by_persona`, `metadata`.
- `write_narrative(self, verdict: Verdict, transcript: Transcript) -> str` — the single LLM
  call. The prompt states the computed verdict and asks only for an explanation. If the call
  fails, a fixed template is used and a warning added. The reply is never parsed for a verdict.
- Deleted: `synthesize_recommendation`.

Consumers
- `judge/ap2_handler.py`: `generate_alpha_packet` takes `key_risks`, `quality_scores
  {faithfulness, integrity, overall}` and the verdict detail from the evaluation;
  `_extract_risks` is deleted.
- `gateway/schema.py`: `RecommendationType.NO_VERDICT = "No Verdict"`; `QualityScores.integrity`
  replaces `consistency`; `AlphaPacket.confidence`, `Notification.confidence`,
  `HistoryItem.confidence` become `Optional[float]`; `AlphaPacket` gains `verdict_detail`.
- `gateway/hitl_manager.py`: `__init__(self, cfg: Optional[HitlConfig] = None)` reads the
  config; `evaluate_hitl_requirement(recommendation: str, confidence: Optional[float])` returns
  ACTIVE for Buy/Sell with confidence ≥ `approval_confidence`, PASSIVE otherwise;
  `create_notification` handles Hold (no confidence) and No Verdict (type `warning`, with the
  reason).
- `gateway/app.py` `run_analysis`: passes the session persona to `evaluate_debate`.
- Frontend: show `integrity`, show "—" when confidence is null, and a banner listing
  `agents_missing` and `warnings`.

## 7. C4 — circuit breaker that can trip

`coordinator/server.py`
- `_fetch_evidence(self, delta_days: int) -> Dict[str, Dict]` — fetches SEC financials, market
  data and news once, before any LLM call. `DeltaAgent.analyze` gains `news: Optional[Dict]`.
- `_check_evidence(self, fetched: Dict) -> List[str]` — agents whose data is present (Charlie:
  financials; Gamma: financials and market; Delta: news). Fewer than `min_valid_agents` →
  `NO_VERDICT / INSUFFICIENT_EVIDENCE` with zero tokens spent.
- `_verify_round1(self) -> None` — verifies each round-1 output, stores it on the transcript
  and in the log, and drops agents with `f_a < min_agent_faithfulness` with a warning. Fewer
  than `min_valid_agents` left → `NO_VERDICT / CIRCUIT_BREAKER_FAITHFULNESS`.
- `class TokenLimitExceeded(Exception)`; `run_debate` catches it and returns
  `NO_VERDICT / TOKEN_LIMIT` instead of raising. The limit comes from config.
- Every early stop returns `status: "NO_VERDICT"` with `reason` and `detail`.

Note: DESIGN.md says "after rounds 1 and 2". Round 2 adds no new checkable claims, so the
faithfulness check runs after round 1 and again on final outputs in Sigma.

`coordinator/debate_logger.py`
- `log_verification(self, round_number: int, report: Dict) -> None` — stored under
  `rounds[i]["verification"]`.
- Round-1 `grounding_sources` become the evidence IDs the agent actually cited, replacing the
  strings the coordinator hardcodes today.

## 8. C7 — persona only as thresholds

- `coordinator/personas.py`: `get_charlie_system_instruction()`, `get_delta_system_instruction()`,
  `get_gamma_system_instruction()` take no persona; `get_persona_prompt`, `PERSONA_DEFINITIONS`
  and Gamma's "parameter guidance" are deleted.
- `CharlieAgent`, `DeltaAgent`, `GammaAgent`: `__init__(self, llm_client=None)`; no `persona`
  in results. `DebateCoordinator` keeps `persona` only for the log and for Sigma.
- `mcp_server/tools/finance_math.py`
  - `calculate_beta(stock: List[Tuple[str, float]], benchmark: List[Tuple[str, float]]) -> Optional[float]`
    — covariance ÷ variance of daily returns on matching dates; None under 60 common days.
  - `capm_discount_rate(risk_free: float, beta: float, equity_risk_premium: float,
    beta_clamp: Tuple[float, float], rate_clamp: Tuple[float, float]) -> float`
    — `clamp(rf + clamp(beta) × ERP)`.
- `mcp_server/tools/market_data.py`: `fetch_market_data(ticker, period="2y")` also returns
  `benchmark_history` (`^GSPC`) and `risk_free_rate` (`^TNX` ÷ 100), each optional with a
  warning when missing. Risk metrics keep using the last year.
- `GammaAgent._assumptions` is deleted; `compute` uses CAPM, or `fallback_rate` plus a warning
  when beta or the yield is unavailable. New evidence records: `mkt:risk_free_rate`,
  `calc:beta`, `assumption:discount_rate` (source "CAPM").
- New fixtures recorded once: two years of AAPL and `^GSPC` closes and a `^TNX` quote.

## 9. Test cases

`tests/unit/test_verifier.py` (AAPL fixture: revenue FY2026 478,834,000,000; FY2025 416,161,000,000)

| # | Case | Expected |
|---|---|---|
| V1 | `equals` 478,834,000,000 on `sec:revenue:FY2026` | verified |
| V2 | `growth` 0.15 on FY2026, FY2025 (true 0.1506) | verified, within 1% |
| V3 | **Number not in the data:** `equals` 520,000,000,000 on `sec:revenue:FY2026` | fails `value_mismatch`, expected 478,834,000,000 |
| V4 | `growth` 0.06 on the same pair | fails `value_mismatch` |
| V5 | value 15.06 with unit `percent`; value 478.834 with unit `USD_bn` | both verified |
| V6 | evidence ID that does not exist | fails `unknown_evidence` |
| V7 | **Number only in the wording:** text "revenue of $600bn" with value 478,834,000,000 | fails `ungrounded_number` |
| V8 | inference "implies 40% upside" based on a verified claim with no such value | fails `ungrounded_number` |
| V9 | text mentioning "FY2026" and "5 years" | not flagged |
| V10 | quote that is a real span of the article summary, different case and spacing | verified |
| V11 | quote not in the article; quote of 5 characters | `quote_not_found`; `quote_too_short` |
| V12 | inference based on one verified and one failed claim | fails `unsupported_inference` |
| V13 | 4 checkable claims, 3 verified | `f_a` = 0.75 |
| V14 | 1 checkable claim, verified, plus inferences | `f_a` = 1/3 (floor of 3) |
| V15 | `ratio` with a zero denominator | fails, no exception |

`tests/unit/test_scoring.py`

| # | Case | Expected |
|---|---|---|
| I1 | all critiques answered, stance unchanged | integrity 1.0 |
| I2 | stance bullish → bearish with reason and `changed_by` | integrity 1.0 (old consistency score would drop) |
| I3 | 2 critiques received, no valid round 3 | integrity 0.0 |
| I4 | 2 round-1 claims failed, one withdrawn, one kept | 1 − 0.5 × 0.5 = 0.75 |
| I5 | one-round debate | integrity 1.0 |
| S1 | three bullish, confidence 80, `f` = 1 | S = 0.80 |
| S2 | bullish 90 (`f` 1.0) and bullish 60 (`f` 0.8), third agent missing | S = 0.69; confidence = 100 × 0.69 × 1 × 2/3 = 46.0 |
| S3 | bullish 80 and bearish 80 | S = 0, Hold, confidence None |
| G1 | S 0.80, margin 0.30, volatility 0.25 | Buy for all three personas; confidence 80.0 |
| G2 | S 0.80, margin 0.10, volatility 0.25 | Zen Hold, Balanced Hold, Alpha Buy; blocked gate named |
| G3 | S 0.80, margin 0.30, volatility 0.40 | Zen Hold (volatility), Balanced Buy, Alpha Buy |
| G4 | S 0.80, margin −1.40 (the AAPL fixture) | Hold for all three |
| G5 | S 0.80, no DCF | Zen Hold, Balanced Hold, Alpha Buy with a warning |
| G6 | S −0.25 | Zen Sell, Balanced Hold, Alpha Hold |
| G7 | S 0.40, margin 0.30, volatility 0.25 | Zen Hold, Balanced Buy, Alpha Buy |
| G8 | one valid agent | No Verdict, confidence None |
| G9 | agent at `f` 0.5 with two others at 1.0 | excluded from S, listed in `agents_missing` |
| R1 | duplicate risks differing in case across agents | one entry, agent-prefixed |

`tests/unit/test_config.py`: file loads; a persona missing `buy_score` raises naming the key;
editing a threshold in a temp YAML changes the verdict of G2; `KAI_THRESHOLDS_PATH` is honoured.

`tests/unit/test_capm.py`: benchmark equal to the stock → beta 1.0; stock moving twice the
benchmark → 2.0; rf 0.04, beta 1.2, ERP 0.05 → 0.10; beta 3.0 clamps to 2.0; rate clamps to
[0.06, 0.15]; under 60 common days → None; Gamma with no benchmark uses 0.10 and warns.

`tests/unit/test_agents.py` (persona): system instructions contain none of "Zen", "Alpha",
"conservative", "aggressive"; Gamma's `parameters` and evidence are identical whichever
persona the coordinator runs with; agent constructors take no `persona`.

`tests/integration/test_judge.py` (fixtures + fake LLM)

| # | Case | Expected |
|---|---|---|
| J1 | full 3-round debate, all claims true | faithfulness 100, verdict computed, exactly one Sigma LLM call |
| J2 | **Charlie states revenue of 520bn in round 1** (2 of 3 checkable claims verify) | Charlie `f` = 0.67, claim reported `value_mismatch`, the report appears in the round-2 and round-3 prompts, overall faithfulness < 100 |
| J3 | Charlie's round-1 claims mostly false (`f` < 0.6) | Charlie dropped with a warning, debate continues with two agents, `agents_missing = ["charlie"]` |
| J4 | two agents fall below 0.6 | `NO_VERDICT / CIRCUIT_BREAKER_FAITHFULNESS`, no round-2 prompts sent |
| J5 | SEC and yfinance down | `NO_VERDICT / INSUFFICIENT_EVIDENCE`, zero LLM calls |
| J6 | token limit reached in round 2 | `NO_VERDICT / TOKEN_LIMIT`, no exception |
| J7 | Sigma's narrative says "strong sell" while the computed verdict is Buy | recommendation stays Buy |
| J8 | narrative LLM call raises | verdict unchanged, template narrative, warning |
| J9 | same debate log, three personas | `verdicts_by_persona` differ only through thresholds; scores identical |
| J10 | gateway run with a Hold | notification with no confidence; passive mode |
| J11 | gateway Buy at confidence 65; at 55 | approval required; notification only |
| J12 | alpha packet | `quality_scores.integrity` present, `key_risks` equal the agents' risks, signature verifies |

Existing tests to update: the Phase 3 token-limit test (expects an exception), Sigma tests
using `evaluate_consistency`, the HITL approval test's packet stub, and the fake LLM's round-3
reply (needs at least three checkable claims).

## 10. Order of work

1. Config loader and YAML. 2. Verifier with unit tests. 3. Integrity and verdict with unit
tests. 4. Transcript serialisation, verifier report in prompts, drop. 5. Coordinator
pre-fetch, breaker, `NO_VERDICT`. 6. Sigma rewrite. 7. Persona removal and CAPM with new
fixtures. 8. AP2, gateway schema, HITL, frontend fields. 9. Tick C4–C7 in FIXES.md.

## 11. Risks and open points

- The wording check (V7–V9) is a heuristic. It only looks at numbers with a unit marker, so a
  bare "revenue reached 600" would pass. Tightening it risks false failures.
- Agents under 60% are dropped after round 1 with no chance to repair. DESIGN.md allows this;
  an alternative is one repair attempt before dropping.
- Existing files in `alpha_packets/` use the old schema and the burned key; they will not
  validate against the new `AlphaPacket`.
- B8 (frontend mock numbers) is not part of this phase; only the field rename and the banner.
