"""
Vega - Catalyst & Options Agent
Looks forward: the next earnings report, analyst consensus and its revisions, and the options
market's implied volatility and implied move. Argues from those numbers only; all of them are
fetched and computed in code (mcp_server/tools/catalysts.py).
"""

import os
import sys
import argparse
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from coordinator.personas import get_vega_system_instruction
from mcp_server.tools.catalysts import fetch_catalysts
from mcp_server.tools.market_data import fetch_market_data
from agents.schema import (
    EvidenceRecord, agent_error, evidence_block, generate_agent_output,
    render_output, schema_instructions,
)
from coordinator.llm_client import MultiLLMClient
from coordinator.llm_config import load_llm_config
from dotenv import load_dotenv

load_dotenv()

# Market records Vega may cite next to the catalysts (same snapshot as every other agent)
MARKET_IDS = ("mkt:price", "mkt:return_1m", "mkt:return_3m")


class VegaAgent:
    """Vega - Catalyst & Options Specialist."""

    def __init__(self, llm_client=None):
        self.name = "Vega"
        self.role = "Catalyst & Options Specialist"
        system_instruction = get_vega_system_instruction()
        self.model = llm_client if llm_client is not None else MultiLLMClient(
            system_instruction=system_instruction, role="vega")

    def analyze(self, ticker: str, catalysts: Optional[Dict] = None, market: Optional[Dict] = None,
                budget_left=None) -> Dict:
        """
        Args:
            catalysts: Pre-fetched fetch_catalysts result; fetched here when None.
            market: Pre-fetched fetch_market_data result (the debate's snapshot).
            budget_left: Optional callable returning the tokens this agent may still spend.

        Returns a result dict with the validated output, or an explicit error. No LLM call is
        made when no catalyst data could be fetched.
        """
        if market is None:
            market = fetch_market_data(ticker)
        if catalysts is None:
            if not market.get("success"):
                return agent_error("vega", ticker, market.get("error", "market data unavailable"), "data_source")
            catalysts = fetch_catalysts(ticker, market["price"], market["market_date"])
        if not catalysts.get("success"):
            return agent_error("vega", ticker, catalysts.get("error", "catalyst data unavailable"), "data_source")

        market_records = [r for r in (market.get("records") or []) if r["id"] in MARKET_IDS]
        evidence: List[EvidenceRecord] = [EvidenceRecord(**r) for r in market_records + catalysts["records"]
                                          if r.get("value") is not None]
        prompt = f"""
{evidence_block(evidence)}

Assess the forward-looking picture for {ticker} using ONLY the data above: the next earnings
report and its consensus, analyst targets, rating and estimate revisions, and what the options
market implies. Say what is likely to move the stock next and in which direction the evidence
leans. Do not judge past fundamentals or news coverage; other agents cover those.
Notes from the data step: {'; '.join(catalysts.get('warnings', [])) or 'none'}

{schema_instructions('vega', 1)}
"""
        print(f"[Vega] Analyzing catalysts for {ticker}...")
        result = generate_agent_output(
            self.model, prompt, agent="vega", round_number=1, evidence=evidence,
            max_output_tokens=load_llm_config().max_output_tokens.round1, budget_left=budget_left,
        )
        if not result.ok:
            return agent_error("vega", ticker, result.error, result.error_type,
                               token_usage=result.token_usage, attempts=result.attempts, models=result.models,
                               repaired_from=result.repaired_from)
        return {
            "agent": self.name,
            "role": self.role,
            "ticker": ticker,
            "earnings_date": catalysts.get("earnings_date"),
            "output": result.output.model_dump(),
            "evidence": [e.model_dump() for e in evidence],
            "analysis": render_output(result.output),
            "warnings": catalysts.get("warnings", []),
            "attempts": result.attempts,
            "token_usage": result.token_usage,
            "models": result.models,
            "repaired_from": result.repaired_from,
        }


def main():
    parser = argparse.ArgumentParser(description="Vega - Catalyst & Options Agent")
    parser.add_argument("--ticker", type=str, required=True)
    args = parser.parse_args()
    result = VegaAgent().analyze(args.ticker)
    print(f"ERROR: {result['error']}" if result.get("error") else result["analysis"])


if __name__ == "__main__":
    main()
