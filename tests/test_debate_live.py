"""
End-to-end live debates (tests/conftest.py RUNS): real tickers, real data, real models, through
the gateway API exactly as the dashboard calls it. Each test re-derives a property of the result
independently (from the evidence, the config and the raw debate log) instead of trusting Kai's
own numbers.
"""

import pytest

from agents.schema import AgentOutput, EvidenceRecord, FinalOutput
from coordinator.config import load_thresholds
from coordinator.llm_config import load_llm_config
from coordinator.transcript import Transcript
from judge.verifier import extract_quantities, verify_output
from quant.models import QuantReport

from .conftest import RUNS

TICKERS = [t for t, _ in RUNS]
CFG = load_thresholds()
LLM = load_llm_config()


def body(runs, t):
    b = runs[t]["status"]
    assert b is not None, f"{t}: no status"
    return b


def debate(runs, t):
    d = body(runs, t).get("debate")
    assert d, f"{t}: no debate in status ({body(runs, t).get('status')}: {body(runs, t).get('error')})"
    return d


def final(runs, t):
    s = body(runs, t).get("sigma")
    assert s, f"{t}: no verdict ({body(runs, t).get('status')}: {body(runs, t).get('error')})"
    return s["final_recommendation"]


@pytest.mark.parametrize("ticker", TICKERS)
def test_analysis_finishes_with_a_verdict_or_a_stated_reason(runs, ticker, record):
    """The analysis ends as completed or waiting for approval, with Buy/Hold/Sell or a reasoned No Verdict."""
    b = body(runs, ticker)
    assert b["status"] in ("completed", "waiting_approval"), (b["status"], b.get("error"))
    fr = final(runs, ticker)
    assert fr["recommendation"] in ("Buy", "Hold", "Sell", "No Verdict")
    if fr["recommendation"] == "No Verdict":
        assert fr["reason"]
    d = debate(runs, ticker)
    record("runs", ticker, {
        "persona": runs[ticker]["persona"], "status": b["status"], "elapsed_s": runs[ticker]["elapsed_s"],
        "recommendation": fr["recommendation"], "confidence": fr["confidence"], "score": fr["score"],
        "hold_conviction": fr.get("hold_conviction"), "coverage": fr.get("coverage"),
        "faithfulness": body(runs, ticker)["sigma"]["evaluation"]["faithfulness_score"],
        "integrity": body(runs, ticker)["sigma"]["evaluation"]["integrity_score"],
        "outcome": d.get("outcome"), "snapshot": d.get("market_snapshot"),
        "agents": {a: {"state": v["state"], "error": v.get("error"), "dropped": v.get("dropped_reason"),
                       "stance_r1": (v.get("round1") or {}).get("stance"),
                       "conf_r1": (v.get("round1") or {}).get("confidence"),
                       "stance_r3": (v.get("round3") or {}).get("stance"),
                       "conf_r3": (v.get("round3") or {}).get("confidence"),
                       "faithfulness_r1": (v.get("verification") or {}).get("faithfulness"),
                       "faithfulness_final": (v.get("final_verification") or {}).get("faithfulness"),
                       "claims_r1": len((v.get("round1") or {}).get("claims", [])),
                       "critiques_r2": len((v.get("round2") or {}).get("critiques", []))}
                   for a, v in d["agents"].items()},
        "calls": d["llm_calls"], "token_budget": d.get("token_budget"), "total_tokens": d["total_tokens"],
        "token_limit": d["token_limit"], "suggestions": fr.get("suggestions", []),
        "gates": fr["gates"], "warnings": fr["warnings"], "analysis": fr["analysis"],
        "verdicts_by_persona": {p: {"recommendation": v["recommendation"], "confidence": v["confidence"]}
                                for p, v in body(runs, ticker)["sigma"]["verdicts_by_persona"].items()},
    })


@pytest.mark.parametrize("ticker", TICKERS)
def test_tokens_stay_within_the_limit_and_each_agents_share(runs, ticker):
    """Total tokens never exceed the limit; the trace adds up; no agent spends more than its share plus the reserve."""
    d = debate(runs, ticker)
    assert d["total_tokens"] <= d["token_limit"] == CFG.circuit_breaker.token_limit
    assert sum(c["total_tokens"] for c in d["llm_calls"]) == d["total_tokens"]
    b = d["token_budget"]
    for agent, v in b["agents"].items():
        assert v["used"] <= v["share"] + b["reserve"], (agent, v)


