"""
Agent output schema (DESIGN.md §2) and the structured-generation helper every agent uses.

An agent turn is a JSON object with a stance, a confidence and a list of claims. Every
checkable claim points at evidence records (IDs like ``sec:revenue:FY2026``) that the agent
was given. The model's reply is constrained by a JSON schema and validated here; one
repair is allowed, then the agent fails.
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

# Claim ids are C1..C15 in every round. Refs to another agent's claim add the agent: "delta.C2".
MAX_CLAIMS = 15
CLAIM_IDS = [f"C{i}" for i in range(1, MAX_CLAIMS + 1)]
CLAIM_ID = Field(pattern=r"^C([1-9]|1[0-5])$")

class NumericClaim(BaseModel):
    """A number derived from one or two evidence records.

    op semantics (evidence order matters):
      equals: value == e0
      growth: value == e0 / e1 - 1   (e0 newer, e1 older)
      ratio:  value == e0 / e1
      diff:   value == e0 - e1
    """
    id: str = CLAIM_ID
    kind: Literal["numeric"]
    text: str
    value: Optional[float] = None     # derived by the verifier from the cited records when omitted
    unit: str = ""
    op: Literal["equals", "growth", "ratio", "diff", "cagr"]
    evidence: List[str]

    @model_validator(mode="after")
    def _arity(self):
        need = 1 if self.op == "equals" else 2
        if len(self.evidence) != need:
            raise ValueError(f"numeric claim {self.id}: op '{self.op}' needs exactly {need} evidence id(s)")
        return self


class QuoteClaim(BaseModel):
    """A verbatim span from one article."""
    id: str = CLAIM_ID
    kind: Literal["quote"]
    text: str
    quote: str = Field(min_length=3)
    evidence: List[str] = Field(min_length=1, max_length=1)


class InferenceClaim(BaseModel):
    """A conclusion resting on other claims in the same output."""
    id: str = CLAIM_ID
    kind: Literal["inference"]
    text: str
    based_on: List[str] = Field(min_length=1)


Claim = Annotated[Union[NumericClaim, QuoteClaim, InferenceClaim], Field(discriminator="kind")]

AgentName = Literal["charlie", "delta", "gamma", "vega"]


class AgentOutput(BaseModel):
    agent: AgentName
    round: int = Field(ge=1, le=3)
    stance: Literal["bullish", "neutral", "bearish"]
    confidence: float = Field(ge=0, le=100)
    claims: List[Claim] = Field(min_length=1)
    risks: List[str] = Field(default_factory=list)
    dropped_malformed: List[str] = Field(default_factory=list)   # claim ids removed for a structural fault
    malformed_notes: List[str] = Field(default_factory=list)     # why each was removed (shown to the repair call)

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
    target: str = Field(pattern=r"^(charlie|delta|gamma|vega)\.[A-Za-z0-9_]+$")   # "<agent>.<claim id>"
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
#
# Kai wire protocol: data is sent in a compact line format and replies are JSON constrained by a
# schema. Measured on a real TSLA debate, the old evidence format cost 3,956 tokens for Charlie's
# 51 records; this one costs about a fifth of that, with values kept exact (no rounding).

SCHEMA_INSTRUCTIONS = """
Return ONLY a JSON object (the schema is enforced):
{{"agent": "{agent}", "round": {round}, "stance": "bullish"|"neutral"|"bearish", "confidence": 0-100,
 "claims": [
  {{"id": "C1", "kind": "numeric", "text": "<claim>", "op": "equals"|"growth"|"ratio"|"diff"|"cagr", "evidence": ["<id>"]}},
  {{"id": "C2", "kind": "quote", "text": "<claim>", "quote": "<verbatim span>", "evidence": ["<news id>"]}},
  {{"id": "C3", "kind": "inference", "text": "<conclusion>", "based_on": ["C1", "C2"]}}],
 "risks": ["<risk>"]}}
