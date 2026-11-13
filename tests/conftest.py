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
import re
import types
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from coordinator.llm_client import FakeLLMClient

FIXTURES = Path(__file__).parent / "fixtures"

# When the fixtures were recorded; the news window is evaluated relative to this.
FIXTURE_NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)

# Data sources a test can switch off: "sec", "sec_facts", "yahoo_rss", "nasdaq_rss",
# "google_rss", "yfinance". See the failing_sources fixture.
FAILING = set()


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
    """Serve recorded SEC / news responses; fail loudly on anything else."""
    url = str(url)
    request = httpx.Request("GET", url)

    def ok(name):
        return httpx.Response(200, content=_load(name), request=request)

    def down():
        return httpx.Response(503, content=b"service unavailable", request=request)

    is_sec = url.startswith(("https://www.sec.gov/", "https://data.sec.gov/"))
    if is_sec and "sec" in FAILING:
        return down()
    if url == "https://www.sec.gov/files/company_tickers.json":
        return ok("sec_company_tickers.json")
    if url == "https://data.sec.gov/submissions/CIK0000320193.json":
        return ok("sec_submissions_aapl.json")
    if url.startswith("https://data.sec.gov/submissions/"):
        return httpx.Response(404, content=b"not found", request=request)
    if url.startswith("https://data.sec.gov/api/xbrl/companyfacts/"):
        if "sec_facts" in FAILING:
            return down()
        if url.endswith("CIK0000320193.json"):
            return ok("sec_companyfacts_aapl.json")
        return httpx.Response(404, content=b"not found", request=request)
    if url.startswith("https://feeds.finance.yahoo.com/rss/"):
        return down() if "yahoo_rss" in FAILING else ok("news_yahoo_rss.xml")
    if url.startswith("https://www.nasdaq.com/feed/rssoutbound"):
        return down() if "nasdaq_rss" in FAILING else ok("news_nasdaq_rss.xml")
    if url.startswith("https://news.google.com/rss/search"):
        return down() if "google_rss" in FAILING else ok("news_rss.xml")
    raise AssertionError(f"Unexpected network call in offline test: {url} (mark test @pytest.mark.live)")


class _FakeYFTicker:
    def __init__(self, symbol, data):
        if "yfinance" in FAILING:
            raise ConnectionError("yfinance unavailable (simulated)")
        self.symbol = symbol
        self._data = data
        q = data["quote"].get(symbol)
        idx = data["indices"].get(symbol)
        if q is None and idx is None:
            raise KeyError(f"no recorded yfinance fixture for {symbol}")
        fi = (q or {}).get("fast_info") or idx
        self.fast_info = types.SimpleNamespace(
            last_price=fi["last_price"], previous_close=fi["previous_close"], shares=fi.get("shares")
        )
        self.info = (q or {}).get("info", {})
        self.news = data.get("news", {}).get(symbol, [])

    def history(self, period="1d", interval="1d"):
        by_period = self._data["history"].get(self.symbol, {})
        rows = by_period.get(period) or by_period.get("1mo", [])
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
    FAILING.clear()
    if "live" in request.keywords:
        return
    monkeypatch.setattr(httpx, "get", _offline_httpx_get)
    fake_yf = _fake_yfinance()
    import gateway.app
    import mcp_server.tools.market_data as market_data
    import mcp_server.tools.news_stream as news_stream
    monkeypatch.setattr(gateway.app, "yf", fake_yf)
    monkeypatch.setattr(market_data, "yf", fake_yf)
    monkeypatch.setattr(news_stream, "yf", fake_yf)
    monkeypatch.setattr(news_stream, "_now", lambda: FIXTURE_NOW)
    yield
    FAILING.clear()


@pytest.fixture
def failing_sources():
    """Add source names to this set to make them fail, e.g. failing_sources.add("sec")."""
    return FAILING


@pytest.fixture
def fake_llm():
    """A FakeLLMClient with a fixed (non-JSON) canned answer."""
    return FakeLLMClient(responses="Mocked Analysis Response")


_EVIDENCE_LINE = re.compile(r"^\[([^\]]+)\]", re.MULTILINE)
_AGENT_IN_PROMPT = re.compile(r'"agent": "(charlie|delta|gamma)", "round": (\d)')


