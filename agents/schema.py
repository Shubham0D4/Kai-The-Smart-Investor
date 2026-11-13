"""
Agent output schema (DESIGN.md §2) and the structured-generation helper every agent uses.

An agent turn is a JSON object with a stance, a confidence and a list of claims. Every
checkable claim points at evidence records (IDs like ``sec:revenue:FY2026``) that the agent
was given. The model's reply is validated here; one retry is allowed, then the agent fails.
"""

import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Dict, Iterable, List, Literal, Optional, Type, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


# --------------------------------------------------------------------------- evidence

class EvidenceRecord(BaseModel):
    """One fetched or computed datum an agent may cite."""
    id: str
    label: str
    value: Optional[float] = None
    unit: Optional[str] = None
    period: Optional[str] = None
    text: Optional[str] = None          # article summary, for quote claims
    source: str
    url: Optional[str] = None


# --------------------------------------------------------------------------- claims

class NumericClaim(BaseModel):
    """A number derived from one or two evidence records.

    op semantics (evidence order matters):
      equals: value == e0
      growth: value == e0 / e1 - 1   (e0 newer, e1 older)
      ratio:  value == e0 / e1
      diff:   value == e0 - e1
    """
    id: str
    kind: Literal["numeric"]
    text: str
    value: float
    unit: str
    op: Literal["equals", "growth", "ratio", "diff"]
    evidence: List[str]

    @model_validator(mode="after")
    def _arity(self):
        need = 1 if self.op == "equals" else 2
        if len(self.evidence) != need:
            raise ValueError(f"numeric claim {self.id}: op '{self.op}' needs exactly {need} evidence id(s)")
        return self


class QuoteClaim(BaseModel):
    """A verbatim span from one article."""
    id: str
    kind: Literal["quote"]
    text: str
    quote: str = Field(min_length=3)
    evidence: List[str] = Field(min_length=1, max_length=1)


class InferenceClaim(BaseModel):
    """A conclusion resting on other claims in the same output."""
    id: str
    kind: Literal["inference"]
    text: str
    based_on: List[str] = Field(min_length=1)


Claim = Annotated[Union[NumericClaim, QuoteClaim, InferenceClaim], Field(discriminator="kind")]

AgentName = Literal["charlie", "delta", "gamma"]