@pytest.mark.parametrize("ticker", TICKERS)
def test_all_four_agents_ran_on_their_configured_models(runs, ticker):
    """Charlie, Delta, Gamma and Vega each ran round 1 (or failed with an explicit reason), each on its own model chain."""
    d = debate(runs, ticker)
    assert set(d["agents"]) == {"charlie", "delta", "gamma", "vega"}
    for a, v in d["agents"].items():
        assert v["round1"] is not None or v["error"], a
    allowed = {a: {s.label for s in LLM.chain(a)} for a in d["agents"]}
    for c in d["llm_calls"]:
        assert set(c["models"]) <= allowed[c["agent"]], c


@pytest.mark.parametrize("ticker", TICKERS)
def test_every_agent_used_the_same_market_snapshot(runs, ticker):
    """Every agent's price is the snapshot price, and P/E = snapshot price / TTM EPS."""
    d = debate(runs, ticker)
    snap = d["market_snapshot"]
    assert snap and snap["fetched_at"] and snap["price"] > 0
    for a, v in d["agents"].items():
        price = next((e["value"] for e in v["evidence"] if e["id"] == "mkt:price"), None)
        if price is not None:
            assert price == pytest.approx(snap["price"], rel=1e-4), a
    gamma = {e["id"]: e for e in d["agents"]["gamma"]["evidence"]}
    if "calc:pe" in gamma and snap.get("eps_ttm"):
        assert "sec:eps_diluted:TTM" in gamma["calc:pe"]["source"]
        assert gamma["calc:pe"]["value"] == pytest.approx(snap["price"] / snap["eps_ttm"], abs=0.01)


@pytest.mark.parametrize("ticker", TICKERS)
def test_fact_check_reproduces_independently(runs, ticker, record):
    """Re-running the verifier on every agent's round-1 claims gives exactly the stored fact-check."""
    d, log = debate(runs, ticker), runs[ticker]["log"]
    report = QuantReport.model_validate(log["quant_report"]) if log and log.get("quant_report") else None
    counts = {}
    for a, v in d["agents"].items():
        if not v["round1"] or not v["verification"]:
            continue
        ev = [EvidenceRecord(**e) for e in v["evidence"]]
        again = verify_output(AgentOutput.model_validate(v["round1"]), ev, CFG.verification, report)
        assert again.faithfulness == pytest.approx(v["verification"]["faithfulness"]), a
        assert [c.verified for c in again.checks] == [c["verified"] for c in v["verification"]["checks"]], a
        counts[a] = {"checkable": again.checkable, "verified": again.verified,
                     "failed": [f"{c.claim_id}: {c.reason}" for c in again.checks if not c.verified]}
    record("fact_check", ticker, counts)


@pytest.mark.parametrize("ticker", TICKERS)
def test_agents_stay_in_their_lane(runs, ticker, record):
    """A stance change in round 3 rests on a dispute of the agent's own claims or on its own failed claims."""
    d = debate(runs, ticker)
    t = Transcript.from_dict(runs[ticker]["log"]["transcript"])
    changes = {}
    for a, v in d["agents"].items():
        r1, r3 = v.get("round1"), v.get("round3")
        if not (r1 and r3) or r1["stance"] == r3["stance"]:
            continue
        grounds = t.stance_grounds(a)
        cited = {x["ref"] for x in r3["changed_by"]}
        assert grounds & cited or t.failed_claims(a), (a, cited, grounds)
        changes[a] = {"from": r1["stance"], "to": r3["stance"], "because": sorted(cited),
                      "reason": r3["stance_change"]["reason"]}
    record("lane_rule", ticker, changes or "no stance changes")


@pytest.mark.parametrize("ticker", TICKERS)
def test_no_accepted_dispute_compares_different_return_windows(runs, ticker):
    """Round-2 disputes never pit a return over one window against a return over another."""
    t = Transcript.from_dict(runs[ticker]["log"]["transcript"])
    for agent, out in t.round2.items():
        for k in out.critiques:
            if k.verdict != "dispute":
                continue
            theirs = t._claim_windows(k.target)
            mine = {w for e in k.evidence for w in [t.return_window(e)] if w}
            assert not (theirs and mine and not theirs & mine), (agent, k.id, theirs, mine)


