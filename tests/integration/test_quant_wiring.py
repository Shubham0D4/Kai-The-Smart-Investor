"""
Phase 4.5 wiring: the coordinator builds the quant report once before round 1, Gamma cites it,
the verifier resolves `quant:` references against it, Sigma receives it as structured input,
contradictions lower confidence, and the alpha packet carries it (signed).

On the AAPL fixtures the report says: P(fair value > price) = 0.06 (Monte Carlo DCF),
P(up in 63 days) = 0.51 (bootstrap, trend removed), base rate and calibration unavailable.
"""

import json
import os

import pytest

from agents.schema import AgentOutput, EvidenceRecord
from coordinator.config import load_thresholds
from coordinator.llm_client import FakeLLMClient
from coordinator.server import DebateCoordinator
from judge.sigma_agent import SigmaAgent
from judge.verifier import verify_output
from quant.models import QuantReport

CFG = load_thresholds()


@pytest.fixture
def debate(make_responder):
    files = []

    def run(persona="zen", rounds=3, **kw):
        llm = FakeLLMClient(responses=make_responder(**kw))
        coord = DebateCoordinator("AAPL", persona, llm_client=llm)
        result = coord.run_debate(rounds=rounds)
        files.append(result["log_file"])
        return coord, result, llm
    yield run
    for f in files:
        if os.path.exists(f):
            os.remove(f)


@pytest.fixture
def margin_ok(monkeypatch):
    """Pretend Gamma's point DCF showed a 30% margin of safety so a Buy can pass the gates;
    volatility still comes from the quant report."""
    real = SigmaAgent.gate_inputs

    def gate_inputs(t, r=None):
        return 0.30, real(t, r)[1]
    monkeypatch.setattr(SigmaAgent, "gate_inputs", staticmethod(gate_inputs))


def sigma():
    return SigmaAgent(llm_client=FakeLLMClient(responses="Narrative."))


# ---------------------------------------------------------------- build once, before round 1

def test_report_built_once_before_round_1_and_logged_separately(debate, monkeypatch):
    import coordinator.server as server
    calls = []
    real = server.build_quant_report

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(server, "build_quant_report", counting)

    coord, result, llm = debate()
    assert len(calls) == 1
    with open(result["log_file"]) as f:
        log = json.load(f)
    assert "quant_report" in log and "quant_report" not in log["transcript"]
    assert QuantReport.model_validate(log["quant_report"]) == coord.quant_report
    gamma_prompt = next(p for p in llm.calls if '"agent": "gamma", "round": 1' in p and "ROUND" not in p)
    assert "[quant:mc_dcf.prob_fair_above_price]" in gamma_prompt
    assert "quant:base_rate.share_up: insufficient data" in gamma_prompt     # listed, not citable
    assert "[quant:base_rate.share_up]" not in gamma_prompt


def test_reports_are_reproducible_across_debates(debate):
    a, _, _ = debate(rounds=1)
    b, _, _ = debate(rounds=1)
    assert a.quant_report.model_dump() == b.quant_report.model_dump()


# ---------------------------------------------------------------- Gamma citing the report

def _gamma_claim(field, value, unit="ratio"):
    return {"id": "C1", "kind": "numeric", "text": f"Report field {field}", "value": value, "unit": unit,
            "op": "equals", "evidence": [field]}


def _verify_gamma(coord, *claims):
    out = AgentOutput.model_validate({"agent": "gamma", "round": 1, "stance": "neutral", "confidence": 50,
                                      "claims": list(claims)})
    v = verify_output(out, coord.transcript.evidence["gamma"], CFG.verification, coord.quant_report)
    return {c.claim_id: c for c in v.checks}


def test_gamma_claim_with_number_not_in_report_fails(debate):
    coord, _, _ = debate(rounds=1)
    true_p = coord.quant_report.value("quant:mc_dcf.prob_fair_above_price")
    checks = _verify_gamma(coord, _gamma_claim("quant:mc_dcf.prob_fair_above_price", 0.80),
                           {**_gamma_claim("quant:mc_dcf.prob_fair_above_price", true_p), "id": "C2"})
    assert checks["C1"].reason == "value_mismatch" and checks["C1"].expected == pytest.approx(true_p)
    assert checks["C2"].verified


def test_gamma_cannot_cite_unavailable_or_invented_quant_fields(debate):
    coord, _, _ = debate(rounds=1)
    checks = _verify_gamma(coord, _gamma_claim("quant:base_rate.share_up", 0.6),
                           {**_gamma_claim("quant:forward.prob_up_magic", 0.6), "id": "C2"})
    assert checks["C1"].reason == "no_value"            # unavailable: no number to cite
    assert checks["C2"].reason == "unknown_evidence"    # not in the report at all


def test_verifier_trusts_the_report_not_gammas_copy(debate):
    coord, _, _ = debate(rounds=1)
    ev = coord.transcript.evidence["gamma"]
    tampered = [EvidenceRecord(**{**e.model_dump(), "value": 0.80}) if e.id == "quant:mc_dcf.prob_fair_above_price"
                else e for e in ev]
    out = AgentOutput.model_validate({"agent": "gamma", "round": 1, "stance": "neutral", "confidence": 50,
                                      "claims": [_gamma_claim("quant:mc_dcf.prob_fair_above_price", 0.80)]})
    assert verify_output(out, tampered, CFG.verification).checks[0].verified            # without the report
    assert not verify_output(out, tampered, CFG.verification, coord.quant_report).checks[0].verified


