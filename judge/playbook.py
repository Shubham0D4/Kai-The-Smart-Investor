"""
Suggestions that accompany the verdict ("playbook"), computed in code from the debate's evidence,
the quant report and the persona thresholds. No LLM writes any number here; Sigma's narrative
may explain these items but cannot change them.

Every item is framed as a condition or a fact to watch ("becomes Buy if...", "options imply a
move of..."), not as a personal instruction. Kai is a research tool, not investment advice.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

from coordinator.config import PersonaThresholds

Kind = Literal["action", "trigger", "level", "event", "risk", "context"]


class Suggestion(BaseModel):
    kind: Kind
    title: str
    detail: str
    sources: List[str] = []          # evidence / quant ids the numbers come from


def _usd(v: float) -> str:
    return f"${v:,.2f}"


def _pct(v: float, digits: int = 1) -> str:
    return f"{v * 100:+.{digits}f}%"


def _apct(v: float, digits: int = 1) -> str:
    return f"{abs(v) * 100:.{digits}f}%"


def build_playbook(verdict, persona_name: str, persona: PersonaThresholds, evidence: Dict[str, float],
                   quant: Optional[object], verdicts_by_persona: Dict[str, object]) -> List[Suggestion]:
    """
    verdict: judge.scoring.Verdict for the selected persona
    evidence: id -> value for every record the agents were given (all agents merged)
    quant: QuantReport or None; verdicts_by_persona: persona -> Verdict
    """
    rec = verdict.recommendation
    if rec == "No Verdict":
        return [Suggestion(kind="action", title="No signal",
                           detail=f"No recommendation was computed ({verdict.reason}). Nothing here supports "
                                  "acting either way; rerun when the missing data or agents are available.")]
    S = verdict.score
    price = evidence.get("mkt:price")
    dcf = evidence.get("calc:dcf.value_per_share")
    q = (lambda f: quant.value(f)) if quant is not None else (lambda f: None)
    out: List[Suggestion] = []
    name = persona_name.capitalize()

    # --- what the verdict means, and what would change it
    if rec == "Buy":
        out.append(Suggestion(kind="action", title="Signals support adding exposure",
                              detail=f"Under {name} thresholds every Buy condition is met (debate score {S:+.2f}, "
                                     f"buy threshold {persona.buy_score:+.2f}). Confidence {verdict.confidence:.0f}%."))
    elif rec == "Sell":
        out.append(Suggestion(kind="action", title="Signals support reducing exposure",
                              detail=f"Debate score {S:+.2f} is at or below the {name} sell threshold "
                                     f"{persona.sell_score:+.2f}. Confidence {verdict.confidence:.0f}%."))
    else:
        out.append(Suggestion(kind="action", title="No action signal: hold or wait",
                              detail=f"Neither the Buy nor the Sell rule is met under {name} thresholds "
                                     f"(debate score {S:+.2f}). Confidence in Hold {verdict.confidence:.0f}%: "
                                     + ("the debate sits well inside the Hold zone."
                                        if (verdict.hold_conviction or 0) >= 0.5 else
                                        "the debate is close to a Buy or Sell trigger, so watch the triggers below.")))

    if rec != "Buy":
        need = [f"debate score reaches {persona.buy_score:+.2f} (now {S:+.2f})"]
        sources = []
        if dcf and dcf > 0:
            entry = dcf * (1 - persona.min_margin_of_safety)
            need.append(f"price is at or below {_usd(entry)} (a {persona.min_margin_of_safety:.0%} margin of "
                        f"safety on the DCF value {_usd(dcf)}; price now {_usd(price) if price else 'n/a'})")
            sources += ["calc:dcf.value_per_share", "mkt:price"]
        elif persona.buy_requires_dcf:
            need.append("a DCF value can be computed (none was: Buy is not possible under these thresholds)")
        vol = q("quant:returns.vol_annual")
        if persona.max_volatility is not None and vol is not None and vol > persona.max_volatility:
            need.append(f"annualised volatility falls to {persona.max_volatility:.0%} or less (now {vol:.0%})")
            sources.append("quant:returns.vol_annual")
        out.append(Suggestion(kind="trigger", title="Would become Buy if",
                              detail="; and ".join(need) + ".", sources=sources))
    if rec != "Sell":
        out.append(Suggestion(kind="trigger", title="Would become Sell if",
                              detail=f"the debate score falls to {persona.sell_score:+.2f} or lower (now {S:+.2f})."))
    if rec == "Buy" and dcf and dcf > 0:
        ceiling = dcf * (1 - persona.min_margin_of_safety)
        out.append(Suggestion(kind="trigger", title="Buy case weakens above",
                              detail=f"{_usd(ceiling)}: above that price the margin of safety on the DCF value "
                                     f"{_usd(dcf)} drops below the {name} minimum of {persona.min_margin_of_safety:.0%}.",
                              sources=["calc:dcf.value_per_share"]))

    # --- price levels
    p10, p50, p90 = (q(f"quant:mc_dcf.fair_value_{k}") for k in ("p10", "p50", "p90"))
    if price and p10 is not None and p50 is not None and p90 is not None:
        where = ("below the whole range" if price < p10 else "above the whole range" if price > p90
                 else "inside the range")
        share = q("quant:mc_dcf.prob_fair_above_price")
        out.append(Suggestion(kind="level", title="Fair-value range (Monte Carlo DCF)",
                              detail=f"{_usd(p10)} to {_usd(p90)}, median {_usd(p50)}; the price {_usd(price)} is {where}"
                                     + (f" ({share:.0%} of simulations put fair value above the price)." if share is not None else "."),
                              sources=["quant:mc_dcf.fair_value_p10", "quant:mc_dcf.fair_value_p50",
                                       "quant:mc_dcf.fair_value_p90", "mkt:price"]))
    tmean, tup = evidence.get("cat:target_mean"), evidence.get("cat:target_mean_upside")
    if tmean is not None and tup is not None:
        out.append(Suggestion(kind="level", title="Analyst consensus target",
                              detail=f"Mean target {_usd(tmean)}, {_pct(tup)} from the snapshot price.",
                              sources=["cat:target_mean", "cat:target_mean_upside"]))

    # --- upcoming event
    days, move = evidence.get("cat:days_to_earnings"), evidence.get("cat:implied_move")
    if days is not None and days >= 0:
        detail = f"Next earnings report in {days:.0f} days."
        if move is not None:
            detail += (f" Options imply a move of about {_apct(move)} either way by the first expiry after it; "
                       "a decision made before the report carries that event risk.")
        out.append(Suggestion(kind="event", title="Earnings ahead", detail=detail,
                              sources=["cat:days_to_earnings"] + (["cat:implied_move"] if move is not None else [])))
    up, down = evidence.get("cat:eps_revisions_up_30d"), evidence.get("cat:eps_revisions_down_30d")
    if up is not None and down is not None and up + down > 0:
        lean = "upward" if up > down else "downward" if down > up else "balanced"
        out.append(Suggestion(kind="event", title="Estimate revisions lean " + lean,
                              detail=f"{up:.0f} upward and {down:.0f} downward EPS revisions for this fiscal year in the last 30 days.",
                              sources=["cat:eps_revisions_up_30d", "cat:eps_revisions_down_30d"]))

    # --- risk (descriptive: what happened over the past year)
    var95, dd, vol = q("quant:returns.var95_hist"), q("quant:returns.max_drawdown"), q("quant:returns.vol_annual")
    if var95 is not None and dd is not None:
        out.append(Suggestion(kind="risk", title="Size for the swings",
                              detail=f"Over the past year, 1 trading day in 20 lost {_apct(var95, 2)} or more, the worst "
                                     f"peak-to-trough fall was {_apct(dd)}"
                                     + (f", and annualised volatility was {vol:.0%}." if vol is not None else ".")
                                     + " Past swings, not a forecast.",
                              sources=["quant:returns.var95_hist", "quant:returns.max_drawdown"]
                                      + (["quant:returns.vol_annual"] if vol is not None else [])))

    # --- context
    others = {p: v.recommendation for p, v in verdicts_by_persona.items()
              if p != persona_name and v.recommendation != rec}
    if others:
        out.append(Suggestion(kind="context", title="Other risk settings disagree",
                              detail="Same debate, different thresholds: " +
                                     ", ".join(f"{p.capitalize()} gives {r}" for p, r in others.items()) + "."))
    if verdict.contradictions:
        agents = sorted({c.agent for c in verdict.contradictions})
        out.append(Suggestion(kind="context", title="Quant report disagrees",
                              detail=f"{', '.join(a.capitalize() for a in agents)} lean against the quant report's "
                                     "probabilities; check that disagreement before acting."))
    if verdict.agents_missing:
        out.append(Suggestion(kind="context", title="Partial committee",
                              detail=f"Not counted: {', '.join(a.capitalize() for a in verdict.agents_missing)}. "
                                     "The verdict rests on fewer domains than usual."))
    return out
