"""
PHASE4_PLAN.md §9, tests J1-J12: the judge end to end on offline fixtures with a fake LLM.
Includes an agent stating a number that is not in the fetched data (J2, J3).
"""

import json
import os

import pytest

from agents.schema import EvidenceRecord
from coordinator.llm_client import FakeLLMClient
from coordinator.server import DebateCoordinator
from judge.ap2_handler import AP2Handler
from judge.sigma_agent import SigmaAgent, SigmaEvaluation

FALSE_REVENUE = {"id": "C1", "kind": "numeric", "text": "Revenue reached $520bn in FY2026",
                 "value": 520_000_000_000, "unit": "USD", "op": "equals", "evidence": ["sec:revenue:FY2026"]}


@pytest.fixture
def debate(make_responder):
    files = []

    def run(persona="zen", rounds=3, token_limit=None, **responder_kw):
        llm = FakeLLMClient(responses=make_responder(**responder_kw))
        coord = DebateCoordinator("AAPL", persona, llm_client=llm)
        if token_limit is not None:
            coord.TOKEN_LIMIT = token_limit
        result = coord.run_debate(rounds=rounds)
        files.append(result["log_file"])
        return coord, result, llm
    yield run
    for f in files:
        if os.path.exists(f):
            os.remove(f)


def sigma(reply="Narrative explaining the verdict."):
    llm = FakeLLMClient(responses=reply)
    return SigmaAgent(llm_client=llm), llm


def for_agent(agent, rnd):
    marker = f'"agent": "{agent}", "round": {rnd}'

    def match(prompt):
        return marker in prompt and (rnd > 1 or "ROUND" not in prompt)
    return match


def charlie_says_520bn(prompt, reply):
    if not for_agent("charlie", 1)(prompt):
        return reply
    data = json.loads(reply)
    data["claims"][0] = FALSE_REVENUE
    return json.dumps(data)


def mostly_false(agent):
    def hook(prompt, reply):
        if not for_agent(agent, 1)(prompt):
            return reply
        data = json.loads(reply)
        for c in data["claims"][:2]:
            if c["kind"] == "numeric":
                c["value"] = c["value"] * 3 + 1
            elif c["kind"] == "quote":
                c["quote"] = "words that never appeared in this article"
        return json.dumps(data)
    return hook


def chain(*hooks):
    def hook(prompt, reply):
        for h in hooks:
            reply = h(prompt, reply)
        return reply
    return hook


# ---------------------------------------------------------------- J1

def test_j1_full_debate_all_claims_true(debate):
    coord, result, _ = debate(stance="bearish", confidence=80)
    s, s_llm = sigma()
    ev = s.evaluate_debate(result["log_file"], persona="zen")

    SigmaEvaluation.model_validate(ev)                     # validated JSON shape
    assert result["status"] == "COMPLETED"
    assert ev["evaluation"]["faithfulness_score"] == 100.0
    assert ev["evaluation"]["integrity_score"] == 100.0
    fr = ev["final_recommendation"]
    assert fr["recommendation"] == "Sell"                  # S = -0.80 <= zen sell threshold -0.20
    assert fr["confidence_before_quant"] == pytest.approx(80.0)
    assert fr["confidence"] == pytest.approx(80.0 * fr["quant_agreement"] * fr["contradiction_factor"], abs=0.01)
    assert fr["supporting_points"] and fr["key_risks"] == ["charlie: canned risk"]
    assert len(s_llm.calls) == 1 and fr["analysis"] == "Narrative explaining the verdict."


# ---------------------------------------------------------------- J2: a number not in the data

def test_j2_charlie_states_520bn(debate):
    coord, result, llm = debate(round1_hook=charlie_says_520bn)

    v = coord.transcript.verification["charlie"]
    c1 = next(c for c in v.checks if c.claim_id == "C1")
    assert c1.reason == "value_mismatch"
    assert c1.stated == 520_000_000_000 and c1.expected == 478_834_000_000
    assert v.faithfulness == pytest.approx(2 / 3, abs=1e-6)
    assert "charlie" not in coord.transcript.dropped       # 0.67 >= 0.60

    round2 = [p for p in llm.calls if "ROUND 2 - CROSS-EXAMINATION" in p]
    round3 = [p for p in llm.calls if "ROUND 3 - FINAL POSITION" in p]
    assert round2 and all("charlie.C1 FAILED (value_mismatch" in p for p in round2)
    assert all("charlie.C1 FAILED (value_mismatch" in p for p in round3)
    charlie_r3 = next(p for p in round3 if "You are charlie." in p)
    assert "Your claims that FAILED verification: C1" in charlie_r3
    change = next(ch for ch in coord.transcript.round3["charlie"].changes if ch.claim == "C1")
    assert change.action == "withdrawn"


