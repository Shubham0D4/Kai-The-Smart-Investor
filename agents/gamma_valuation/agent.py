"""
Gamma - Valuation Analysis Agent
Fetches price, shares and a year of daily prices (yfinance) plus EPS, cash flows and debt
(SEC XBRL), computes PE/PEG, a DCF and risk metrics in code, and argues from those numbers.
"""

import math
import os
import sys
import argparse
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from coordinator.personas import RiskPersona, get_gamma_system_instruction
from mcp_server.tools.finance_math import (
    calculate_dcf_value, calculate_pe_ratio, calculate_peg_ratio, calculate_risk_metrics,
)
from mcp_server.tools.market_data import fetch_market_data
from mcp_server.tools.sec_financials import fetch_financial_statements, latest, series
from agents.schema import (
    EvidenceRecord, agent_error, format_evidence, generate_agent_output,
    render_output, schema_instructions,
)

# Import Multi-LLM Client
from coordinator.llm_client import MultiLLMClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

PROJECTION_YEARS = 5
FCF_GROWTH_CLAMP = (-0.05, 0.15)
SEC_METRICS_FOR_GAMMA = ("eps_diluted", "operating_cash_flow", "capex", "cash",
                         "long_term_debt", "commercial_paper", "shares_outstanding")


def _calc(id_: str, label: str, value: float, unit: str, formula: str, period: Optional[str] = None) -> Dict:
    return {"id": id_, "label": label, "value": value, "unit": unit, "period": period,
            "source": f"computed: {formula}", "url": None}


def _cagr(first: float, last: float, years: int) -> Optional[float]:
    if years < 1 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