ops: equals = e0 (one id); growth = e0/e1 - 1 (newer first, total change, not annual); cagr = (e0/e1)^(1/years) - 1 with
years taken from the two FY ids (newer first; use for "per year" or CAGR); ratio = e0/e1; diff = e0 - e1.
Do NOT write the result number as a field: the system computes it from the cited ids. Write the figure in "text"
as you would say it (24.4%, $98.8B, 1.2x). Cited ids must be exactly the inputs of the op, and every number in
"text" must be the result or one of the cited values, so cite every figure you mention and put one result per
claim. Use the right direction (rose vs fell; expanded vs narrowed). A wrong figure or direction fails the claim.
quote: copy a span verbatim from the cited article's title or summary.
Claim ids are C1, C2, ... (at most C15); inference based_on lists those ids, never evidence ids.
Use only the evidence above, no outside knowledge. At least 3 numeric or quote claims.
"""


def schema_instructions(agent: str, round_number: int) -> str:
    return SCHEMA_INSTRUCTIONS.format(agent=agent, round=round_number).strip()


def _fmt(v: float) -> str:
    """Exact digits for large amounts (no separators, no scientific notation), 6 significant
    digits otherwise."""
    return f"{v:.0f}" if abs(v) >= 1000 else f"{v:.6g}"


EVIDENCE_LEGEND = ("Format: [id] value unit | note | date range. Prefixes: sec = SEC 10-K/10-Q XBRL "
                   "(FY = fiscal year, TTM = trailing 12 months), mkt = market data, cat = catalysts and "
                   "analysts, calc / quant / assumption = computed in code, news = [id] date | title | "
                   "summary. Values are exact. A change or return is only comparable with one over the "
                   "same date range.")


def format_evidence(records: Iterable[EvidenceRecord]) -> str:
    """One compact line per record. Numbers: '[id] value unit', plus the label when the id does
    not already say what it is (SEC fiscal-year ids do) and the date range for anything that is
    not a fiscal-year figure. Articles: '[id] date | title | summary'."""
    lines = []
    for r in records:
        if r.value is not None:
            line = f"[{r.id}] {_fmt(r.value)}" + (f" {r.unit}" if r.unit else "")
            fiscal = r.id.startswith("sec:") and ":FY" in r.id
            if not fiscal or "split-adjusted" in r.label:
                line += f" | {r.label}"
            if r.period and not fiscal:
                line += f" | {r.period}"
        else:
            date = (r.period or "")[:10]
            line = f"[{r.id}] {date} | {r.label}" + (f" | {r.text}" if r.text else "")
        lines.append(line)
    return "\n".join(lines)


def evidence_block(records: Iterable[EvidenceRecord]) -> str:
    """The evidence section every round's prompt starts with. It is identical across an
    agent's three calls, so providers that cache prompt prefixes can reuse it."""
    return f"## Your evidence (the only ids you may cite)\n{EVIDENCE_LEGEND}\n{format_evidence(records)}"


def compact_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(by_alias=True), separators=(",", ":"), ensure_ascii=False)


# JSON schemas sent to the provider. Strict mode needs every property listed in "required" and
# additionalProperties false at every level. Enums carry the ids an agent may use, so citing an
# id it was not given is impossible rather than retried.

def _obj(props: Dict[str, Dict]) -> Dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def _str(enum: Optional[List[str]] = None) -> Dict:
    return {"type": "string", "enum": list(enum)} if enum else {"type": "string"}


def _arr(items: Dict, min_items: int = 0, max_items: Optional[int] = None) -> Dict:
    out = {"type": "array", "items": items}
    if min_items:
        out["minItems"] = min_items
    if max_items:
        out["maxItems"] = max_items
    return out


def claim_json_schema(evidence: Iterable[EvidenceRecord]) -> Dict:
    evidence = list(evidence)
    numeric_ids = [e.id for e in evidence if e.value is not None]
    text_ids = [e.id for e in evidence if e.value is None]
    variants = [_obj({"id": _str(CLAIM_IDS), "kind": _str(["inference"]), "text": _str(),
                      "based_on": _arr(_str(CLAIM_IDS), 1)})]
    if numeric_ids:
        variants.insert(0, _obj({
            "id": _str(CLAIM_IDS), "kind": _str(["numeric"]), "text": _str(), "op": _str(["equals", "growth", "ratio", "diff", "cagr"]),
            "evidence": _arr(_str(numeric_ids), 1, 2)}))
    if text_ids:
        variants.insert(1, _obj({"id": _str(CLAIM_IDS), "kind": _str(["quote"]), "text": _str(), "quote": _str(),
                                 "evidence": _arr(_str(text_ids), 1, 1)}))
    return {"anyOf": variants}


def _output_props(agent: str, round_number: int, evidence: Iterable[EvidenceRecord]) -> Dict[str, Dict]:
    return {
        "agent": _str([agent]), "round": {"type": "integer", "enum": [round_number]},
        "stance": _str(["bullish", "neutral", "bearish"]), "confidence": {"type": "number"},
        "claims": _arr(claim_json_schema(evidence), 1), "risks": _arr(_str()),
    }


def output_json_schema(agent: str, round_number: int, evidence: Iterable[EvidenceRecord]) -> Dict:
    """Schema of a round-1 reply (AgentOutput)."""
    return _obj(_output_props(agent, round_number, evidence))


