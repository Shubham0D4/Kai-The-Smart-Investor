"""
Integration test for Debate Coordinator (offline: recorded fixtures + fake LLM).
"""

import inspect
import json
import os

import pytest

from coordinator.personas import RiskPersona
from coordinator.server import DebateCoordinator


def _load_log(result):
    with open(result["log_file"]) as f:
        return json.load(f)


@pytest.fixture
def cleanup():
    files = []
    yield files
    for f in files:
        if os.path.exists(f):
            os.remove(f)


def test_debate_flow(schema_llm, cleanup):
    coordinator = DebateCoordinator("AAPL", RiskPersona.ZEN, llm_client=schema_llm)
    result = coordinator.run_debate(rounds=2)
    cleanup.append(result["log_file"])

    assert result["ticker"] == "AAPL"
    assert result["rounds_completed"] == 2
    assert os.path.exists(result["log_file"])


def test_round1_logs_structured_grounded_output(schema_llm, cleanup):
    coordinator = DebateCoordinator("AAPL", RiskPersona.ALPHA, llm_client=schema_llm)
    result = coordinator.run_debate(rounds=1)
    cleanup.append(result["log_file"])

    round1 = _load_log(result)["rounds"][0]["agents"]
    assert set(round1) == {"charlie", "delta", "gamma"}
    for name, entry in round1.items():
        meta = entry["metadata"]
        assert meta["output"]["agent"] == name
        cited = {e for c in meta["output"]["claims"] for e in c.get("evidence", [])}
        assert cited <= {e["id"] for e in meta["evidence"]}
    # Gamma's DCF ran and was traced
    formulas = {f["name"] for f in round1["gamma"]["metadata"].get("formulas", [])}
    assert {"PE Ratio", "DCF Value per Share"} <= formulas


def test_sec_down_leaves_one_agent_so_no_debate(schema_llm, failing_sources, cleanup):
    # Charlie and Gamma both need SEC data; Delta alone is not a debate (PHASE4_PLAN J5)
    failing_sources.add("sec_facts")
    coordinator = DebateCoordinator("AAPL", RiskPersona.ZEN, llm_client=schema_llm)
    result = coordinator.run_debate(rounds=1)
    cleanup.append(result["log_file"])

    assert result["status"] == "NO_VERDICT" and result["reason"] == "INSUFFICIENT_EVIDENCE"
    assert any("HTTP 503" in w for w in result["warnings"])
    assert schema_llm.calls == []


def test_run_debate_takes_no_user_valuation_inputs():
    params = set(inspect.signature(DebateCoordinator.run_debate).parameters)
    assert not {"gamma_price", "gamma_eps", "gamma_growth_rate"} & params
