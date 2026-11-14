"""
Live gateway API: market endpoints against real providers, history of the live runs, and the
human-approval rule applied to the real verdicts.
"""

import pytest

from coordinator.config import load_thresholds

from .conftest import RUNS

CFG = load_thresholds()


def test_market_endpoints_return_real_quotes(gateway, record):
    """Indices, quote, price history and news come from the providers (no substitute numbers)."""
    idx = gateway.get("/api/v1/market/indices")
    assert idx.status_code == 200 and any(i["available"] for i in idx.json())
    q = gateway.get("/api/v1/ticker/NVDA/quote")
    assert q.status_code == 200 and float(str(q.json()["price"]).replace(",", "")) > 0
    h = gateway.get("/api/v1/ticker/NVDA/history?period=1mo")
    assert h.status_code == 200 and len(h.json()) >= 15
    n = gateway.get("/api/v1/ticker/NVDA/news")
    assert n.status_code == 200 and n.json()["articles"]
    record("gateway", "market_endpoints", {"indices": [i["name"] for i in idx.json() if i["available"]],
                                           "nvda_quote": q.json()["price"], "history_points": len(h.json())})


def test_history_lists_every_live_run(gateway, runs):
    """Every analysis the suite ran is in the stored history with its recommendation."""
    hist = gateway.get("/api/v1/history").json()["analyses"]
    ids = {h["session_id"] for h in hist}
    for t, _ in RUNS:
        assert runs[t]["session_id"] in ids


@pytest.mark.parametrize("ticker", [t for t, _ in RUNS])
def test_human_approval_rule_on_the_real_verdict(gateway, runs, ticker, record):
    """Buy/Sell at confidence >= 60 waits for approval (then approving completes it); anything else notifies only."""
    b = runs[ticker]["status"]
    fr = (b.get("sigma") or {}).get("final_recommendation") or {}
    rec, conf = fr.get("recommendation"), fr.get("confidence")
    strong = rec in ("Buy", "Sell") and conf is not None and conf >= CFG.hitl.approval_confidence
    if strong:
        assert b["status"] == "waiting_approval" and b["step_up_challenge"]
        ch = b["step_up_challenge"]["challenge_id"]
        r = gateway.post(f"/api/v1/approve/{b['session_id']}", json={"challenge_id": ch})
        assert r.status_code == 200, r.text
        after = gateway.get(f"/api/v1/status/{b['session_id']}").json()
        assert after["approval_status"] == "approved"
        record("hitl", ticker, f"{rec} {conf}: approval required, approved via API")
    else:
        assert b["status"] == "completed" and b["notification"]
        record("hitl", ticker, f"{rec} {conf}: {b['notification']['type']} notification, no approval needed")