def critique_json_schema(agent: str, targets: List[str], own_ids: List[str]) -> Dict:
    """Schema of a round-2 reply (CritiqueOutput)."""
    critique = _obj({"id": _str(), "target": _str(targets), "verdict": _str(["dispute", "qualify", "support"]),
                     "argument": _str(), "evidence": _arr(_str(own_ids), 1)})
    return _obj({"agent": _str([agent]), "round": {"type": "integer", "enum": [2]}, "critiques": _arr(critique, 1)})


def final_json_schema(agent: str, evidence: Iterable[EvidenceRecord], own_claims: List[str],
                      received: List[str], refs: List[str], round1_stance: str) -> Dict:
    """Schema of a round-3 reply (FinalOutput)."""
    props = _output_props(agent, 3, evidence)
    props["changes"] = _arr(_obj({"claim": _str(own_claims), "action": _str(["kept", "revised", "withdrawn"]),
                                  "reason": _str(), "in_response_to": _arr(_str(received))}))
    props["changed_by"] = _arr(_obj({"ref": _str(refs), "reason": _str()}))
    props["stance_change"] = _obj({"from": _str([round1_stance]), "to": _str(["bullish", "neutral", "bearish"]),
                                   "reason": _str()})
    return _obj(props)


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


_OPS = ("equals", "growth", "ratio", "diff", "cagr")


def _drop_malformed_numeric(data: Any) -> None:
    """Remove numeric claims with a structural fault (unknown op, wrong number of evidence ids) and the
    inferences resting on them. They never reach the analysis, so nothing false is credited; the ids
    are recorded in `dropped_malformed`. One stubborn malformed claim no longer discards a whole
    agent's otherwise valid output. Any other fault (unknown evidence id, bad JSON) still fails."""
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        return
    for c in data["claims"]:
        # Results are computed by the verifier from the cited records. A model-written number or unit
        # ("38.7" with unit "B") is a second, unverified statement of the same figure: ignore it. The
        # claim's WORDING is still checked against the computed result and the cited records.
        if isinstance(c, dict) and c.get("kind") == "numeric":
            c["value"], c["unit"] = None, ""
    bad = set()
    notes = []
    for c in data["claims"]:
        if isinstance(c, dict) and c.get("kind") == "numeric":
            ev = c.get("evidence")
            need = 1 if c.get("op") == "equals" else 2
            if c.get("op") not in _OPS or not isinstance(ev, list) or len(ev) != need:
                bad.add(c.get("id"))
                notes.append(f'{c.get("id")} "{str(c.get("text"))[:90]}": op {c.get("op")!r} with '
                             f'{len(ev) if isinstance(ev, list) else 0} evidence id(s); '
                             f'equals needs exactly 1, growth/cagr/ratio/diff exactly 2')
    changed = True
    while changed:
        changed = False
        for c in data["claims"]:
            if isinstance(c, dict) and c.get("id") not in bad and c.get("kind") == "inference" \
                    and any(b in bad for b in (c.get("based_on") or [])):
                bad.add(c.get("id")); changed = True
    if bad:
        data["claims"] = [c for c in data["claims"] if not (isinstance(c, dict) and c.get("id") in bad)]
        data["dropped_malformed"] = sorted(str(b) for b in bad)
        data["malformed_notes"] = notes


def validate_output(
    text: str, *, agent: str, round_number: int, evidence_ids: Iterable[str]
) -> AgentOutput:
    """Parse and validate a round-1 reply. Raises ValueError with a readable reason."""
    try:
        data = extract_json(text)
        _drop_malformed_numeric(data)
        text = json.dumps(data)
    except (ValueError, json.JSONDecodeError):
        pass                                   # parse_model reports it
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
    error_type: Optional[str]           # "llm" | "invalid_output" | "budget"
    attempts: int
    token_usage: Dict[str, int] = field(default_factory=dict)
    models: List[str] = field(default_factory=list)     # "provider:model" per attempt
    repaired_from: Optional[str] = None  # why the first reply was rejected, when a repair succeeded

    @property
    def ok(self) -> bool:
        return self.output is not None


USAGE_KEYS = ("prompt_tokens", "candidates_tokens", "reasoning_tokens", "cached_tokens", "total_tokens")


def _usage(response) -> Dict[str, int]:
    meta = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": getattr(meta, "prompt_token_count", 0) or 0,
        "candidates_tokens": getattr(meta, "candidates_token_count", 0) or 0,
        "reasoning_tokens": getattr(meta, "thoughts_token_count", 0) or 0,
        "cached_tokens": getattr(meta, "cached_content_token_count", 0) or 0,
        "total_tokens": getattr(meta, "total_token_count", 0) or 0,
    }


