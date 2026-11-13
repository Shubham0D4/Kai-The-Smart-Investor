"""
Coordinator Server for A2A Debate Orchestration
Manages 3-round round-robin debates between Charlie, Delta, and Gamma.
"""

import os
import sys
import json
import argparse
from typing import Dict, List, Optional
from pathlib import Path

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from coordinator.personas import RiskPersona
from coordinator.debate_logger import DebateLogger
from agents.charlie_fundamental.agent import CharlieAgent
from agents.delta_sentiment.agent import DeltaAgent
from agents.gamma_valuation.agent import GammaAgent
from judge.sigma_agent import SigmaAgent
from mcp_server.tools.sec_financials import fetch_financial_statements
from coordinator.transcript import Transcript
from agents.schema import generate_validated, render_critiques, render_final

from dotenv import load_dotenv
load_dotenv()


class DebateCoordinator:
    """
    Coordinates A2A debates between specialist agents.
    Implements 3-round round-robin with cross-critique.
    """
    
    def __init__(self, ticker: str, persona: RiskPersona = RiskPersona.ZEN, llm_client=None):
        """
        Initialize debate coordinator.
        
        Args:
            ticker: Stock ticker to analyze
            persona: Risk persona for agents
            llm_client: Optional LLM client shared by all agents and Sigma
                        (e.g. FakeLLMClient in tests). Default: one real
                        MultiLLMClient per agent.
        """
        self.ticker = ticker.upper()
        self.persona = persona
        
        # Initialize logger and the shared debate transcript
        self.logger = DebateLogger(ticker, persona.value)
        self.transcript = Transcript(self.ticker)
        
        # Load agent cards for A2A handshake
        self.agent_cards = self._load_agent_cards()
        
        # Governance
        self.TOKEN_LIMIT = 50000
        self.total_tokens_used = 0
        self.CIRCUIT_BREAKER_MIN_FAITHFULNESS = 50.0
        self.sigma = SigmaAgent(llm_client=llm_client)  # scores only during the debate
        
        # Initialize agents
        print(f"[COORDINATOR] Initializing agents with {persona.value} persona...")
        self.charlie = CharlieAgent(persona=persona, llm_client=llm_client)
        self.delta = DeltaAgent(persona=persona, llm_client=llm_client)
        self.gamma = GammaAgent(persona=persona, llm_client=llm_client)
        
        print(f"[COORDINATOR] A2A Handshake complete")
        print(f"[COORDINATOR] Agents ready: Charlie, Delta, Gamma")
        print(f"[COORDINATOR] MCP tools verified\n")
        
    def _track_usage(self, agent_name: str, round_number: int, usage: Dict, ok: bool = True):
        """Count the tokens of one LLM call (all attempts, accepted or not) against the limit.
        Called after every LLM call in every round."""
        total_tokens = usage.get("total_tokens", 0)
        self.total_tokens_used += total_tokens
        self.transcript.record_call(agent_name, round_number, usage, ok)
        print(f"[GOVERNANCE] {agent_name} round {round_number} used {total_tokens} tokens. "
              f"Total: {self.total_tokens_used}/{self.TOKEN_LIMIT}")
        
        if self.total_tokens_used > self.TOKEN_LIMIT:
            msg = f"HARD LIMIT EXCEEDED: Debate used {self.total_tokens_used} tokens (Limit: {self.TOKEN_LIMIT})"
            self.logger.log_system_message(msg)
            self._save_transcript()
            raise Exception(msg)

    def _save_transcript(self):
        self.logger.debate_log["transcript"] = self.transcript.to_dict()
        self.logger.debate_log["metadata"]["total_tokens"] = self.total_tokens_used
        self.logger._save_log()
    
    def _load_agent_cards(self) -> Dict:
        """Load A2A agent cards for capability discovery."""
        cards = {}
        
        agent_dirs = {
            "charlie": "agents/charlie_fundamental",
            "delta": "agents/delta_sentiment",
            "gamma": "agents/gamma_valuation"
        }
        
        for agent_name, agent_dir in agent_dirs.items():
            card_path = os.path.join(agent_dir, "agent.json")
            if os.path.exists(card_path):
                with open(card_path, 'r') as f:
                    cards[agent_name] = json.load(f)
        
        return cards
    
    def run_debate(
        self,
        rounds: int = 3,
        charlie_filing_type: str = "10-K",
        delta_days: int = 7,
    ) -> Dict:
        """
        Run a complete multi-round debate.
        
        Args:
            rounds: Number of debate rounds (default: 3)
            charlie_filing_type: SEC filing type for Charlie's reference link
            delta_days: Days of news for Delta

        Gamma fetches its own price, EPS, cash flows, shares and price history.
            
        Returns:
            Complete debate results
        """
        print(f"\n[COORDINATOR] Starting {rounds}-round debate for {self.ticker}")
        print(f"[COORDINATOR] Persona: {self.persona.value}\n")
        
        # Round 1: Initial Analysis
        self.logger.log_round_start(1)
        self._run_round_1(charlie_filing_type, delta_days)
        
        def finish(rounds_completed: int, **extra) -> Dict:
            self._save_transcript()
            return {
                "ticker": self.ticker,
                "persona": self.persona.value,
                "rounds_completed": rounds_completed,
                "total_tokens_used": self.total_tokens_used,
                "log_file": self.logger.finalize(),
                "debate_id": self.logger.debate_id,
                **extra,
            }

        if rounds >= 2 and len(self.transcript.participants()) < 2:
            reason = (f"only {len(self.transcript.participants())} agent(s) produced a valid round-1 "
                      f"output; a debate needs at least two")
            self.logger.log_system_message(f"Debate stopped after round 1: {reason}")
            return finish(1, status="TERMINATED_EARLY", reason="INSUFFICIENT_AGENTS", detail=reason)

        if rounds >= 2:
            # Round 2: Cross-examination of specific claims
            self.logger.log_round_start(2)
            self._run_round_2()
            
            # Circuit breaker after round 2: deterministic scores only, no LLM synthesis
            print(f"[GOVERNANCE] Checking Phase 2 Circuit Breaker...")
            scores = self.sigma.score_debate(self.logger.debate_log)
            faithfulness = scores["faithfulness_score"]
            print(f"[GOVERNANCE] Current Faithfulness Score: {faithfulness}/100")
            if faithfulness < self.CIRCUIT_BREAKER_MIN_FAITHFULNESS:
                print(f"[GOVERNANCE] !!! CIRCUIT BREAKER TRIGGERED !!!")
                self.logger.log_system_message(
                    f"Circuit breaker: faithfulness {faithfulness} < {self.CIRCUIT_BREAKER_MIN_FAITHFULNESS}")
                return finish(2, status="TERMINATED_EARLY", reason="CIRCUIT_BREAKER_FAITHFULNESS",
                              scores=scores)
        
        if rounds >= 3:
            # Round 3: Final positions with stated changes
            self.logger.log_round_start(3)
            self._run_round_3()
        
        return finish(rounds)
    
    def _run_round_1(
        self,
        charlie_filing_type: str,
        delta_days: int,
    ) -> Dict:
        """
        Round 1: Initial independent analysis from each agent, each grounded in fetched data.
        SEC financials are fetched once and shared by Charlie and Gamma.
        """
        results = {}
        self.logger.log_system_message(f"Fetching SEC XBRL financial statements for {self.ticker}...")
        financials = fetch_financial_statements(self.ticker)
        if not financials.get("success"):
            self.logger.log_system_message(f"SEC financial data unavailable: {financials.get('error')}")
        
        # Charlie: Fundamental Analysis
        self.logger.log_system_message(f"Charlie (Fundamental Specialist) is fetching SEC filings for {self.ticker}...")
        print(f"[COORDINATOR] Calling Charlie for fundamental analysis...")
        charlie_result = self.charlie.analyze(self.ticker, charlie_filing_type, financials=financials)
        self._track_round1_usage("charlie", charlie_result)
        
        if not charlie_result.get("error"):
            grounding_sources = [f"SEC XBRL company facts ({', '.join(financials['fiscal_years'])})"]
            ref = charlie_result.get("filing_analyzed")
            if ref:
                grounding_sources.append(f"{ref['form']} filing {ref['filing_date']} (reference)")
            self.logger.log_agent_response(
                round_number=1,
                agent_name="charlie",
                analysis=charlie_result["analysis"],
                grounding_sources=grounding_sources,
                mcp_tools_used=["fetch_financial_statements", "fetch_filing_data"],
                metadata=self._structured_metadata(charlie_result, filing_reference=ref)
            )
            results["charlie"] = charlie_result
            self.transcript.add_round1("charlie", charlie_result["output"], charlie_result["evidence"])
        else:
            print(f"[ERROR] Charlie failed: {charlie_result['error']}")
            self.logger.log_agent_error(1, "charlie", charlie_result["error"])
        
        # Delta: Sentiment Analysis
        self.logger.log_system_message(f"Delta (Sentiment Analyst) is searching news articles for {self.ticker}...")
        print(f"[COORDINATOR] Calling Delta for sentiment analysis...")
        delta_result = self.delta.analyze(self.ticker, days=delta_days)
        self._track_round1_usage("delta", delta_result)
        
        if not delta_result.get("error"):
            grounding_sources = [
                f"{delta_result['news_analyzed']['article_count']} news articles ({delta_result['news_analyzed']['time_range']})"
            ]
            self.logger.log_agent_response(
                round_number=1,
                agent_name="delta",
                analysis=delta_result["analysis"],
                grounding_sources=grounding_sources,
                mcp_tools_used=["get_news_stream"],
                metadata=self._structured_metadata(delta_result, news_analyzed=delta_result.get("news_analyzed", {}))
            )
            results["delta"] = delta_result
            self.transcript.add_round1("delta", delta_result["output"], delta_result["evidence"])
        else:
            print(f"[ERROR] Delta failed: {delta_result['error']}")
            self.logger.log_agent_error(1, "delta", delta_result["error"])
        
        # Gamma: Valuation Analysis
        self.logger.log_system_message(f"Gamma (Valuation Specialist) is calculating DCF and multiples for {self.ticker}...")
        print(f"[COORDINATOR] Calling Gamma for valuation analysis...")
        gamma_result = self.gamma.analyze(ticker=self.ticker, financials=financials)
        self._track_round1_usage("gamma", gamma_result)
        
        if not gamma_result.get("error"):
            self.logger.log_agent_response(
                round_number=1,
                agent_name="gamma",
                analysis=gamma_result["analysis"],
                grounding_sources=["yfinance price and 1y daily history", "SEC XBRL company facts"],
                mcp_tools_used=["fetch_market_data", "fetch_financial_statements",
                                "calculate_valuation_metrics", "calculate_risk_metrics"],
                metadata=self._structured_metadata(gamma_result, parameters=gamma_result.get("parameters", {}))
            )
            
            # Log formulas for traceability
            metrics = gamma_result.get("valuation_metrics", {})
            params = gamma_result.get("parameters", {})
            if metrics.get("pe_ratio") is not None:
                self.logger.log_formula(
                    round_number=1, agent_name="gamma", formula_name="PE Ratio",
                    formula="PE = Price / Diluted EPS",
                    parameters={"price": params.get("current_price"), "eps": params.get("eps")},
                    result=metrics["pe_ratio"],
                )
            if metrics.get("dcf_analysis"):
                self.logger.log_formula(
                    round_number=1, agent_name="gamma", formula_name="DCF Value per Share",
                    formula="(PV(5y FCF) + PV(terminal value) - net debt) / shares",
                    parameters={k: params.get(k) for k in
                                ("discount_rate", "terminal_growth", "fcf_growth", "net_debt", "shares_outstanding")},
                    result=metrics["dcf_analysis"]["value_per_share"],
                )
            
            results["gamma"] = gamma_result
            self.transcript.add_round1("gamma", gamma_result["output"], gamma_result["evidence"])
        else:
            print(f"[ERROR] Gamma failed: {gamma_result['error']}")
            self.logger.log_agent_error(1, "gamma", gamma_result["error"])
        
        return results
    
    def _track_round1_usage(self, agent_name: str, result: Dict):
        # Data-source failures make no LLM call and carry no token_usage
        if "token_usage" in result:
            self._track_usage(agent_name, 1, result["token_usage"], ok=not result.get("error"))

    def _run_round_2(self):
        """
        Round 2: every agent examines the other agents' claims by id, backed by its own evidence.
        Each prompt carries the agent's own round-1 output and the others' full claims.
        """
        t = self.transcript
        for name in t.participants():
            print(f"[COORDINATOR] {name.capitalize()} cross-examining {', '.join(t.others(name))}...")
            agent = getattr(self, name)
            result = generate_validated(agent.model, t.round2_prompt(name),
                                        lambda text, n=name: t.validate_round2(n, text))
            self._track_usage(name, 2, result.token_usage, ok=result.ok)
            if not result.ok:
                print(f"[ERROR] {name.capitalize()} round 2 failed: {result.error}")
                self.logger.log_agent_error(2, name, result.error)
                continue
            out = result.output
            t.add_round2(name, out)
            self.logger.log_agent_response(
                round_number=2,
                agent_name=name,
                analysis=render_critiques(out),
                grounding_sources=sorted({e for k in out.critiques for e in k.evidence}),
                mcp_tools_used=[],
                metadata={"output": out.model_dump(by_alias=True), "response_type": "cross_examination"},
            )
            for k in out.critiques:
                self.logger.log_cross_critique(
                    round_number=2, critic_agent=name, target_agent=k.target.split(".", 1)[0],
                    critique=f"[{k.id}] {k.verdict.upper()} {k.target}: {k.argument}",
                )

    def _run_round_3(self):
        """
        Round 3: every agent gives a final stance and states which claims or critiques changed
        its view and what happened to each of its critiqued claims.
        """
        t = self.transcript
        for name in t.participants():
            print(f"[COORDINATOR] {name.capitalize()} providing final position...")
            agent = getattr(self, name)
            result = generate_validated(agent.model, t.round3_prompt(name),
                                        lambda text, n=name: t.validate_round3(n, text))
            self._track_usage(name, 3, result.token_usage, ok=result.ok)
            if not result.ok:
                print(f"[ERROR] {name.capitalize()} final position failed: {result.error}")
                self.logger.log_agent_error(3, name, result.error)
                continue
            out = result.output
            t.add_round3(name, out)
            self.logger.log_agent_response(
                round_number=3,
                agent_name=name,
                analysis=render_final(out),
                grounding_sources=sorted(set(out.evidence_ids())),
                mcp_tools_used=[],
                metadata={"output": out.model_dump(by_alias=True), "response_type": "final_position"},
            )

    @staticmethod
    def _structured_metadata(result: Dict, **extra) -> Dict:
        """Keep the validated output, the evidence it may cite and any data warnings in the log."""
        return {
            "output": result.get("output"),
            "evidence": result.get("evidence", []),
            "warnings": result.get("warnings", []),
            **extra,
        }


def main():
    """Main entry point for running debates."""
    parser = argparse.ArgumentParser(description="Kai - Economist Debate Coordinator")
    parser.add_argument("--ticker", type=str, required=True, help="Stock ticker symbol")
    parser.add_argument("--persona", type=str, default="zen", choices=["zen", "alpha", "balanced"], help="Risk persona")
    parser.add_argument("--rounds", type=int, default=3, choices=[1, 2, 3], help="Number of debate rounds")
    
    args = parser.parse_args()
    
    # Create coordinator
    if args.persona == "zen":
        persona = RiskPersona.ZEN
    elif args.persona == "alpha":
        persona = RiskPersona.ALPHA
    else:
        persona = RiskPersona.SIGMA
        
    coordinator = DebateCoordinator(args.ticker, persona)
    
    # Run debate
    result = coordinator.run_debate(rounds=args.rounds)
    
    print(f"\n[COORDINATOR] Debate complete!")
    print(f"Debate ID: {result['debate_id']}")
    print(f"Log file: {result['log_file']}")


if __name__ == "__main__":
    main()
