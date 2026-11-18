"""
Gamma - Valuation Analysis Agent
Fetches price, shares and daily prices (yfinance) plus EPS, cash flows and debt (SEC XBRL),
computes PE/PEG and a CAPM-discounted DCF in code, and argues from those numbers plus the quant
layer's report (quant/, DESIGN.md §10): volatility, beta, the CAPM rate, a Monte Carlo DCF and
forward probabilities come from that report, and Gamma cites them by their `quant:` ids.
The discount rate is the same for every persona (FIXES.md C7).
"""

import os
import sys
import argparse
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from datetime import date

from coordinator.config import Thresholds, load_thresholds
from coordinator.personas import get_gamma_system_instruction
from mcp_server.tools.finance_math import (
    calculate_dcf_value, calculate_pe_ratio, calculate_peg_ratio, calculate_risk_metrics,
)
from mcp_server.tools.market_data import fetch_market_data
from mcp_server.tools.sec_financials import fcf_history, fetch_financial_statements, latest, net_debt, series
from quant import QuantReport, build_quant_report, inputs_from_fetched
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


# Records dropped from Gamma's prompt: they repeat another record or carry no information. Gamma's
# evidence is the largest of the four agents, and Groq's free tier rejects a request above 8,000
# tokens per minute (a live call of 8,927 tokens failed). Everything computed from them (EPS growth,
# free cash flow, DCF, margin of safety) is still in the pack and in the judge's gates.
_REPEATED_IDS = {
    "mkt:shares_outstanding",        # same figure as sec:shares_outstanding:latest
    "mkt:price_1y_ago",              # mkt:return_1y already states the move
}
# With the default zero-drift setting the normal model is exactly 0.5 (interval included): no signal.
_NORMAL_IDS = ("quant:forward.prob_up_normal", "quant:forward.prob_up_normal.low", "quant:forward.prob_up_normal.high")
_HISTORY_YEARS = 3                   # fiscal years kept of each raw SEC series and its free-cash-flow calc


def slim_evidence(records):
    """Gamma's evidence without repeated records and with only the latest few fiscal years of each series."""
    records = list(records)
    years = sorted({r.id.rsplit(":FY", 1)[1] for r in records
                    if r.id.startswith(("sec:", "calc:fcf:")) and ":FY" in r.id})
    old = set(years[:-_HISTORY_YEARS])
    flat = [r for r in records if r.id in _NORMAL_IDS]
    drop_normal = len(flat) == len(_NORMAL_IDS) and all(r.value == 0.5 for r in flat)
    return [r for r in records
            if r.id not in _REPEATED_IDS and not (drop_normal and r.id in _NORMAL_IDS) and not (":FY" in r.id and r.id.rsplit(":FY", 1)[1] in old
                                                   and r.id.startswith(("sec:", "calc:fcf:")))]


