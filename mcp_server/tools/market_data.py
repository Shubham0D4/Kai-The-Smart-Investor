"""
Market data via yfinance: current price, shares outstanding, ten years of daily closes (two for
Gamma, ten for the quant layer's base rates), stock splits, the S&P 500 over two years (for
beta) and the 10-year Treasury yield (risk-free rate for CAPM). Price and history are required; the benchmark and yield are optional and
their absence is reported as a warning. Failures return success=False with an explicit error.
"""

from typing import Dict, List, Optional

import yfinance as yf

BENCHMARK = "^GSPC"      # S&P 500
RISK_FREE = "^TNX"       # 10-year Treasury yield, quoted in percent
TRADING_DAYS_1Y = 252
TRADING_DAYS_2Y = 504


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
        "source": "yfinance fast_info.last_price",
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

    return {
        "success": True,
        "ticker": t,
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
