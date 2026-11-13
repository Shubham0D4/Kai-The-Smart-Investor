# Kai fix list

## Conceptual
- [ ] C1 Charlie never reads the filing: prompt has only form type, date, URL.
- [ ] C2 Delta sees only headlines. Gamma depends on user-typed price/EPS/growth;
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