def valid_output_for(prompt: str) -> str:
    """A schema-valid reply that cites the first evidence ids listed in the prompt."""
    m = _AGENT_IN_PROMPT.search(prompt)
    agent, rnd = m.group(1), int(m.group(2))
    ids = _EVIDENCE_LINE.findall(prompt)[:3]
    claims = []
    for n, eid in enumerate(ids, start=1):
        if eid.startswith("news:"):
            claims.append({"id": f"C{n}", "kind": "quote", "text": "Coverage point",
                           "quote": "placeholder quote", "evidence": [eid]})
        else:
            claims.append({"id": f"C{n}", "kind": "numeric", "text": f"Value of {eid}",
                           "value": 1.0, "unit": "USD", "op": "equals", "evidence": [eid]})
    claims.append({"id": f"C{len(ids) + 1}", "kind": "inference", "text": "Overall view",
                   "based_on": [c["id"] for c in claims]})
    return json.dumps({"agent": agent, "round": rnd, "stance": "neutral", "confidence": 55,
                       "claims": claims, "risks": ["canned risk"]})


def _section_ids(prompt: str, heading: str):
    """Evidence ids listed under a '## <heading>' section of a round 2/3 prompt."""
    body = prompt.split(heading, 1)[1].split("\n## ", 1)[0]
    return _EVIDENCE_LINE.findall(body)


def valid_round2_for(prompt: str) -> str:
    agent = re.search(r"You are (charlie|delta|gamma)\.", prompt).group(1)
    own = _section_ids(prompt, "## Your evidence")
    targets = re.search(r"Claims you may target: (.*)", prompt).group(1).split(", ")
    first_per_agent = {}
    for t in targets:
        first_per_agent.setdefault(t.split(".")[0], t)
    critiques = [{"id": f"K{i}", "target": t, "verdict": "qualify",
                  "argument": f"Canned argument from {agent} about {t}", "evidence": [own[0]]}
                 for i, t in enumerate(first_per_agent.values(), start=1)]
    return json.dumps({"agent": agent, "round": 2, "critiques": critiques})


def valid_round3_for(prompt: str) -> str:
    agent = re.search(r"You are (charlie|delta|gamma)\.", prompt).group(1)
    stance = re.search(r"Your round-1 stance: (\w+)", prompt).group(1)
    own = _section_ids(prompt, "## Your evidence")
    received = re.findall(r"^- ((?:charlie|delta|gamma)\.K\d+) \(\w+, targets \w+\.(\w+)\)", prompt, re.M)
    by_claim = {}
    for ref, claim in received:
        by_claim.setdefault(claim, []).append(ref)
    changes = [{"claim": c, "action": "kept", "reason": "evidence unchanged", "in_response_to": refs}
               for c, refs in by_claim.items()]
    claims = [{"id": "C1", "kind": "numeric", "text": f"Value of {own[0]}", "value": 1.0, "unit": "USD",
               "op": "equals", "evidence": [own[0]]},
              {"id": "C2", "kind": "inference", "text": "Final view", "based_on": ["C1"]}]
    return json.dumps({"agent": agent, "round": 3, "stance": stance, "confidence": 60,
                       "claims": claims, "risks": ["canned risk"], "changes": changes,
                       "changed_by": [], "stance_change": {"from": stance, "to": stance, "reason": ""}})


def _schema_responder(prompt: str) -> str:
    if "ROUND 2 - CROSS-EXAMINATION" in prompt:
        return valid_round2_for(prompt)
    if "ROUND 3 - FINAL POSITION" in prompt:
        return valid_round3_for(prompt)
    if "Return ONLY a JSON object" in prompt:
        return valid_output_for(prompt)
    return "Canned text."


@pytest.fixture
def make_valid_output():
    """The function that builds a schema-valid reply for a given prompt."""
    return valid_output_for


@pytest.fixture
def schema_llm():
    """FakeLLMClient that answers round 1, 2 and 3 prompts with schema-valid JSON."""
    return FakeLLMClient(responses=_schema_responder)