@pytest.mark.parametrize("ticker", TICKERS)
def test_verdict_and_confidence_recompute_from_the_scores(runs, ticker):
    """S is the judge-weighted mean of agent scores; confidence follows the published formula, Hold included."""
    s = body(runs, ticker)["sigma"]
    fr = final(runs, ticker)
    if fr["recommendation"] == "No Verdict":
        assert fr["confidence"] is None
        return
    v = s["verdicts_by_persona"][s["persona"]]
    w = CFG.judge.weights
    valid = v["agents_valid"]
    S = sum(w[a] * v["agent_scores"][a] for a in valid) / sum(w[a] for a in valid)
    assert fr["score"] == pytest.approx(S, abs=1e-5)
    coverage = sum(w[a] for a in valid) / sum(w.values())
    integrity = s["evaluation"]["integrity_score"] / 100
    assert fr["confidence"] is not None
    if fr["recommendation"] == "Hold":
        expected = 100 * fr["hold_conviction"] * integrity * coverage
        assert fr["confidence"] == pytest.approx(expected, abs=0.02)
    else:
        base = 100 * abs(S) * integrity * coverage
        assert fr["confidence_before_quant"] == pytest.approx(base, abs=0.02)
        assert fr["confidence"] == pytest.approx(base * fr["quant_agreement"] * fr["contradiction_factor"], abs=0.02)


@pytest.mark.parametrize("ticker", TICKERS)
def test_suggestions_numbers_come_from_the_evidence(runs, ticker):
    """Each playbook number that has a source is recomputed from that source (entry price, earnings days)."""
    fr = final(runs, ticker)
    items = fr.get("suggestions", [])
    assert items and items[0]["kind"] == "action"
    d = debate(runs, ticker)
    ev = {e["id"]: e["value"] for v in d["agents"].values() for e in v["evidence"] if e.get("value") is not None}
    persona = CFG.persona(body(runs, ticker)["sigma"]["persona"])
    if fr["recommendation"] not in ("Buy", "No Verdict") and ev.get("calc:dcf.value_per_share", 0) > 0:
        entry = ev["calc:dcf.value_per_share"] * (1 - persona.min_margin_of_safety)
        trig = next(x for x in items if x["title"] == "Would become Buy if")
        assert f"${entry:,.2f}" in trig["detail"]
    if "cat:days_to_earnings" in ev and ev["cat:days_to_earnings"] >= 0:
        earn = next(x for x in items if x["title"] == "Earnings ahead")
        assert f"{ev['cat:days_to_earnings']:.0f} days" in earn["detail"]
    for x in items:
        assert set(x["sources"]) <= set(ev) | {s for s in x["sources"] if s.startswith("quant:")}, x


@pytest.mark.parametrize("ticker", TICKERS)
def test_explanation_names_the_computed_verdict(runs, ticker, record):
    """Sigma's explanation states the computed recommendation; numbers it uses that are not in the material are counted."""
    fr = final(runs, ticker)
    if fr["recommendation"] == "No Verdict":
        return
    text = fr["analysis"]
    assert fr["recommendation"].lower() in text.lower()
    d = debate(runs, ticker)
    allowed = [e["value"] for v in d["agents"].values() for e in v["evidence"] if e.get("value") is not None]
    allowed += [fr["score"], fr["confidence"] or 0] + [g["value"] for g in fr["gates"] if g["value"] is not None]
    allowed += [g["threshold"] for g in fr["gates"] if g["threshold"] is not None]
    for x in fr.get("suggestions", []):
        allowed += [q.value for q in extract_quantities(x["detail"])]
    log = runs[ticker]["log"]
    allowed += [f["value"] for f in (log.get("quant_report") or {}).get("fields", {}).values() if f.get("value") is not None]
    untraced = []
    for q in extract_quantities(text):
        if not any(abs(abs(q.value) - abs(a)) <= max(q.half_unit, 0.01 * abs(a), 1e-9) for a in allowed):
            untraced.append(q.text)
    record("narrative", ticker, {"words": len(text.split()), "untraced_numbers": untraced})


@pytest.mark.parametrize("ticker", TICKERS)
def test_signed_packet_verifies(runs, ticker):
    """The alpha packet's Ed25519 signature verifies and it carries the verdict and suggestions."""
    from judge.ap2_handler import AP2Handler
    b = body(runs, ticker)
    packet = b.get("alpha_packet")
    assert packet, b.get("status")
    assert AP2Handler().verify_alpha_packet(packet)
    assert packet["recommendation"] == final(runs, ticker)["recommendation"]
    assert packet["verdict_detail"]["suggestions"] == final(runs, ticker)["suggestions"]