def test_debate_with_false_quant_claim_lowers_gammas_faithfulness(debate):
    def gamma_says_80pct(prompt, reply):
        if '"agent": "gamma", "round": 1' not in prompt or "ROUND" in prompt:
            return reply
        data = json.loads(reply)
        data["claims"][0] = _gamma_claim("quant:mc_dcf.prob_fair_above_price", 0.80)
        return json.dumps(data)
    coord, _, _ = debate(round1_hook=gamma_says_80pct)
    v = coord.transcript.verification["gamma"]
    assert v.checks[0].reason == "value_mismatch"
    assert v.faithfulness == pytest.approx(2 / 3, abs=1e-6)


# ---------------------------------------------------------------- Sigma, contradictions, confidence

def test_bullish_agents_contradict_report_flag_and_lower_confidence(debate, margin_ok):
    _, result, _ = debate(stance="bullish", confidence=80)
    ev = sigma().evaluate_debate(result["log_file"], persona="zen")
    fr = ev["final_recommendation"]

    assert fr["recommendation"] == "Buy"                        # the report never changes the verdict
    flagged = {(c["agent"], c["field"]) for c in fr["contradictions"]}
    assert flagged == {(a, "quant:mc_dcf.prob_fair_above_price") for a in ("charlie", "delta", "gamma")}
    assert any(w.startswith("contradiction: charlie is bullish") for w in fr["warnings"])

    p_fair = fr["quant_signals"]["quant:mc_dcf.prob_fair_above_price"]
    p_up = fr["quant_signals"]["quant:forward.prob_up_bootstrap"]
    q = 1 - 0.5 * (max(-(2 * (p_fair - 0.5)), 0) + max(-(2 * (p_up - 0.5)), 0)) / 2
    assert fr["quant_agreement"] == pytest.approx(q, abs=1e-6)
    assert fr["contradiction_factor"] == pytest.approx(0.5)     # all three valid agents contradict
    assert fr["confidence_before_quant"] == pytest.approx(80.0)
    assert fr["confidence"] == pytest.approx(80.0 * q * 0.5, abs=0.01)
    assert fr["confidence"] < 60 <= fr["confidence_before_quant"]   # drops below the approval line


def test_bearish_agents_agree_with_report_no_contradiction(debate):
    _, result, _ = debate(stance="bearish", confidence=80)
    fr = sigma().evaluate_debate(result["log_file"], persona="zen")["final_recommendation"]
    assert fr["recommendation"] == "Sell"
    assert fr["contradictions"] == [] and fr["contradiction_factor"] == 1.0
    assert fr["quant_agreement"] > 0.95                          # only the ~0.51 forward signal leans against


def test_hold_still_flags_contradictions_without_confidence(debate):
    _, result, _ = debate(stance="bullish", confidence=80)      # AAPL's real DCF margin blocks Buy
    fr = sigma().evaluate_debate(result["log_file"], persona="zen")["final_recommendation"]
    assert fr["recommendation"] == "Hold" and fr["confidence"] is None
    assert len(fr["contradictions"]) == 3 and fr["quant_agreement"] is None


def test_sigma_uses_the_report_it_is_given(debate):
    coord, result, _ = debate()
    custom = coord.quant_report.model_copy(deep=True)
    custom.fields["quant:returns.vol_annual"] = custom.fields["quant:returns.vol_annual"].model_copy(
        update={"value": 0.99})
    ev = sigma().evaluate_debate(result["log_file"], persona="zen", quant_report=custom)
    vol_gate = next(g for g in ev["final_recommendation"]["gates"] if g["name"] == "volatility")
    assert vol_gate["value"] == 0.99
    assert ev["quant_report"]["fields"]["quant:returns.vol_annual"]["value"] == 0.99
    # without an explicit report, Sigma reads the one stored in the log
    ev2 = sigma().evaluate_debate(result["log_file"], persona="zen")
    assert ev2["quant_report"] == coord.quant_report.model_dump(mode="json")


def test_bad_calibration_drops_forward_signal(debate, margin_ok, monkeypatch):
    import coordinator.server as server
    real = server.build_quant_report
    bad_pairs = [(0.9, 0)] * 20 + [(0.1, 1)] * 20                # Brier 0.81: worse than a coin flip
    monkeypatch.setattr(server, "build_quant_report",
                        lambda *a, **k: real(*a, **k, calibration_pairs=bad_pairs))
    _, result, _ = debate(stance="bullish", confidence=80)
    fr = sigma().evaluate_debate(result["log_file"], persona="zen")["final_recommendation"]
    assert "quant:forward.prob_up_bootstrap" not in fr["quant_signals"]
    assert any("forward probability ignored" in w for w in fr["warnings"])


# ---------------------------------------------------------------- alpha packet

def test_alpha_packet_carries_signed_report(debate):
    from judge.ap2_handler import AP2Handler
    _, result, _ = debate()
    ev = sigma().evaluate_debate(result["log_file"], persona="zen")
    ap2 = AP2Handler()
    mandate = ap2.create_intent_mandate("u1", "AAPL", "zen")
    packet = ap2.generate_alpha_packet(mandate, ev, result["log_file"])
    assert packet["quant_report"]["fields"]["quant:market.beta"]["value"] == pytest.approx(1.07136, abs=1e-4)
    assert ap2.verify_alpha_packet(packet)
    tampered = json.loads(json.dumps(packet))
    tampered["quant_report"]["fields"]["quant:mc_dcf.prob_fair_above_price"]["value"] = 0.9
    assert not ap2.verify_alpha_packet(tampered)
