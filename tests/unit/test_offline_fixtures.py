"""
Tests that exercise the SEC, news and yfinance code paths against the recorded
fixtures in tests/fixtures (no network), plus the fake LLM client and startup safety.
"""

import importlib
import os

import pytest

from coordinator.llm_client import FakeLLMClient
from judge.ap2_handler import AP2Handler
from mcp_server.tools.news_stream import get_news_stream
from mcp_server.tools.sec_filings import fetch_filing_data


def test_sec_filings_from_fixture():
    result = fetch_filing_data("AAPL", "10-K", count=1)
    assert result["success"]
    assert result["company_name"] == "Apple Inc."
    assert result["filings"][0]["form"] == "10-K"


def test_sec_unknown_ticker():
    result = fetch_filing_data("ZZZZ", "10-K", count=1)
    assert not result["success"]


def test_news_from_fixture():
    result = get_news_stream("AAPL", days=7)
    assert result["success"]
    assert result["count"] > 0


def test_network_is_blocked_by_default():
    import httpx
    with pytest.raises(AssertionError, match="Unexpected network call"):
        httpx.get("https://example.com")


def _login(client):
    client.post("/auth/register", json={"username": "u", "email": "u@t.com", "password": "p"})
    token = client.post("/auth/token", data={"username": "u", "password": "p"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_yfinance_quote_and_history(client):
    headers = _login(client)
    quote = client.get("/api/v1/ticker/AAPL/quote", headers=headers)
    assert quote.status_code == 200
    assert quote.json()["symbol"] == "AAPL"
    hist = client.get("/api/v1/ticker/AAPL/history?period=1mo", headers=headers)
    assert hist.status_code == 200 and len(hist.json()) > 5


def test_yfinance_indices(client):
    headers = _login(client)
    res = client.get("/api/v1/market/indices", headers=headers)
    assert [r["name"] for r in res.json()] == ["S&P 500", "NASDAQ", "DOW", "VIX"]


def test_fake_llm_client_interface():
    fake = FakeLLMClient(system_instruction="x", responses=["one", "two"])
    assert fake.generate_content("a").text == "one"
    r = fake.generate_content("b")
    assert r.text == "two"
    assert r.usage_metadata.total_token_count > 0
    assert fake.calls == ["a", "b"]
    assert fake.generate_content("c").text == "two"  # last item repeats


def test_missing_gateway_secret_fails_startup(monkeypatch):
    import gateway.auth as auth
    monkeypatch.delenv("GATEWAY_SECRET_KEY")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    try:
        with pytest.raises(RuntimeError, match="GATEWAY_SECRET_KEY"):
            importlib.reload(auth)
    finally:
        monkeypatch.setenv("GATEWAY_SECRET_KEY", "test-only-secret-not-for-production")
        importlib.reload(auth)


def test_ap2_generates_key_at_env_path_when_missing(tmp_path, monkeypatch):
    path = tmp_path / "keys" / "ap2_private_key.pem"
    monkeypatch.setenv("AP2_PRIVATE_KEY_PATH", str(path))
    AP2Handler()
    assert path.exists()
    assert (tmp_path / "keys" / "ap2_public_key.pem").exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    # second instance loads the same key rather than regenerating
    mtime = path.stat().st_mtime_ns
    AP2Handler()
    assert path.stat().st_mtime_ns == mtime


def test_ap2_public_path_never_equals_private_path(tmp_path):
    handler = AP2Handler(private_key_path=str(tmp_path / "mykey.pem"))
    assert handler.public_key_path != handler.private_key_path


# ---------------------------------------------------------------- Phase 2 data sources

from mcp_server.tools.finance_math import calculate_dcf_value
from mcp_server.tools.market_data import fetch_market_data
from mcp_server.tools.news_stream import merge_articles
from mcp_server.tools.sec_financials import fetch_financial_statements


def test_sec_financials_from_fixture():
    r = fetch_financial_statements("AAPL")
    assert r["success"] and r["company_name"] == "Apple Inc."
    assert r["fiscal_years"] == ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"]
    by_id = {x["id"]: x for x in r["records"]}
    assert by_id["sec:revenue:FY2026"]["value"] == 478834000000
    assert by_id["sec:eps_diluted:FY2026"]["value"] == 8.98
    assert "0000320193-26-000026" in by_id["sec:revenue:FY2026"]["source"]


def test_sec_financials_failure_is_explicit(failing_sources):
    failing_sources.add("sec_facts")
    r = fetch_financial_statements("AAPL")
    assert not r["success"] and "HTTP 503" in r["error"]


def test_market_data_from_fixture_and_failure(failing_sources):
    r = fetch_market_data("AAPL")
    assert r["success"] and len(r["history"]) > 200 and r["shares_outstanding"] > 0
    failing_sources.add("yfinance")
    r = fetch_market_data("AAPL")
    assert not r["success"] and "unavailable" in r["error"]


def test_news_all_providers_down(failing_sources):
    failing_sources.update({"yfinance", "yahoo_rss", "nasdaq_rss", "google_rss"})
    r = get_news_stream("AAPL")
    assert not r["success"]
    assert set(r["providers_failed"]) == {"yfinance", "yahoo_rss", "nasdaq_rss", "google_rss"}


def test_news_merge_majority_and_tie_break():
    base = {"title": "Apple beats estimates", "url": "https://x.com/a?utm=1", "summary": None}
    merged = merge_articles({
        "yfinance": [dict(base, published_at="2026-11-12T10:00:00Z", source="Reuters", summary="S1")],
        "yahoo_rss": [dict(base, url="https://x.com/a", published_at="2026-11-12T11:00:00Z", source=None)],
        "nasdaq_rss": [dict(base, url="https://x.com/a/", published_at="2026-11-12T11:00:00Z", source="Reuters")],
        "google_rss": [dict(base, url="https://news.google.com/r/1", published_at="2026-11-12T10:00:00Z",
                            source="AP")],
    })
    assert len(merged) == 1                      # same article: by URL, then by title
    a = merged[0]
    assert a["providers"] == ["yfinance", "yahoo_rss", "nasdaq_rss", "google_rss"]
    assert a["source"] == "Reuters"              # 2 votes vs 1
    assert a["published_at"] == "2026-11-12T10:00:00Z"   # 2-2 tie -> yfinance's value
    assert a["summary"] == "S1" and not a["headline_only"]


def test_dcf_subtracts_net_debt():
    without = calculate_dcf_value([100, 100], 0.10, 0.02, shares_outstanding=10)
    with_debt = calculate_dcf_value([100, 100], 0.10, 0.02, shares_outstanding=10, net_debt=50)
    assert with_debt["equity_value"] == pytest.approx(without["enterprise_value"] - 50)
    assert with_debt["value_per_share"] == pytest.approx(without["value_per_share"] - 5, abs=0.01)