def test_j2b_repeating_the_false_number_lowers_faithfulness_and_integrity(debate):
    def keep_false_number(prompt, reply):
        if "You are charlie." not in prompt:
            return reply
        data = json.loads(reply)
        data["claims"][0] = FALSE_REVENUE
        for ch in data["changes"]:
            if ch["claim"] == "C1":
                ch.update(action="kept", reason="I stand by it")
        return json.dumps(data)

    _, result, _ = debate(round1_hook=charlie_says_520bn, round3_hook=keep_false_number)
    ev = sigma()[0].evaluate_debate(result["log_file"])
    assert ev["verification"]["charlie"]["checks"][0]["reason"] == "value_mismatch"
    assert ev["verification"]["charlie"]["faithfulness"] == pytest.approx(2 / 3, abs=1e-6)
    assert ev["evaluation"]["faithfulness_score"] == pytest.approx(100 * 8 / 9, abs=0.01)
    # Round-1 failures: C1 (the false number) and C4 (an inference resting on it).
    # C4 was withdrawn, C1 kept: integrity = 1 - 0.5 x 1/2 = 0.75
    integ = ev["integrity"]["charlie"]
    assert (integ["failed_round1_claims"], integ["retained_failed"]) == (2, 1)
    assert integ["integrity"] == pytest.approx(0.75)
    assert ev["evaluation"]["integrity_score"] == pytest.approx(100 * 2.75 / 3, abs=0.01)


# ---------------------------------------------------------------- J3, J4: circuit breaker

def test_j3_mostly_false_agent_is_dropped_and_debate_continues(debate):
    coord, result, llm = debate(round1_hook=mostly_false("charlie"))
    assert coord.transcript.dropped.keys() == {"charlie"}
    assert result["status"] == "COMPLETED"
    round2 = [p for p in llm.calls if "ROUND 2 - CROSS-EXAMINATION" in p]
    assert len(round2) == 2 and not any("You are charlie." in p for p in round2)
    assert not any("charlie.C1" in p.split("Claims you may target:")[1].split("\n")[0] for p in round2)

    fr = sigma()[0].evaluate_debate(result["log_file"])["final_recommendation"]
    assert fr["agents_missing"] == ["charlie"]
    assert any("charlie dropped after round 1" in w for w in fr["warnings"])


def test_j4_two_agents_below_floor_is_no_verdict(debate):
    coord, result, llm = debate(round1_hook=chain(mostly_false("charlie"), mostly_false("delta")))
    assert result["status"] == "NO_VERDICT" and result["reason"] == "CIRCUIT_BREAKER_FAITHFULNESS"
    assert not any("ROUND 2" in p for p in llm.calls)

    s, s_llm = sigma()
    fr = s.evaluate_debate(result["log_file"])["final_recommendation"]
    assert fr["recommendation"] == "No Verdict" and fr["confidence"] is None
    assert "CIRCUIT_BREAKER_FAITHFULNESS" in fr["reason"]
    assert s_llm.calls == []


# ---------------------------------------------------------------- J5, J6: early stops

def test_j5_no_data_no_tokens(debate, failing_sources):
    failing_sources.update({"sec", "yfinance"})
    _, result, llm = debate()
    assert result["status"] == "NO_VERDICT" and result["reason"] == "INSUFFICIENT_EVIDENCE"
    assert llm.calls == [] and result["total_tokens_used"] == 0
    fr = sigma()[0].evaluate_debate(result["log_file"])["final_recommendation"]
    assert fr["recommendation"] == "No Verdict" and "INSUFFICIENT_EVIDENCE" in fr["reason"]


def test_j6_token_limit_in_round_2(debate):
    coord, _, _ = debate(rounds=1)
    coord, result, _ = debate(token_limit=coord.total_tokens_used + 1)
    assert result["status"] == "NO_VERDICT" and result["reason"] == "TOKEN_LIMIT"
    fr = sigma()[0].evaluate_debate(result["log_file"])["final_recommendation"]
    assert fr["recommendation"] == "No Verdict" and "TOKEN_LIMIT" in fr["reason"]


# ---------------------------------------------------------------- J7-J9: verdict is computed

@pytest.fixture
def favourable_gates(monkeypatch):
    """Pretend Gamma's DCF showed a 30% margin of safety and 25% volatility."""
    def set_gates(mos=0.30, vol=0.25):
        monkeypatch.setattr(SigmaAgent, "gate_inputs", staticmethod(lambda t, r=None: (mos, vol)))
    return set_gates


