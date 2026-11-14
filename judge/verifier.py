"""
Claim verification and faithfulness (FIXES.md C4, PHASE4_PLAN.md §4).

Pure functions: no I/O and no LLM. Each claim an agent makes is checked against the evidence
records it cites:

- numeric claims are recomputed from the cited values (equals / growth / ratio / diff) and must
  match the stated value within a relative tolerance;
- quote claims must appear verbatim (after normalising case, whitespace and typographic quotes)
  in the cited article's title or summary;
- any quantity written in a claim's wording ($, %, x, k/mn/bn/tn) must also be backed by a value
  the claim is entitled to use, so a number cannot be smuggled in through the text;
- inference claims are verified only if everything they rest on is verified.

Faithfulness = verified checkable claims / max(checkable claims, floor). Inferences are not
counted as checkable.
"""

import html
import re
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel

from agents.schema import AgentOutput, EvidenceRecord, InferenceClaim, NumericClaim, QuoteClaim
from coordinator.config import VerificationConfig

if TYPE_CHECKING:
    from quant.models import QuantReport

QUANT_PREFIX = "quant:"


class ClaimCheck(BaseModel):
    claim_id: str
    kind: str
    checkable: bool
    verified: bool
    reason: Optional[str] = None     # unknown_evidence | no_value | value_mismatch | bad_operation |
                                     # quote_not_found | quote_too_short | ungrounded_number |
                                     # unsupported_inference | cycle
    expected: Optional[float] = None
    stated: Optional[float] = None
    detail: Optional[str] = None


class AgentVerification(BaseModel):
    agent: str
    round: int
    checks: List[ClaimCheck]
    checkable: int
    verified: int
    denominator: int                 # max(checkable, floor)
    faithfulness: float              # 0..1

    def failed(self) -> List[ClaimCheck]:
        return [c for c in self.checks if not c.verified]


# --------------------------------------------------------------------------- units and numbers

_UNIT_SCALE = {
    "percent": 0.01, "%": 0.01, "pct": 0.01,
    "usd_k": 1e3, "usd_thousand": 1e3,
    "usd_mn": 1e6, "usd_m": 1e6, "usd_million": 1e6, "million": 1e6,
    "usd_bn": 1e9, "usd_b": 1e9, "usd_billion": 1e9, "billion": 1e9,
    "usd_tn": 1e12, "usd_trillion": 1e12, "trillion": 1e12,
}


def unit_scale(unit: Optional[str]) -> float:
    """Multiplier that converts a stated value in `unit` to the base unit of the evidence."""
    return _UNIT_SCALE.get((unit or "").strip().lower().replace(" ", "_"), 1.0)


_SUFFIX_SCALE = {
    "%": 0.01, "percent": 0.01, "x": 1.0,
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "tn": 1e12, "trillion": 1e12,
}

# A quantity needs a marker: a leading "$" and/or a unit suffix. Digits glued to letters
# (FY2026, 10-K, Q3) are not quantities.
_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?P<cur>\$)?\s?(?P<num>[-−]?\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<suf>%|percent\b|x\b|k\b|thousand\b|mn\b|m\b|million\b|bn\b|b\b|billion\b|tn\b|t\b|trillion\b))?",
    re.IGNORECASE,
)


class Quantity(BaseModel):
    text: str
    value: float
    half_unit: float                 # half of the last stated digit, scaled: rounding allowance


def extract_quantities(text: str) -> List[Quantity]:
    """Quantities written with a currency sign or unit suffix, e.g. "$478.8bn", "15%", "39.5x"."""
    out = []
    for m in _QUANTITY_RE.finditer(text or ""):
        cur, suf = m.group("cur"), (m.group("suf") or "").lower()
        if not cur and not suf:
            continue
        raw = m.group("num").replace(",", "").replace("−", "-")
        if suf == "m" and not cur and "." not in raw and len(raw.lstrip("-")) <= 2:
            continue    # "1m", "3m", "12m": a time window (1-month return), not 1 million
        try:
            num = float(raw)
        except ValueError:
            continue
        scale = _SUFFIX_SCALE.get(suf, 1.0)
        decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
        out.append(Quantity(text=m.group(0).strip(), value=num * scale,
                            half_unit=0.5 * 10 ** (-decimals) * scale))
    return out


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1e-9)


