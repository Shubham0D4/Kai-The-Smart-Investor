"""
PHASE4_PLAN.md §9: debate integrity (I1-I6, FIXES.md C5), verdict and confidence (S1-S3, G1-G9,
C6), persona thresholds (C7) and key risks (R1).
"""

import pytest

from agents.schema import AgentOutput, CritiqueOutput, FinalOutput
from coordinator.config import load_thresholds
from coordinator.transcript import Transcript
from judge.scoring import (
    agent_integrity, collect_key_risks, collect_supporting_points, decide_verdict, overall_integrity,
)
from judge.verifier import AgentVerification, ClaimCheck

CFG = load_thresholds()
PERSONAS = ("zen", "balanced", "alpha")


def out(agent, stance="bullish", confidence=80, rnd=1, risks=("risk",), n_claims=3):
    claims = [{"id": f"C{i}", "kind": "numeric", "text": f"t{i}", "value": 1.0, "unit": "USD",
               "op": "equals", "evidence": ["e"]} for i in range(1, n_claims + 1)]
    return AgentOutput.model_validate({"agent": agent, "round": rnd, "stance": stance,
                                       "confidence": confidence, "claims": claims, "risks": list(risks)})


def final(agent, stance, changes=(), changed_by=(), from_=None, reason=""):
    base = out(agent, stance).model_dump()
    base.update(round=3, changes=list(changes), changed_by=list(changed_by),
                stance_change={"from": from_ or stance, "to": stance, "reason": reason})
    return FinalOutput.model_validate(base)


def critique(agent, *targets):
    return CritiqueOutput.model_validate({"agent": agent, "round": 2, "critiques": [
        {"id": f"K{i}", "target": t, "verdict": "dispute", "argument": "a", "evidence": ["e"]}
        for i, t in enumerate(targets, start=1)]})


def verification(agent, failed=()):
    checks = [ClaimCheck(claim_id=f"C{i}", kind="numeric", checkable=True, verified=f"C{i}" not in failed)
              for i in range(1, 4)]
    verified = sum(c.verified for c in checks)
    return AgentVerification(agent=agent, round=1, checks=checks, checkable=3, verified=verified,
                             denominator=3, faithfulness=verified / 3)


def transcript(rounds_started=3, charlie_stance="bullish"):
    t = Transcript("AAPL")
    t.round1 = {"charlie": out("charlie", charlie_stance), "delta": out("delta")}
    t.rounds_started = rounds_started
    return t


INT = CFG.integrity


# ---------------------------------------------------------------- I: integrity (C5)

def test_i1_all_critiques_answered():
    t = transcript()
    t.round2 = {"delta": critique("delta", "charlie.C1")}
    t.round3 = {"charlie": final("charlie", "bullish", changes=[
        {"claim": "C1", "action": "kept", "reason": "data", "in_response_to": ["delta.K1"]}])}
    i = agent_integrity("charlie", t, verification("charlie"), INT)
    assert (i.received, i.answered, i.integrity) == (1, 1, 1.0)


def test_i2_reasoned_stance_change_costs_nothing():
    t = transcript(charlie_stance="bullish")
    t.round2 = {"delta": critique("delta", "charlie.C1")}
    t.round3 = {"charlie": final("charlie", "bearish", from_="bullish", reason="delta's news evidence",
                                 changes=[{"claim": "C1", "action": "revised", "reason": "r",
                                           "in_response_to": ["delta.K1"]}],
                                 changed_by=[{"ref": "delta.K1", "reason": "convincing"}])}
    i = agent_integrity("charlie", t, verification("charlie"), INT)
    assert i.integrity == 1.0 and not i.unexplained_stance_change


def test_i3_critiques_received_but_no_round3():
    t = transcript()
    t.round2 = {"delta": critique("delta", "charlie.C1", "charlie.C2")}
    i = agent_integrity("charlie", t, verification("charlie"), INT)
    assert (i.received, i.answered, i.integrity) == (2, 0, 0.0)


def test_i4_failed_claims_one_withdrawn_one_kept():
    t = transcript()
    t.round3 = {"charlie": final("charlie", "bullish", changes=[
        {"claim": "C1", "action": "withdrawn", "reason": "wrong", "in_response_to": []},
        {"claim": "C2", "action": "kept", "reason": "still think so", "in_response_to": []}])}
    i = agent_integrity("charlie", t, verification("charlie", failed=("C1", "C2")), INT)
    assert (i.failed_round1_claims, i.retained_failed) == (2, 1)
    assert i.integrity == pytest.approx(1 - 0.5 * 0.5)


def test_i5_one_round_debate():
    t = transcript(rounds_started=1)
    t.round2 = {"delta": critique("delta", "charlie.C1")}
    assert agent_integrity("charlie", t, verification("charlie", failed=("C1",)), INT).integrity == 1.0


def test_i6_unexplained_stance_change_is_penalised():
    t = transcript(charlie_stance="bullish")
    t.round3 = {"charlie": final("charlie", "bearish", from_="bullish", reason="")}
    i = agent_integrity("charlie", t, verification("charlie"), INT)
    assert i.unexplained_stance_change and i.integrity == pytest.approx(0.75)
    assert overall_integrity([i]) == pytest.approx(0.75)


# ---------------------------------------------------------------- S / G: verdict (C6, C7)

def outputs(*stances, confidence=80):
    names = ["charlie", "delta", "gamma"]
    return {names[i]: out(names[i], s, confidence) for i, s in enumerate(stances)}


