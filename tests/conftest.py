"""
Pytest configuration and fixtures.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# --- Hermetic environment: must be set before any gateway/judge import --------
import tempfile

os.environ["GATEWAY_SECRET_KEY"] = "test-only-secret-not-for-production"
_AP2_DIR = tempfile.mkdtemp(prefix="kai-ap2-test-")
os.environ["AP2_PRIVATE_KEY_PATH"] = os.path.join(_AP2_DIR, "ap2_private_key.pem")
os.environ.pop("AP2_PUBLIC_KEY_PATH", None)
# Empty (not unset) so load_dotenv() will not pull real keys in from a .env file
os.environ["GOOGLE_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from gateway.database import Base, get_db
from gateway.app import app
from gateway import models

from sqlalchemy.pool import StaticPool

# Use in-memory SQLite for tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    """Create a fresh database session for each test."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db_session):
    """Test client with database dependency override."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

# --- Offline mode: fake LLM, recorded SEC / news / yfinance fixtures -----------
import json
import types
from pathlib import Path

import httpx
import pandas as pd

from coordinator.llm_client import FakeLLMClient

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="run tests marked @pytest.mark.live (need network / API keys)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="live test: run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def _load(name):
    return (FIXTURES / name).read_bytes()


def _offline_httpx_get(url, *args, **kwargs):
    """Serve recorded SEC / Google News responses; fail loudly on anything else."""
    url = str(url)
    request = httpx.Request("GET", url)
    if url == "https://www.sec.gov/files/company_tickers.json":
        return httpx.Response(200, content=_load("sec_company_tickers.json"), request=request)
    if url == "https://data.sec.gov/submissions/CIK0000320193.json":
        return httpx.Response(200, content=_load("sec_submissions_aapl.json"), request=request)
    if url.startswith("https://data.sec.gov/submissions/"):
        return httpx.Response(404, content=b"not found", request=request)
    if url.startswith("https://news.google.com/rss/search"):
        return httpx.Response(200, content=_load("news_rss.xml"), request=request)
    raise AssertionError(f"Unexpected network call in offline test: {url} (mark test @pytest.mark.live)")


class _FakeYFTicker:
    def __init__(self, symbol, data):
        self.symbol = symbol
        self._data = data
        q = data["quote"].get(symbol)
        idx = data["indices"].get(symbol)
        if q is None and idx is None:
            raise KeyError(f"no recorded yfinance fixture for {symbol}")
        fi = (q or {}).get("fast_info") or idx
        self.fast_info = types.SimpleNamespace(
            last_price=fi["last_price"], previous_close=fi["previous_close"]
        )
        self.info = (q or {}).get("info", {})

    def history(self, period="1d", interval="1d"):
        rows = self._data["history"].get(self.symbol, {}).get("1mo", [])
        return pd.DataFrame(
            {"Close": [r["close"] for r in rows]},
            index=pd.to_datetime([r["date"] for r in rows]),
        )


def _fake_yfinance():
    data = json.loads(_load("yfinance.json"))

    class _Tickers:
        def __init__(self, symbols):
            self.tickers = {s: _FakeYFTicker(s, data) for s in symbols.split()}

    return types.SimpleNamespace(
        Ticker=lambda symbol: _FakeYFTicker(symbol, data), Tickers=_Tickers
    )


@pytest.fixture(autouse=True)
def offline(request, monkeypatch):
    """Every test runs offline unless marked @pytest.mark.live."""
    if "live" in request.keywords:
        return
    monkeypatch.setattr(httpx, "get", _offline_httpx_get)
    import gateway.app
    monkeypatch.setattr(gateway.app, "yf", _fake_yfinance())


@pytest.fixture
def fake_llm():
    """A FakeLLMClient with a fixed canned answer."""
    return FakeLLMClient(responses="Mocked Analysis Response")
