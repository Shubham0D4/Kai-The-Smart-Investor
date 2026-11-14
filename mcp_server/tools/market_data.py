"""
Market data via yfinance: current price, shares outstanding, ten years of daily closes (two for
Gamma, ten for the quant layer's base rates), stock splits, the S&P 500 over two years (for
beta) and the 10-year Treasury yield (risk-free rate for CAPM). Price and history are required; the benchmark and yield are optional and
their absence is reported as a warning. Failures return success=False with an explicit error.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

import yfinance as yf

BENCHMARK = "^GSPC"      # S&P 500
RISK_FREE = "^TNX"       # 10-year Treasury yield, quoted in percent
TRADING_DAYS_1Y = 252
TRADING_DAYS_2Y = 504
# Price-return windows in trading days; every return record carries its exact start..end dates,
# so agents cannot compare a 1-month move with a 1-year one by accident.
RETURN_WINDOWS = (("5d", "5 trading days", 5), ("1m", "1 month", 21), ("3m", "3 months", 63),
                  ("6m", "6 months", 126), ("1y", "1 year", 252))


def return_records(history: List[Dict], ticker: str) -> List[Dict]:
    """Windowed price returns, year to date, and the drawdown from the 52-week high."""
    last = history[-1]
    url = f"https://finance.yahoo.com/quote/{ticker}/history"
    out = []
    for key, label, n in RETURN_WINDOWS:
        if len(history) > n:
            start = history[-1 - n]
            out.append({"id": f"mkt:return_{key}", "label": f"Price return, {label}",
                        "value": round(last["close"] / start["close"] - 1, 6), "unit": "ratio",
                        "period": f"{start['date']}..{last['date']}",
                        "source": "yfinance daily closes (split-adjusted)", "url": url})
    prior_year = [p for p in history if p["date"][:4] < last["date"][:4]]
    if prior_year:
        start = prior_year[-1]
        out.append({"id": "mkt:return_ytd", "label": "Price return, year to date",
                    "value": round(last["close"] / start["close"] - 1, 6), "unit": "ratio",
                    "period": f"{start['date']}..{last['date']}",
                    "source": "yfinance daily closes (split-adjusted)", "url": url})
    year = history[-TRADING_DAYS_1Y:]
    high = max(year, key=lambda p: p["close"])
    out.append({"id": "mkt:high_52w", "label": f"52-week closing high (on {high['date']})",
                "value": round(high["close"], 4), "unit": "USD", "period": f"{year[0]['date']}..{last['date']}",
                "source": "yfinance daily closes (split-adjusted)", "url": url})
    out.append({"id": "mkt:drawdown_from_52w_high", "label": "Last close vs 52-week high",
                "value": round(last["close"] / high["close"] - 1, 6), "unit": "ratio",
                "period": f"{high['date']}..{last['date']}",
                "source": "yfinance daily closes (split-adjusted)", "url": url})
    return out


def _closes(ticker, period: str) -> List[Dict]:
    hist = ticker.history(period=period, interval="1d")
    return [
        {"date": idx.strftime("%Y-%m-%d"), "close": float(row["Close"])}
        for idx, row in hist.iterrows()
        if row["Close"] == row["Close"]  # drop NaN
    ]


def fetch_market_data(ticker: str, period: str = "10y", benchmark_period: str = "2y") -> Dict:
    """
    MCP tool: price, shares outstanding, daily closes, benchmark closes and risk-free rate.

    Returns {success, ticker, price, previous_close, shares_outstanding,
    history (last 2y) / history_1y / history_long (full period) [{date, close}],
    splits [{date, ratio}], benchmark_history, risk_free_rate, records[], warnings[]}
    or {success: False, ticker, error}. Closes are split-adjusted by yfinance.
    """
    t = ticker.upper()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        tk = yf.Ticker(t)
        fi = tk.fast_info
        price = fi.last_price
        history_long = _closes(tk, period)
        shares = getattr(fi, "shares", None)
    except Exception as e:
        return {"success": False, "ticker": t, "error": f"yfinance market data unavailable: {e}"}

    if price is None or not price > 0:
        return {"success": False, "ticker": t, "error": "yfinance returned no current price"}
    if len(history_long) < 30:
        return {"success": False, "ticker": t,
                "error": f"yfinance returned only {len(history_long)} daily prices for {period}"}
    history = history_long[-TRADING_DAYS_2Y:]

    warnings: List[str] = []
    splits: List[Dict] = []
    try:
        splits = [{"date": idx.strftime("%Y-%m-%d"), "ratio": float(v)} for idx, v in tk.splits.items()]
    except Exception as e:
        warnings.append(f"split history unavailable: {e}")
    benchmark: List[Dict] = []
    try:
        benchmark = _closes(yf.Ticker(BENCHMARK), benchmark_period)
    except Exception as e:
        warnings.append(f"benchmark {BENCHMARK} history unavailable: {e}")
    risk_free: Optional[float] = None
    try:
        tnx = yf.Ticker(RISK_FREE).fast_info.last_price
        if tnx and tnx > 0:
            risk_free = round(float(tnx) / 100, 6)
        else:
            warnings.append(f"risk-free rate {RISK_FREE} returned no value")
    except Exception as e:
        warnings.append(f"risk-free rate {RISK_FREE} unavailable: {e}")

    history_1y = history[-TRADING_DAYS_1Y:]
    price = float(price)
    records = [{
        "id": "mkt:price",
        "label": "Last price",
        "value": round(price, 4),
        "unit": "USD",
        "period": history[-1]["date"],
        "source": "yfinance fast_info.last_price (fetch time in the debate's market snapshot)",
        "url": f"https://finance.yahoo.com/quote/{t}",
    }, {
        "id": "mkt:price_1y_ago",
        "label": "Close one year ago (start of 1y window)",
        "value": round(history_1y[0]["close"], 4),
        "unit": "USD",
        "period": history_1y[0]["date"],
        "source": "yfinance history",
        "url": f"https://finance.yahoo.com/quote/{t}/history",
    }]
    if shares:
        records.append({
            "id": "mkt:shares_outstanding",
            "label": "Shares outstanding (yfinance)",
            "value": float(shares),
            "unit": "shares",
            "period": history[-1]["date"],
            "source": "yfinance fast_info.shares",
            "url": f"https://finance.yahoo.com/quote/{t}",
        })
    if risk_free is not None:
        records.append({
            "id": "mkt:risk_free_rate",
            "label": "10-year Treasury yield",
            "value": risk_free,
            "unit": "ratio",
            "period": history[-1]["date"],
            "source": f"yfinance {RISK_FREE} / 100",
            "url": "https://finance.yahoo.com/quote/%5ETNX",
        })
    records += return_records(history, t)

    return {
        "success": True,
        "ticker": t,
        "fetched_at": fetched_at,
        "market_date": history[-1]["date"],
        "price": price,
        "previous_close": getattr(fi, "previous_close", None),
        "shares_outstanding": float(shares) if shares else None,
        "history": history,
        "history_1y": history_1y,
        "history_long": history_long,
        "splits": splits,
        "benchmark_history": benchmark,
        "risk_free_rate": risk_free,
        "records": records,
        "warnings": warnings,
    }