def ungrounded_numbers(text: str, allowed: Iterable[float], tol: float) -> List[str]:
    """Quantities in `text` that match none of the allowed values.

    A quantity matches when its magnitude equals an allowed magnitude within the relative
    tolerance, or within the rounding implied by how it is written ("$0.5 trillion" allows
    +/-0.05 trillion). Sign is ignored because wording carries direction ("fell 5%").
    """
    allowed = [abs(a) for a in allowed if a is not None]
    bad = []
    for q in extract_quantities(text):
        v = abs(q.value)
        if not any(_close(v, a, tol) or abs(v - a) <= q.half_unit for a in allowed):
            bad.append(q.text)
    return bad


# --------------------------------------------------------------------------- claim checks

def _expected(op: str, values: List[float]) -> Optional[float]:
    e0 = values[0]
    if op == "equals":
        return e0
    e1 = values[1]
    if op == "diff":
        return e0 - e1
    if e1 == 0:
        return None
    return e0 / e1 - 1 if op == "growth" else e0 / e1


def verify_numeric(claim: NumericClaim, evidence: Dict[str, EvidenceRecord], tol: float) -> ClaimCheck:
    def fail(reason, **kw):
        return ClaimCheck(claim_id=claim.id, kind="numeric", checkable=True, verified=False,
                          reason=reason, **kw)

    missing = [e for e in claim.evidence if e not in evidence]
    if missing:
        return fail("unknown_evidence", detail=f"not in fetched data: {missing}")
    values = [evidence[e].value for e in claim.evidence]
    if any(v is None for v in values):
        return fail("no_value", detail="cited evidence has no numeric value")
    expected = _expected(claim.op, values)
    stated = claim.value * unit_scale(claim.unit)
    if expected is None:
        return fail("bad_operation", stated=stated, detail=f"{claim.op} with a zero denominator")
    if not _close(stated, expected, tol):
        return fail("value_mismatch", expected=expected, stated=stated,
                    detail=f"stated {stated:,.6g}, data gives {expected:,.6g}")
    bad = ungrounded_numbers(claim.text, [stated, expected, claim.value, *values], tol)
    if bad:
        return fail("ungrounded_number", expected=expected, stated=stated,
                    detail=f"wording contains numbers not in the data: {bad}")
    return ClaimCheck(claim_id=claim.id, kind="numeric", checkable=True, verified=True,
                      expected=expected, stated=stated)


_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _normalise(text: str) -> str:
    t = html.unescape(text or "").translate(_QUOTE_MAP).lower()
    return re.sub(r"\s+", " ", t).strip()


def _article_text(rec: EvidenceRecord) -> str:
    title = (rec.label or "").replace(" [headline only]", "")
    return f"{title} {rec.text or ''}"


def verify_quote(claim: QuoteClaim, evidence: Dict[str, EvidenceRecord], min_chars: int,
                 tol: float = 0.01) -> ClaimCheck:
    def fail(reason, detail=None):
        return ClaimCheck(claim_id=claim.id, kind="quote", checkable=True, verified=False,
                          reason=reason, detail=detail)

    eid = claim.evidence[0]
    if eid not in evidence:
        return fail("unknown_evidence", f"not in fetched data: {eid}")
    quote = _normalise(claim.quote)
    if len(quote) < min_chars:
        return fail("quote_too_short", f"{len(quote)} characters < {min_chars}")
    source = _article_text(evidence[eid])
    if quote not in _normalise(source):
        return fail("quote_not_found", f"quote is not in {eid}")
    allowed = [q.value for q in extract_quantities(source)]
    bad = ungrounded_numbers(claim.text, allowed, tol)
    if bad:
        return fail("ungrounded_number", f"wording contains numbers not in the article: {bad}")
    return ClaimCheck(claim_id=claim.id, kind="quote", checkable=True, verified=True)


