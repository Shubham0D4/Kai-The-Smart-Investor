"""
Real /status payloads for the frontend's render tests (kai-frontend/src/test/fixtures/*.json).

Each scenario runs a full debate through the gateway (offline: recorded data + fake LLM) and
captures the response. Normally this test checks that the committed fixtures still have the
shape the API produces, so a schema change cannot silently leave the UI tests stale.
Regenerate them with:   KAI_WRITE_UI_FIXTURES=1 pytest tests/integration/test_ui_fixtures.py
"""

import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from judge.sigma_agent import SigmaAgent

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "kai-frontend" / "src" / "test" / "fixtures"
WRITE = bool(os.getenv("KAI_WRITE_UI_FIXTURES"))


def shape(value):
    """Structure of a JSON value: dict keys recursively, list element shape, scalar type names.
    None is a wildcard (a field that is null in one scenario is a value in another)."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    if value is None:
        return None
    return {bool: "bool", int: "number", float: "number", str: "string"}[type(value)]


def compatible(fresh, committed, path="$"):
    """Structural differences between a freshly produced body and the committed fixture: keys
    present in only one, or different scalar types. Values are not compared (ids, timestamps and
    token counts differ every run). None matches anything."""
    if fresh is None or committed is None:
        return []
    if isinstance(fresh, dict) and isinstance(committed, dict):
        out = []
        for key in sorted(set(fresh) | set(committed)):
            if key not in fresh or key not in committed:
                out.append(f"{path}.{key}: present in only one of fresh/committed")
            else:
                out += compatible(fresh[key], committed[key], f"{path}.{key}")
        return out
    if isinstance(fresh, list) and isinstance(committed, list):
        return compatible(fresh[0], committed[0], path + "[]") if fresh and committed else []
    kind = lambda v: "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else type(v).__name__
    return [] if kind(fresh) == kind(committed) else [f"{path}: {kind(fresh)} vs {kind(committed)}"]


def start(client, ticker="AAPL", persona="zen"):
    return client.post("/api/v1/analyze", json={"ticker": ticker, "persona": persona}).json()["session_id"]


def mostly_false(agent):
    def hook(prompt, reply):
        if f'"agent": "{agent}", "round": 1' not in prompt or "ROUND" in prompt:
            return reply
        data = json.loads(reply)
        for c in data["claims"][:2]:
            if c["kind"] == "numeric":
                c["value"] = c["value"] * 3 + 1
            elif c["kind"] == "quote":
                c["quote"] = "words that never appeared in this article"
        return json.dumps(data)
    return hook


def both(h1, h2):
    return lambda prompt, reply: h2(prompt, h1(prompt, reply))


@pytest.fixture
def scenarios(make_gateway, make_responder, wait_status, monkeypatch):
    """name -> /status body, one real run per UI state."""
    out = {}

    def run(name, **kw):
        with TestClient(make_gateway(db_name=f"{name}.db", **kw)) as c:
            out[name] = wait_status(c, start(c))

    run("hold_completed")                                                  # neutral -> Hold, passive
    run("waiting_approval", stance="bearish", confidence=80)               # Sell >= 60 -> needs approval
    run("circuit_breaker", responder=make_responder(
        round1_hook=both(mostly_false("charlie"), mostly_false("delta"))))  # NO_VERDICT, two agents dropped

    real_gate = SigmaAgent.gate_inputs                                     # let a bullish Buy pass its gates
    monkeypatch.setattr(SigmaAgent, "gate_inputs", staticmethod(lambda t, r=None: (0.30, real_gate(t, r)[1])))
    run("buy_contradiction", stance="bullish", confidence=80)              # contradicts the quant report
    monkeypatch.setattr(SigmaAgent, "gate_inputs", staticmethod(real_gate))

    def broken(ticker, persona):
        raise RuntimeError("no coordinator today")
    run("failed", coordinator_factory=broken)

    release, entered = threading.Event(), threading.Event()
    base = make_responder()

    def responder(prompt):
        if '"agent": "delta", "round": 1' in prompt and "ROUND" not in prompt:
            entered.set()
            assert release.wait(20)
        return base(prompt)
    with TestClient(make_gateway(db_name="running.db", responder=responder)) as c:
        sid = start(c)
        assert entered.wait(20)
        out["running"] = c.get(f"/api/v1/status/{sid}").json()
        release.set()
        wait_status(c, sid)
    return out


EXPECTED = {
    "hold_completed": ("completed", "Hold"),
    "waiting_approval": ("waiting_approval", "Sell"),
    "circuit_breaker": ("completed", "No Verdict"),
    "buy_contradiction": ("completed", "Buy"),
    "failed": ("failed", None),
    "running": ("running", None),
}


def test_scenarios_show_the_states_they_are_named_for(scenarios):
    for name, (status, rec) in EXPECTED.items():
        body = scenarios[name]
        assert body["status"] == status, (name, body.get("error"))
        if rec:
            assert body["sigma"]["final_recommendation"]["recommendation"] == rec, name
    assert scenarios["circuit_breaker"]["debate"]["outcome"]["reason"] == "CIRCUIT_BREAKER_FAITHFULNESS"
    assert {a["state"] for a in scenarios["circuit_breaker"]["debate"]["agents"].values()} == {"dropped", "done"}
    contradiction = scenarios["buy_contradiction"]["sigma"]["final_recommendation"]
    assert contradiction["contradictions"] and contradiction["confidence"] < contradiction["confidence_before_quant"]
    assert scenarios["waiting_approval"]["step_up_challenge"]["status"] == "pending"
    assert scenarios["failed"]["error"] and scenarios["failed"]["debate"] is None
    assert {a["state"] for a in scenarios["running"]["debate"]["agents"].values()} == {"done", "analyzing", "waiting"}


def test_ui_fixtures_match_what_the_api_produces(scenarios):
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    problems = []
    for name, body in scenarios.items():
        path = FIXTURE_DIR / f"{name}.json"
        if WRITE:
            path.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
            continue
        assert path.exists(), f"missing {path}; run with KAI_WRITE_UI_FIXTURES=1"
        problems += [f"{name}{p[1:]}" for p in compatible(body, json.loads(path.read_text()))]
    assert not problems, "UI fixtures are stale; regenerate with KAI_WRITE_UI_FIXTURES=1:\n" + "\n".join(problems[:15])
