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
