"""
Unit tests for Agents (Charlie, Delta, Gamma, Sigma).

Charlie, Delta and Gamma run against recorded SEC / news / yfinance fixtures (tests/fixtures)
with a fake LLM. Each agent is tested for: grounded success, data-source failure (no LLM
call, explicit error), invalid JSON retried once then accepted, and invalid JSON twice
failing cleanly.
"""

import json

import pytest

from agents.charlie_fundamental.agent import CharlieAgent
from agents.delta_sentiment.agent import DeltaAgent
from agents.gamma_valuation.agent import GammaAgent
from agents.schema import AgentOutput
from coordinator.llm_client import FakeLLMClient
from coordinator.personas import RiskPersona
from judge.sigma_agent import SigmaAgent


def _retry_then_valid(valid_output_for):
    """First reply is junk; the second is valid for whatever the retry prompt asks."""
    state = {"n": 0}

    def respond(prompt):
        state["n"] += 1
        return "Sure! Here is my analysis: bullish." if state["n"] == 1 else valid_output_for(prompt)
    return FakeLLMClient(responses=respond)


AGENTS = {
    "charlie": lambda llm: (CharlieAgent(llm_client=llm), lambda a: a.analyze("AAPL")),
    "delta": lambda llm: (DeltaAgent(llm_client=llm), lambda a: a.analyze("AAPL", days=7)),
    "gamma": lambda llm: (GammaAgent(llm_client=llm), lambda a: a.analyze("AAPL")),
}


class TestCharlieAgent:
    def test_prompt_contains_sec_numbers_and_output_validates(self, schema_llm):
        result = CharlieAgent(persona=RiskPersona.ZEN, llm_client=schema_llm).analyze("AAPL")

        assert "error" not in result, result.get("error")
        prompt = schema_llm.calls[0]
        # Real statement numbers from the recorded company facts are in the prompt
        assert "[sec:revenue:FY2026] Revenue FY2026: 478,834,000,000 USD" in prompt
        assert "[sec:eps_diluted:FY2026]" in prompt and "[sec:operating_cash_flow:FY2022]" in prompt
        # The filing link is a reference in the result, not part of the evidence
        assert result["filing_analyzed"]["form"] == "10-K"
        assert "Archives/edgar" not in prompt.split("EVIDENCE:")[0]
        AgentOutput.model_validate(result["output"])
        evidence_ids = {e["id"] for e in result["evidence"]}
        assert set(AgentOutput.model_validate(result["output"]).evidence_ids()) <= evidence_ids

    def test_filing_link_failure_is_only_a_warning(self, schema_llm, monkeypatch):
        monkeypatch.setattr("agents.charlie_fundamental.agent.fetch_filing_data",
                            lambda *a, **k: {"success": False, "error": "boom"})
        result = CharlieAgent(llm_client=schema_llm).analyze("AAPL")
        assert "error" not in result
        assert result["filing_analyzed"] is None
        assert any("link unavailable" in w for w in result["warnings"])

    def test_unknown_ticker_is_data_source_error(self, schema_llm):
        result = CharlieAgent(llm_client=schema_llm).analyze("ZZZZ")
        assert result["error_type"] == "data_source"
        assert "not found" in result["error"]
        assert schema_llm.calls == []


class TestDeltaAgent:
    def test_prompt_contains_summaries_not_just_titles(self, schema_llm):
        result = DeltaAgent(persona=RiskPersona.ALPHA, llm_client=schema_llm).analyze("AAPL", days=7)

        assert "error" not in result, result.get("error")
        prompt = schema_llm.calls[0]
        assert "summary: Investors rotated toward defensive stocks amid AI-sector uncertainty." in prompt
        news = result["news_analyzed"]
        assert news["article_count"] > 0
        assert set(news["providers_ok"]) == {"yfinance", "yahoo_rss", "nasdaq_rss", "google_rss"}
        assert news["headline_only_count"] < news["article_count"]

    def test_one_provider_down_still_works_with_warning(self, schema_llm, failing_sources):
        failing_sources.add("yahoo_rss")
        result = DeltaAgent(llm_client=schema_llm).analyze("AAPL", days=7)
        assert "error" not in result
        assert result["news_analyzed"]["providers_failed"] == ["yahoo_rss"]
        assert any("yahoo_rss failed" in w for w in result["warnings"])


