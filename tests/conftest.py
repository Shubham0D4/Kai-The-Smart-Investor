"""
Kai's test suite runs LIVE: real LLM calls (Groq, Gemini) and real data (SEC EDGAR, Yahoo
Finance, news feeds). There is no fake model and no recorded data.

Cost and time: a full run makes three complete debates (about 50-70k tokens each) plus a few
small model checks, and takes roughly 10-20 minutes. Both GROQ_API_KEY and GOOGLE_API_KEY must
be set (in .env); without them every test is skipped with that reason.

Everything a run writes goes to tests/artifacts/ (gitignored): its own SQLite database, debate
logs, signed alpha packets and a throwaway signing key, so the live system's data is untouched.
Results are collected into tests/artifacts/results.json; `python scripts/write_test_results.py`
turns them into TESTRESULTS.md.
"""

import json
import os
import shutil
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "tests" / "artifacts"
load_dotenv(ROOT / ".env")

# Isolate every output of the run before any Kai module is imported
if ARTIFACTS.exists():
    shutil.rmtree(ARTIFACTS)
(ARTIFACTS / "runs").mkdir(parents=True)
os.environ["KAI_DEBATE_LOG_DIR"] = str(ARTIFACTS / "debate_logs")
os.environ["KAI_PACKET_DIR"] = str(ARTIFACTS / "alpha_packets")
os.environ["AP2_PRIVATE_KEY_PATH"] = str(ARTIFACTS / "test_ap2_private_key.pem")
os.environ["KAI_DB_URL"] = f"sqlite:///{ARTIFACTS / 'kai_test.db'}"

MISSING_KEYS = [k for k in ("GROQ_API_KEY", "GOOGLE_API_KEY") if not os.getenv(k)]

_results = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tests": [], "metrics": {}}


def pytest_collection_modifyitems(config, items):
    if MISSING_KEYS:
        skip = pytest.mark.skip(reason=f"live tests need {', '.join(MISSING_KEYS)} in .env")
        for item in items:
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        entry = {"id": item.nodeid, "outcome": rep.outcome, "duration_s": round(rep.duration, 2),
                 "doc": (item.function.__doc__ or "").strip().split("\n")[0] if hasattr(item, "function") else ""}
        if rep.outcome != "passed":
            entry["detail"] = str(rep.longrepr)[-1500:] if rep.longrepr else ""
            if rep.skipped and isinstance(rep.longrepr, tuple):
                entry["detail"] = rep.longrepr[2]
        _results["tests"].append(entry)


def pytest_sessionfinish(session, exitstatus):
    _results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _results["exitstatus"] = int(exitstatus)
    (ARTIFACTS / "results.json").write_text(json.dumps(_results, indent=1, default=str))


@pytest.fixture(scope="session")
def record():
    """record(section, key, value): store a measured number or fact for TESTRESULTS.md."""
    def _record(section, key, value):
        _results["metrics"].setdefault(section, {})[key] = value
    return _record


# The debates the suite runs: one ticker per persona, chosen to exercise the known hard cases
# (NVDA: 2024 stock split and earnings within days; AAPL: large stable filer; TSLA: volatile).
RUNS = [("NVDA", "balanced"), ("AAPL", "zen"), ("TSLA", "alpha")]


@pytest.fixture(scope="session")
def gateway():
    """The real FastAPI app (real coordinator, Sigma, signer), on the isolated test database."""
    from fastapi.testclient import TestClient
    from gateway.app import create_app
    with TestClient(create_app()) as client:
        yield client


def _run_analysis(client, ticker, persona, timeout_s=900):
    started = time.time()
    r = client.post("/api/v1/analyze", json={"ticker": ticker, "persona": persona})
    r.raise_for_status()
    sid = r.json()["session_id"]
    body = None
    running_snapshot = None
    while time.time() - started < timeout_s:
        body = client.get(f"/api/v1/status/{sid}").json()
        if body["status"] not in ("pending", "running"):
            break
        # keep one real mid-debate state (round 2 under way) for the dashboard's tests
        if running_snapshot is None and (body.get("debate") or {}).get("rounds_started", 0) >= 2:
            running_snapshot = body
            (ARTIFACTS / "runs" / f"{ticker}_{persona}_running.json").write_text(json.dumps(body, indent=1))
        time.sleep(3)
    elapsed = round(time.time() - started, 1)
    log = None
    log_dir = Path(os.environ["KAI_DEBATE_LOG_DIR"])
    debate_id = (body or {}).get("debate", {}) and body["debate"].get("debate_id")
    if debate_id and (log_dir / f"{debate_id}.json").exists():
        log = json.loads((log_dir / f"{debate_id}.json").read_text())
    run = {"ticker": ticker, "persona": persona, "session_id": sid, "elapsed_s": elapsed,
           "status": body, "log": log}
    (ARTIFACTS / "runs" / f"{ticker}_{persona}.json").write_text(json.dumps(run, indent=1, default=str))
    return run


@pytest.fixture(scope="session")
def runs(gateway):
    """Run every debate in RUNS once, end to end, through the gateway API."""
    return {t: _run_analysis(gateway, t, p) for t, p in RUNS}