def verdict(outs, faith=None, integrity=1.0, mos=0.30, vol=0.25, persona="balanced"):
    faith = faith or {a: 1.0 for a in outs}
    return decide_verdict(outs, faith, integrity, mos, vol, CFG.persona(persona), CFG)


def test_s1_score_is_mean_of_weighted_stances():
    v = verdict(outputs("bullish", "bullish", "bullish"))
    assert v.score == pytest.approx(0.80)


def test_s2_faithfulness_weights_and_missing_agent():
    outs = {"charlie": out("charlie", "bullish", 90), "delta": out("delta", "bullish", 60)}
    v = verdict(outs, faith={"charlie": 1.0, "delta": 0.8})
    assert v.score == pytest.approx(0.69)
    assert v.recommendation == "Buy"
    assert v.confidence == pytest.approx(46.0)
    assert v.agents_missing == ["gamma"]


def test_s3_bulls_and_bears_cancel_to_hold_without_confidence():
    v = verdict({"charlie": out("charlie", "bullish"), "delta": out("delta", "bearish")})
    assert (v.score, v.recommendation, v.confidence) == (0.0, "Hold", None)


@pytest.mark.parametrize("case, stances, conf, mos, vol, expected", [
    ("G1", "bullish", 80, 0.30, 0.25, {"zen": "Buy", "balanced": "Buy", "alpha": "Buy"}),
    ("G2", "bullish", 80, 0.10, 0.25, {"zen": "Hold", "balanced": "Hold", "alpha": "Buy"}),
    ("G3", "bullish", 80, 0.30, 0.40, {"zen": "Hold", "balanced": "Buy", "alpha": "Buy"}),
    ("G4", "bullish", 80, -1.40, 0.25, {"zen": "Hold", "balanced": "Hold", "alpha": "Hold"}),
    ("G5", "bullish", 80, None, 0.25, {"zen": "Hold", "balanced": "Hold", "alpha": "Buy"}),
    ("G6", "bearish", 25, 0.30, 0.25, {"zen": "Sell", "balanced": "Hold", "alpha": "Hold"}),
    ("G7", "bullish", 40, 0.30, 0.25, {"zen": "Hold", "balanced": "Buy", "alpha": "Buy"}),
])
def test_g_persona_thresholds(case, stances, conf, mos, vol, expected):
    outs = outputs(stances, stances, stances, confidence=conf)
    got = {p: verdict(outs, mos=mos, vol=vol, persona=p) for p in PERSONAS}
    assert {p: v.recommendation for p, v in got.items()} == expected, case
    # scores never depend on the persona
    assert len({v.score for v in got.values()}) == 1
    for p, v in got.items():
        assert (v.confidence is None) == (v.recommendation == "Hold")
        if v.recommendation == "Hold" and v.score >= CFG.persona(p).buy_score:
            assert any(w.startswith("Buy blocked:") for w in v.warnings), (case, p)


def test_g1_confidence_value():
    v = verdict(outputs("bullish", "bullish", "bullish"), persona="zen")
    assert v.confidence == pytest.approx(80.0)


def test_g2_names_the_blocking_gate():
    v = verdict(outputs("bullish", "bullish", "bullish"), mos=0.10, persona="zen")
    gate = next(g for g in v.gates if g.name == "margin_of_safety")
    assert not gate.passed and "+0.10 < +0.25" in gate.note


def test_g5_alpha_buy_without_dcf_warns():
    v = verdict(outputs("bullish", "bullish", "bullish"), mos=None, persona="alpha")
    assert v.recommendation == "Buy"
    assert any("no DCF" in w for w in v.warnings)


def test_g8_one_valid_agent_is_no_verdict():
    v = verdict({"charlie": out("charlie", "bullish")})
    assert v.recommendation == "No Verdict" and v.confidence is None and v.reason


def test_g9_low_faithfulness_agent_excluded():
    outs = outputs("bearish", "bullish", "bullish")
    v = verdict(outs, faith={"charlie": 0.5, "delta": 1.0, "gamma": 1.0})
    assert v.agents_valid == ["delta", "gamma"] and v.agents_missing == ["charlie"]
    assert v.score == pytest.approx(0.80)                       # charlie's bearish vote not counted
    assert any("charlie excluded" in w for w in v.warnings)


def test_integrity_scales_confidence():
    v = verdict(outputs("bullish", "bullish", "bullish"), integrity=0.5)
    assert v.confidence == pytest.approx(40.0)


# ---------------------------------------------------------------- R: risks and supporting points

def test_r1_risks_deduplicated_and_prefixed():
    outs = {"charlie": out("charlie", risks=("China demand", "Regulation")),
            "delta": out("delta", risks=("china  DEMAND", "Lawsuit"))}
    assert collect_key_risks(outs) == ["charlie: China demand", "charlie: Regulation", "delta: Lawsuit"]


def test_supporting_points_are_verified_claims_aligned_with_verdict():
    outs = {"charlie": out("charlie", "bullish"), "delta": out("delta", "bearish", 10),
            "gamma": out("gamma", "bullish")}
    v = verdict(outs)
    assert v.recommendation == "Buy"                            # S = (0.8 - 0.1 + 0.8) / 3 = 0.5
    ver = {"charlie": verification("charlie", failed=("C2",)), "delta": verification("delta"),
           "gamma": verification("gamma")}
    points = collect_supporting_points(outs, ver, v)
    assert "charlie C2: t2" not in points                       # failed verification
    assert not any(p.startswith("delta") for p in points)       # bearish, verdict leans bullish
    assert "charlie C1: t1" in points