def test_j7_narrative_cannot_change_the_verdict(debate, favourable_gates):
    favourable_gates()
    _, result, _ = debate(stance="bullish", confidence=80)
    s, _ = sigma("STRONG SELL. Dump the stock immediately.")
    fr = s.evaluate_debate(result["log_file"], persona="zen")["final_recommendation"]
    assert fr["recommendation"] == "Buy" and fr["confidence_before_quant"] == pytest.approx(80.0)


def test_j8_narrative_failure_keeps_verdict(debate, favourable_gates):
    favourable_gates()
    _, result, _ = debate(stance="bullish", confidence=80)

    def boom(prompt):
        raise RuntimeError("provider down")
    s = SigmaAgent(llm_client=FakeLLMClient(responses=boom))
    fr = s.evaluate_debate(result["log_file"], persona="zen")["final_recommendation"]
    assert fr["recommendation"] == "Buy"
    assert fr["analysis"].startswith(f"Verdict: Buy, confidence {fr['confidence']:.0f}")
    assert any("narrative unavailable" in w for w in fr["warnings"])


def test_j9_personas_differ_only_through_thresholds(debate, favourable_gates):
    favourable_gates(mos=0.10, vol=0.25)
    _, result, _ = debate(stance="bullish", confidence=80)
    s, _ = sigma()
    evs = {p: s.evaluate_debate(result["log_file"], persona=p) for p in ("zen", "balanced", "alpha")}
    by = evs["zen"]["verdicts_by_persona"]
    assert {p: v["recommendation"] for p, v in by.items()} == {"zen": "Hold", "balanced": "Hold", "alpha": "Buy"}
    assert len({v["score"] for v in by.values()}) == 1
    assert evs["zen"]["evaluation"] == evs["balanced"]["evaluation"] == evs["alpha"]["evaluation"]
    assert {p: e["final_recommendation"]["recommendation"] for p, e in evs.items()} == \
           {"zen": "Hold", "balanced": "Hold", "alpha": "Buy"}


# ---------------------------------------------------------------- J10-J12: gateway

class ForcedSigma:
    """The real Sigma, with the recommendation/confidence overridden (to hit a HITL threshold)."""
    def __init__(self, force):
        self.real = SigmaAgent(llm_client=FakeLLMClient(responses="Narrative."))
        self.force = force

    def evaluate_debate(self, path, persona=None, quant_report=None):
        ev = self.real.evaluate_debate(path, persona=persona, quant_report=quant_report)
        ev["final_recommendation"].update(self.force)
        return ev


@pytest.fixture
def gateway_run(make_gateway, wait_status):
    """Run one analysis through the gateway app and return the final /status body."""
    from fastapi.testclient import TestClient

    def run(stance="neutral", confidence=55, force=None, persona="zen"):
        kw = {"sigma": ForcedSigma(force)} if force else {}
        app = make_gateway(stance=stance, confidence=confidence, **kw)
        with TestClient(app) as client:
            sid = client.post("/api/v1/analyze", json={"ticker": "AAPL", "persona": persona}).json()["session_id"]
            body = wait_status(client, sid)
        assert body["status"] != "failed", body.get("error")
        return body, app
    return run


def test_j10_gateway_hold_is_a_notification_without_confidence(gateway_run):
    body, _ = gateway_run()
    assert body["status"] == "completed" and body["hitl_mode"] == "passive"
    assert body["notification"]["type"] == "info" and body["notification"]["confidence"] is None
    packet = body["alpha_packet"]
    assert packet["recommendation"] == "Hold" and packet["confidence"] is None


@pytest.mark.parametrize("conf, status, mode", [(65.0, "waiting_approval", "active"), (55.0, "completed", "passive")])
def test_j11_gateway_buy_confidence_threshold(gateway_run, conf, status, mode):
    body, _ = gateway_run(force={"recommendation": "Buy", "confidence": conf})
    assert body["status"] == status and body["hitl_mode"] == mode
    if mode == "active":
        assert body["step_up_challenge"]["recommendation"] == "Buy"
    else:
        assert body["notification"]["type"] == "warning"
        assert body["notification"]["message"].startswith("Low-confidence Buy")


def test_j12_alpha_packet_carries_integrity_risks_and_valid_signature(gateway_run):
    body, app = gateway_run()
    packet, ev = body["alpha_packet"], body["sigma"]
    assert set(packet["quality_scores"]) == {"faithfulness", "integrity", "overall"}
    assert packet["quality_scores"]["integrity"] == ev["evaluation"]["integrity_score"]
    assert packet["key_risks"] == ev["final_recommendation"]["key_risks"] == ["charlie: canned risk"]
    assert packet["verdict_detail"]["gates"]
    ap2 = app.state.ctx.ap2
    assert ap2.verify_alpha_packet(packet)
    assert not ap2.verify_alpha_packet(dict(packet, recommendation="Buy"))