def _repair_prompt(prompt: str, reply: str, error: str, hint: str) -> str:
    """What to send after a rejected reply. A reply that is valid JSON but breaks a rule gets a
    short repair request (the reply, the error and the allowed values), not the whole prompt
    again: on a real debate the full resend made one failed round-3 call cost 37k tokens.
    A reply that is not JSON at all has nothing to repair, so the full prompt is resent."""
    try:
        extract_json(reply)
    except (ValueError, json.JSONDecodeError):
        return (f"{prompt}\n\nYour previous reply was rejected: {error}\n"
                "Reply again with ONLY the JSON object.")
    return (f"REPAIR. Your JSON reply was rejected: {error}\n"
            + (f"{hint}\n" if hint else "")
            + f"Your reply:\n{reply[:6000]}\n"
            "Fix only what the error names and keep everything else. Return ONLY the corrected JSON object.")


def generate_validated(model, prompt: str, validate: Callable[[str], BaseModel], *,
                       schema: Optional[Dict] = None, schema_name: str = "reply",
                       max_output_tokens: Optional[int] = None, repair_hint: str = "",
                       budget_left: Optional[Callable[[], int]] = None) -> StructuredResult:
    """Call the model, validate the reply, repair once on invalid output, then give a different
    model in the chain one last try before failing the agent cleanly.

    schema: JSON schema the provider enforces (strict mode); max_output_tokens caps each call.
    budget_left(): tokens this agent may still spend. Before every attempt the prompt is
    estimated and the output cap is lowered to what remains, so a call can never overspend;
    if not even a third of the normal cap is left, the step is skipped with error_type "budget".
    The last try (attempt 3) skips the models whose replies were already rejected, so a model that
    keeps breaking the format cannot drop the agent from the debate while another could answer.
    Token usage is summed over every attempt."""
    usage = {k: 0 for k in USAGE_KEYS}
    models: List[str] = []
    schema_text = json.dumps(schema) if schema else ""
    current_prompt = prompt
    first_error = last_error = None
    can_switch = len(getattr(model, "chain", ())) > 1   # a client with a model chain can switch models
    for attempt in (1, 2, 3):
        cap = max_output_tokens
        if budget_left is not None and max_output_tokens:
            room = budget_left() - _estimate(model, current_prompt + schema_text)
            if room < max_output_tokens // 3:
                why = "this agent's token share is used up" if attempt == 1 else "no token budget left to repair the reply"
                err = f"token budget: {why} (~{max(room, 0)} tokens of room, {max_output_tokens // 3} needed)"
                if last_error:
                    err += f"; rejected reply: {last_error}"
                return StructuredResult(None, err, "budget", attempt - 1, usage, models)
            cap = min(max_output_tokens, room)
        try:
            extra = {"exclude": set(models)} if attempt == 3 else {}
            response = model.generate_content(current_prompt, schema=schema, schema_name=schema_name,
                                              max_output_tokens=cap, **extra)
        except Exception as e:  # every model in the chain failed: no retry here
            return StructuredResult(None, f"LLM call failed: {e}", "llm", attempt, usage, models)
        for k, v in _usage(response).items():
            usage[k] += v
        models.append(getattr(response, "model", None) or "unknown")
        text = getattr(response, "text", "") or ""
        try:
            return StructuredResult(validate(text), None, None, attempt, usage, models, first_error)
        except ValueError as e:
            last_error = str(e)
            first_error = first_error or last_error
            if attempt == 3 or (attempt == 2 and not can_switch):
                break
            current_prompt = _repair_prompt(prompt, text, last_error, repair_hint)
    detail = last_error if last_error == first_error else f"{last_error} (first reply: {first_error})"
    return StructuredResult(None, f"invalid output after {attempt} attempts: {detail}", "invalid_output",
                            attempt, usage, models)


def _estimate(model, text: str) -> int:
    est = getattr(model, "estimate_tokens", None)
    return est(text) if callable(est) else int(len(text) / 2.5) + 1


def generate_agent_output(
    model, prompt: str, *, agent: str, round_number: int, evidence: Iterable[EvidenceRecord],
    max_output_tokens: Optional[int] = None, budget_left: Optional[Callable[[], int]] = None,
) -> StructuredResult:
    """Round-1 generation: an AgentOutput citing only the given evidence (enforced by the schema)."""
    evidence = list(evidence)
    evidence_ids = [e.id for e in evidence]
    return generate_validated(
        model, prompt,
        lambda text: validate_output(text, agent=agent, round_number=round_number, evidence_ids=evidence_ids),
        schema=output_json_schema(agent, round_number, evidence), schema_name=f"{agent}_round{round_number}",
        max_output_tokens=max_output_tokens, budget_left=budget_left,
    )


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
        "error_type": error_type,   # "data_source" | "llm" | "invalid_output" | "budget"
        "analysis": None,
        **extra,
    }
