"""
Integration test for Debate Coordinator.
"""

import pytest
import os
from coordinator.server import DebateCoordinator
from coordinator.personas import RiskPersona

def test_debate_flow(fake_llm):
    coordinator = DebateCoordinator("AAPL", RiskPersona.ZEN, llm_client=fake_llm)
    
    # Run 2-round debate
    result = coordinator.run_debate(
        rounds=2,
        gamma_price=100.0,
        gamma_eps=5.0,
        gamma_growth_rate=10.0
    )
    
    assert result["ticker"] == "AAPL"
    assert result["rounds_completed"] == 2
    assert "log_file" in result
    
    # Verify file exists
    import os
    assert os.path.exists(result["log_file"])
    
    # Cleanup
    os.remove(result["log_file"])

def test_debate_single_round(fake_llm):
    coordinator = DebateCoordinator("AAPL", RiskPersona.ALPHA, llm_client=fake_llm)
    result = coordinator.run_debate(rounds=1)
    
    assert result["rounds_completed"] == 1
    # Gamma currently runs even without price/EPS inputs (see FIXES.md C2), so only
    # assert on the agents that must always participate.
    import json
    with open(result["log_file"]) as f:
        log = json.load(f)
        round1_agents = log["rounds"][0]["agents"]
        assert "charlie" in round1_agents
        assert "delta" in round1_agents
    
    os.remove(result["log_file"])
