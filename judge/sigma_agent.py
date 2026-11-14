"""
Sigma - the judge (FIXES.md C4-C6, PHASE4_PLAN.md §6).

Sigma computes everything in code from the saved debate transcript:
  faithfulness  - claims re-checked against the fetched data (judge/verifier.py)
  integrity     - critiques answered, failed claims fixed, stance changes explained (judge/scoring.py)
  verdict       - debate score S against the persona's thresholds (judge/scoring.py)
It also receives the quant report (quant/, DESIGN.md §10) as a separate structured input: the
volatility gate reads it, `quant:` claims are verified against it, and it lowers a Buy/Sell
confidence (agreement Q, contradictions C) without ever changing the recommendation.
It also computes a playbook of suggestions (judge/playbook.py): what would turn the verdict into
Buy or Sell, price levels, upcoming events and risk, every number from the evidence.
Its result is a pydantic-validated object (SigmaEvaluation). The LLM is called once, only to
write a plain-language explanation of the verdict and the playbook; its reply is never parsed
for a verdict.
"""

import os
import sys
import json
import argparse
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field, model_validator

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from coordinator.config import Thresholds, load_thresholds
from coordinator.personas import get_sigma_system_instruction
from coordinator.transcript import Transcript
from coordinator.llm_client import MultiLLMClient
from judge.playbook import Suggestion, build_playbook
from judge.scoring import (
    AgentIntegrity, Contradiction, Gate, Verdict, agent_integrity, apply_quant, collect_key_risks,
    collect_supporting_points, decide_verdict, no_verdict, overall_integrity, quant_signals,
)
from quant.models import QuantReport
from judge.verifier import AgentVerification, overall_faithfulness, verify_output
from dotenv import load_dotenv

load_dotenv()

PERSONAS = ("zen", "balanced", "alpha")


# --------------------------------------------------------------------------- output schema

class QualityEvaluation(BaseModel):
    faithfulness_score: float = Field(ge=0, le=100)
    integrity_score: float = Field(ge=0, le=100)
    overall_quality: float = Field(ge=0, le=100)


class FinalRecommendation(BaseModel):
    recommendation: Literal["Buy", "Hold", "Sell", "No Verdict"]
    confidence: Optional[float] = Field(default=None, ge=0, le=100)
    score: float
    supporting_points: List[str]
    key_risks: List[str]
    gates: List[Gate]
    agents_valid: List[str]
    agents_missing: List[str]
    warnings: List[str]
    reason: Optional[str] = None
    analysis: str
    confidence_before_quant: Optional[float] = None
    quant_agreement: Optional[float] = None
    contradiction_factor: Optional[float] = None
    quant_signals: Dict[str, float] = {}
    contradictions: List[Contradiction] = []
    hold_conviction: Optional[float] = None
    coverage: Optional[float] = None
    suggestions: List[Suggestion] = []

    @model_validator(mode="after")
    def _confidence_for_every_verdict(self):
        if self.recommendation == "No Verdict":
            if self.confidence is not None:
                raise ValueError("No Verdict carries no confidence")
            if not self.reason:
                raise ValueError("No Verdict needs a reason")
        elif self.confidence is None:
            raise ValueError(f"{self.recommendation} needs a confidence")
        return self


class SigmaEvaluation(BaseModel):
    agent: Literal["Sigma"] = "Sigma"
    debate_id: str
    ticker: str
    persona: str
    evaluation: QualityEvaluation
    final_recommendation: FinalRecommendation
    verification: Dict[str, AgentVerification]
    integrity: Dict[str, AgentIntegrity]
    verdicts_by_persona: Dict[str, Verdict]
    quant_report: Optional[QuantReport] = None
    metadata: Dict[str, Any]


# --------------------------------------------------------------------------- agent

