"""
Market-data endpoints (FIXES.md B9): a yfinance failure is an error response, never invented
numbers. Offline: recorded yfinance/news fixtures, failures switched on via failing_sources.
"""

import types

import pandas as pd
import pytest

import gateway.app as gateway_app

OLD_HARDCODED_NUMBERS = ("4,783.45", "15,055.65", "37,695.73")


def test_indices_quote_history_from_fixtures(client):
    indices = client.get("/api/v1/market/indices").json()
    assert [i["name"] for i in indices] == ["S&P 500", "NASDAQ", "DOW", "VIX"]
    assert all(i["available"] and i["value"] and i["change"].endswith("%") for i in indices)

    quote = client.get("/api/v1/ticker/AAPL/quote")
    assert quote.status_code == 200
    q = quote.json()
    assert q["symbol"] == "AAPL" and q["name"] == "Apple Inc." and q["price"] == "354.88"
    assert q["dividend_yield"] == "0.30%"               # yfinance already reports percent
    assert q["pe_ratio"] > 30 and q["high_52w"].startswith("$")

    history = client.get("/api/v1/ticker/AAPL/history?period=1mo").json()
    assert len(history) > 5 and set(history[0]) == {"name", "price"}


def test_yfinance_failure_is_an_error_not_hardcoded_numbers(client, failing_sources):
    failing_sources.add("yfinance")
    indices = client.get("/api/v1/market/indices")
    assert indices.status_code == 502
    assert "yfinance" in indices.json()["detail"]
    assert not any(n in indices.text for n in OLD_HARDCODED_NUMBERS)

    quote = client.get("/api/v1/ticker/AAPL/quote")
    assert quote.status_code == 502 and "yfinance" in quote.json()["detail"]

    history = client.get("/api/v1/ticker/AAPL/history?period=1mo")
    assert history.status_code == 502 and history.json() != []          # not an empty chart either


def test_one_failing_index_is_reported_individually(client, monkeypatch):
    real = gateway_app.yf.Ticker

    def flaky(symbol):
        if symbol == "^VIX":
            raise ConnectionError("vix feed down")
        return real(symbol)
    monkeypatch.setattr(gateway_app.yf, "Ticker", flaky)

    res = client.get("/api/v1/market/indices")
    assert res.status_code == 200
    by_name = {i["name"]: i for i in res.json()}
    assert by_name["S&P 500"]["available"] and by_name["S&P 500"]["value"]
    assert by_name["VIX"] == {"name": "VIX", "symbol": "^VIX", "available": False, "value": None,
                              "change": None, "positive": None, "error": "vix feed down"}


def test_quote_without_a_price_is_404_and_empty_history_is_404(client, monkeypatch):
    class NoPrice:
        fast_info = types.SimpleNamespace(last_price=None, previous_close=None)
        info = {}

        def history(self, **kw):
            return pd.DataFrame({"Close": []})
    monkeypatch.setattr(gateway_app.yf, "Ticker", lambda s: NoPrice())
    assert client.get("/api/v1/ticker/ZZZZ/quote").status_code == 404
    assert client.get("/api/v1/ticker/ZZZZ/history?period=1mo").status_code == 404


def test_quote_survives_missing_statistics(client, monkeypatch):
    real = gateway_app.yf.Ticker

    class Broken:
        def __init__(self, t):
            self._t = t
            self.fast_info = t.fast_info

        @property
        def info(self):
            raise TimeoutError("info endpoint timed out")
    monkeypatch.setattr(gateway_app.yf, "Ticker", lambda s: Broken(real(s)))
    res = client.get("/api/v1/ticker/AAPL/quote")
    assert res.status_code == 200
    body = res.json()
    assert body["price"] == "354.88" and body["name"] == "AAPL" and body["market_cap"] is None


def test_invalid_history_period_is_422(client):
    assert client.get("/api/v1/ticker/AAPL/history?period=10y").status_code == 422


# ---------------------------------------------------------------- news

def test_ticker_and_market_news_from_fixtures(client):
    res = client.get("/api/v1/ticker/AAPL/news?limit=5")
    assert res.status_code == 200
    body = res.json()
    assert 0 < len(body["articles"]) <= 5
    first = body["articles"][0]
    assert first["title"] and first["providers"] and first["id"].startswith("news:")
    assert any(a["summary"] for a in body["articles"])
    market = client.get("/api/v1/market/news")
    assert market.status_code == 200 and market.json()["articles"]


def test_news_with_one_provider_down_still_works_and_says_so(client, failing_sources):
    failing_sources.add("yahoo_rss")
    body = client.get("/api/v1/ticker/AAPL/news").json()
    assert "yahoo_rss" in body["providers_failed"] and body["articles"]


def test_news_with_every_provider_down_is_an_error(client, failing_sources):
    failing_sources.update({"yfinance", "yahoo_rss", "nasdaq_rss", "google_rss"})
    res = client.get("/api/v1/ticker/AAPL/news")
    assert res.status_code == 502 and "All news providers failed" in res.json()["detail"]
    assert client.get("/api/v1/market/news").status_code == 502