class TestGammaAgent:
    def test_fetches_inputs_and_runs_dcf_and_risk(self, schema_llm):
        result = GammaAgent(persona=RiskPersona.SIGMA, llm_client=schema_llm).analyze("AAPL")

        assert "error" not in result, result.get("error")
        v, r, p = result["valuation_metrics"], result["risk_metrics"], result["parameters"]
        # Inputs come from fixtures: yfinance price, SEC diluted EPS FY2026 = 8.98
        assert p["eps"] == 8.98
        assert p["current_price"] == pytest.approx(354.88, abs=0.01)
        assert v["pe_ratio"] == round(p["current_price"] / 8.98, 2)
        # DCF actually ran, with net debt subtracted
        dcf = v["dcf_analysis"]
        assert dcf is not None and dcf["value_per_share"] > 0
        assert dcf["equity_value"] == pytest.approx(dcf["enterprise_value"] - p["net_debt"], abs=1)
        assert p["net_debt"] == 78114000000 + 1997000000 - 36305000000
        assert v["margin_of_safety"] is not None
        # Risk metrics ran on ~1y of daily closes
        assert r["price_count"] > 200
        assert r["ma_200"] is not None and r["volatility_annual"] > 0
        prompt = schema_llm.calls[0]
        for eid in ("mkt:price", "calc:dcf.value_per_share", "calc:volatility_annual", "calc:fcf:FY2026"):
            assert f"[{eid}]" in prompt

    def test_no_user_supplied_inputs(self):
        import inspect
        params = inspect.signature(GammaAgent.analyze).parameters
        assert not {"price", "eps", "growth_rate"} & set(params)

    @pytest.mark.parametrize("source", ["yfinance", "sec"])
    def test_data_source_failure(self, schema_llm, failing_sources, source):
        failing_sources.add(source)
        result = GammaAgent(llm_client=schema_llm).analyze("AAPL")
        assert result["error_type"] == "data_source"
        assert result["analysis"] is None
        assert schema_llm.calls == []


# ---------------------------------------------------------------- shared behaviour, all three

@pytest.mark.parametrize("name", list(AGENTS))
def test_all_sources_down_returns_explicit_error_without_llm(name, schema_llm, failing_sources):
    failing_sources.update({"sec", "yfinance", "yahoo_rss", "nasdaq_rss", "google_rss"})
    agent, run = AGENTS[name](schema_llm)
    result = run(agent)
    assert result["error_type"] == "data_source"
    assert result["error"]
    assert result["analysis"] is None
    assert schema_llm.calls == [], "must not fall back to the model's own knowledge"


@pytest.mark.parametrize("name", list(AGENTS))
def test_invalid_json_is_retried_once(name, make_valid_output):
    llm = _retry_then_valid(make_valid_output)
    agent, run = AGENTS[name](llm)
    result = run(agent)
    assert "error" not in result, result.get("error")
    assert result["attempts"] == 2
    assert len(llm.calls) == 2
    assert "Your previous reply was rejected: invalid JSON" in llm.calls[1]


@pytest.mark.parametrize("name", list(AGENTS))
def test_invalid_json_twice_fails_cleanly(name, fake_llm):
    agent, run = AGENTS[name](fake_llm)          # always "Mocked Analysis Response"
    result = run(agent)
    assert result["error_type"] == "invalid_output"
    assert "after 2 attempts" in result["error"]
    assert result["analysis"] is None
    assert len(fake_llm.calls) == 2


def test_citing_unknown_evidence_is_rejected(make_valid_output):
    def respond(prompt):
        data = json.loads(make_valid_output(prompt))
        data["claims"][0]["evidence"] = ["sec:made_up:FY1999"]
        return json.dumps(data)
    llm = FakeLLMClient(responses=respond)
    result = CharlieAgent(llm_client=llm).analyze("AAPL")
    assert result["error_type"] == "invalid_output"
    assert "sec:made_up:FY1999" in result["error"]


class TestSigmaAgent:
    def test_evaluate_debate(self, fake_llm, tmp_path):
        agent = SigmaAgent(llm_client=fake_llm)
        debate_log = {
            "debate_id": "test_debate",
            "ticker": "AAPL",
            "persona": "zen",
            "metadata": {"agents_participated": ["charlie", "delta"], "mcp_tools_used": ["fetch_filing_data"]},
            "rounds": [
                {"round": 1, "agents": {
                    "charlie": {"analysis": "Buy because growth.", "grounding_sources": ["10-K"]},
                    "delta": {"analysis": "Sell because bad news.", "grounding_sources": ["News"]},
                }},
                {"round": 2, "agents": {
                    "charlie": {"analysis": "Still Buy.", "grounding_sources": []},
                }},
            ],
        }
        path = tmp_path / "debate.json"
        path.write_text(json.dumps(debate_log))

        result = agent.evaluate_debate(str(path))

        assert result["debate_id"] == "test_debate"
        assert result["evaluation"]["faithfulness_score"] > 0
        assert result["evaluation"]["consistency_score"] > 0
        assert "final_recommendation" in result

    def test_consistency_calculation(self, fake_llm):
        agent = SigmaAgent(llm_client=fake_llm)
        debate_log = {
            "rounds": [
                {"agents": {"charlie": {"analysis": "Buy strong growth"}}},
                {"agents": {"charlie": {"analysis": "Sell weak internals"}}},
            ]
        }
        assert agent.evaluate_consistency(debate_log) < 100
