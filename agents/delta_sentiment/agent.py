"""
Delta - Sentiment Analysis Agent
Reads recent article titles and summaries merged from several free news providers and
argues from that text only.
"""

import os
import sys
import argparse
from typing import Dict, Optional

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from coordinator.personas import get_delta_system_instruction
from mcp_server.tools.news_stream import get_news_stream
from agents.schema import (
    EvidenceRecord, agent_error, evidence_block, generate_agent_output,
    render_output, schema_instructions,
)

# Import Multi-LLM Client
from coordinator.llm_client import MultiLLMClient
from coordinator.llm_config import load_llm_config
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# Market records Delta may use, only to check what stories say about the price move
MARKET_IDS = ("mkt:price", "mkt:high_52w", "mkt:drawdown_from_52w_high")


def _article_evidence(a: Dict) -> EvidenceRecord:
    label = a["title"] + (" [headline only]" if a.get("headline_only") else "")
    return EvidenceRecord(
        id=a["id"],
        label=label,
        period=a.get("published_at"),
        text=a.get("summary"),
        source=f"{a.get('source', 'Unknown')} via {', '.join(a.get('providers', []))}",
        url=a.get("url"),
    )


class DeltaAgent:
    """
    Delta - Sentiment Analysis Specialist

    Analyzes news sentiment and narratives from article summaries.
    """

    def __init__(self, llm_client=None):
        """
        Initialize Delta agent. Persona-free: the same analysis for every user (C7).

        Args:
            llm_client: Optional LLM client (same interface as MultiLLMClient);
                        defaults to a real MultiLLMClient.
        """
        self.name = "Delta"
        self.role = "Sentiment Analysis Specialist"

        system_instruction = get_delta_system_instruction()
        self.model = llm_client if llm_client is not None else MultiLLMClient(system_instruction=system_instruction, role="delta")

    def analyze(self, ticker: str, days: int = 7, max_articles: int = 15,
                news: Optional[Dict] = None, market: Optional[Dict] = None, budget_left=None) -> Dict:
        """
        Perform sentiment analysis on a ticker.

        Args:
            news: Pre-fetched get_news_stream result (the coordinator fetches all data before
                  any LLM call); fetched here when None.
            market: The debate's market snapshot; its price and dated return windows are added
                    so Delta can check what stories say about the move (naming the window).

        Returns a result dict with the validated output, or an explicit error. No LLM call
        is made when no news could be fetched.
        """
        if news is None:
            print(f"[Delta] Fetching news for {ticker} (last {days} days)...")
            news = get_news_stream(ticker, days=days, max_articles=max_articles)

        if not news.get("success"):
            detail = news.get("error", "news unavailable")
            if news.get("providers_failed"):
                detail += f" ({'; '.join(f'{p}: {m}' for p, m in news['providers_failed'].items())})"
            return agent_error("delta", ticker, detail, "data_source")

        articles = news.get("articles", [])
        if not articles:
            return agent_error("delta", ticker,
                               f"no news articles found for {ticker} in the last {days} days", "data_source")

        evidence = [_article_evidence(a) for a in articles]
        evidence += [EvidenceRecord(**r) for r in (market or {}).get("records", [])
                     if r["id"] in MARKET_IDS or r["id"].startswith("mkt:return_")]
        prompt = f"""
{evidence_block(evidence)}

Analyze the news sentiment and market narrative for {ticker} using ONLY the {len(articles)}
articles above (last {days} days). Articles marked [headline only] have no summary; weigh
them less. The price records are there only to check what a story says about the move: name
the window (for example "down 25% over 1 month") and cite that window's id.
Identify the dominant narratives, whether coverage is positive, negative or mixed, and any
risk signals. State your sentiment stance. Use quote claims for points taken from articles.

{schema_instructions('delta', 1)}
"""
        print(f"[Delta] Analyzing sentiment for {ticker}...")
        result = generate_agent_output(
            self.model, prompt, agent="delta", round_number=1, evidence=evidence,
            max_output_tokens=load_llm_config().max_output_tokens.round1, budget_left=budget_left,
        )
        if not result.ok:
            return agent_error("delta", ticker, result.error, result.error_type,
                               token_usage=result.token_usage, attempts=result.attempts, models=result.models,
                               repaired_from=result.repaired_from)

        return {
            "agent": self.name,
            "role": self.role,
            "ticker": ticker,
            "news_analyzed": {
                "article_count": len(articles),
                "headline_only_count": sum(a["headline_only"] for a in articles),
                "time_range": news.get("time_range", f"Last {days} days"),
                "sources": news.get("sources", []),
                "providers_ok": news.get("providers_ok", []),
                "providers_failed": list(news.get("providers_failed", {})),
            },
            "output": result.output.model_dump(),
            "evidence": [e.model_dump() for e in evidence],
            "analysis": render_output(result.output),
            "warnings": news.get("warnings", []),
            "attempts": result.attempts,
            "token_usage": result.token_usage,
            "models": result.models,
            "repaired_from": result.repaired_from,
        }

    def get_agent_card(self) -> Dict:
        """Load the agent card (agent.json)."""
        import json
        card_path = os.path.join(os.path.dirname(__file__), "agent.json")
        try:
            with open(card_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[Delta] Error loading agent card: {e}")
            return {
                "name": "Delta",
                "role": "Sentiment Analysis Specialist",
                "persona_support": ["zen", "alpha", "balanced"]
            }


def main():
    """Main entry point for testing Delta agent."""
    parser = argparse.ArgumentParser(description="Delta - Sentiment Analysis Agent")
    parser.add_argument("--ticker", type=str, required=True, help="Stock ticker symbol")
    parser.add_argument("--days", type=int, default=7, help="Number of days to look back")
    parser.add_argument("--max-articles", type=int, default=15, help="Maximum articles to analyze")
    args = parser.parse_args()

    result = DeltaAgent().analyze(args.ticker, args.days, args.max_articles)

    print("\n" + "=" * 80)
    print(f"DELTA'S SENTIMENT ANALYSIS - {args.ticker.upper()}")
    print("=" * 80 + "\n")
    print(f"ERROR: {result['error']}" if result.get("error") else result["analysis"])


if __name__ == "__main__":
    main()