class AgentOutput(BaseModel):
    agent: AgentName
    round: int = Field(ge=1, le=3)
    stance: Literal["bullish", "neutral", "bearish"]
    confidence: float = Field(ge=0, le=100)
    claims: List[Claim] = Field(min_length=1)
    risks: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references(self):
        ids = [c.id for c in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("claim ids must be unique")
        for c in self.claims:
            if isinstance(c, InferenceClaim):
                bad = [b for b in c.based_on if b not in ids or b == c.id]
                if bad:
                    raise ValueError(f"inference claim {c.id} is based_on unknown claim(s): {bad}")
        return self

    def evidence_ids(self) -> List[str]:
        out: List[str] = []
        for c in self.claims:
            out.extend(getattr(c, "evidence", []))
        return out


# --------------------------------------------------------------------------- rounds 2 and 3

Verdict = Literal["dispute", "qualify", "support"]
Stance = Literal["bullish", "neutral", "bearish"]


class Critique(BaseModel):
    """Round 2: challenge or confirm one claim of another agent, backed by own evidence."""
    id: str = Field(pattern=r"^K\d+$")
    target: str = Field(pattern=r"^(charlie|delta|gamma)\.[A-Za-z0-9_]+$")   # "<agent>.<claim id>"
    verdict: Verdict
    argument: str = Field(min_length=1)
    evidence: List[str] = Field(min_length=1)


class CritiqueOutput(BaseModel):
    agent: AgentName
    round: Literal[2]
    critiques: List[Critique] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        ids = [c.id for c in self.critiques]
        if len(ids) != len(set(ids)):
            raise ValueError("critique ids must be unique")
        return self


class ClaimChange(BaseModel):
    """Round 3: what happened to one of the agent's own round-1 claims."""
    claim: str
    action: Literal["kept", "revised", "withdrawn"]
    reason: str = Field(min_length=1)
    in_response_to: List[str] = Field(default_factory=list)   # "<agent>.K<n>"


class ViewChange(BaseModel):
    """Round 3: another agent's claim or critique that changed this agent's view."""
    ref: str                                                    # "<agent>.C<n>" or "<agent>.K<n>"
    reason: str = Field(min_length=1)


class StanceChange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: Stance = Field(alias="from")
    to: Stance
    reason: str = ""


class FinalOutput(AgentOutput):
    """Round 3: a full AgentOutput plus what changed and why."""
    round: Literal[3]
    changes: List[ClaimChange] = Field(default_factory=list)
    changed_by: List[ViewChange] = Field(default_factory=list)
    stance_change: StanceChange


# --------------------------------------------------------------------------- prompting

SCHEMA_INSTRUCTIONS = """
Return ONLY a JSON object, no prose and no code fences, with exactly this shape:
{{
  "agent": "{agent}", "round": {round},
  "stance": "bullish" | "neutral" | "bearish",
  "confidence": <number 0-100>,
  "claims": [
    {{"id": "C1", "kind": "numeric", "text": "<plain-English claim>", "value": <number>, "unit": "<USD|ratio|percent|...>",
      "op": "equals" | "growth" | "ratio" | "diff", "evidence": ["<evidence id>", ...]}},
    {{"id": "C2", "kind": "quote", "text": "<claim>", "quote": "<verbatim span from the article>", "evidence": ["<news id>"]}},
    {{"id": "C3", "kind": "inference", "text": "<conclusion>", "based_on": ["C1", "C2"]}}
  ],
  "risks": ["<risk>", ...]
}}
Rules:
- Use ONLY the evidence listed above. Do not use outside knowledge, memory or assumptions about
  the company. If the evidence does not support a point, do not make it.
- Every evidence id you cite must appear in the evidence list exactly as written.
- numeric ops: equals -> value of evidence[0]; growth -> evidence[0]/evidence[1] - 1 (newer first);
  ratio -> evidence[0]/evidence[1]; diff -> evidence[0] - evidence[1]. "equals" takes one id, the others two.
- quote claims copy a span verbatim from the cited article's title or summary.
- inference claims cite other claim ids in based_on, never evidence ids.
- Make at least 3 numeric or quote claims.
"""


def schema_instructions(agent: str, round_number: int) -> str:
    return SCHEMA_INSTRUCTIONS.format(agent=agent, round=round_number).strip()


def _fmt(v: float) -> str:
    """Full digits for large amounts (no scientific notation), 6 significant digits otherwise."""
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.6g}"


def format_evidence(records: Iterable[EvidenceRecord]) -> str:
    """One line per record: [id] label: value unit (period) and, for articles, the summary."""
    lines = []
    for r in records:
        parts = [f"[{r.id}] {r.label}"]
        if r.value is not None:
            parts.append(f": {_fmt(r.value)} {r.unit or ''}".rstrip())
        if r.period:
            parts.append(f" ({r.period})")
        if r.source:
            parts.append(f" | source: {r.source}")
        line = "".join(parts)
        if r.text:
            line += f"\n    summary: {r.text}"
        lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- parsing

