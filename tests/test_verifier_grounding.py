"""Offline tests: the numeric verifier accepts honest presentation and rejects false claims."""
import pytest
from agents.schema import EvidenceRecord, NumericClaim
from judge.verifier import verify_numeric


def ev(i, v, unit="USD"):
    return EvidenceRecord(id=i, label=i, value=v, unit=unit, period=i, source="test")


E = {e.id: e for e in [ev("sec:Revenue:FY2025", 1000e6), ev("sec:Revenue:FY2023", 810e6), ev("sec:NetIncome:FY2025", 244e6),
                       ev("sec:Debt:FY2025", 50e6)]}


def claim(text, op, ids, value=None, unit=""):
    return NumericClaim(id="C1", kind="numeric", text=text, value=value, unit=unit, op=op, evidence=ids)


def ok(*a, **k):
    return verify_numeric(claim(*a, **k), E, 0.01).verified


def reason(*a, **k):
    return verify_numeric(claim(*a, **k), E, 0.01).reason


R = ["sec:Revenue:FY2025"]
G = ["sec:Revenue:FY2025", "sec:Revenue:FY2023"]


def test_derived_value_is_computed_and_wording_checked():
    assert ok("Revenue grew 23.5% over two years", "growth", G)
    assert ok("Revenue compounded at 11.1% per year", "cagr", G)
    assert ok("Net margin is 24.4%", "ratio", ["sec:NetIncome:FY2025", "sec:Revenue:FY2025"])
    assert ok("Revenue was $1.0B", "equals", R)


def test_wrong_figure_in_wording_fails():
    assert reason("Revenue grew 40% over two years", "growth", G) == "ungrounded_number"
    assert reason("Revenue compounded at 23.5% per year", "cagr", G) == "ungrounded_number"  # total, not annual
    assert reason("Revenue was $2.0B", "equals", R) == "ungrounded_number"


def test_uncited_figure_in_wording_fails():
    assert reason("Revenue grew 23.5%, versus debt of $50M", "growth", G) == "ungrounded_number"
    assert ok("Revenue grew 23.5% while debt was $50M", "growth", G + []) is False


def _skip_old():
    pass


def test_direction_must_match():
    assert reason("Revenue fell 23.5%", "growth", G) == "direction_mismatch"
    assert reason("Revenue fell 23.5%", "growth", G[::-1]) == "direction_mismatch"
    assert ok("Revenue rose 23.5% after earlier weakness fell", "growth", G)  # mixed wording is not judged


def test_citation_order_does_not_change_growth_direction():
    # "from FY2023 to FY2025" invites oldest-first citation; growth/cagr are newer vs older
    # regardless of the order the records were cited in (mega-cap breaker root cause, Nov 19).
    assert ok("Revenue grew 23.5% from $810M FY2023 to $1.0B FY2025", "growth", G[::-1])
    assert ok("Revenue compounded at 11.1% per year FY2023-FY2025", "cagr", G[::-1])
    assert ok("Revenue grew 23.5%", "growth", G)  # newest-first, the previous convention, still works
    assert reason("Revenue grew 17% from FY2023 to FY2025", "growth", G[::-1]) == "ungrounded_number"  # wrong figure still fails
    assert reason("Revenue fell 11.1% per year", "cagr", G[::-1]) == "direction_mismatch"


def test_explicit_value_still_strict():
    assert ok("Revenue grew 23.5%", "growth", G, value=0.2346, unit="ratio")
    assert ok("Revenue grew 23.5%", "growth", G, value=23.5, unit="percent")
    assert ok("Revenue grew 23.5%", "growth", G, value=23.46, unit="percent")            # honest rounding
    assert reason("Revenue grew 23.5%", "growth", G, value=0.235, unit="percent") == "value_mismatch"  # fraction as percent: rejected
    assert reason("Revenue was $1.0B", "equals", R, value=1000, unit="USD") == "value_mismatch"        # millions as USD: rejected
    assert reason("Revenue grew 30%", "growth", G, value=30, unit="percent") == "value_mismatch"


def test_rounding_is_bounded():
    assert reason("Revenue grew 25%", "growth", G, value=25, unit="percent") == "value_mismatch"


def test_malformed_numeric_claim_is_dropped_not_credited():
    import json
    from agents.schema import validate_output
    good = lambda i: {"id": f"C{i}", "kind": "numeric", "text": "Revenue was $1.0B", "op": "equals", "evidence": ["sec:Revenue:FY2025"]}
    data = {"agent": "charlie", "round": 1, "stance": "neutral", "confidence": 50, "risks": [],
            "claims": [good(1), good(2), good(3),
                       {"id": "C4", "kind": "numeric", "text": "x", "op": "sum", "evidence": ["sec:Revenue:FY2025"]},
                       {"id": "C5", "kind": "numeric", "text": "x", "op": "growth", "evidence": ["sec:Revenue:FY2025"]},
                       {"id": "C6", "kind": "inference", "text": "so", "based_on": ["C4"]}]}
    out = validate_output(json.dumps(data), agent="charlie", round_number=1, evidence_ids=["sec:Revenue:FY2025"])
    assert [c.id for c in out.claims] == ["C1", "C2", "C3"] and out.dropped_malformed == ["C4", "C5", "C6"]
    data["claims"][0]["evidence"] = ["sec:Nope"]              # unknown evidence id is still a hard failure
    with pytest.raises(ValueError):
        validate_output(json.dumps(data), agent="charlie", round_number=1, evidence_ids=["sec:Revenue:FY2025"])


def test_reframed_but_derivable_figure_passes_while_wrong_ones_fail():
    PX = {"mkt:price": ev("mkt:price", 148.1), "calc:fv": ev("calc:fv", 93.1)}
    c = lambda t: verify_numeric(claim(t, "diff", ["calc:fv", "mkt:price"]), PX, 0.01)
    assert c("Price of $148.1 is 59% above fair value of $93.1").verified          # 148.1/93.1-1 = 59.1%
    assert c("Price of $148.1 is 37% below... fair value of $93.1").verified      # 1-93.1/148.1 = 37.1%
    assert c("Price of $148.1 is 70% above fair value of $93.1").reason == "ungrounded_number"
    assert c("Fair value of $93.1 beats an EPS of $7.90").reason == "ungrounded_number"


def test_model_written_value_and_unit_are_ignored_but_wording_still_checked():
    import json
    from agents.schema import validate_output
    from judge.verifier import verify_numeric
    ids = ["sec:Revenue:FY2025"]
    mk = lambda text: {"agent": "charlie", "round": 1, "stance": "neutral", "confidence": 50, "risks": [],
                       "claims": [{"id": f"C{i}", "kind": "numeric", "text": text, "op": "equals", "evidence": ids,
                                   "value": 1.0, "unit": "B"} for i in (1, 2, 3)]}
    out = validate_output(json.dumps(mk("Revenue was $1.0B")), agent="charlie", round_number=1, evidence_ids=ids)
    assert all(c.value is None and c.unit == "" for c in out.claims)           # stated "1.0 B" no longer decides anything
    assert verify_numeric(out.claims[0], E, 0.01).verified
    bad = validate_output(json.dumps(mk("Revenue was $3.0B")), agent="charlie", round_number=1, evidence_ids=ids)
    assert verify_numeric(bad.claims[0], E, 0.01).reason == "ungrounded_number"  # a wrong figure in the wording still fails
