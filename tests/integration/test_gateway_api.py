"""
Gateway API (phase 5): no auth, debates in a thread pool, sessions/history/chat persisted in
SQLite, structured responses. Offline: recorded fixtures, fake LLM, a temporary database file.
"""

import json
import os
import threading

import pytest
from fastapi.testclient import TestClient

from gateway.config import DEFAULT_CORS_ORIGINS, LOCAL_USER, load_settings
from gateway.schema import AlphaPacket, AnalysisRequest, StatusResponse
from judge.sigma_agent import SigmaEvaluation


def start(client, ticker="AAPL", persona="zen"):
    res = client.post("/api/v1/analyze", json={"ticker": ticker, "persona": persona})
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------- Part A: no auth

def test_no_auth_surface(client):
    for method, path in (("post", "/auth/token"), ("post", "/auth/register"), ("get", "/auth/me")):
        assert getattr(client, method)(path).status_code == 404
    spec = json.dumps(client.get("/openapi.json").json())
    assert "user_id" not in spec and "OAuth2" not in spec and "Bearer" not in spec
    assert "/auth" not in spec
    with pytest.raises(ModuleNotFoundError):
        import gateway.auth  # noqa: F401


def test_requests_carry_no_user_or_valuation_inputs():
    assert set(AnalysisRequest.model_fields) == {"ticker", "persona"}


def test_every_route_works_without_credentials(client):
    assert client.get("/api/v1/history").status_code == 200
    assert client.get("/api/v1/market/indices").status_code == 200
    assert client.get("/api/v1/chat/history").status_code == 200
    assert client.post("/api/v1/analyze", json={"ticker": "AAPL"}).status_code == 200


def test_invalid_tickers_rejected(client):
    for bad in ("", "WAYTOOLONG123", "AA PL", "../x"):
        assert client.post("/api/v1/analyze", json={"ticker": bad}).status_code == 422
    assert start(client, "aapl")["ticker"] == "AAPL"