class SigmaAgent:
    """Sigma - Final Decision Judge."""

    def __init__(self, llm_client=None, thresholds: Optional[Thresholds] = None):
        self.name = "Sigma"
        self.role = "Final Decision Judge"
        self.thresholds = thresholds
        self.model = llm_client if llm_client is not None else MultiLLMClient(
            system_instruction=get_sigma_system_instruction(), role="sigma")

    @property
    def cfg(self) -> Thresholds:
        return self.thresholds or load_thresholds()

    def score_debate(self, transcript: Transcript, quant_report: Optional[QuantReport] = None) -> Dict:
        """Deterministic scores on each agent's latest output. No LLM call. `quant:` evidence is
        resolved against the quant report."""
        cfg = self.cfg
        finals = transcript.final_outputs()
        verifications = {a: verify_output(out, transcript.evidence.get(a, []), cfg.verification, quant_report)
                         for a, out in finals.items()}
        integrity = {a: agent_integrity(a, transcript, transcript.verification.get(a), cfg.integrity)
                     for a in finals}
        return {
            "finals": finals,
            "verifications": verifications,
            "faithfulness": overall_faithfulness(list(verifications.values())),
            "faithfulness_by_agent": {a: v.faithfulness for a, v in verifications.items()},
            "integrity_by_agent": integrity,
            "integrity": overall_integrity(list(integrity.values())),
        }

    @staticmethod
    def gate_inputs(transcript: Transcript,
                    quant_report: Optional[QuantReport] = None) -> Tuple[Optional[float], Optional[float]]:
        """Margin of safety from Gamma's point DCF; annualised volatility from the quant report."""
        records = {e.id: e.value for e in transcript.evidence.get("gamma", [])}
        vol = quant_report.value("quant:returns.vol_annual") if quant_report is not None else None
        return records.get("calc:dcf.margin_of_safety"), vol

    def evaluate_debate(self, debate_log_path: str, persona: Optional[str] = None,
                        quant_report: Optional[Union[QuantReport, Dict]] = None) -> Dict:
        """
        Score a completed debate log and decide the verdict for `persona` (default: the log's).
        `quant_report` is the coordinator's report, passed directly; when omitted it is read from
        the log's `quant_report` entry and validated. Returns SigmaEvaluation as a validated,
        JSON-serialisable dict.
        """
        with open(debate_log_path) as f:
            log = json.load(f)
        cfg = self.cfg
        if quant_report is None and log.get("quant_report"):
            quant_report = log["quant_report"]
        if isinstance(quant_report, dict):
            quant_report = QuantReport.model_validate(quant_report)
        transcript = Transcript.from_dict(log.get("transcript") or {}, log.get("ticker", ""))
        persona_name = persona or log.get("persona") or "balanced"
        if persona_name == "sigma":      # old logs
            persona_name = "balanced"
        outcome = log.get("outcome") or {}
        warnings = list(outcome.get("warnings", []))

        sc = self.score_debate(transcript, quant_report)
        mos, vol = self.gate_inputs(transcript, quant_report)
        signals, signal_warnings = quant_signals(quant_report, cfg.quant)

        def verdict_for(p: str) -> Verdict:
            if outcome.get("status") == "NO_VERDICT":
                return no_verdict(f"{outcome.get('reason')}: {outcome.get('detail')}",
                                  warnings=warnings)
            v = decide_verdict(sc["finals"], sc["faithfulness_by_agent"], sc["integrity"], mos, vol,
                               cfg.persona(p), cfg, warnings=warnings)
            return apply_quant(v, sc["finals"], signals, signal_warnings, cfg.quant)

        verdict = verdict_for(persona_name)
        by_persona = {p: verdict_for(p) for p in PERSONAS}
        evidence = {e.id: e.value for ev in transcript.evidence.values() for e in ev if e.value is not None}
        playbook = build_playbook(verdict, persona_name, cfg.persona(persona_name), evidence, quant_report,
                                  by_persona)
        supporting = collect_supporting_points(sc["finals"], sc["verifications"], verdict)
        risks = collect_key_risks(sc["finals"])
        analysis, narrative_warning = self.write_narrative(verdict, transcript, supporting, risks, playbook)
        all_warnings = verdict.warnings + ([narrative_warning] if narrative_warning else [])

        faith, integ = round(sc["faithfulness"] * 100, 2), round(sc["integrity"] * 100, 2)
        result = SigmaEvaluation(
            debate_id=log.get("debate_id", ""),
            ticker=log.get("ticker", transcript.ticker),
            persona=persona_name,
            evaluation=QualityEvaluation(faithfulness_score=faith, integrity_score=integ,
                                         overall_quality=round((faith + integ) / 2, 2)),
            final_recommendation=FinalRecommendation(
                recommendation=verdict.recommendation, confidence=verdict.confidence, score=verdict.score,
                supporting_points=supporting, key_risks=risks, gates=verdict.gates,
                agents_valid=verdict.agents_valid, agents_missing=verdict.agents_missing,
                warnings=all_warnings, reason=verdict.reason, analysis=analysis,
                confidence_before_quant=verdict.confidence_before_quant,
                quant_agreement=verdict.quant_agreement, contradiction_factor=verdict.contradiction_factor,
                quant_signals=verdict.quant_signals, contradictions=verdict.contradictions,
                hold_conviction=verdict.hold_conviction, coverage=verdict.coverage, suggestions=playbook,
            ),
            verification=sc["verifications"],
            integrity=sc["integrity_by_agent"],
            verdicts_by_persona=by_persona,
            quant_report=quant_report,
            metadata={
                "rounds_evaluated": len(log.get("rounds", [])),
                "agents_evaluated": list(sc["finals"]),
                "outcome": outcome,
            },
        )
        return result.model_dump(mode="json")

    def write_narrative(self, verdict: Verdict, transcript: Transcript, supporting: List[str],
                        risks: List[str], playbook: Optional[List[Suggestion]] = None) -> Tuple[str, Optional[str]]:
        """The only LLM call: explain the computed verdict. Returns (text, warning or None).
        Falls back to a fixed template when there is no verdict to explain or the call fails."""
        template = self._template(verdict, supporting, risks)
        if verdict.recommendation == "No Verdict":
            return template, None
        conf = f"{verdict.confidence:.0f}" + (" (confidence in Hold)" if verdict.recommendation == "Hold" else "")
        prompt = f"""
Explain this investment committee verdict for {transcript.ticker} in at most 200 words, then
say what an investor should watch, using the playbook below. The verdict, confidence and every
number in the playbook were computed in code and are final: do not change, soften or contradict
them, do not add any number that is not given here, and do not recommend anything else.

Verdict: {verdict.recommendation}
Confidence: {conf}
Debate score S: {verdict.score:+.2f}
Gates (computed; state them exactly as PASSED or FAILED here):
{chr(10).join(('- PASSED: ' if g.passed else '- FAILED: ') + g.note for g in verdict.gates)}
Quant report adjustment: agreement Q = {verdict.quant_agreement}, contradiction factor C = {verdict.contradiction_factor}
Contradictions with the quant report: {'; '.join(f"{x.agent} is {x.stance} but {x.field} = {x.value:.2f}" for x in verdict.contradictions) or 'none'}
Agents counted: {', '.join(verdict.agents_valid)}; missing or excluded: {', '.join(verdict.agents_missing) or 'none'}
Verified supporting points:
{chr(10).join('- ' + p for p in supporting) or '- none'}
Risks raised by the agents:
{chr(10).join('- ' + r for r in risks) or '- none'}
Playbook (computed):
{chr(10).join(f'- {x.title}: {x.detail}' for x in (playbook or [])) or '- none'}
"""
        try:
            from coordinator.llm_config import load_llm_config
            response = self.model.generate_content(
                prompt, max_output_tokens=load_llm_config().max_output_tokens.narrative)
            text = (response.text or "").strip()
        except Exception as e:
            return template, f"narrative unavailable (LLM call failed: {e}); template used"
        if not text:
            return template, "narrative unavailable (empty LLM reply); template used"
        return text, None

    @staticmethod
    def _template(verdict: Verdict, supporting: List[str], risks: List[str]) -> str:
        if verdict.recommendation == "No Verdict":
            return f"No verdict: {verdict.reason}."
        conf = f"confidence {verdict.confidence:.0f}" if verdict.confidence is not None else "no confidence"
        lines = [f"Verdict: {verdict.recommendation}, {conf}, debate score {verdict.score:+.2f}.",
                 "Gates: " + "; ".join(g.note for g in verdict.gates) + "."]
        if supporting:
            lines.append("Supporting points: " + "; ".join(supporting) + ".")
        if risks:
            lines.append("Risks: " + "; ".join(risks) + ".")
        return "\n".join(lines)

    def generate_report(self, evaluation: Dict) -> str:
        fr, ev = evaluation["final_recommendation"], evaluation["evaluation"]
        conf = f"{fr['confidence']:.2f}/100" if fr["confidence"] is not None else "n/a"
        return "\n".join([
            "=" * 80,
            f"SIGMA'S EVALUATION - {evaluation['ticker']} ({evaluation['persona'].upper()})",
            "=" * 80,
            f"Faithfulness: {ev['faithfulness_score']}/100   Integrity: {ev['integrity_score']}/100",
            f"Decision: {fr['recommendation']}   Confidence: {conf}   Score: {fr['score']:+.2f}",
            *(f"  gate {g['name']}: {'pass' if g['passed'] else 'FAIL'} - {g['note']}" for g in fr["gates"]),
            "",
            fr["analysis"],
            "=" * 80,
        ])


def main():
    parser = argparse.ArgumentParser(description="Sigma - Final Decision Judge")
    parser.add_argument("--debate-log", type=str, required=True, help="Path to debate log JSON file")
    parser.add_argument("--persona", type=str, choices=PERSONAS, help="Persona thresholds to apply")
    args = parser.parse_args()

    sigma = SigmaAgent()
    evaluation = sigma.evaluate_debate(args.debate_log, persona=args.persona)
    print(sigma.generate_report(evaluation))
    eval_file = args.debate_log.replace(".json", "_evaluation.json")
    with open(eval_file, "w") as f:
        json.dump(evaluation, f, indent=2)
    print(f"\nEvaluation saved to: {eval_file}")


if __name__ == "__main__":
    main()
