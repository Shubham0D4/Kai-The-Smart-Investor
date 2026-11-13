"""
Phase 3 (FIXES.md C3, B7): the debate transcript, rounds 2-3, token accounting and the
score-only circuit breaker. Offline: recorded fixtures + a fake LLM that returns valid JSON.
"""

import json
import os

import pytest

from agents.schema import CritiqueOutput
from coordinator.llm_client import FakeLLMClient
from coordinator.personas import RiskPersona
from coordinator.server import DebateCoordinator
from coordinator.transcript import Transcript
from judge.sigma_agent import SigmaAgent

LONG_RISK = "Long risk sentence that must survive intact. " * 15   # ~700 chars, > old 300 cut


def dump(model) -> str:
    return json.dumps(model.model_dump(by_alias=True), indent=1)


@pytest.fixture
def run(schema_llm):
    """Run a debate and clean up its log file afterwards."""
    files = []

    def _run(rounds=3, llm=schema_llm, token_limit=None):
        coord = DebateCoordinator("AAPL", RiskPersona.BALANCED, llm_client=llm)
        if token_limit is not None:
            coord.TOKEN_LIMIT = token_limit
        files.append(coord.logger)
        return coord, coord.run_debate(rounds=rounds)
    yield _run
    for logger in files:
        path = getattr(logger, "log_file", None)
        if path and os.path.exists(path):
            os.remove(path)


def _prompts(llm, marker, agent):
    found = [p for p in llm.calls if marker in p and f"You are {agent}." in p]
    assert found, f"no {marker!r} prompt for {agent}"
    return found[-1]


def test_round2_and_round3_prompts_contain_earlier_rounds(run, schema_llm):
    base = schema_llm.responses

    def respond(prompt):   # give Charlie a long round-1 risk to prove nothing is truncated
        text = base(prompt)
        if '"agent": "charlie", "round": 1' in prompt and "ROUND" not in prompt:
            data = json.loads(text)
            data["risks"] = [LONG_RISK]
            text = json.dumps(data)
        return text
    schema_llm.responses = respond

    coord, result = run(rounds=3)
    t = coord.transcript
    assert result["rounds_completed"] == 3
    assert set(t.round1) == set(t.round2) == set(t.round3) == {"charlie", "delta", "gamma"}

    for agent in t.participants():
        r2 = _prompts(schema_llm, "ROUND 2 - CROSS-EXAMINATION", agent)
        assert dump(t.round1[agent]) in r2                      # own previous output, in full
        for other in t.others(agent):
            assert dump(t.round1[other]) in r2                  # others' full structured claims
        r3 = _prompts(schema_llm, "ROUND 3 - FINAL POSITION", agent)
        assert dump(t.round1[agent]) in r3 and dump(t.round2[agent]) in r3
        for other in t.others(agent):
            assert dump(t.round1[other]) in r3 and dump(t.round2[other]) in r3
        for ref, _, k in t.critiques_of(agent):                 # critiques of own claims listed
            assert ref in r3 and k.argument in r3

    # Charlie's long round-1 risk reached the other agents' prompts uncut
    assert LONG_RISK in _prompts(schema_llm, "ROUND 2 - CROSS-EXAMINATION", "delta")
    assert LONG_RISK in _prompts(schema_llm, "ROUND 3 - FINAL POSITION", "gamma")


def test_round2_targets_claims_by_id_with_own_evidence(run):
    coord, _ = run(rounds=2)
    t = coord.transcript
    for agent, out in t.round2.items():
        own = {e.id for e in t.evidence[agent]}
        targeted = {k.target.split(".")[0] for k in out.critiques}
        assert targeted == set(t.others(agent))
        for k in out.critiques:
            assert k.target in t.claim_refs(t.others(agent))
            assert set(k.evidence) <= own


def test_round3_states_changes_and_answers_every_critique(run):
    coord, result = run(rounds=3)
    t = coord.transcript
    with open(result["log_file"]) as f:
        log = json.load(f)
    assert set(log["transcript"]["round3"]) == {"charlie", "delta", "gamma"}
    for agent, final in t.round3.items():
        answered = {r for ch in final.changes for r in ch.in_response_to}
        assert answered == {ref for ref, _, _ in t.critiques_of(agent)}
        assert final.stance_change.from_ == t.round1[agent].stance


class CountingFake(FakeLLMClient):
    """Fake that independently totals the tokens of every call it serves."""
    def __init__(self, responses):
        super().__init__(responses=responses)
        self.total = 0

    def generate_content(self, prompt, max_retries=3):
        r = super().generate_content(prompt, max_retries)
        self.total += r.usage_metadata.total_token_count
        return r


def test_every_llm_call_in_every_round_is_counted_including_retries(run, schema_llm):
    base = schema_llm.responses
    state = {"junk_sent": False}

    def respond(prompt):    # Delta's first round-2 reply is invalid -> one retry
        if "ROUND 2" in prompt and "You are delta." in prompt and not state["junk_sent"]:
            state["junk_sent"] = True
            return "I broadly agree with everyone."
        return base(prompt)

    llm = CountingFake(respond)
    coord, result = run(rounds=3, llm=llm)

    assert len(llm.calls) == 10                          # 3 agents x 3 rounds + 1 retry
    assert coord.total_tokens_used == llm.total == result["total_tokens_used"]
    rounds = [c["round"] for c in coord.transcript.calls]
    assert rounds.count(1) == rounds.count(2) == rounds.count(3) == 3
    with open(result["log_file"]) as f:
        assert json.load(f)["metadata"]["total_tokens"] == llm.total


