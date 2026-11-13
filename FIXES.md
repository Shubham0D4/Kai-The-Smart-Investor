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
- [-] B2 Hardcoded default JWT secret in gateway/auth.py. (dropped: auth removed; the file and the secret are gone)
- [-] B3 /auth/register accepts role from the client (anyone can be admin). (dropped: auth removed)
- [-] B4 user_id comes from request body, not the token; /chat and /status have no auth. (dropped: auth removed; user_id removed everywhere, one constant LOCAL_USER server-side)
- [x] B5 run_analysis is async but runs blocking code; server freezes during a debate.
- [x] B6 Sessions and history are in-memory dicts.
- [x] B7 Token limit counts round 1 only; Sigma's LLM synthesis runs twice.
- [x] B8 Frontend shows Math.random() signals, prices, trace metrics; CitationPopover is mock.
- [x] B9 Gateway returns hardcoded index values when yfinance fails.
- [x] B10 Tests need live API keys; mcp_config.json has a hardcoded local path;
       README clone URL is wrong.

## Deployment
- [ ] Add auth before any public deployment. Kai is a single-user local app: no authentication,
      the gateway listens on 127.0.0.1 and accepts browser requests only from the Vite dev origin
      (GATEWAY_HOST / GATEWAY_CORS_ORIGINS). Nothing here is safe to expose on a network as is.

## Statistics (phase 4.5)
- [x] Q1 Add a deterministic quant layer (quant/): return distribution, beta/correlation,
      Monte Carlo DCF, forward probability (bootstrap + normal), base rates, calibration.
- [x] Q2 Gamma cites quant fields by id; the verifier resolves `quant:` ids against the report.
- [x] Q3 Sigma takes the report as structured input; confidence x Q x C; contradictions flagged.
- [x] Q4 Alpha packet carries the signed report.
- [ ] Q5 Run scripts/calibration_backtest.py (live) so calibration stops being "unavailable".
- [ ] Q6 Base rates need a cross-sectional case table; one stock's 10-year history gives too few
      similar cases (7 of 37 for AAPL).
- [x] Q7 Frontend does not show the quant report, Q/C factors or contradictions yet. (done in phase 5+6)

## Rules for every session
- Checkbox legend: [x] fixed, [ ] open, [-] dropped (no longer applies).
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

## Phase 5+6 (branch phase-5-app): simplify the gateway, fix the frontend
- Auth removed (gateway/auth.py, /auth/*, User model, route guards, Login/Register/AuthContext,
  token interceptor, python-jose/passlib/bcrypt and python-multipart from requirements.txt).
  user_id is gone from every request, query and schema; LOCAL_USER ("local") is the only user key.
  GATEWAY_HOST defaults to 127.0.0.1; CORS is limited to the Vite origins; both overridable.
- B5: debates run in a ThreadPoolExecutor (KAI_MAX_CONCURRENT_DEBATES, default 2); endpoints are
  plain `def`. test_status_responds_while_a_debate_is_running blocks a debate mid-run and checks
  /status, /health, /logs and /history still answer; it fails (rather than hangs) if the debate
  ever runs inline.
- B6: sessions, history and chat are in SQLite (KAI_DB_URL, default ./kai.db; the old users.db is
  unused). gateway/store.py is the only place that touches session rows. The coordinator is never
  stored: live state is built from the persisted debate log, which is now written atomically,
  checkpointed after every agent step, and has unique ids. The HITL challenge is a plain dict in
  the session row, so a pending approval survives a restart; sessions left `running` by a dead
  process are marked failed at startup.
- B9: yfinance failures return 502 (per-index `available:false` when only some fail), no quote or
  history -> 404; the hardcoded index values are gone. Chat without GROQ_API_KEY is 503, not a
  canned reply. News endpoints (/ticker/{symbol}/news, /market/news) use the existing providers.
- Responses: StatusResponse carries `debate` (per-agent round 1/2/3 output, evidence,
  verification of round-1 and round-3 claims, outcome, token use, events, quant report), `sigma`
  (the validated verdict) and the signed alpha packet. They reuse the backend models, so the API
  cannot drift from what the judge produces.
- B8: no Math.random(), simulated action or mock data source remains in the frontend (a test
  scans for them). Dashboard news, index values, prices, trace table and citations are real or
  say "unavailable". The UI renders stance, claims with clickable evidence, round-2 critiques,
  round-3 changes, Sigma's verdict, gates and contradiction flags, the quant report with sample
  sizes and kinds, and the loading / failed / circuit-breaker / approval states.
- Frontend tests (npm test, vitest): real /status payloads exported from the backend
  (tests/integration/test_ui_fixtures.py, which fails if the API shape drifts from them).
- Fixed while here: chat history dropped `role` (every message looked like the assistant's) and
  returned the oldest 50 instead of the latest; dividend yield was multiplied by 100 again;
  concurrent debates for one ticker in the same second shared a log file; the Groq client was
  created before load_dotenv(); system messages before round 1 were dropped from the log;
  pre-existing lint errors (31) in the frontend.
- Found: .env.example had real API keys pasted into it (uncommitted, never pushed). They were
  moved to the gitignored .env and the example restored to blanks. Treat those keys as exposed
  (they appeared in session output) and rotate them.
- Not done: the frontend has been linted, built and rendered under jsdom with real payloads but
  never opened in a real browser against a live backend; charts and layout are unchecked
  visually. The 4.5 quant UI shows what the report contains; Q7 is now ticked.
- Open: price shown next to the fair-value range is Gamma's fetched last price, not live.

## UI redesign (after phase 5+6)
- New design system in kai-frontend/src/index.css: light theme by default (dark is an
  alternative), driven by Bootstrap's data-bs-theme so Bootstrap components follow the theme;
  one accent colour; green/red/amber only where they carry meaning; a defined type scale.
- Fonts: Inter and JetBrains Mono are bundled with @fontsource-variable (they work offline;
  before, "Inter" was named but never loaded). Tabular figures only on numeric columns.
- Sticky header with brand mark, market strip, full-width search card, "How it works" when no
  analysis is open, tinted status chips instead of solid badges, aligned quant tables with a
  labelled fair-value range, consistent cards, modal, chat and ticker page. Favicon and page
  title are Kai's own. Approval wording from the backend no longer has emoji or "tap" phrasing.
- Checked visually in headless Chromium (light, dark, 390px phone width) against the real /status
  fixtures. Not checked: Safari/Firefox, a live backend with a real model's output, a formal
  accessibility audit (contrast was chosen to meet WCAG AA by design, not measured).
- Screenshots were deliberately NOT added to the repo: the fixtures use a fake LLM, so their
  agent text ("canned risk", "Value of sec:revenue:FY2022") would misrepresent the product in a
  pitch. Take them from a real run.
