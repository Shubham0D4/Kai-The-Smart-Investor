# Kai fix list

## Conceptual
- [x] C1 Charlie never reads the filing: prompt has only form type, date, URL.
- [x] C2 Delta sees only headlines. Gamma depends on user-typed price/EPS/growth;
      DCF and risk metrics never run (coordinator passes no cash flows or price history).
- [x] C3 Rounds 2 and 3 are fresh LLM calls with no history. Round 2 gets a
      300-char truncation; round 3 gets no debate content at all.
- [x] C4 Faithfulness only checks that grounding_sources is non-empty, and the
      coordinator hardcodes those strings. Circuit breaker cannot trip.
- [x] C5 Consistency is keyword counting and penalises changing position.
- [x] C6 Verdict is a substring match on "buy"/"sell"; confidence is the mean of
      C4 and C5; key_risks parsing is broken.
- [x] C7 Persona changes the discount rate (facts) instead of decision thresholds.
- [ ] C8 A2A / MCP / ADK are claimed but not implemented; agents import tools directly.

## Bugs and security
- [x] B1 ap2_private_key.pem committed to a public repo.
- [x] B2 Hardcoded default JWT secret in gateway/auth.py.
- [ ] B3 /auth/register accepts role from the client (anyone can be admin).
- [ ] B4 user_id comes from request body, not the token; /chat and /status have no auth.
- [ ] B5 run_analysis is async but runs blocking code; server freezes during a debate.
- [ ] B6 Sessions and history are in-memory dicts.
- [x] B7 Token limit counts round 1 only; Sigma's LLM synthesis runs twice.
- [ ] B8 Frontend shows Math.random() signals, prices, trace metrics; CitationPopover is mock.
- [ ] B9 Gateway returns hardcoded index values when yfinance fails.
- [x] B10 Tests need live API keys; mcp_config.json has a hardcoded local path;
       README clone URL is wrong.

## Statistics (phase 4.5)
- [x] Q1 Add a deterministic quant layer (quant/): return distribution, beta/correlation,
      Monte Carlo DCF, forward probability (bootstrap + normal), base rates, calibration.
- [x] Q2 Gamma cites quant fields by id; the verifier resolves `quant:` ids against the report.
- [x] Q3 Sigma takes the report as structured input; confidence x Q x C; contradictions flagged.
- [x] Q4 Alpha packet carries the signed report.
- [ ] Q5 Run scripts/calibration_backtest.py (live) so calibration stops being "unavailable".
- [ ] Q6 Base rates need a cross-sectional case table; one stock's 10-year history gives too few
      similar cases (7 of 37 for AAPL).
- [ ] Q7 Frontend does not show the quant report, Q/C factors or contradictions yet.