def test_token_limit_after_round_1_returns_no_verdict(run, schema_llm):
    # PHASE4_PLAN J6: the limit is still enforced in later rounds, but ends the debate with
    # NO_VERDICT instead of raising
    coord, _ = run(rounds=1)
    round1_tokens = coord.total_tokens_used
    coord, result = run(rounds=3, llm=FakeLLMClient(responses=schema_llm.responses),
                        token_limit=round1_tokens + 1)
    assert result["status"] == "NO_VERDICT" and result["reason"] == "TOKEN_LIMIT"
    assert "HARD LIMIT EXCEEDED" in result["detail"]
    assert coord.transcript.rounds_started == 2 and not coord.transcript.round3


def test_no_sigma_llm_call_during_the_debate(run, schema_llm, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Sigma's narrative must not run during the debate")
    monkeypatch.setattr(SigmaAgent, "write_narrative", boom)

    coord, result = run(rounds=3)
    assert result["rounds_completed"] == 3
    assert not any("Explain this investment committee verdict" in p for p in schema_llm.calls)


def test_fewer_than_two_agents_with_data_stops_before_round_1(run, schema_llm, failing_sources):
    failing_sources.update({"sec_facts", "yfinance"})      # Charlie and Gamma lose their data
    coord, result = run(rounds=3)
    assert result["status"] == "NO_VERDICT"
    assert result["reason"] == "INSUFFICIENT_EVIDENCE"
    assert result["rounds_completed"] == 0
    assert schema_llm.calls == []


# ------------------------------------------------------------------ transcript validation

EVIDENCE = {
    "charlie": [{"id": "sec:revenue:FY2026", "label": "Revenue FY2026", "value": 100.0, "unit": "USD",
                 "source": "test"}],
    "delta": [{"id": "news:1", "label": "Apple shares climb after strong quarter", "source": "test",
               "text": "Apple shares rose after the company reported a strong quarter."}],
}


def _transcript(make_valid_output):
    from agents.schema import format_evidence, EvidenceRecord
    t = Transcript("AAPL")
    for agent, ev in EVIDENCE.items():
        records = [EvidenceRecord(**e) for e in ev]
        prompt = f'"agent": "{agent}", "round": 1\n{format_evidence(records)}\n'
        t.add_round1(agent, json.loads(make_valid_output(prompt)), ev)
    return t


def test_round2_rejects_foreign_evidence_and_unknown_targets(make_valid_output):
    t = _transcript(make_valid_output)
    good = {"agent": "delta", "round": 2, "critiques": [
        {"id": "K1", "target": "charlie.C1", "verdict": "dispute", "argument": "a", "evidence": ["news:1"]}]}
    assert isinstance(t.validate_round2("delta", json.dumps(good)), CritiqueOutput)

    bad_ev = json.loads(json.dumps(good)); bad_ev["critiques"][0]["evidence"] = ["sec:revenue:FY2026"]
    with pytest.raises(ValueError, match="not yours"):
        t.validate_round2("delta", json.dumps(bad_ev))
    bad_target = json.loads(json.dumps(good)); bad_target["critiques"][0]["target"] = "charlie.C9"
    with pytest.raises(ValueError, match="not another agent's claim"):
        t.validate_round2("delta", json.dumps(bad_target))
    self_target = json.loads(json.dumps(good)); self_target["critiques"][0]["target"] = "delta.C1"
    with pytest.raises(ValueError, match="not another agent's claim"):
        t.validate_round2("delta", json.dumps(self_target))


def test_round3_requires_answers_and_reasoned_stance_change(make_valid_output):
    t = _transcript(make_valid_output)
    t.add_round2("delta", t.validate_round2("delta", json.dumps({"agent": "delta", "round": 2, "critiques": [
        {"id": "K1", "target": "charlie.C1", "verdict": "dispute", "argument": "a", "evidence": ["news:1"]}]})))
    final = {"agent": "charlie", "round": 3, "stance": "neutral", "confidence": 50,
             "claims": [{"id": "C1", "kind": "numeric", "text": "t", "value": 1, "unit": "USD",
                         "op": "equals", "evidence": ["sec:revenue:FY2026"]}],
             "changes": [{"claim": "C1", "action": "kept", "reason": "r", "in_response_to": ["delta.K1"]}],
             "stance_change": {"from": "neutral", "to": "neutral", "reason": ""}}
    t.validate_round3("charlie", json.dumps(final))

    unanswered = dict(final, changes=[])
    with pytest.raises(ValueError, match="not answered"):
        t.validate_round3("charlie", json.dumps(unanswered))
    flipped = dict(final, stance="bearish", stance_change={"from": "neutral", "to": "bearish", "reason": "x"})
    with pytest.raises(ValueError, match="changed_by"):
        t.validate_round3("charlie", json.dumps(flipped))
    flipped["changed_by"] = [{"ref": "delta.K1", "reason": "their news evidence"}]
    t.validate_round3("charlie", json.dumps(flipped))