def test_settings_default_to_localhost_and_vite_origin(monkeypatch):
    for var in ("GATEWAY_HOST", "GATEWAY_PORT", "GATEWAY_CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.host == "127.0.0.1" and s.port == 8080
    assert s.cors_origins == DEFAULT_CORS_ORIGINS.split(",") and "*" not in s.cors_origins
    monkeypatch.setenv("GATEWAY_HOST", "0.0.0.0")
    monkeypatch.setenv("GATEWAY_PORT", "9000")
    monkeypatch.setenv("GATEWAY_CORS_ORIGINS", "http://a.test, http://b.test")
    s = load_settings()
    assert (s.host, s.port, s.cors_origins) == ("0.0.0.0", 9000, ["http://a.test", "http://b.test"])


def test_cors_allows_only_the_vite_origin(client):
    ok = client.options("/api/v1/history", headers={"Origin": "http://localhost:5173",
                                                    "Access-Control-Request-Method": "GET"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    evil = client.get("/api/v1/history", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    preflight = client.options("/api/v1/history", headers={"Origin": "http://evil.example",
                                                           "Access-Control-Request-Method": "GET"})
    assert preflight.headers.get("access-control-allow-origin") != "http://evil.example"


def test_cors_origins_overridable(make_gateway, monkeypatch):
    monkeypatch.setenv("GATEWAY_CORS_ORIGINS", "http://my.app")
    with TestClient(make_gateway()) as c:
        r = c.get("/health", headers={"Origin": "http://my.app"})
        assert r.headers["access-control-allow-origin"] == "http://my.app"
        r = c.get("/health", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in r.headers


# ---------------------------------------------------------------- Part B: thread pool (B5)

def test_status_responds_while_a_debate_is_running(make_gateway, make_responder, timed_call, wait_status,
                                                   join_stuck_threads):
    release, entered = threading.Event(), threading.Event()
    base = make_responder()

    def responder(prompt):                  # Delta's round-1 LLM call blocks until released
        if '"agent": "delta", "round": 1' in prompt and "ROUND" not in prompt:
            entered.set()
            assert release.wait(20)
        return base(prompt)

    with TestClient(make_gateway(responder=responder)) as client:
        try:
            # If the debate ran inline, even this request would not return while Delta is blocked.
            sid = timed_call(lambda: start(client), timeout=10)["session_id"]
            assert entered.wait(20), "the debate never reached Delta"
            # These calls would hang (and fail via the timeout) if the debate blocked the server.
            body = timed_call(lambda: client.get(f"/api/v1/status/{sid}").json())
            health = timed_call(lambda: client.get("/health").json())
            logs = timed_call(lambda: client.get(f"/api/v1/sessions/{sid}/logs").json())
            history = timed_call(lambda: client.get("/api/v1/history").json())
        finally:
            release.set()
            join_stuck_threads()            # let a blocked request finish so the client can close

        assert health["status"] == "healthy" and history["total_count"] == 1
        assert body["status"] == "running" and body["progress"]
        debate = body["debate"]
        # live state comes from the persisted transcript: Charlie is done, Delta is working
        assert debate["rounds_started"] == 1 and not debate["finished"]
        assert {a: v["state"] for a, v in debate["agents"].items()} == \
               {"charlie": "done", "delta": "analyzing", "gamma": "waiting"}
        assert debate["agents"]["charlie"]["round1"]["claims"]
        assert any("Fetching SEC" in e["message"] for e in debate["events"])
        assert [e["message"] for e in logs] == [e["message"] for e in debate["events"]]
        assert debate["quant_report"]["fields"]["quant:market.beta"]["status"] == "ok"

        final = wait_status(client, sid)
        assert final["status"] == "completed", final.get("error")


def test_executor_limits_concurrent_debates(make_gateway):
    app = make_gateway()
    assert app.state.ctx.executor._max_workers == 2            # KAI_MAX_CONCURRENT_DEBATES default


# ---------------------------------------------------------------- full workflow, structured response

def test_full_analysis_returns_structured_agent_output_verdict_and_quant_report(client, wait_status):
    sid = start(client)["session_id"]
    body = wait_status(client, sid)
    assert body["status"] == "completed", body.get("error")
    StatusResponse.model_validate(body)

    debate = body["debate"]
    assert debate["finished"] and debate["outcome"]["status"] == "COMPLETED"
    assert debate["total_tokens"] > 0 and debate["token_limit"] == 50000
    for name, agent in debate["agents"].items():
        assert agent["state"] == "done"
        assert agent["round1"]["agent"] == name and agent["round1"]["claims"]
        assert agent["evidence"] and agent["verification"]["faithfulness"] == 1.0
        assert agent["final_verification"]["faithfulness"] == 1.0          # round-3 claims are checked too
        assert {c["claim_id"] for c in agent["final_verification"]["checks"]} == \
               {c["id"] for c in agent["round3"]["claims"]}
        assert agent["round2"]["critiques"] and agent["round3"]["stance_change"]["from"] == "neutral"
    cited = {e for c in debate["agents"]["charlie"]["round1"]["claims"] for e in c.get("evidence", [])}
    assert cited <= {e["id"] for e in debate["agents"]["charlie"]["evidence"]}
    assert {c["agent"] for c in debate["llm_calls"]} == {"charlie", "delta", "gamma"}

    sigma = body["sigma"]
    verdict = sigma["final_recommendation"]
    assert verdict["recommendation"] == "Hold" and verdict["confidence"] is None
    assert verdict["gates"] and sigma["evaluation"]["faithfulness_score"] == 100.0
    assert set(sigma["verdicts_by_persona"]) == {"zen", "balanced", "alpha"}
    SigmaEvaluation.model_validate({**sigma, "verification": {}, "quant_report": None})

    quant = debate["quant_report"]
    assert quant["seed"] == 42 and quant["drift_mode"] == "zero"
    vol = quant["fields"]["quant:returns.vol_annual"]
    assert vol["n"] == 252 and vol["n_kind"] == "observations" and vol["kind"] == "descriptive"
    fair = quant["fields"]["quant:mc_dcf.fair_value_p50"]
    assert fair["n_kind"] == "simulations" and fair["kind"] == "model"
    assert quant["fields"]["quant:base_rate.share_up"]["status"] == "unavailable"
    assert quant["fields"]["quant:base_rate.share_up"]["reason"].startswith("insufficient data")

    packet = AlphaPacket(**body["alpha_packet"])
    assert packet.quant_report["fields"]["quant:market.beta"]["status"] == "ok"
    assert body["notification"]["type"] == "info" and body["hitl_mode"] == "passive"


def test_history_lists_completed_and_failed_sessions(make_gateway, wait_status):
    def broken(ticker, persona):
        raise RuntimeError("no coordinator today")

    good, bad = make_gateway(), make_gateway(coordinator_factory=broken, db_name="kai.db")
    with TestClient(good) as c:
        done = start(c, "AAPL", "balanced")["session_id"]
        wait_status(c, done)
    with TestClient(bad) as c:
        failed = start(c, "MSFT", "alpha")["session_id"]
        body = wait_status(c, failed)
        assert body["status"] == "failed" and "RuntimeError: no coordinator today" in body["error"]
        hist = c.get("/api/v1/history").json()
    assert hist["total_count"] == 2
    by_id = {h["session_id"]: h for h in hist["analyses"]}
    assert by_id[done]["status"] == "completed" and by_id[done]["recommendation"] == "Hold"
    assert by_id[done]["persona"] == "balanced" and by_id[done]["alpha_packet_id"]
    assert by_id[failed]["status"] == "failed" and by_id[failed]["recommendation"] is None
    assert [h["created_at"] for h in hist["analyses"]] == sorted((h["created_at"] for h in hist["analyses"]), reverse=True)


def test_unknown_session_is_404(client):
    for path in ("/api/v1/status/nope", "/api/v1/sessions/nope/logs"):
        assert client.get(path).status_code == 404
    assert client.post("/api/v1/reject/nope").status_code == 404


# ---------------------------------------------------------------- Part B: persistence (B6)

def test_session_survives_a_server_restart(make_gateway, wait_status):
    with TestClient(make_gateway(db_name="restart.db")) as first:
        sid = start(first, "AAPL", "zen")["session_id"]
        before = wait_status(first, sid)
        history_before = first.get("/api/v1/history").json()
    assert before["status"] == "completed"

    with TestClient(make_gateway(db_name="restart.db")) as second:     # a brand-new server process
        after = second.get(f"/api/v1/status/{sid}").json()
        history_after = second.get("/api/v1/history").json()
        logs = second.get(f"/api/v1/sessions/{sid}/logs").json()

    assert after == before                                              # everything, incl. the structured debate
    assert history_after == history_before and history_after["total_count"] == 1
    assert len(logs) == len(before["debate"]["events"]) > 10
    assert after["alpha_packet"]["signature"] and after["sigma"]["final_recommendation"]["gates"]


def test_session_running_during_a_restart_is_reported_failed(make_gateway):
    first = make_gateway(db_name="crash.db")
    with TestClient(first) as c:
        c.get("/health")                                                # lifespan: tables exist
        first.state.ctx.store.create("sess_crashed", "AAPL", "zen")     # a run the old process never finished
    with TestClient(make_gateway(db_name="crash.db")) as c:
        body = c.get("/api/v1/status/sess_crashed").json()
    assert body["status"] == "failed" and "restarted" in body["error"]


def test_pending_approval_survives_a_restart(make_gateway, wait_status):
    with TestClient(make_gateway(db_name="hitl.db", stance="bearish", confidence=80)) as first:
        sid = start(first, "AAPL", "zen")["session_id"]
        body = wait_status(first, sid)
    assert body["status"] == "waiting_approval" and body["hitl_mode"] == "active"
    challenge = body["step_up_challenge"]
    assert challenge["status"] == "pending" and challenge["recommendation"] == "Sell"

    with TestClient(make_gateway(db_name="hitl.db")) as second:
        assert second.get(f"/api/v1/status/{sid}").json()["step_up_challenge"]["challenge_id"] == challenge["challenge_id"]
        res = second.post(f"/api/v1/approve/{sid}", json={"challenge_id": challenge["challenge_id"],
                                                          "user_confirmation": "approved"})
        assert res.status_code == 200 and res.json()["ready_for_execution"] is True
        assert second.post(f"/api/v1/approve/{sid}", json={"challenge_id": challenge["challenge_id"]}).status_code == 400

    with TestClient(make_gateway(db_name="hitl.db")) as third:
        final = third.get(f"/api/v1/status/{sid}").json()
    assert final["status"] == "completed" and final["approval_status"] == "approved"
    assert final["step_up_challenge"]["status"] == "approved" and final["step_up_challenge"]["decided_at"]


def test_reject_and_bad_challenge_ids(make_gateway, wait_status):
    with TestClient(make_gateway(stance="bearish", confidence=80)) as c:
        sid = start(c)["session_id"]
        challenge = wait_status(c, sid)["step_up_challenge"]
        wrong = c.post(f"/api/v1/approve/{sid}", json={"challenge_id": "chal_wrong"})
        assert wrong.status_code == 400 and "does not match" in wrong.json()["detail"]
        res = c.post(f"/api/v1/reject/{sid}")
        assert res.status_code == 200 and res.json()["status"] == "rejected"
        again = c.post(f"/api/v1/approve/{sid}", json={"challenge_id": challenge["challenge_id"]})
        assert again.status_code == 400 and "not waiting" in again.json()["detail"]
        final = c.get(f"/api/v1/status/{sid}").json()
        assert final["status"] == "rejected" and final["approval_status"] == "rejected"


def test_expired_challenge_cannot_be_approved(make_gateway, wait_status):
    app = make_gateway(stance="bearish", confidence=80)
    with TestClient(app) as c:
        sid = start(c)["session_id"]
        challenge = wait_status(c, sid)["step_up_challenge"]
        store = app.state.ctx.store
        store.update(sid, challenge={**store.get(sid)["challenge"], "expires_at": "2000-01-01T00:00:00"})
        assert c.get(f"/api/v1/status/{sid}").json()["step_up_challenge"]["status"] == "expired"
        res = c.post(f"/api/v1/approve/{sid}", json={"challenge_id": challenge["challenge_id"]})
        assert res.status_code == 400 and "expired" in res.json()["detail"]


# ---------------------------------------------------------------- Part B: chat persisted

class FakeGroq:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, messages, model):
        self.calls.append(messages)
        if self.fail:
            raise ConnectionError("groq is down")
        msg = type("M", (), {"content": f"echo: {messages[-1]['content']}"})()
        return type("C", (), {"choices": [type("Ch", (), {"message": msg})()]})()


def test_chat_unavailable_without_a_model_and_nothing_is_stored(client):
    res = client.post("/api/v1/chat", json={"message": "hello"})
    assert res.status_code == 503 and "GROQ_API_KEY" in res.json()["detail"]
    assert client.get("/api/v1/chat/history").json() == []


def test_chat_is_persisted_with_roles_and_survives_restart(make_gateway):
    groq = FakeGroq()
    with TestClient(make_gateway(db_name="chat.db", groq_client=groq)) as c:
        assert c.post("/api/v1/chat", json={"message": "first"}).json()["response"] == "echo: first"
        c.post("/api/v1/chat", json={"message": "second"})
        assert groq.calls[-1][0]["role"] == "system"
        assert [m["content"] for m in groq.calls[-1][1:]] == ["first", "echo: first", "second"]
    with TestClient(make_gateway(db_name="chat.db")) as c:
        hist = c.get("/api/v1/chat/history").json()
        assert [(m["role"], m["response"]) for m in hist] == [
            ("user", "first"), ("assistant", "echo: first"), ("user", "second"), ("assistant", "echo: second")]
        last_two = c.get("/api/v1/chat/history?limit=2").json()          # latest messages, oldest first
        assert [m["response"] for m in last_two] == ["second", "echo: second"]


def test_chat_model_failure_is_an_error_not_a_canned_answer(make_gateway):
    with TestClient(make_gateway(groq_client=FakeGroq(fail=True))) as c:
        res = c.post("/api/v1/chat", json={"message": "hi"})
        assert res.status_code == 502 and "groq is down" in res.json()["detail"]
        assert [m["role"] for m in c.get("/api/v1/chat/history").json()] == ["user"]


def test_local_user_is_the_only_user_key(make_gateway):
    app = make_gateway(groq_client=FakeGroq())
    with TestClient(app) as c:
        c.post("/api/v1/chat", json={"message": "x"})
        from gateway import models
        with app.state.ctx.session_factory() as db:
            assert {m.user_id for m in db.query(models.ChatMessage).all()} == {LOCAL_USER}


def test_debate_logs_are_valid_json_at_every_moment(make_gateway, make_responder, wait_status):
    """The gateway reads the log while the coordinator rewrites it: writes must be atomic."""
    app = make_gateway()
    stop, bad = threading.Event(), []

    def hammer(path_box):
        while not stop.is_set():
            path = path_box.get("path")
            if path and os.path.exists(path):
                try:
                    json.load(open(path))
                except ValueError as e:
                    bad.append(str(e))
    box = {}
    t = threading.Thread(target=hammer, args=(box,), daemon=True)
    t.start()
    try:
        with TestClient(app) as c:
            sid = start(c)["session_id"]
            for _ in range(400):
                row = app.state.ctx.store.get(sid)
                if row["debate_log"]:
                    box["path"] = row["debate_log"]
                if row["status"] != "running":
                    break
                threading.Event().wait(0.02)
            wait_status(c, sid)
    finally:
        stop.set()
        t.join(5)
    assert bad == []
