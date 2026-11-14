"""
Forward-looking catalysts via yfinance, for Vega: the next earnings date and consensus
estimates, analyst price targets, rating and estimate revisions, and what the options market
implies (at-the-money implied volatility and the implied move to an expiry after earnings).

Every number is computed here, in code, from the provider's data and becomes an evidence record
with its date or date range. Sections that fail are reported as warnings; the result is
success=False only when nothing could be fetched.
"""

from datetime import date, timedelta
from typing import Dict, List, Optional

import yfinance as yf

EARNINGS_HORIZON_DAYS = 90       # an earnings date further away is reported but not used for options
MIN_EXPIRY_DAYS = 7              # nearest expiry considered when there is no near earnings date


def _rec(id_: str, label: str, value, unit: str, period: Optional[str], source: str, url: str) -> Dict:
    return {"id": id_, "label": label, "value": None if value is None else float(value), "unit": unit,
            "period": period, "source": source, "url": url}


def _mid(row) -> Optional[float]:
    bid, ask, last = float(row.get("bid") or 0), float(row.get("ask") or 0), float(row.get("lastPrice") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return last if last > 0 else None


def pick_expiry(expiries: List[str], market_date: str, earnings: Optional[str]) -> Optional[str]:
    """First expiry on or after the earnings date when earnings are near; otherwise the first
    expiry at least MIN_EXPIRY_DAYS away."""
    today = date.fromisoformat(market_date)
    if earnings and 0 <= (date.fromisoformat(earnings) - today).days <= EARNINGS_HORIZON_DAYS:
        after = [e for e in expiries if e >= earnings]
        if after:
            return after[0]
    later = [e for e in expiries if e >= (today + timedelta(days=MIN_EXPIRY_DAYS)).isoformat()]
    return later[0] if later else None


def fetch_catalysts(ticker: str, price: float, market_date: str) -> Dict:
    """
    MCP tool: catalysts for `ticker` relative to `market_date` (ISO date of the last close) and
    `price` (the debate's snapshot price, so every agent works from the same number).

    Returns {success, ticker, earnings_date, records[], warnings[]} or {success: False, error}.
    """
    t = ticker.upper()
    url = f"https://finance.yahoo.com/quote/{t}/analysis"
    records: List[Dict] = []
    warnings: List[str] = []
    try:
        tk = yf.Ticker(t)
    except Exception as e:
        return {"success": False, "ticker": t, "error": f"yfinance catalyst data unavailable: {e}"}
    today = date.fromisoformat(market_date)

    # --- next earnings date and consensus
    earnings: Optional[str] = None
    try:
        cal = tk.calendar or {}
        dates = [d for d in (cal.get("Earnings Date") or []) if d]
        if dates:
            earnings = min(dates).isoformat()
            days = (min(dates) - today).days
            records.append(_rec("cat:days_to_earnings", f"Days to next earnings report ({earnings})", days,
                                "days", f"{market_date}..{earnings}", "yfinance calendar 'Earnings Date'", url))
        for key, id_, label, unit in (("Earnings Average", "cat:eps_estimate_next", "Consensus EPS, next report", "USD/shares"),
                                      ("Earnings Low", "cat:eps_estimate_low", "Lowest EPS estimate, next report", "USD/shares"),
                                      ("Earnings High", "cat:eps_estimate_high", "Highest EPS estimate, next report", "USD/shares"),
                                      ("Revenue Average", "cat:revenue_estimate_next", "Consensus revenue, next report", "USD")):
            if cal.get(key) is not None:
                records.append(_rec(id_, label, round(float(cal[key]), 6), unit, earnings, "yfinance calendar", url))
        if not dates:
            warnings.append("no upcoming earnings date published")
    except Exception as e:
        warnings.append(f"earnings calendar unavailable: {e}")

    # --- analyst price targets
    try:
        tg = tk.analyst_price_targets or {}
        for key in ("mean", "median", "high", "low"):
            if tg.get(key):
                records.append(_rec(f"cat:target_{key}", f"Analyst price target, {key}", round(float(tg[key]), 4),
                                    "USD", market_date, "yfinance analyst_price_targets", url))
        if tg.get("mean"):
            records.append(_rec("cat:target_mean_upside", "Mean analyst target vs snapshot price",
                                round(float(tg["mean"]) / price - 1, 6), "ratio", market_date,
                                "computed: cat:target_mean / mkt:price - 1", url))
    except Exception as e:
        warnings.append(f"analyst price targets unavailable: {e}")

    # --- rating mix now vs three months ago
    try:
        rec = tk.recommendations
        if rec is not None and len(rec):
            rows = {str(r["period"]): r for _, r in rec.iterrows()}

            def buy_share(r):
                total = sum(float(r[k]) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
                return (float(r["strongBuy"]) + float(r["buy"])) / total if total else None
            for period, id_, label in (("0m", "cat:buy_rating_share", "Share of analysts rating Buy, this month"),
                                       ("-3m", "cat:buy_rating_share_3m_ago", "Share of analysts rating Buy, 3 months ago")):
                if period in rows and buy_share(rows[period]) is not None:
                    records.append(_rec(id_, label, round(buy_share(rows[period]), 6), "ratio", period,
                                        "yfinance recommendations (strongBuy + buy) / all", url))
    except Exception as e:
        warnings.append(f"analyst ratings unavailable: {e}")

    # --- upgrades / downgrades and target changes in the last 30 days
    try:
        ud = tk.upgrades_downgrades
        if ud is not None and len(ud):
            start = (today - timedelta(days=30)).isoformat()
            recent = [r for idx, r in ud.iterrows() if start <= str(idx)[:10] <= market_date]
            window = f"{start}..{market_date}"
            for id_, label, test in (
                    ("cat:upgrades_30d", "Analyst upgrades, last 30 days", lambda r: r.get("Action") == "up"),
                    ("cat:downgrades_30d", "Analyst downgrades, last 30 days", lambda r: r.get("Action") == "down"),
                    ("cat:target_raises_30d", "Price-target raises, last 30 days", lambda r: r.get("priceTargetAction") == "Raises"),
                    ("cat:target_cuts_30d", "Price-target cuts, last 30 days", lambda r: r.get("priceTargetAction") == "Lowers")):
                records.append(_rec(id_, label, sum(1 for r in recent if test(r)), "count", window,
                                    "yfinance upgrades_downgrades", url))
    except Exception as e:
        warnings.append(f"analyst actions unavailable: {e}")

    # --- EPS estimate revisions, current fiscal year
    try:
        rv = tk.eps_revisions
        if rv is not None and "0y" in rv.index:
            row = rv.loc["0y"]
            window = f"{(today - timedelta(days=30)).isoformat()}..{market_date}"
            records.append(_rec("cat:eps_revisions_up_30d", "Upward EPS estimate revisions (this FY), last 30 days",
                                int(row["upLast30days"]), "count", window, "yfinance eps_revisions", url))
            records.append(_rec("cat:eps_revisions_down_30d", "Downward EPS estimate revisions (this FY), last 30 days",
                                int(row["downLast30days"]), "count", window, "yfinance eps_revisions", url))
    except Exception as e:
        warnings.append(f"EPS revisions unavailable: {e}")

    # --- options: at-the-money implied volatility and implied move
    try:
        expiry = pick_expiry(list(tk.options or ()), market_date, earnings)
        if expiry is None:
            warnings.append("no suitable option expiry")
        else:
            chain = tk.option_chain(expiry)
            calls, puts = chain.calls, chain.puts
            strike = min(calls["strike"], key=lambda k: abs(float(k) - price))
            call = calls[calls["strike"] == strike].iloc[0].to_dict()
            put = puts[puts["strike"] == strike].iloc[0].to_dict() if (puts["strike"] == strike).any() else None
            ivs = [float(x["impliedVolatility"]) for x in (call, put) if x and x.get("impliedVolatility")]
            window = f"{market_date}..{expiry}"
            why = "first expiry after earnings" if earnings and expiry >= earnings and \
                (date.fromisoformat(earnings) - today).days <= EARNINGS_HORIZON_DAYS else "nearest expiry"
            if ivs:
                records.append(_rec("cat:implied_vol_atm", f"At-the-money implied volatility ({why}, strike {strike:g})",
                                    round(sum(ivs) / len(ivs), 6), "ratio", window, "yfinance option_chain", url))
            mids = [_mid(x) for x in (call, put) if x]
            if put is not None and all(m is not None for m in mids):
                records.append(_rec("cat:implied_move", f"Options-implied move to {expiry} (ATM straddle / price)",
                                    round(sum(mids) / price, 6), "ratio", window,
                                    "computed: (call mid + put mid) / mkt:price", url))
    except Exception as e:
        warnings.append(f"options data unavailable: {e}")

    if not records:
        return {"success": False, "ticker": t, "error": "no catalyst data: " + "; ".join(warnings)}
    return {"success": True, "ticker": t, "earnings_date": earnings, "records": records, "warnings": warnings}
