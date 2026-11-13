"""
Charlie - Fundamental Analysis Agent
Reads five years of financial statement numbers from SEC XBRL company facts and argues
from those numbers only.
"""

import os
import sys
import argparse
from typing import Dict, Optional

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from coordinator.personas import get_charlie_system_instruction
from mcp_server.tools.sec_filings import fetch_filing_data
from mcp_server.tools.sec_financials import fetch_financial_statements
from agents.schema import (
    EvidenceRecord, agent_error, format_evidence, generate_agent_output,
    render_output, schema_instructions,
)

# Import Multi-LLM Client
from coordinator.llm_client import MultiLLMClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class CharlieAgent:
    """
    Charlie - Fundamental Analysis Specialist

    Analyzes revenue, margins, earnings, cash flow and balance sheet from SEC XBRL data.
    """

    def __init__(self, llm_client=None):
        """
        Initialize Charlie agent. Persona-free: the same analysis for every user (C7).

        Args:
            llm_client: Optional LLM client (same interface as MultiLLMClient);
                        defaults to a real MultiLLMClient.
        """
        self.name = "Charlie"
        self.role = "Fundamental Analysis Specialist"

        system_instruction = get_charlie_system_instruction()
        self.model = llm_client if llm_client is not None else MultiLLMClient(system_instruction=system_instruction)

    def analyze(self, ticker: str, filing_type: str = "10-K", financials: Optional[Dict] = None) -> Dict:
        """
        Perform fundamental analysis on a ticker.

        Args:
            ticker: Stock ticker symbol
            filing_type: Filing type used for the reference link only (default: 10-K)
            financials: Pre-fetched result of fetch_financial_statements (the coordinator
                        shares one fetch with Gamma); fetched here when None.

        Returns:
            Result dict with the validated output, or an explicit error. No LLM call is
            made when the financial data is unavailable.
        """
        print(f"[Charlie] Fetching SEC financial statements for {ticker}...")
        if financials is None:
            financials = fetch_financial_statements(ticker)
        if not financials.get("success"):
            return agent_error("charlie", ticker,
                               financials.get("error", "SEC financial data unavailable"), "data_source")

        evidence = [EvidenceRecord(**r) for r in financials["records"]]
        company_name = financials.get("company_name", ticker)
        warnings = []

        # Filing link is a reference for the reader only; it is not evidence.
        filing_ref = None
        filing_data = fetch_filing_data(ticker, filing_type, count=1)
        if filing_data.get("success") and filing_data.get("filings"):
            f = filing_data["filings"][0]
            filing_ref = {"form": f["form"], "filing_date": f["filing_date"], "url": f["document_url"]}
        else:
            warnings.append(f"latest {filing_type} link unavailable: "
                            f"{filing_data.get('error') or 'no filings found'}")

        prompt = f"""
Analyze {company_name} ({ticker}) from a fundamental perspective using ONLY the SEC financial
statement data below (fiscal years {', '.join(financials['fiscal_years'])}).

EVIDENCE:
{format_evidence(evidence)}

Assess revenue and earnings trend, margins, cash generation, leverage and balance-sheet
strength. State your fundamental stance.

{schema_instructions('charlie', 1)}
"""
        print(f"[Charlie] Analyzing {ticker}...")
        result = generate_agent_output(
            self.model, prompt, agent="charlie", round_number=1,
            evidence_ids=[e.id for e in evidence],
        )
        if not result.ok:
            return agent_error("charlie", ticker, result.error, result.error_type,
                               token_usage=result.token_usage, attempts=result.attempts)

        return {
            "agent": self.name,
            "role": self.role,
            "ticker": ticker,
            "company_name": company_name,
            "filing_analyzed": filing_ref,
            "output": result.output.model_dump(),
            "evidence": [e.model_dump() for e in evidence],
            "analysis": render_output(result.output),
            "warnings": warnings,
            "attempts": result.attempts,
            "token_usage": result.token_usage,
        }

    def get_agent_card(self) -> Dict:
        """Load the agent card (agent.json)."""
        import json
        card_path = os.path.join(os.path.dirname(__file__), "agent.json")
        try:
            with open(card_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[Charlie] Error loading agent card: {e}")
            return {
                "name": "Charlie",
                "role": "Fundamental Analysis Specialist",
                "persona_support": ["zen", "alpha", "balanced"]
            }


def main():
    """Main entry point for testing Charlie agent."""
    parser = argparse.ArgumentParser(description="Charlie - Fundamental Analysis Agent")
    parser.add_argument("--ticker", type=str, required=True, help="Stock ticker symbol")
    args = parser.parse_args()

    result = CharlieAgent().analyze(args.ticker)

    print("\n" + "=" * 80)
    print(f"CHARLIE'S FUNDAMENTAL ANALYSIS - {args.ticker.upper()}")
    print("=" * 80 + "\n")
    print(f"ERROR: {result['error']}" if result.get("error") else result["analysis"])


if __name__ == "__main__":
    main()
