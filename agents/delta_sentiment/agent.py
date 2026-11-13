"""
Delta - Sentiment Analysis Agent
Reads recent article titles and summaries merged from several free news providers and
argues from that text only.
"""

import os
import sys
import argparse
from typing import Dict

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from coordinator.personas import RiskPersona, get_delta_system_instruction
from mcp_server.tools.news_stream import get_news_stream
from agents.schema import (
    EvidenceRecord, agent_error, format_evidence, generate_agent_output,
    render_output, schema_instructions,
)

# Import Multi-LLM Client
from coordinator.llm_client import MultiLLMClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


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

    def __init__(self, persona: RiskPersona = RiskPersona.ALPHA, llm_client=None):
        """
        Initialize Delta agent.

        Args:
            persona: Risk persona to apply (Zen or Alpha)
            llm_client: Optional LLM client (same interface as MultiLLMClient);
                        defaults to a real MultiLLMClient.
        """
        self.persona = persona
        self.name = "Delta"
        self.role = "Sentiment Analysis Specialist"

        system_instruction = get_delta_system_instruction(persona)
        self.model = llm_client if llm_client is not None else MultiLLMClient(system_instruction=system_instruction)

    def analyze(self, ticker: str, days: int = 7, max_articles: int = 15) -> Dict:
        """
        Perform sentiment analysis on a ticker.

        Returns a result dict with the validated output, or an explicit error. No LLM call
        is made when no news could be fetched.
        """
        persona = self.persona.value
        print(f"[Delta] Fetching news for {ticker} (last {days} days)...")
        news = get_news_stream(ticker, days=days, max_articles=max_articles)

        if not news.get("success"):
            detail = news.get("error", "news unavailable")
            if news.get("providers_failed"):
                detail += f" ({'; '.join(f'{p}: {m}' for p, m in news['providers_failed'].items())})"
            return agent_error("delta", ticker, persona, detail, "data_source")

        articles = news.get("articles", [])
        if not articles:
            return agent_error("delta", ticker, persona,
                               f"no news articles found for {ticker} in the last {days} days", "data_source")

        evidence = [_article_evidence(a) for a in articles]
        prompt = f"""
Analyze the news sentiment and market narrative for {ticker} using ONLY the {len(articles)}
articles below (last {days} days). Articles marked [headline only] have no summary; weigh
them less.

EVIDENCE:
{format_evidence(evidence)}

Identify the dominant narratives, whether coverage is positive, negative or mixed, and any
risk signals. State your sentiment stance. Use quote claims for points taken from articles.

{schema_instructions('delta', 1)}
"""
        print(f"[Delta] Analyzing sentiment for {ticker} with {persona} persona...")
        result = generate_agent_output(
            self.model, prompt, agent="delta", round_number=1,
            evidence_ids=[e.id for e in evidence],
        )
        if not result.ok:
            return agent_error("delta", ticker, persona, result.error, result.error_type,
                               token_usage=result.token_usage, attempts=result.attempts)

        return {
            "agent": self.name,
            "role": self.role,
            "ticker": ticker,
            "persona": persona,
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
    parser.add_argument("--persona", type=str, default="alpha", choices=["zen", "alpha"], help="Risk persona")
    parser.add_argument("--days", type=int, default=7, help="Number of days to look back")
    parser.add_argument("--max-articles", type=int, default=15, help="Maximum articles to analyze")
    args = parser.parse_args()

    persona = RiskPersona.ZEN if args.persona == "zen" else RiskPersona.ALPHA
    result = DeltaAgent(persona=persona).analyze(args.ticker, args.days, args.max_articles)

    print("\n" + "=" * 80)
    print(f"DELTA'S SENTIMENT ANALYSIS - {args.ticker.upper()}")
    print("=" * 80 + "\n")
    print(f"ERROR: {result['error']}" if result.get("error") else result["analysis"])


if __name__ == "__main__":
    main()