def verify_inference(claim: InferenceClaim, checks: Dict[str, ClaimCheck],
                     allowed: Iterable[float] = (), tol: float = 0.01) -> ClaimCheck:
    """`checks` must already contain the results for the claims it is based on."""
    def fail(reason, detail=None):
        return ClaimCheck(claim_id=claim.id, kind="inference", checkable=False, verified=False,
                          reason=reason, detail=detail)

    unsupported = [b for b in claim.based_on if b not in checks or not checks[b].verified]
    if unsupported:
        return fail("unsupported_inference", f"rests on unverified claims: {unsupported}")
    bad = ungrounded_numbers(claim.text, allowed, tol)
    if bad:
        return fail("ungrounded_number", f"wording contains numbers not in its premises: {bad}")
    return ClaimCheck(claim_id=claim.id, kind="inference", checkable=False, verified=True)


# --------------------------------------------------------------------------- whole output

def resolve_evidence(evidence: List[EvidenceRecord],
                     quant_report: Optional["QuantReport"] = None) -> Dict[str, EvidenceRecord]:
    """Evidence by id. With a quant report, every `quant:` id is resolved against the report
    itself (the agent's own copies are ignored), and unavailable fields resolve to a record with
    no value, so citing them fails with `no_value`; ids the report lacks fail `unknown_evidence`."""
    if quant_report is None:
        return {e.id: e for e in evidence}
    by_id = {e.id: e for e in evidence if not e.id.startswith(QUANT_PREFIX)}
    for rec in quant_report.evidence_records(include_unavailable=True):
        by_id[rec["id"]] = EvidenceRecord(**rec)
    return by_id


def verify_output(output: AgentOutput, evidence: List[EvidenceRecord], cfg: VerificationConfig,
                  quant_report: Optional["QuantReport"] = None) -> AgentVerification:
    by_id = resolve_evidence(evidence, quant_report)
    claims = {c.id: c for c in output.claims}
    tol = cfg.numeric_tolerance
    checks: Dict[str, ClaimCheck] = {}

    for c in output.claims:
        if isinstance(c, NumericClaim):
            checks[c.id] = verify_numeric(c, by_id, tol)
        elif isinstance(c, QuoteClaim):
            checks[c.id] = verify_quote(c, by_id, cfg.min_quote_chars, tol)

    def premise_values(cid: str, seen: Set[str]) -> List[float]:
        c = claims.get(cid)
        if c is None or cid in seen:
            return []
        seen.add(cid)
        if isinstance(c, NumericClaim):
            vals = [c.value * unit_scale(c.unit), c.value]
            vals += [by_id[e].value for e in c.evidence if e in by_id and by_id[e].value is not None]
            if checks.get(cid) and checks[cid].expected is not None:
                vals.append(checks[cid].expected)
            return vals
        if isinstance(c, QuoteClaim):
            return [q.value for e in c.evidence if e in by_id for q in extract_quantities(_article_text(by_id[e]))]
        return [v for b in c.based_on for v in premise_values(b, seen)]

    def resolve(cid: str, stack: Set[str]) -> None:
        c = claims[cid]
        if cid in checks:
            return
        if cid in stack:
            checks[cid] = ClaimCheck(claim_id=cid, kind="inference", checkable=False, verified=False,
                                     reason="cycle", detail="inference depends on itself")
            return
        stack.add(cid)
        for b in c.based_on:
            if b in claims and b not in checks:
                resolve(b, stack)
        stack.discard(cid)
        if cid not in checks:
            checks[cid] = verify_inference(c, checks, premise_values(cid, set()), tol)

    for c in output.claims:
        if isinstance(c, InferenceClaim):
            resolve(c.id, set())

    ordered = [checks[c.id] for c in output.claims]
    checkable = sum(c.checkable for c in ordered)
    verified = sum(c.checkable and c.verified for c in ordered)
    denominator = max(checkable, cfg.min_checkable_claims)
    return AgentVerification(agent=output.agent, round=output.round, checks=ordered,
                             checkable=checkable, verified=verified, denominator=denominator,
                             faithfulness=round(verified / denominator, 6))


def overall_faithfulness(verifications: List[AgentVerification]) -> float:
    """Sum of verified checkable claims over the sum of floored denominators (0..1)."""
    den = sum(v.denominator for v in verifications)
    return round(sum(v.verified for v in verifications) / den, 6) if den else 0.0
