"""
Debate integrity (FIXES.md C5) and the computed verdict (C6). PHASE4_PLAN.md §5-§6.

Everything here is deterministic. Persona enters only as a row of thresholds (C7).

  agent score  s_a = sign(stance) x confidence/100 x faithfulness_a
  debate score S   = mean(s_a) over valid agents
  Buy   if S >= buy_score and every Buy gate passes (margin of safety, volatility)
  Sell  if S <= sell_score
  Hold  otherwise
  confidence (Buy/Sell only) = 100 x |S| x integrity x (valid agents / 3)
"""

from typing import TYPE_CHECKING, Dict, List, Literal, Optional

from pydantic import BaseModel

from agents.schema import AgentOutput
from coordinator.config import IntegrityConfig, PersonaThresholds, Thresholds
from judge.verifier import AgentVerification

if TYPE_CHECKING:
    from coordinator.transcript import Transcript

ALL_AGENTS = ["charlie", "delta", "gamma"]
STANCE_SIGN = {"bullish": 1, "neutral": 0, "bearish": -1}
DIRECTION = {"Buy": "bullish", "Sell": "bearish", "Hold": "neutral"}


# --------------------------------------------------------------------------- C5: integrity

class AgentIntegrity(BaseModel):
    agent: str
    received: int
    answered: int
    failed_round1_claims: int
    retained_failed: int
    unexplained_stance_change: bool
    integrity: float                 # 0..1


def agent_integrity(agent: str, transcript: "Transcript", round1_checks: Optional[AgentVerification],
                    cfg: IntegrityConfig) -> AgentIntegrity:
    """
    integrity = clamp(answered_ratio - unexplained_penalty x unexplained
                      - retained_penalty x retained_ratio, 0, 1)

    answered_ratio: critiques of this agent's claims answered in its round-3 `changes`
                    (1 if none received; 0 if some received and no valid round 3)
    retained_ratio: round-1 claims that failed verification and were not revised or withdrawn
    unexplained:    final stance differs from round 1 without a reason and a changed_by entry
    A debate that never reached round 3 has nothing to answer: integrity 1.
    """
    received = [ref for ref, _, _ in transcript.critiques_of(agent)]
    failed = [c.claim_id for c in round1_checks.checks if not c.verified] if round1_checks else []
    if transcript.rounds_started < 3:
        return AgentIntegrity(agent=agent, received=len(received), answered=0,
                              failed_round1_claims=len(failed), retained_failed=0,
                              unexplained_stance_change=False, integrity=1.0)

    final = transcript.round3.get(agent)
    if final is None:
        answered = 0
        retained = len(failed)
        unexplained = False
    else:
        answered_refs = {r for ch in final.changes for r in ch.in_response_to}
        answered = len(set(received) & answered_refs)
        fixed = {ch.claim for ch in final.changes if ch.action in ("revised", "withdrawn")}
        retained = len([c for c in failed if c not in fixed])
        r1 = transcript.round1[agent]
        sc = final.stance_change
        unexplained = final.stance != r1.stance and (not sc.reason.strip() or not final.changed_by)

    answered_ratio = answered / len(received) if received else 1.0
    retained_ratio = retained / len(failed) if failed else 0.0
    value = (answered_ratio
             - cfg.unexplained_stance_penalty * unexplained
             - cfg.retained_failed_claim_penalty * retained_ratio)
    return AgentIntegrity(agent=agent, received=len(received), answered=answered,
                          failed_round1_claims=len(failed), retained_failed=retained,
                          unexplained_stance_change=unexplained,
                          integrity=round(min(max(value, 0.0), 1.0), 6))


def overall_integrity(items: List[AgentIntegrity]) -> float:
    return round(sum(i.integrity for i in items) / len(items), 6) if items else 0.0


# --------------------------------------------------------------------------- C6: verdict

class Gate(BaseModel):
    name: str
    value: Optional[float]
    threshold: Optional[float]
    passed: bool
    note: str


class Verdict(BaseModel):
    recommendation: Literal["Buy", "Hold", "Sell", "No Verdict"]
    confidence: Optional[float]      # None for Hold and No Verdict
    score: float                     # S
    agent_scores: Dict[str, float]
    gates: List[Gate]
    agents_valid: List[str]
    agents_missing: List[str]
    warnings: List[str]
    reason: Optional[str] = None     # set for No Verdict


def agent_score(output: AgentOutput, faithfulness: float) -> float:
    return STANCE_SIGN[output.stance] * output.confidence / 100 * faithfulness


def no_verdict(reason: str, agents_valid: List[str] = (), warnings: List[str] = (),
               agent_scores: Optional[Dict[str, float]] = None) -> Verdict:
    valid = list(agents_valid)
    return Verdict(recommendation="No Verdict", confidence=None, score=0.0,
                   agent_scores=agent_scores or {}, gates=[], agents_valid=valid,
                   agents_missing=[a for a in ALL_AGENTS if a not in valid],
                   warnings=list(warnings), reason=reason)