class GammaAgent:
    """
    Gamma - Valuation Analysis Specialist

    Computes valuation and risk metrics deterministically; the LLM only interprets them.
    """

    def __init__(self, llm_client=None, thresholds: Optional[Thresholds] = None):
        """
        Initialize Gamma agent. Persona-free: the same valuation for every user (C7).

        Args:
            llm_client: Optional LLM client (same interface as MultiLLMClient);
                        defaults to a real MultiLLMClient.
            thresholds: Config (CAPM constants); defaults to config/thresholds.yaml.
        """
        self.name = "Gamma"
        self.role = "Valuation Analysis Specialist"
        self.thresholds = thresholds

        system_instruction = get_gamma_system_instruction()
        self.model = llm_client if llm_client is not None else MultiLLMClient(system_instruction=system_instruction, role="gamma")

    @property
    def cfg(self) -> Thresholds:
        return self.thresholds or load_thresholds()

    def _discount_rate(self, report: QuantReport, evidence: List[Dict], warnings: List[str]):
        """The quant report's CAPM rate (persona-free); configured fallback with a warning when the
        report could not compute it. Returns (discount_rate, terminal_growth)."""
        capm = self.cfg.capm
        evidence.append(_calc("assumption:equity_risk_premium", "Equity risk premium", capm.equity_risk_premium,
                              "ratio", "config/thresholds.yaml capm.equity_risk_premium"))
        evidence.append(_calc("assumption:terminal_growth", "Terminal growth", capm.terminal_growth, "ratio",
                              "config/thresholds.yaml capm.terminal_growth"))
        rate = report.value("quant:capm.discount_rate")
        if rate is not None:
            evidence.append(_calc("assumption:discount_rate", "Discount rate (CAPM)", rate, "ratio",
                                  "quant:capm.discount_rate (CAPM from quant:market.beta and mkt:risk_free_rate)"))
        else:
            reason = report.get("quant:capm.discount_rate").reason
            rate = capm.fallback_rate
            warnings.append(f"CAPM rate unavailable ({reason}): discount rate set to fallback {rate:.0%}")
            evidence.append(_calc("assumption:discount_rate", "Discount rate (fallback)", rate, "ratio",
                                  f"config/thresholds.yaml capm.fallback_rate ({reason})"))
        return rate, capm.terminal_growth

    def build_report(self, financials: Dict, market: Dict) -> QuantReport:
        """Used only when Gamma runs on its own; the coordinator passes its report in."""
        cfg = self.cfg
        return build_quant_report(inputs_from_fetched(financials, market), date.today(), cfg.quant, cfg.capm)

    def compute(self, financials: Dict, market: Dict, quant_report: Optional[QuantReport] = None) -> Dict:
        """Computation: fetched data + quant report -> evidence records, metrics and warnings."""
        report = quant_report if quant_report is not None else self.build_report(financials, market)
        sec = financials["records"]
        evidence: List[Dict] = [r for r in sec if r["id"].split(":")[1] in SEC_METRICS_FOR_GAMMA]
        evidence += market["records"]
        evidence += report.evidence_records()          # every available quant field, citable by id
        warnings: List[str] = list(market.get("warnings", []))
        price = market["price"]
        discount_rate, terminal_growth = self._discount_rate(report, evidence, warnings)

        valuation: Dict = {"pe_ratio": None, "peg_ratio": None, "eps_growth_pct": None,
                           "dcf_analysis": None, "margin_of_safety": None}

        # --- PE on trailing-twelve-month EPS (same snapshot as the price), PEG from FY EPS growth
        eps_rec = next((r for r in sec if r["id"] == "sec:eps_diluted:TTM"), None)
        if eps_rec is None:
            eps_rec = latest(sec, "eps_diluted")
            if eps_rec:
                warnings.append(f"no TTM EPS: PE uses {eps_rec['id']} ({eps_rec.get('period')})")
        eps = eps_rec["value"] if eps_rec else None
        eps_hist = series(sec, "eps_diluted")
        if eps and eps > 0:
            pe = calculate_pe_ratio(price, eps)
            valuation["pe_ratio"] = pe
            ttm = eps_rec["id"].endswith(":TTM")
            evidence.append(_calc("calc:pe", f"Price / diluted EPS ({'trailing 12 months' if ttm else 'last fiscal year'})",
                                  pe, "x", f"mkt:price / {eps_rec['id']}", eps_rec.get("period")))
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

        # --- Free cash flow history (same helper the quant layer uses)
        fcf_hist = []
        for f in fcf_history(sec):
            fcf_hist.append((f["fy"], f["value"]))
            evidence.append(_calc(f"calc:fcf:{f['fy']}", f"Free cash flow {f['fy']}", f["value"], "USD",
                                  f"{f['ocf_id']} - {f['capex_id']}", f["fy"]))

        # --- Shares and net debt
        shares_rec = latest(sec, "shares_outstanding")
        shares = shares_rec["value"] if shares_rec else market.get("shares_outstanding")
        if not shares_rec and shares:
            warnings.append("shares outstanding taken from yfinance (not in SEC cover data)")
        nd = net_debt(sec)
        for m in nd["missing"]:
            warnings.append(f"no {m.replace('_', ' ')} in SEC data: treated as 0 in net debt")
        net_debt_value = nd["value"]
        evidence.append(_calc("calc:net_debt", "Net debt", net_debt_value, "USD",
                              f"{' + '.join(nd['debt_ids']) or '0'} - {nd['cash_id'] or '0'}"))

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
            dcf = calculate_dcf_value(projected, discount_rate, terminal_growth, shares, net_debt_value)
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
                        alt = calculate_dcf_value(projected, dr, terminal_growth, shares, net_debt_value)
                        evidence.append(_calc(f"calc:dcf.value_per_share.dr_{tag}",
                                              f"DCF value per share at discount rate {dr:.0%}",
                                              alt["value_per_share"], "USD", f"same DCF, discount rate {dr:.4f}"))
        else:
            reason = ("fewer than 2 years of FCF" if len(fcf_hist) < 2 else
                      "latest FCF is not positive" if fcf_hist[-1][1] <= 0 else "no share count")
            warnings.append(f"DCF not computed: {reason}")

        # --- Moving averages, Sharpe and 1y return (volatility and beta come from the quant report)
        window = market.get("history_1y") or market["history"]
        closes = [p["close"] for p in window]
        risk = calculate_risk_metrics(prices=closes)
        risk["return_1y"] = round(closes[-1] / closes[0] - 1, 6)
        period = f"{window[0]['date']}..{window[-1]['date']}"
        evidence.append(_calc("calc:sharpe", "Sharpe ratio (rf 2%)", risk.get("sharpe_ratio"), "x",
                              "(mean daily return * 252 - 0.02) / annualised volatility", period))
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
                "net_debt": net_debt_value,
            },
            "quant_unavailable": report.unavailable_summary(),
            "warnings": warnings,
        }

    def analyze(self, ticker: str, financials: Optional[Dict] = None, market: Optional[Dict] = None,
                quant_report: Optional[QuantReport] = None, budget_left=None) -> Dict:
        """
        Perform valuation analysis on a ticker. All inputs are fetched, none are user-supplied.

        Args:
            ticker: Stock ticker symbol
            financials: Pre-fetched fetch_financial_statements result (shared with Charlie)
            market: Pre-fetched fetch_market_data result

        Returns a result dict with the validated output, or an explicit error. No LLM call
        is made when a data source fails.
        """
        print(f"[Gamma] Fetching market and SEC data for {ticker}...")
        if market is None:
            market = fetch_market_data(ticker)
        if not market.get("success"):
            return agent_error("gamma", ticker, market.get("error", "market data unavailable"), "data_source")
        if financials is None:
            financials = fetch_financial_statements(ticker)
        if not financials.get("success"):
            return agent_error("gamma", ticker,
                               financials.get("error", "SEC financial data unavailable"), "data_source")

        computed = self.compute(financials, market, quant_report)
        evidence = slim_evidence(computed["evidence"])

        prompt = f"""
{evidence_block(evidence)}

Assess the valuation of {ticker} using ONLY the market data, SEC figures, computed metrics and
quant-report fields above. All of them were computed in code; do not recompute them with
different inputs. Quant fields (ids starting "quant:") state their method and sample size; a
claim using one must cite that id, and its value must match the report.

Notes from the data step: {'; '.join(computed['warnings']) or 'none'}
Quant fields that could not be computed (do not cite them): {'; '.join(computed['quant_unavailable']) or 'none'}

Judge whether the stock looks undervalued, fairly valued or overvalued, how sensitive that is
to the discount rate, and what the risk metrics say. State your valuation stance.

{schema_instructions('gamma', 1)}
"""
        print(f"[Gamma] Analyzing valuation for {ticker}...")
        result = generate_agent_output(
            self.model, prompt, agent="gamma", round_number=1, evidence=evidence,
            max_output_tokens=load_llm_config().max_output_tokens.round1, budget_left=budget_left,
        )
        if not result.ok:
            # The computed metrics stay available to the judge's gates even when the
            # LLM output fails (margin of safety, volatility).
            return agent_error("gamma", ticker, result.error, result.error_type,
                               token_usage=result.token_usage, attempts=result.attempts, models=result.models,
                               repaired_from=result.repaired_from,
                               evidence=[e.model_dump() for e in evidence])

        return {
            "agent": self.name,
            "role": self.role,
            "ticker": ticker,
            "valuation_metrics": computed["valuation_metrics"],
            "risk_metrics": computed["risk_metrics"],
            "parameters": computed["parameters"],
            "output": result.output.model_dump(),
            "evidence": [e.model_dump() for e in evidence],
            "analysis": render_output(result.output),
            "warnings": computed["warnings"],
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
    args = parser.parse_args()

    result = GammaAgent().analyze(args.ticker)

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