def extract_json(text: str) -> Any:
    """Parse a JSON object from a model reply, tolerating code fences and stray prose."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("reply contains no JSON object")
    return json.loads(s[start:end + 1])


M = TypeVar("M", bound=BaseModel)


def parse_model(text: str, model_cls: Type[M]) -> M:
    """Extract JSON from a reply and validate it against a pydantic model.
    Raises ValueError with a readable reason."""
    try:
        data = extract_json(text)
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid JSON: {e}") from e
    try:
        return model_cls.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"schema violation: {e.errors(include_url=False)}") from e


def validate_output(
    text: str, *, agent: str, round_number: int, evidence_ids: Iterable[str]
) -> AgentOutput:
    """Parse and validate a round-1 reply. Raises ValueError with a readable reason."""
    out = parse_model(text, AgentOutput)
    if out.agent != agent or out.round != round_number:
        raise ValueError(f"expected agent={agent!r} round={round_number}, got agent={out.agent!r} round={out.round}")
    known = set(evidence_ids)
    unknown = sorted({e for e in out.evidence_ids() if e not in known})
    if unknown:
        raise ValueError(f"claims cite evidence ids that were not provided: {unknown}")
    return out


@dataclass
class StructuredResult:
    output: Optional[BaseModel]
    error: Optional[str]
    error_type: Optional[str]           # "llm" | "invalid_output"
    attempts: int
    token_usage: Dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.output is not None


def _usage(response) -> Dict[str, int]:
    meta = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": getattr(meta, "prompt_token_count", 0) or 0,
        "candidates_tokens": getattr(meta, "candidates_token_count", 0) or 0,
        "total_tokens": getattr(meta, "total_token_count", 0) or 0,
    }


def generate_validated(model, prompt: str, validate: Callable[[str], BaseModel]) -> StructuredResult:
    """Call the model, validate the reply, retry once on invalid output, then fail cleanly.
    Token usage is summed over every attempt, including rejected ones."""
    usage = {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}
    current_prompt = prompt
    last_error = None
    for attempt in (1, 2):
        try:
            response = model.generate_content(current_prompt)
        except Exception as e:  # provider failure: no retry here, MultiLLMClient already retries
            return StructuredResult(None, f"LLM call failed: {e}", "llm", attempt, usage)
        for k, v in _usage(response).items():
            usage[k] += v
        text = getattr(response, "text", "") or ""
        try:
            return StructuredResult(validate(text), None, None, attempt, usage)
        except ValueError as e:
            last_error = str(e)
            current_prompt = (
                f"{prompt}\n\nYour previous reply was rejected: {last_error}\n"
                f"Previous reply:\n{text[:4000]}\n\n"
                "Reply again with ONLY the corrected JSON object."
            )
    return StructuredResult(None, f"invalid output after 2 attempts: {last_error}", "invalid_output", 2, usage)


def generate_agent_output(
    model, prompt: str, *, agent: str, round_number: int, evidence_ids: Iterable[str]
) -> StructuredResult:
    """Round-1 generation: an AgentOutput citing only the given evidence ids."""
    evidence_ids = list(evidence_ids)
    return generate_validated(model, prompt, lambda text: validate_output(
        text, agent=agent, round_number=round_number, evidence_ids=evidence_ids))


def render_output(out: AgentOutput) -> str:
    """Human-readable rendering, used as the 'analysis' text in logs and the UI."""
    lines = [f"Stance: {out.stance} (confidence {out.confidence:g})", "Claims:"]
    for c in out.claims:
        ref = getattr(c, "evidence", None) or getattr(c, "based_on", [])
        lines.append(f"- [{c.id}] {c.text} ({', '.join(ref)})")
    if out.risks:
        lines.append("Risks:")
        lines.extend(f"- {r}" for r in out.risks)
    return "\n".join(lines)


def render_critiques(out: CritiqueOutput) -> str:
    lines = [f"Round 2 critiques by {out.agent}:"]
    for c in out.critiques:
        lines.append(f"- [{c.id}] {c.verdict.upper()} {c.target}: {c.argument} ({', '.join(c.evidence)})")
    return "\n".join(lines)


def render_final(out: FinalOutput) -> str:
    sc = out.stance_change
    head = (f"Final stance: {out.stance} (confidence {out.confidence:g}); "
            + (f"changed from {sc.from_}: {sc.reason}" if sc.from_ != sc.to else f"unchanged from round 1"))
    lines = [head, render_output(out).split("\n", 1)[1]]
    if out.changed_by:
        lines.append("Changed my view:")
        lines.extend(f"- {v.ref}: {v.reason}" for v in out.changed_by)
    if out.changes:
        lines.append("Own claims after critique:")
        lines.extend(f"- {c.claim} {c.action}: {c.reason}"
                     + (f" (re {', '.join(c.in_response_to)})" if c.in_response_to else "") for c in out.changes)
    return "\n".join(lines)


def agent_error(agent: str, ticker: str, error: str, error_type: str, **extra) -> Dict:
    """Uniform failure result. analysis is None so nothing downstream mistakes it for output."""
    return {
        "agent": agent.capitalize(),
        "ticker": ticker,
        "error": error,
        "error_type": error_type,   # "data_source" | "llm" | "invalid_output"
        "analysis": None,
        **extra,
    }
