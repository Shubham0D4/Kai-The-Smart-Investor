"""
Debate transcript (DESIGN.md §3): the coordinator's single record of what every agent said.

Calls to the LLM stay stateless; instead every round-2 and round-3 prompt is built from this
transcript in full (nothing truncated): the agent's own earlier output, its own evidence, and
the other agents' complete structured claims and critiques.
"""

import json
from typing import Dict, List, Optional, Set

from agents.schema import (
    AgentOutput, CritiqueOutput, EvidenceRecord, FinalOutput, format_evidence, parse_model,
)

AGENT_ORDER = ["charlie", "delta", "gamma"]


def _dump(model) -> str:
    return json.dumps(model.model_dump(by_alias=True), indent=1)


class Transcript:
    """Structured state of one debate, owned by the coordinator."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.evidence: Dict[str, List[EvidenceRecord]] = {}
        self.round1: Dict[str, AgentOutput] = {}
        self.round2: Dict[str, CritiqueOutput] = {}
        self.round3: Dict[str, FinalOutput] = {}
        self.calls: List[Dict] = []          # one entry per LLM call (agent, round, tokens)

    # ------------------------------------------------------------------ recording

    def add_round1(self, agent: str, output: Dict, evidence: List[Dict]):
        self.round1[agent] = AgentOutput.model_validate(output)
        self.evidence[agent] = [EvidenceRecord(**e) for e in evidence]

    def add_round2(self, agent: str, output: CritiqueOutput):
        self.round2[agent] = output

    def add_round3(self, agent: str, output: FinalOutput):
        self.round3[agent] = output

    def record_call(self, agent: str, round_number: int, token_usage: Dict, ok: bool):
        self.calls.append({"agent": agent, "round": round_number, "ok": ok,
                           "total_tokens": token_usage.get("total_tokens", 0)})

    # ------------------------------------------------------------------ queries

    def participants(self) -> List[str]:
        return [a for a in AGENT_ORDER if a in self.round1]

    def others(self, agent: str) -> List[str]:
        return [a for a in self.participants() if a != agent]

    def claim_refs(self, agents: List[str]) -> Set[str]:
        return {f"{a}.{c.id}" for a in agents for c in self.round1[a].claims}

    def critique_refs(self, agents: List[str]) -> Set[str]:
        return {f"{a}.{k.id}" for a in agents if a in self.round2 for k in self.round2[a].critiques}

    def critiques_of(self, agent: str) -> List[tuple]:
        """(ref, critic, critique) for every round-2 critique that targets this agent's claims."""
        out = []
        for critic in self.others(agent):
            if critic in self.round2:
                for k in self.round2[critic].critiques:
                    if k.target.split(".", 1)[0] == agent:
                        out.append((f"{critic}.{k.id}", critic, k))
        return out

    def _cited_evidence(self, agent: str) -> List[EvidenceRecord]:
        """Records another agent cited, so a reader can see what its evidence ids mean."""
        cited = set(self.round1[agent].evidence_ids())
        return [e for e in self.evidence[agent] if e.id in cited]

    def to_dict(self) -> Dict:
        return {
            "round1": {a: o.model_dump(by_alias=True) for a, o in self.round1.items()},
            "round2": {a: o.model_dump(by_alias=True) for a, o in self.round2.items()},
            "round3": {a: o.model_dump(by_alias=True) for a, o in self.round3.items()},
            "llm_calls": self.calls,
        }

    # ------------------------------------------------------------------ prompts

    def _others_section(self, agent: str, include_critiques: bool) -> str:
        parts = []
        for other in self.others(agent):
            parts.append(f"### {other.upper()} round-1 output (full)\n{_dump(self.round1[other])}")
            cited = self._cited_evidence(other)
            if cited:
                parts.append(f"Evidence cited by {other}:\n{format_evidence(cited)}")
            if include_critiques and other in self.round2:
                parts.append(f"### {other.upper()} round-2 critiques (full)\n{_dump(self.round2[other])}")
        return "\n\n".join(parts)

    def round2_prompt(self, agent: str) -> str:
        targets = sorted(self.claim_refs(self.others(agent)))
        return f"""
ROUND 2 - CROSS-EXAMINATION for {self.ticker}. You are {agent}.

## Your round-1 output (full)
{_dump(self.round1[agent])}

## Your evidence (the only ids you may cite)
{format_evidence(self.evidence[agent])}

## The other agents' claims
{self._others_section(agent, include_critiques=False)}

## Task
Examine the other agents' claims against YOUR evidence. For specific claims, by id, say whether
you dispute, qualify or support them, and back every critique with at least one id from YOUR
evidence list above. Critique at least one claim of each other agent: {', '.join(self.others(agent))}.
Claims you may target: {', '.join(targets)}

Return ONLY a JSON object, no prose and no code fences:
{{
  "agent": "{agent}", "round": 2,
  "critiques": [
    {{"id": "K1", "target": "<agent>.<claim id>", "verdict": "dispute" | "qualify" | "support",
      "argument": "<why, referring to your evidence>", "evidence": ["<id from your evidence>"]}}
  ]
}}
Use only your evidence and the claims shown. Do not use outside knowledge.
""".strip()

    def round3_prompt(self, agent: str) -> str:
        received = self.critiques_of(agent)
        received_lines = "\n".join(
            f"- {ref} ({k.verdict}, targets {k.target}): {k.argument} [evidence: {', '.join(k.evidence)}]"
            for ref, _, k in received) or "- none"
        own_r2 = (_dump(self.round2[agent]) if agent in self.round2
                  else "(you produced no valid critiques in round 2)")
        refs = sorted(self.claim_refs(self.others(agent)) | self.critique_refs(self.others(agent)))
        r1 = self.round1[agent]
        return f"""
ROUND 3 - FINAL POSITION for {self.ticker}. You are {agent}.

## Your round-1 output (full)
{_dump(r1)}
Your round-1 stance: {r1.stance}

## Your round-2 critiques (full)
{own_r2}

## Your evidence (the only ids your claims may cite)
{format_evidence(self.evidence[agent])}

## The other agents' claims and critiques
{self._others_section(agent, include_critiques=True)}

## Critiques of your claims (answer every one)
{received_lines}

## Task
Give your final stance and claims. Say which of the other agents' claims or critiques changed
your view and why, and what happened to each of your own round-1 claims that was critiqued.

Return ONLY a JSON object, no prose and no code fences:
{{
  "agent": "{agent}", "round": 3,
  "stance": "bullish" | "neutral" | "bearish",
  "confidence": <number 0-100>,
  "claims": [ <same claim format as round 1, citing only your evidence ids> ],
  "risks": ["<risk>", ...],
  "changes": [
    {{"claim": "<your round-1 claim id>", "action": "kept" | "revised" | "withdrawn",
      "reason": "<why>", "in_response_to": ["<critique ref, e.g. delta.K1>"]}}
  ],
  "changed_by": [ {{"ref": "<agent>.<claim or critique id>", "reason": "<how it changed your view>"}} ],
  "stance_change": {{"from": "{r1.stance}", "to": "<final stance>", "reason": "<why, or empty if unchanged>"}}
}}
Rules: every critique listed above must appear in some change's in_response_to. If your stance
changed, list at least one entry in changed_by. Refs you may use in changed_by: {', '.join(refs) or 'none'}.
Use only the material shown. Do not use outside knowledge.
""".strip()

    # ------------------------------------------------------------------ validation

    def validate_round2(self, agent: str, text: str) -> CritiqueOutput:
        out = parse_model(text, CritiqueOutput)
        if out.agent != agent:
            raise ValueError(f"expected agent={agent!r}, got {out.agent!r}")
        others = self.others(agent)
        allowed = self.claim_refs(others)
        own_ids = {e.id for e in self.evidence[agent]}
        for k in out.critiques:
            if k.target not in allowed:
                raise ValueError(f"critique {k.id} targets {k.target!r}, which is not another agent's claim; "
                                 f"allowed: {sorted(allowed)}")
            bad = [e for e in k.evidence if e not in own_ids]
            if bad:
                raise ValueError(f"critique {k.id} cites evidence that is not yours: {bad}")
        missing = set(others) - {k.target.split(".", 1)[0] for k in out.critiques}
        if missing:
            raise ValueError(f"no critique of: {sorted(missing)}")
        return out

    def validate_round3(self, agent: str, text: str) -> FinalOutput:
        out = parse_model(text, FinalOutput)
        if out.agent != agent:
            raise ValueError(f"expected agent={agent!r}, got {out.agent!r}")
        own_ids = {e.id for e in self.evidence[agent]}
        bad = sorted({e for e in out.evidence_ids() if e not in own_ids})
        if bad:
            raise ValueError(f"claims cite evidence ids that are not yours: {bad}")
        r1 = self.round1[agent]
        own_claims = {c.id for c in r1.claims}
        received = {ref for ref, _, _ in self.critiques_of(agent)}
        answered: Set[str] = set()
        for ch in out.changes:
            if ch.claim not in own_claims:
                raise ValueError(f"change refers to {ch.claim!r}, not one of your round-1 claims {sorted(own_claims)}")
            unknown = [r for r in ch.in_response_to if r not in received]
            if unknown:
                raise ValueError(f"change for {ch.claim} answers unknown critiques {unknown}; received: {sorted(received)}")
            answered.update(ch.in_response_to)
        if received - answered:
            raise ValueError(f"critiques not answered in changes: {sorted(received - answered)}")
        refs = self.claim_refs(self.others(agent)) | self.critique_refs(self.others(agent))
        unknown = [v.ref for v in out.changed_by if v.ref not in refs]
        if unknown:
            raise ValueError(f"changed_by refers to unknown refs {unknown}")
        sc = out.stance_change
        if sc.from_ != r1.stance or sc.to != out.stance:
            raise ValueError(f"stance_change must go from your round-1 stance {r1.stance!r} "
                             f"to your final stance {out.stance!r}")
        if sc.from_ != sc.to and (not sc.reason or not out.changed_by):
            raise ValueError("stance changed: give a reason and at least one changed_by entry")
        return out