class GammaAgent:
    """
    Gamma - Valuation Analysis Specialist

    Computes valuation and risk metrics deterministically; the LLM only interprets them.
    """

    def __init__(self, persona: RiskPersona = RiskPersona.ZEN, llm_client=None):
        """
        Initialize Gamma agent.

        Args:
            persona: Risk persona to apply (Zen or Alpha)
            llm_client: Optional LLM client (same interface as MultiLLMClient);
                        defaults to a real MultiLLMClient.
        """
        self.persona = persona
        self.name = "Gamma"
        self.role = "Valuation Analysis Specialist"

        system_instruction = get_gamma_system_instruction(persona)
        self.model = llm_client if llm_client is not None else MultiLLMClient(system_instruction=system_instruction)

    def _assumptions(self):
        # NOTE: persona still sets the discount rate here; that is FIXES.md C7 (a later phase).
        if self.persona == RiskPersona.ZEN:
            return 0.12, 0.025
        if self.persona == RiskPersona.ALPHA:
            return 0.08, 0.035
        return 0.10, 0.03

    def compute(self, financials: Dict, market: Dict) -> Dict:
        """Pure computation: fetched data -> evidence records, metrics and warnings."""
        sec = financials["records"]
        evidence: List[Dict] = [r for r in sec if r["id"].split(":")[1] in SEC_METRICS_FOR_GAMMA]
        evidence += market["records"]
        warnings: List[str] = []
        price = market["price"]
        discount_rate, terminal_growth = self._assumptions()
        evidence.append(_calc("assumption:discount_rate", "Discount rate", discount_rate, "ratio",
                              f"persona setting ({self.persona.value})"))
        evidence.append(_calc("assumption:terminal_growth", "Terminal growth", terminal_growth, "ratio",
                              f"persona setting ({self.persona.value})"))

        valuation: Dict = {"pe_ratio": None, "peg_ratio": None, "eps_growth_pct": None,
                           "dcf_analysis": None, "margin_of_safety": None}

        # --- PE and PEG from SEC diluted EPS
        eps_rec = latest(sec, "eps_diluted")
        eps = eps_rec["value"] if eps_rec else None
        eps_hist = series(sec, "eps_diluted")
        if eps and eps > 0:
            pe = calculate_pe_ratio(price, eps)
            valuation["pe_ratio"] = pe
            evidence.append(_calc("calc:pe", "Price / diluted EPS", pe, "x",
                                  f"mkt:price / {eps_rec['id']}"))
            g = _cagr(eps_hist[0]["value"], eps_hist[-1]["value"], len(eps_hist) - 1) if len(eps_hist) >= 2 else None
            if g is not None:
                valuation["eps_growth_pct"] = round(g * 100, 4)
                evidence.append(_calc("calc:eps_cagr", f"Diluted EPS CAGR over {len(eps_hist) - 1}y",
                                      round(g, 6), "ratio", f"({eps_hist[-1]['id']} / {eps_hist[0]['id']})^(1/n) - 1"))
                if g > 0:
                    peg = calculate_peg_ratio(pe, g * 100)
                    valuation["peg_ratio"] = peg
                    evidence.append(_calc("calc:peg", "PE / EPS growth (%)", peg, "x", "calc:pe / (calc:eps_cagr * 100)"))
        else:
            warnings.append("no positive diluted EPS in SEC data: PE and PEG not computed")

        # --- Free cash flow history
        ocf = {r["id"].rsplit(":", 1)[1]: r for r in series(sec, "operating_cash_flow")}
        capex = {r["id"].rsplit(":", 1)[1]: r for r in series(sec, "capex")}
        fcf_hist = []
        for fy in sorted(set(ocf) & set(capex)):
            v = ocf[fy]["value"] - capex[fy]["value"]
            fcf_hist.append((fy, v))
            evidence.append(_calc(f"calc:fcf:{fy}", f"Free cash flow {fy}", v, "USD",
                                  f"sec:operating_cash_flow:{fy} - sec:capex:{fy}", fy))

        # --- Shares and net debt
        shares_rec = latest(sec, "shares_outstanding")
        shares = shares_rec["value"] if shares_rec else market.get("shares_outstanding")
        if not shares_rec and shares:
            warnings.append("shares outstanding taken from yfinance (not in SEC cover data)")
        debt = latest(sec, "long_term_debt")
        cp = latest(sec, "commercial_paper")
        cash = latest(sec, "cash")
        if not debt:
            warnings.append("no long-term debt in SEC data: treated as 0 in net debt")
        if not cash:
            warnings.append("no cash in SEC data: treated as 0 in net debt")
        net_debt = sum(r["value"] for r in (debt, cp) if r) - (cash["value"] if cash else 0.0)
        parts = " + ".join(r["id"] for r in (debt, cp) if r) or "0"
        evidence.append(_calc("calc:net_debt", "Net debt", net_debt, "USD",
                              f"{parts} - {cash['id'] if cash else '0'}"))

        # --- DCF
        fcf_growth = None
        if len(fcf_hist) >= 2 and fcf_hist[-1][1] > 0 and shares:
            raw = _cagr(fcf_hist[0][1], fcf_hist[-1][1], len(fcf_hist) - 1)
            if raw is None:
                raw = 0.0
                warnings.append("historical FCF CAGR undefined (non-positive start): projection growth set to 0")
            fcf_growth = min(max(raw, FCF_GROWTH_CLAMP[0]), FCF_GROWTH_CLAMP[1])
            evidence.append(_calc("assumption:fcf_growth", "FCF projection growth (historical CAGR, clamped)",
                                  round(fcf_growth, 6), "ratio",
                                  f"CAGR(calc:fcf:{fcf_hist[0][0]} -> calc:fcf:{fcf_hist[-1][0]}) clamped to {FCF_GROWTH_CLAMP}"))
            base = fcf_hist[-1][1]
            projected = [base * (1 + fcf_growth) ** k for k in range(1, PROJECTION_YEARS + 1)]
            dcf = calculate_dcf_value(projected, discount_rate, terminal_growth, shares, net_debt)
            if "error" in dcf:
                warnings.append(f"DCF not computed: {dcf['error']}")
            else:
                valuation["dcf_analysis"] = dcf
                vps = dcf["value_per_share"]
                mos = (vps - price) / vps if vps > 0 else None
                valuation["margin_of_safety"] = round(mos, 6) if mos is not None else None
                evidence.append(_calc("calc:dcf.enterprise_value", "DCF enterprise value",
                                      dcf["enterprise_value"], "USD",
                                      f"5y FCF projection at assumption:fcf_growth, discount assumption:discount_rate, terminal assumption:terminal_growth"))
                evidence.append(_calc("calc:dcf.value_per_share", "DCF equity value per share", vps, "USD",
                                      "(calc:dcf.enterprise_value - calc:net_debt) / shares"))
                if mos is not None:
                    evidence.append(_calc("calc:dcf.margin_of_safety", "Margin of safety vs DCF",
                                          round(mos, 6), "ratio", "(calc:dcf.value_per_share - mkt:price) / calc:dcf.value_per_share"))
                for delta, tag in ((-0.02, "minus_2pct"), (0.02, "plus_2pct")):
                    dr = discount_rate + delta
                    if dr > terminal_growth:
                        alt = calculate_dcf_value(projected, dr, terminal_growth, shares, net_debt)
                        evidence.append(_calc(f"calc:dcf.value_per_share.dr_{tag}",
                                              f"DCF value per share at discount rate {dr:.0%}",
                                              alt["value_per_share"], "USD", f"same DCF, discount rate {dr:.4f}"))
        else:
            reason = ("fewer than 2 years of FCF" if len(fcf_hist) < 2 else
                      "latest FCF is not positive" if fcf_hist[-1][1] <= 0 else "no share count")
            warnings.append(f"DCF not computed: {reason}")

        # --- Risk metrics from a year of daily closes
        closes = [p["close"] for p in market["history"]]
        risk = calculate_risk_metrics(prices=closes)
        risk["volatility_annual"] = round(risk["volatility"] * math.sqrt(252), 6)
        risk["return_1y"] = round(closes[-1] / closes[0] - 1, 6)
        period = f"{market['history'][0]['date']}..{market['history'][-1]['date']}"
        evidence.append(_calc("calc:volatility_annual", "Annualised volatility of daily returns",
                              risk["volatility_annual"], "ratio", "stdev(daily returns) * sqrt(252)", period))
        evidence.append(_calc("calc:sharpe", "Sharpe ratio (rf 2%)", risk.get("sharpe_ratio"), "x",
                              "(mean daily return * 252 - 0.02) / annualised volatility", period))
        evidence.append(_calc("calc:return_1y", "Price return over history", risk["return_1y"], "ratio",
                              "mkt:price_1y_ago -> last close", period))
        for ma in ("ma_50", "ma_200"):
            if risk.get(ma) is not None:
                evidence.append(_calc(f"calc:{ma}", f"{ma[3:]}-day moving average", risk[ma], "USD",
                                      f"mean of last {ma[3:]} closes", period))

        return {
            "evidence": [EvidenceRecord(**e) for e in evidence if e.get("value") is not None],
            "valuation_metrics": valuation,
            "risk_metrics": risk,
            "parameters": {
                "discount_rate": discount_rate,
                "terminal_growth": terminal_growth,
                "current_price": price,
                "eps": eps,
                "eps_growth_pct": valuation["eps_growth_pct"],
                "fcf_growth": fcf_growth,
                "shares_outstanding": shares,
                "net_debt": net_debt,
            },
            "warnings": warnings,
        }

    def analyze(self, ticker: str, financials: Optional[Dict] = None, market: Optional[Dict] = None) -> Dict:
        """
        Perform valuation analysis on a ticker. All inputs are fetched, none are user-supplied.

        Args:
            ticker: Stock ticker symbol
            financials: Pre-fetched fetch_financial_statements result (shared with Charlie)
            market: Pre-fetched fetch_market_data result

        Returns a result dict with the validated output, or an explicit error. No LLM call
        is made when a data source fails.
        """
        persona = self.persona.value
        print(f"[Gamma] Fetching market and SEC data for {ticker}...")
        if market is None:
            market = fetch_market_data(ticker)
        if not market.get("success"):
            return agent_error("gamma", ticker, persona, market.get("error", "market data unavailable"), "data_source")
        if financials is None:
            financials = fetch_financial_statements(ticker)
        if not financials.get("success"):
            return agent_error("gamma", ticker, persona,
                               financials.get("error", "SEC financial data unavailable"), "data_source")

        computed = self.compute(financials, market)
        evidence = computed["evidence"]

        prompt = f"""
Assess the valuation of {ticker} using ONLY the market data, SEC figures and computed metrics
below. All metrics were computed in code; do not recompute them with different inputs.

EVIDENCE:
{format_evidence(evidence)}

Notes from the data step: {'; '.join(computed['warnings']) or 'none'}

Judge whether the stock looks undervalued, fairly valued or overvalued, how sensitive that is
to the discount rate, and what the risk metrics say. State your valuation stance.

{schema_instructions('gamma', 1)}
"""
        print(f"[Gamma] Analyzing valuation for {ticker} with {persona} persona...")
        result = generate_agent_output(
            self.model, prompt, agent="gamma", round_number=1,
            evidence_ids=[e.id for e in evidence],
        )
        if not result.ok:
            return agent_error("gamma", ticker, persona, result.error, result.error_type,
                               token_usage=result.token_usage, attempts=result.attempts)

        return {
            "agent": self.name,
            "role": self.role,
            "ticker": ticker,
            "persona": persona,
            "valuation_metrics": computed["valuation_metrics"],
            "risk_metrics": computed["risk_metrics"],
            "parameters": computed["parameters"],
            "output": result.output.model_dump(),
            "evidence": [e.model_dump() for e in evidence],
            "analysis": render_output(result.output),
            "warnings": computed["warnings"],
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
            print(f"[Gamma] Error loading agent card: {e}")
            return {
                "name": "Gamma",
                "role": "Valuation Analysis Specialist",
                "persona_support": ["zen", "alpha", "balanced"]
            }


def main():
    """Main entry point for testing Gamma agent."""
    parser = argparse.ArgumentParser(description="Gamma - Valuation Analysis Agent")
    parser.add_argument("--ticker", type=str, required=True, help="Stock ticker symbol")
    parser.add_argument("--persona", type=str, default="zen", choices=["zen", "alpha"], help="Risk persona")
    args = parser.parse_args()

    persona = RiskPersona.ZEN if args.persona == "zen" else RiskPersona.ALPHA
    result = GammaAgent(persona=persona).analyze(args.ticker)

    print("\n" + "=" * 80)
    print(f"GAMMA'S VALUATION ANALYSIS - {args.ticker.upper()}")
    print("=" * 80 + "\n")
    if result.get("error"):
        print(f"ERROR: {result['error']}")
    else:
        print(result["analysis"])
        print(f"\nPE {result['valuation_metrics']['pe_ratio']}  "
              f"DCF/share {(result['valuation_metrics']['dcf_analysis'] or {}).get('value_per_share')}")


if __name__ == "__main__":
    main()
