"""
Debate coordinator: runs the three-round debate between Charlie, Delta and Gamma.

Order of a debate (PHASE4_PLAN.md §7, DESIGN.md §10):
  1. fetch all data (SEC, market, news) before any LLM call; build the quant report once from it
     (pure statistics, no LLM); stop with NO_VERDICT / INSUFFICIENT_EVIDENCE if fewer than two
     agents have data
  2. round 1: independent grounded analysis
  3. circuit breaker: verify every round-1 claim in code; drop agents below the faithfulness
     floor; stop with NO_VERDICT / CIRCUIT_BREAKER_FAITHFULNESS if fewer than two remain
  4. round 2: cross-examination of claims by id; round 3: final positions
Every LLM call counts against the token limit; hitting it ends the debate with
NO_VERDICT / TOKEN_LIMIT. The verdict itself is computed afterwards by Sigma.
"""

import os
import sys
import json
import argparse
from datetime import date
from typing import Dict, List, Optional, Tuple, Union

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from coordinator.config import Thresholds, load_thresholds
from coordinator.personas import RiskPersona
from coordinator.debate_logger import DebateLogger
from coordinator.transcript import Transcript
from agents.charlie_fundamental.agent import CharlieAgent
from agents.delta_sentiment.agent import DeltaAgent
from agents.gamma_valuation.agent import GammaAgent
from agents.schema import generate_validated, render_critiques, render_final
from judge.verifier import verify_output
from mcp_server.tools.market_data import fetch_market_data
from mcp_server.tools.news_stream import get_news_stream
from mcp_server.tools.sec_financials import fetch_financial_statements
from quant import QuantReport, build_quant_report, inputs_from_fetched

from dotenv import load_dotenv
load_dotenv()


class TokenLimitExceeded(Exception):
    """Raised by _track_usage; run_debate turns it into a NO_VERDICT result."""


