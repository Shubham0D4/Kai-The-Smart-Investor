"""
Live model checks: every model in config/llm.yaml answers a real request under Kai's strict JSON
schema, with the configured reasoning setting, and its reply passes Kai's validation.
"""

import pytest

from agents.schema import AgentOutput, EvidenceRecord, output_json_schema, parse_model
from coordinator.llm_client import MultiLLMClient
from coordinator.llm_config import load_llm_config
from coordinator.personas import AGENT_PROMPTS, load_prompt
from mcp_server.tools.market_data import fetch_market_data
from mcp_server.tools.sec_financials import fetch_financial_statements

CFG = load_llm_config()
SPECS = {s.label: s for role in ("charlie", "delta", "gamma", "vega", "sigma") for s in CFG.chain(role)}


@pytest.fixture(scope="module")
def evidence():
    m = fetch_market_data("AAPL")
    f = fetch_financial_statements("AAPL", splits=m.get("splits"))
    assert f["success"], f.get("error")
    keep = [r for r in f["records"] if r["id"].startswith(("sec:revenue:", "sec:net_income:"))]
    return [EvidenceRecord(**r) for r in keep]


def test_every_prompt_file_loads():
    """Every agent's system prompt loads from prompts/ with its shared rules included."""
    for agent in AGENT_PROMPTS:
        text = load_prompt(agent)
        assert "{{include" not in text and "<!--" not in text
        if agent != "sigma":
            assert "STAY IN YOUR LANE" in text and "COMMS PROTOCOL" in text


@pytest.mark.parametrize("label", sorted(SPECS))
def test_model_answers_under_the_strict_schema(label, evidence, record):
    """The model replies with JSON that passes the schema and Kai's validation, citing real ids."""
    spec = SPECS[label]
    client = MultiLLMClient(load_prompt("charlie"), role="charlie")
    client.chain = [spec]
    lines = "\n".join(f"[{e.id}] {e.value:.0f} {e.unit}" for e in evidence)
    prompt = (f"## Your evidence\n{lines}\n\nGive a round-1 output for agent charlie on AAPL with two numeric "
              f"claims (one 'growth' between two fiscal years) and one inference. Be brief.")
    r = client.generate_content(prompt, schema=output_json_schema("charlie", 1, evidence),
                                max_output_tokens=1500)
    out = parse_model(r.text, AgentOutput)
    known = {e.id for e in evidence}
    assert set(out.evidence_ids()) <= known
    u = r.usage_metadata
    record("models", label, {"prompt": u.prompt_token_count, "output": u.candidates_token_count,
                             "reasoning": u.thoughts_token_count, "total": u.total_token_count,
                             "claims": len(out.claims)})


def test_debaters_start_on_different_models():
    """The four debating agents start on four different models (rate limits spread, no echo chamber)."""
    firsts = [CFG.chain(a)[0].label for a in ("charlie", "delta", "gamma", "vega")]
    assert len(set(firsts)) == 4, firsts
