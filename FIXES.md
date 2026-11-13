# Kai fix list

## Conceptual
- [x] C1 Charlie never reads the filing: prompt has only form type, date, URL.
- [x] C2 Delta sees only headlines. Gamma depends on user-typed price/EPS/growth;
      DCF and risk metrics never run (coordinator passes no cash flows or price history).
- [ ] C3 Rounds 2 and 3 are fresh LLM calls with no history. Round 2 gets a
      300-char truncation; round 3 gets no debate content at all.
- [ ] C4 Faithfulness only checks that grounding_sources is non-empty, and the
      coordinator hardcodes those strings. Circuit breaker cannot trip.
- [ ] C5 Consistency is keyword counting and penalises changing position.
- [ ] C6 Verdict is a substring match on "buy"/"sell"; confidence is the mean of
      C4 and C5; key_risks parsing is broken.
- [ ] C7 Persona changes the discount rate (facts) instead of decision thresholds.
- [ ] C8 A2A / MCP / ADK are claimed but not implemented; agents import tools directly.

## Bugs and security
- [x] B1 ap2_private_key.pem committed to a public repo.
- [x] B2 Hardcoded default JWT secret in gateway/auth.py.
- [ ] B3 /auth/register accepts role from the client (anyone can be admin).
- [ ] B4 user_id comes from request body, not the token; /chat and /status have no auth.
- [ ] B5 run_analysis is async but runs blocking code; server freezes during a debate.
- [ ] B6 Sessions and history are in-memory dicts.
- [ ] B7 Token limit counts round 1 only; Sigma's LLM synthesis runs twice.
- [ ] B8 Frontend shows Math.random() signals, prices, trace metrics; CitationPopover is mock.
- [ ] B9 Gateway returns hardcoded index values when yfinance fails.
- [x] B10 Tests need live API keys; mcp_config.json has a hardcoded local path;
       README clone URL is wrong.

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