## Rules for every session
- One phase = one branch = one PR. Do not touch other phases.
- Tests must pass offline (no API keys, no network).
- Tick items here when fixed, and note anything new you find.
## Phase 0 (branch phase-0-safety) notes
- B1: pem files removed from the tree and `*.pem` gitignored; history NOT rewritten,
  so the old key is burned. Re-sign/discard anything signed with it (alpha_packets/*.json).
  AP2Handler reads AP2_PRIVATE_KEY_PATH, generates on first run, writes the key 0600.
- B2: gateway/auth.py raises RuntimeError at import if GATEWAY_SECRET_KEY is unset.
  The old default secret is public in git history: rotate any deployment that used it.
- LLM client is injectable (`llm_client=` on Charlie/Delta/Gamma/Sigma/DebateCoordinator);
  `FakeLLMClient` lives in coordinator/llm_client.py. Tests are offline: recorded
  SEC/news/yfinance fixtures in tests/fixtures, network blocked by default,
  `@pytest.mark.live` tests skipped unless `pytest --live`.
- Found: AP2Handler derived the public path with `.replace("private","public")`; a custom
  path without "private" would have overwritten the private key with the public one. Fixed.
- Found: DebateCoordinator runs Gamma even with no price/EPS (docstring says it is skipped).
  Relates to C2; test no longer asserts it.
- Found: test_hitl_approval_flow was already failing (unregistered challenge, incomplete
  AlphaPacket stub); fixed the test.
- Found: the coordinator passes one shared fake to all agents; with a real client each agent
  still builds its own persona-specific MultiLLMClient (injection is per instance).
- Found: gateway/app.py builds AP2Handler()/SigmaAgent() at import, so importing the app
  has side effects (key generation, client init).

## Phase 2 (branch phase-2-grounding) notes
- C1: Charlie gets 5 fiscal years of SEC XBRL company facts (revenue, gross profit, operating
  income, net income, diluted EPS, operating cash flow, capex, cash, long-term debt,
  commercial paper, equity, shares) as evidence records in the prompt. The filing link is
  a reference only; if it fails it is a warning, not an error.
- C2 Gamma: fetches price, shares and 1y of daily closes (yfinance) and EPS, cash flows and
  debt (SEC); price/eps/growth_rate removed from coordinator, gateway AnalysisRequest and
  scripts/simulate_debate.py (the frontend never sent them). PE, PEG, DCF and risk metrics
  run in code every time the data is there.
- C2 Delta: news from yfinance, Yahoo RSS, Nasdaq RSS and Google RSS in parallel, merged by
  majority; prompt carries summaries; headline-only articles are flagged.
- All three agents return the DESIGN.md §2 schema (agents/schema.py), validated with
  pydantic, retried once, then failed with error_type invalid_output. Claims citing evidence
  IDs that were not provided are rejected. A failed data source returns error_type
  data_source and makes no LLM call.
- Fixed while here: DCF now subtracts net debt (equity value / shares); news `days`
  filter was computed but never applied.
- Not done here (other phases): discount rate and terminal growth are still persona-based
  (C7, and DESIGN.md's CAPM rate); rounds 2-3 are still free text (C3); Sigma still scores
  the rendered text (C4-C6). Latest-quarter (10-Q) figures from DESIGN.md §1 are not
  fetched yet; annual figures only.
- Found: yfinance "AAPL" news includes many stories mainly about other companies (e.g.
  Nvidia). No relevance filter yet; Delta is told to weigh evidence, but this will need one.
- Found: the semantic cache in mcp_server still returns results by embedding similarity
  (DESIGN.md §7); the new tools are exposed through it unchanged.

## Phase 3 (branch phase-3-debate) notes
- C3: coordinator/transcript.py holds every round's structured output. Round 2 and 3 prompts
  carry the agent's own earlier output(s) in full, its own evidence, and the other agents'
  full claims (plus the evidence they cited) and, in round 3, their critiques.
  `_extract_key_points` / `_prepare_round_context` are deleted.
- Round 2: every agent critiques claims by id (`charlie.C2`), at least one per other agent,
  each backed by ids from its OWN evidence; validated, one retry.
- Round 3: final stance and claims plus `changes` (answering every critique received),
  `changed_by` (which claims/critiques changed the view and why) and `stance_change`;
  a stance change without a reason and a changed_by entry is rejected.
- B7: tokens of every LLM call in every round, including rejected retries, count against
  TOKEN_LIMIT and are stored in the log (`metadata.total_tokens`, `transcript.llm_calls`).
  The mid-debate circuit breaker calls `SigmaAgent.score_debate` (no LLM); Sigma's synthesis
  now runs once, in the gateway after the debate.
- Added: a debate with fewer than two valid round-1 agents stops after round 1
  (TERMINATED_EARLY / INSUFFICIENT_AGENTS) instead of running critiques against nobody.
- Still open (C4): the circuit breaker now runs cheaply, but faithfulness still only checks
  that grounding_sources is non-empty, so it still cannot trip in practice.
- Found: when the token limit is hit the coordinator raises; the gateway marks the session
  FAILED rather than returning a partial result. Consider a NO_VERDICT result instead.

## Phase 4 (branch phase-4-judge) notes
Implemented per PHASE4_PLAN.md: config/thresholds.yaml + coordinator/config.py,
judge/verifier.py (C4), judge/scoring.py (C5, C6), Sigma rewrite, persona-free agents with a
CAPM discount rate (C7), circuit breaker that can trip, NO_VERDICT outcomes, gateway/AP2/HITL
updated. Tests: test_verifier (V1-V15), test_scoring (I1-I6, S1-S3, G1-G9, R1),
test_config, test_capm, test_hitl, test_judge (J1-J12).

Deviations from PHASE4_PLAN.md:
- HITL: `hitl.low_confidence` replaced by `hitl.low_confidence_action` ("warn" | "approve").
  Buy/Sell below 60 is never a plain notification: default is an explicit low-confidence
  warning; "approve" makes it need approval too. Buy/Sell with no confidence needs approval.
- Sigma's validated output adds `supporting_points` (verified claims of the agents whose stance
  matches the verdict), requested for phase 4 but not in the plan.
- `write_narrative` also receives the supporting points and risks and returns
  (text, warning) so a failed LLM call is reported, not hidden.
- `decide_verdict` takes an extra `warnings` list; `AgentVerification` stores its
  `denominator`; `Transcript` has `rounds_started` (so a debate that never reached round 3
  scores integrity 1, as in I5) and `final_outputs()`.
- Verifier: wording numbers may also match within the rounding implied by how they are
  written ("$0.5 trillion" = +/-0.05tn); quote claims may use numbers that appear in the cited
  article; zero denominators fail with reason `bad_operation`.
- Integrity counts every round-1 claim that failed verification, including inferences that
  fail because they rest on a false number.
- `INSUFFICIENT_AGENTS` kept as a NO_VERDICT reason when LLM failures (not faithfulness drops)
  leave fewer than two agents after round 1.

Still open / found:
- B8: kai-frontend/src/App.jsx still fills faithfulness/consistency with Math.random().
  SigmaCard/CommitteeContext now read `integrity`, show "-" for a missing confidence and a
  warnings banner; the frontend was not built or run (no node_modules here).
- Existing alpha_packets/*.json use the old schema and the burned key.
- The wording check only sees numbers with a $, %, x or scale marker.

## Phase 4.5 (branch phase-4.5-quant) notes
- quant/ is pure (numpy, scipy, statsmodels); seeded; every field has value, unit, n and kind,
  or an explicit "unavailable" with a reason. Built once per debate before round 1.
- Beta, CAPM rate and annualised volatility now come only from the report (Gamma's own beta
  and volatility calculations were removed).
- Fixtures: 10y AAPL closes, AAPL split history, full first-reported diluted-EPS history.
- Found: the test venv had drifted to pandas 3.1 / numpy 2.2 (unpinned transitive installs),
  which breaks statsmodels 0.14.4. The venv is now on the pinned pandas 2.2.0 / numpy 1.26.0;
  any environment must install from requirements.txt to match.
- Found: AAPL's Monte Carlo DCF gives P(fair value > price) = 0.06, so bullish agents are
  flagged as contradicting the report on every fixture run.
