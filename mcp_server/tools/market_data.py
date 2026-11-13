"""
Market data for Gamma via yfinance: current price, shares outstanding and one year of
daily closes. Failures return success=False with an explicit error.
"""

from typing import Dict

import yfinance as yf


def fetch_market_data(ticker: str, period: str = "1y") -> Dict:
    """
    MCP tool: price, shares outstanding and daily closing prices.

    Returns {success, ticker, price, previous_close, shares_outstanding, history[{date, close}],
    records[]} or {success: False, ticker, error}.
    """
    t = ticker.upper()
    try:
        tk = yf.Ticker(t)
        fi = tk.fast_info
        price = fi.last_price
        hist = tk.history(period=period, interval="1d")
        history = [
            {"date": idx.strftime("%Y-%m-%d"), "close": float(row["Close"])}
            for idx, row in hist.iterrows()
            if row["Close"] == row["Close"]  # drop NaN
        ]
        shares = getattr(fi, "shares", None)
    except Exception as e:
        return {"success": False, "ticker": t, "error": f"yfinance market data unavailable: {e}"}

    if price is None or not price > 0:
        return {"success": False, "ticker": t, "error": "yfinance returned no current price"}
    if len(history) < 30:
        return {"success": False, "ticker": t,
                "error": f"yfinance returned only {len(history)} daily prices for {period}"}

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
        "label": "Close at start of price history",
        "value": round(history[0]["close"], 4),
        "unit": "USD",
        "period": history[0]["date"],
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

    return {
        "success": True,
        "ticker": t,
        "price": price,
        "previous_close": getattr(fi, "previous_close", None),
        "shares_outstanding": float(shares) if shares else None,
        "history": history,
        "records": records,
    }
