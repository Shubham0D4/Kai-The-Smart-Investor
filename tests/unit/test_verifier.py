"""
PHASE4_PLAN.md §9, tests V1-V15: claim verification and faithfulness (FIXES.md C4).

Evidence comes from the recorded AAPL SEC fixture:
revenue FY2026 = 478,834,000,000; FY2025 = 416,161,000,000 (true growth 15.06%).
"""

import json
from pathlib import Path

import pytest

from agents.schema import AgentOutput, EvidenceRecord
from coordinator.config import load_thresholds
from judge.verifier import (
    extract_quantities, overall_faithfulness, ungrounded_numbers, verify_output,
)
from mcp_server.tools.sec_financials import parse_company_facts

FIXTURES = Path(__file__).parent.parent / "fixtures"
CFG = load_thresholds().verification


@pytest.fixture(scope="module")
def evidence():
    facts = json.loads((FIXTURES / "sec_companyfacts_aapl.json").read_text())
    records = [EvidenceRecord(**r) for r in parse_company_facts(facts, "0000320193")["records"]]
    records.append(EvidenceRecord(
        id="news:1", label="Why Did AAPL, COST, JNJ Stocks Hit 52-Week Highs Today?",
        text="Investors rotated toward defensive stocks amid AI-sector uncertainty.", source="test"))
    records.append(EvidenceRecord(id="calc:zero", label="Zero", value=0.0, unit="USD", source="test"))
    return records


def numeric(cid, value, op="equals", evidence=("sec:revenue:FY2026",), unit="USD", text=None):
    return {"id": cid, "kind": "numeric", "text": text or f"claim {cid}", "value": value, "unit": unit,
            "op": op, "evidence": list(evidence)}


def quote(cid, q, eid="news:1", text="Coverage point"):
    return {"id": cid, "kind": "quote", "text": text, "quote": q, "evidence": [eid]}


def inference(cid, based_on, text="Overall view"):
    return {"id": cid, "kind": "inference", "text": text, "based_on": list(based_on)}


def check(evidence, *claims):
    out = AgentOutput.model_validate({"agent": "charlie", "round": 1, "stance": "neutral",
                                      "confidence": 50, "claims": list(claims)})
    v = verify_output(out, evidence, CFG)
    return v, {c.claim_id: c for c in v.checks}


GROWTH = ("sec:revenue:FY2026", "sec:revenue:FY2025")


def test_v1_equals_true_value(evidence):
    _, c = check(evidence, numeric("C1", 478_834_000_000))
    assert c["C1"].verified


def test_v2_growth_within_tolerance(evidence):
    _, c = check(evidence, numeric("C1", 0.15, op="growth", evidence=GROWTH, unit="ratio"))
    assert c["C1"].verified
    assert c["C1"].expected == pytest.approx(0.150600, abs=1e-5)


def test_v3_number_not_in_the_data(evidence):
    _, c = check(evidence, numeric("C1", 520_000_000_000, text="Revenue reached $520bn in FY2026"))
    assert not c["C1"].verified
    assert c["C1"].reason == "value_mismatch"
    assert c["C1"].expected == 478_834_000_000
    assert c["C1"].stated == 520_000_000_000


def test_v4_wrong_growth(evidence):
    _, c = check(evidence, numeric("C1", 0.06, op="growth", evidence=GROWTH, unit="ratio"))
    assert c["C1"].reason == "value_mismatch"


def test_v5_units_are_scaled(evidence):
    _, c = check(evidence,
                 numeric("C1", 15.06, op="growth", evidence=GROWTH, unit="percent"),
                 numeric("C2", 478.834, unit="USD_bn"))
    assert c["C1"].verified and c["C2"].verified


def test_v6_unknown_evidence_id(evidence):
    _, c = check(evidence, numeric("C1", 1.0, evidence=("sec:revenue:FY1999",)))
    assert c["C1"].reason == "unknown_evidence"


def test_v7_number_only_in_the_wording(evidence):
    _, c = check(evidence, numeric("C1", 478_834_000_000, text="Revenue of $600bn in FY2026"))
    assert not c["C1"].verified
    assert c["C1"].reason == "ungrounded_number"
    assert "$600bn" in c["C1"].detail