class DebateCoordinator:
    """
    Coordinates the debate between specialist agents.
    Persona is recorded for the judge only; agents never see it (FIXES.md C7).
    """

    def __init__(self, ticker: str, persona: Union[RiskPersona, str] = RiskPersona.ZEN,
                 llm_client=None, thresholds: Optional[Thresholds] = None):
        """
        Args:
            ticker: Stock ticker to analyze
            persona: Risk persona; selects decision thresholds in the judge only
            llm_client: Optional LLM client shared by all agents (e.g. FakeLLMClient in tests).
                        Default: one real MultiLLMClient per agent.
            thresholds: Config; defaults to config/thresholds.yaml.
        """
        self.ticker = ticker.upper()
        self.persona = persona if isinstance(persona, RiskPersona) else RiskPersona(persona)
        self.thresholds = thresholds or load_thresholds()

        self.logger = DebateLogger(ticker, self.persona.value)
        self.transcript = Transcript(self.ticker)
        self.agent_cards = self._load_agent_cards()

        # Governance
        self.TOKEN_LIMIT = self.thresholds.circuit_breaker.token_limit
        self.total_tokens_used = 0
        self.warnings: List[str] = []
        self.quant_report: Optional[QuantReport] = None

        self.charlie = CharlieAgent(llm_client=llm_client)
        self.delta = DeltaAgent(llm_client=llm_client)
        self.gamma = GammaAgent(llm_client=llm_client, thresholds=self.thresholds)
        print(f"[COORDINATOR] Agents ready: Charlie, Delta, Gamma (persona {self.persona.value} "
              f"applies to the verdict thresholds only)")

    # ------------------------------------------------------------------ governance

    def _track_usage(self, agent_name: str, round_number: int, usage: Dict, ok: bool = True):
        """Count the tokens of one LLM call (all attempts, accepted or not) against the limit.
        Called after every LLM call in every round."""
        total_tokens = usage.get("total_tokens", 0)
        self.total_tokens_used += total_tokens
        self.transcript.record_call(agent_name, round_number, usage, ok)
        print(f"[GOVERNANCE] {agent_name} round {round_number} used {total_tokens} tokens. "
              f"Total: {self.total_tokens_used}/{self.TOKEN_LIMIT}")
        if self.total_tokens_used > self.TOKEN_LIMIT:
            raise TokenLimitExceeded(
                f"HARD LIMIT EXCEEDED: Debate used {self.total_tokens_used} tokens (Limit: {self.TOKEN_LIMIT})")

    def _save_transcript(self):
        self.logger.debate_log["transcript"] = self.transcript.to_dict()
        self.logger.debate_log["metadata"]["total_tokens"] = self.total_tokens_used
        self.logger._save_log()

    def _load_agent_cards(self) -> Dict:
        """Agent cards (agent.json) are kept as documentation of each agent's schema."""
        cards = {}
        for name, d in {"charlie": "agents/charlie_fundamental", "delta": "agents/delta_sentiment",
                        "gamma": "agents/gamma_valuation"}.items():
            path = os.path.join(d, "agent.json")
            if os.path.exists(path):
                with open(path) as f:
                    cards[name] = json.load(f)
        return cards

    # ------------------------------------------------------------------ debate

    def run_debate(self, rounds: int = 3, charlie_filing_type: str = "10-K", delta_days: int = 7) -> Dict:
        """
        Run the debate. Never raises for data, faithfulness or token-limit problems: those end
        the debate early with status NO_VERDICT and a reason.

        Returns:
            {ticker, persona, status: COMPLETED | NO_VERDICT, reason, detail, rounds_completed,
             total_tokens_used, warnings, log_file, debate_id}
        """
        print(f"\n[COORDINATOR] Starting {rounds}-round debate for {self.ticker}")
        try:
            fetched = self._fetch_evidence(delta_days)
            self._build_quant_report(fetched)
            available = self._check_evidence(fetched)
            need = self.thresholds.circuit_breaker.min_valid_agents
            if len(available) < need:
                detail = (f"data available for {len(available)} agent(s) ({', '.join(available) or 'none'}); "
                          f"{need} needed")
                self.logger.log_system_message(f"Debate not started: {detail}")
                return self._finish(0, "NO_VERDICT", "INSUFFICIENT_EVIDENCE", detail)

            self.transcript.rounds_started = 1
            self.logger.log_round_start(1)
            self._run_round_1(charlie_filing_type, fetched)

            stop = self._verify_round1()
            if stop:
                reason, detail = stop
                self.logger.log_system_message(f"Debate stopped after round 1: {detail}")
                return self._finish(1, "NO_VERDICT", reason, detail)

            if rounds >= 2:
                self.transcript.rounds_started = 2
                self.logger.log_round_start(2)
                self._run_round_2()
            if rounds >= 3:
                self.transcript.rounds_started = 3
                self.logger.log_round_start(3)
                self._run_round_3()
            return self._finish(rounds, "COMPLETED")
        except TokenLimitExceeded as e:
            self.logger.log_system_message(str(e))
            return self._finish(self.transcript.rounds_started, "NO_VERDICT", "TOKEN_LIMIT", str(e))

    def _finish(self, rounds_completed: int, status: str, reason: Optional[str] = None,
                detail: Optional[str] = None) -> Dict:
        outcome = {"status": status, "reason": reason, "detail": detail, "warnings": list(self.warnings)}
        self.logger.debate_log["outcome"] = outcome
        self._save_transcript()
        return {
            "ticker": self.ticker,
            "persona": self.persona.value,
            "rounds_completed": rounds_completed,
            "total_tokens_used": self.total_tokens_used,
            **outcome,
            "log_file": self.logger.finalize(),
            "debate_id": self.logger.debate_id,
        }

    def _fetch_evidence(self, delta_days: int) -> Dict[str, Dict]:
        """Fetch every data source once, before any LLM call."""
        self.logger.log_system_message(f"Fetching SEC financials, market data and news for {self.ticker}...")
        fetched = {
            "financials": fetch_financial_statements(self.ticker),
            "market": fetch_market_data(self.ticker),
            "news": get_news_stream(self.ticker, days=delta_days),
        }
        for name, data in fetched.items():
            if not data.get("success"):
                self.warnings.append(f"{name} unavailable: {data.get('error')}")
                self.logger.log_system_message(f"{name} unavailable: {data.get('error')}")
        return fetched

    def _build_quant_report(self, fetched: Dict[str, Dict], as_of: Optional[date] = None):
        """Build the quant report once per debate, before round 1, and keep it in the debate log
        under `quant_report`, separate from the transcript (DESIGN.md §10.2)."""
        cfg = self.thresholds
        self.quant_report = build_quant_report(
            inputs_from_fetched(fetched["financials"], fetched["market"]), as_of or date.today(),
            cfg.quant, cfg.capm)
        self.logger.debate_log["quant_report"] = self.quant_report.model_dump(mode="json")
        missing = self.quant_report.unavailable_summary()
        self.logger.log_system_message(
            f"Quant report built: {len(self.quant_report.fields) - len(missing)} of "
            f"{len(self.quant_report.fields)} fields available (seed {self.quant_report.seed})")
        self.warnings.extend(f"quant field unavailable - {m}" for m in missing)

    def _check_evidence(self, fetched: Dict[str, Dict]) -> List[str]:
        """Agents whose required data is present."""
        ok = {k: bool(v.get("success")) for k, v in fetched.items()}
        has_news = ok["news"] and fetched["news"].get("count", 0) > 0
        available = []
        if ok["financials"]:
            available.append("charlie")
        if has_news:
            available.append("delta")
        if ok["financials"] and ok["market"]:
            available.append("gamma")
        return available

    def _run_round_1(self, charlie_filing_type: str, fetched: Dict[str, Dict]):
        """Round 1: independent analysis from each agent, each grounded in the fetched data."""
        financials, market, news = fetched["financials"], fetched["market"], fetched["news"]

        print(f"[COORDINATOR] Calling Charlie for fundamental analysis...")
        r = self.charlie.analyze(self.ticker, charlie_filing_type, financials=financials)
        self._record_round1("charlie", r, ["fetch_financial_statements", "fetch_filing_data"],
                            filing_reference=r.get("filing_analyzed"))

        print(f"[COORDINATOR] Calling Delta for sentiment analysis...")
        r = self.delta.analyze(self.ticker, news=news)
        self._record_round1("delta", r, ["get_news_stream"], news_analyzed=r.get("news_analyzed", {}))

        print(f"[COORDINATOR] Calling Gamma for valuation analysis...")
        r = self.gamma.analyze(ticker=self.ticker, financials=financials, market=market,
                               quant_report=self.quant_report)
        if r.get("evidence"):
            # Keep Gamma's computed metrics for the verdict gates even if its LLM output failed
            self.transcript.set_evidence("gamma", r["evidence"])
        self._record_round1("gamma", r, ["fetch_market_data", "fetch_financial_statements",
                                         "calculate_valuation_metrics", "calculate_risk_metrics"],
                            parameters=r.get("parameters", {}))
        if not r.get("error"):
            self._log_gamma_formulas(r)

    def _record_round1(self, name: str, result: Dict, tools: List[str], **meta):
        if "token_usage" in result:   # data-source failures make no LLM call
            self._track_usage(name, 1, result["token_usage"], ok=not result.get("error"))
        if result.get("error"):
            print(f"[ERROR] {name.capitalize()} failed: {result['error']}")
            self.logger.log_agent_error(1, name, result["error"])
            self.warnings.append(f"{name} failed in round 1: {result['error']}")
            return
        self.transcript.add_round1(name, result["output"], result["evidence"])
        self.warnings.extend(f"{name}: {w}" for w in result.get("warnings", []))
        self.logger.log_agent_response(
            round_number=1,
            agent_name=name,
            analysis=result["analysis"],
            grounding_sources=sorted(set(self.transcript.round1[name].evidence_ids())),
            mcp_tools_used=tools,
            metadata=self._structured_metadata(result, **meta),
        )

    def _log_gamma_formulas(self, r: Dict):
        metrics, params = r.get("valuation_metrics", {}), r.get("parameters", {})
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

    def _verify_round1(self) -> Optional[Tuple[str, str]]:
        """Circuit breaker (C4): verify every round-1 claim against the fetched data in code and
        drop agents below the faithfulness floor. Returns (reason, detail) to stop, else None."""
        cb = self.thresholds.circuit_breaker
        report = {}
        for name in self.transcript.participants():
            v = verify_output(self.transcript.round1[name], self.transcript.evidence[name],
                              self.thresholds.verification, self.quant_report)
            self.transcript.set_verification(name, v)
            report[name] = v.model_dump()
            if v.faithfulness < cb.min_agent_faithfulness:
                why = (f"faithfulness {v.faithfulness:.2f} < {cb.min_agent_faithfulness:.2f} "
                       f"({v.verified}/{v.checkable} checkable claims verified)")
                self.transcript.drop(name, why)
                self.warnings.append(f"{name} dropped after round 1: {why}")
                self.logger.log_system_message(f"Circuit breaker: {name} dropped, {why}")
        self.logger.log_verification(1, report)

        remaining = self.transcript.participants()
        if len(remaining) >= cb.min_valid_agents:
            return None
        detail = (f"{len(remaining)} agent(s) left after round 1 ({', '.join(remaining) or 'none'}); "
                  f"{cb.min_valid_agents} needed")
        if self.transcript.dropped:
            return "CIRCUIT_BREAKER_FAITHFULNESS", detail
        return "INSUFFICIENT_AGENTS", detail

    def _run_round_2(self):
        """
        Round 2: every agent examines the other agents' claims by id, backed by its own evidence.
        Each prompt carries the agent's own round-1 output, the others' full claims and the
        verifier report.
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
                self.warnings.append(f"{name} failed in round 2: {result.error}")
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
        Round 3: every agent gives a final stance, states which claims or critiques changed its
        view, and answers each critique and each of its own claims that failed verification.
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
                self.warnings.append(f"{name} failed in round 3: {result.error}")
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
    parser = argparse.ArgumentParser(description="Kai debate coordinator")
    parser.add_argument("--ticker", type=str, required=True, help="Stock ticker symbol")
    parser.add_argument("--persona", type=str, default="zen", choices=["zen", "alpha", "balanced"],
                        help="Risk persona (decision thresholds only)")
    parser.add_argument("--rounds", type=int, default=3, choices=[1, 2, 3], help="Number of debate rounds")
    args = parser.parse_args()

    result = DebateCoordinator(args.ticker, RiskPersona(args.persona)).run_debate(rounds=args.rounds)
    print(f"\n[COORDINATOR] Debate {result['status']}" + (f" ({result['reason']})" if result.get("reason") else ""))
    print(f"Debate ID: {result['debate_id']}")
    print(f"Log file: {result['log_file']}")


if __name__ == "__main__":
    main()