def decide_verdict(outputs: Dict[str, AgentOutput], faithfulness: Dict[str, float], integrity: float,
                   margin_of_safety: Optional[float], volatility: Optional[float],
                   persona: PersonaThresholds, cfg: Thresholds, total_agents: int = 3,
                   warnings: Optional[List[str]] = None) -> Verdict:
    warnings = list(warnings or [])
    min_f = cfg.circuit_breaker.min_agent_faithfulness
    scores, valid = {}, []
    for agent, out in outputs.items():
        f = faithfulness.get(agent, 0.0)
        scores[agent] = round(agent_score(out, f), 6)
        if f >= min_f:
            valid.append(agent)
        else:
            warnings.append(f"{agent} excluded: faithfulness {f:.2f} < {min_f:.2f}")
    valid = [a for a in ALL_AGENTS if a in valid]

    if len(valid) < cfg.circuit_breaker.min_valid_agents:
        return no_verdict(f"only {len(valid)} valid agent(s); at least "
                          f"{cfg.circuit_breaker.min_valid_agents} needed", valid, warnings, scores)

    S = round(sum(scores[a] for a in valid) / len(valid), 6)

    gates = [Gate(name="buy_score", value=S, threshold=persona.buy_score, passed=S >= persona.buy_score,
                  note=f"S {S:+.2f} {'>=' if S >= persona.buy_score else '<'} buy threshold {persona.buy_score:+.2f}")]
    if margin_of_safety is None:
        if persona.buy_requires_dcf:
            gates.append(Gate(name="margin_of_safety", value=None, threshold=persona.min_margin_of_safety,
                              passed=False, note="no DCF available: Buy requires a margin of safety"))
        else:
            gates.append(Gate(name="margin_of_safety", value=None, threshold=persona.min_margin_of_safety,
                              passed=True, note="skipped: no DCF available"))
            if S >= persona.buy_score:
                warnings.append("no DCF available: Buy allowed without a margin-of-safety check")
    else:
        ok = margin_of_safety >= persona.min_margin_of_safety
        gates.append(Gate(name="margin_of_safety", value=margin_of_safety, threshold=persona.min_margin_of_safety,
                          passed=ok, note=f"margin of safety {margin_of_safety:+.2f} "
                                          f"{'>=' if ok else '<'} {persona.min_margin_of_safety:+.2f}"))
    if persona.max_volatility is None:
        gates.append(Gate(name="volatility", value=volatility, threshold=None, passed=True,
                          note="no volatility limit for this persona"))
    elif volatility is None:
        gates.append(Gate(name="volatility", value=None, threshold=persona.max_volatility, passed=False,
                          note="no volatility data: Buy requires it for this persona"))
    else:
        ok = volatility <= persona.max_volatility
        gates.append(Gate(name="volatility", value=volatility, threshold=persona.max_volatility, passed=ok,
                          note=f"volatility {volatility:.2f} {'<=' if ok else '>'} {persona.max_volatility:.2f}"))
    gates.append(Gate(name="sell_score", value=S, threshold=persona.sell_score, passed=S <= persona.sell_score,
                      note=f"S {S:+.2f} {'<=' if S <= persona.sell_score else '>'} sell threshold {persona.sell_score:+.2f}"))

    buy_gates = [g for g in gates if g.name in ("buy_score", "margin_of_safety", "volatility")]
    if all(g.passed for g in buy_gates):
        rec = "Buy"
    elif S <= persona.sell_score:
        rec = "Sell"
    else:
        rec = "Hold"
        if gates[0].passed:
            blocked = "; ".join(g.note for g in buy_gates if not g.passed)
            warnings.append(f"Buy blocked: {blocked}")

    confidence = None
    if rec in ("Buy", "Sell"):
        confidence = round(100 * abs(S) * integrity * (len(valid) / total_agents), 2)

    return Verdict(recommendation=rec, confidence=confidence, score=S, agent_scores=scores, gates=gates,
                   agents_valid=valid, agents_missing=[a for a in ALL_AGENTS if a not in valid],
                   warnings=warnings)


# --------------------------------------------------------------------------- explanation material

def collect_key_risks(outputs: Dict[str, AgentOutput], limit: int = 5) -> List[str]:
    """Agents' own `risks`, de-duplicated case-insensitively, prefixed with the agent name."""
    seen, out = set(), []
    for agent in ALL_AGENTS:
        if agent not in outputs:
            continue
        for risk in outputs[agent].risks:
            key = " ".join(risk.lower().split())
            if key and key not in seen:
                seen.add(key)
                out.append(f"{agent}: {risk.strip()}")
    return out[:limit]


def collect_supporting_points(outputs: Dict[str, AgentOutput], verifications: Dict[str, AgentVerification],
                              verdict: Verdict, limit: int = 5) -> List[str]:
    """Verified claims from valid agents whose stance matches the verdict's direction
    (all valid agents when none match, e.g. a Hold from bulls and bears cancelling out)."""
    valid = [a for a in verdict.agents_valid if a in outputs]
    want = DIRECTION.get(verdict.recommendation)
    aligned = [a for a in valid if outputs[a].stance == want] or valid
    points = []
    for agent in aligned:
        checks = {c.claim_id: c for c in verifications[agent].checks} if agent in verifications else {}
        for claim in outputs[agent].claims:
            if checks.get(claim.id) and checks[claim.id].verified:
                points.append(f"{agent} {claim.id}: {claim.text}")
    return points[:limit]