def test_v7b_rounded_true_number_in_wording_is_fine(evidence):
    _, c = check(evidence, numeric("C1", 478_834_000_000, text="Revenue of $478.8bn (about $0.5 trillion)"))
    assert c["C1"].verified


def test_v8_inference_with_invented_number(evidence):
    _, c = check(evidence, numeric("C1", 478_834_000_000),
                 inference("C2", ["C1"], text="Strong sales imply 40% upside"))
    assert c["C1"].verified
    assert c["C2"].reason == "ungrounded_number"


def test_v8b_inference_may_use_premise_numbers(evidence):
    _, c = check(evidence, numeric("C1", 0.1506, op="growth", evidence=GROWTH, unit="ratio"),
                 inference("C2", ["C1"], text="15% growth points to strong demand"))
    assert c["C2"].verified


def test_v9_years_and_counts_are_not_flagged(evidence):
    _, c = check(evidence, numeric("C1", 478_834_000_000, text="FY2026 revenue, best in 5 years of 10-K data"))
    assert c["C1"].verified
    assert extract_quantities("FY2026 over 5 years, Q3 10-K") == []


def test_v10_quote_matches_despite_case_and_spacing(evidence):
    _, c = check(evidence, quote("C1", "INVESTORS  rotated toward\n defensive stocks"))
    assert c["C1"].verified


def test_v11_quote_not_found_and_too_short(evidence):
    _, c = check(evidence, quote("C1", "investors fled to bonds after the report"), quote("C2", "Apple"))
    assert c["C1"].reason == "quote_not_found"
    assert c["C2"].reason == "quote_too_short"


def test_v12_inference_on_a_failed_claim(evidence):
    _, c = check(evidence, numeric("C1", 478_834_000_000), numeric("C2", 1.0),
                 inference("C3", ["C1", "C2"]))
    assert c["C3"].reason == "unsupported_inference"
    assert not c["C3"].checkable


def test_v13_faithfulness_three_of_four(evidence):
    v, _ = check(evidence, numeric("C1", 478_834_000_000), numeric("C2", 416_161_000_000,
                 evidence=("sec:revenue:FY2025",)), quote("C3", "rotated toward defensive stocks"),
                 numeric("C4", 1.0))
    assert (v.checkable, v.verified, v.faithfulness) == (4, 3, 0.75)


def test_v14_floor_of_three_checkable_claims(evidence):
    v, _ = check(evidence, numeric("C1", 478_834_000_000), inference("C2", ["C1"]), inference("C3", ["C2"]))
    assert v.checkable == 1 and v.verified == 1
    assert v.faithfulness == pytest.approx(1 / 3, abs=1e-6)


def test_v15_zero_denominator_fails_without_exception(evidence):
    _, c = check(evidence, numeric("C1", 1.0, op="ratio", evidence=("sec:revenue:FY2026", "calc:zero")))
    assert c["C1"].reason == "bad_operation"


def test_inference_cycle_fails(evidence):
    _, c = check(evidence, numeric("C1", 478_834_000_000), inference("C2", ["C1", "C3"]),
                 inference("C3", ["C2"]))
    assert not c["C2"].verified and not c["C3"].verified
    assert {c["C2"].reason, c["C3"].reason} <= {"cycle", "unsupported_inference"}


def test_overall_faithfulness_uses_floored_denominators(evidence):
    a, _ = check(evidence, numeric("C1", 478_834_000_000))                       # 1 / max(1, 3)
    b, _ = check(evidence, numeric("C1", 478_834_000_000), numeric("C2", 1.0),
                 numeric("C3", 478_834_000_000), numeric("C4", 478_834_000_000))  # 3 / 4
    assert overall_faithfulness([a, b]) == pytest.approx(4 / 7, abs=1e-6)


def test_ungrounded_numbers_ignores_sign():
    assert ungrounded_numbers("revenue fell 5%", [-0.05], 0.01) == []
