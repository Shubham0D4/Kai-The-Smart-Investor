"""
HITL rule (PHASE4_PLAN.md §6, reviewed in phase 4): no Buy/Sell is ever passed silently.
"""

import pytest

from coordinator.config import HitlConfig, load_thresholds
from gateway.hitl_manager import HITLManager
from gateway.schema import HITLMode


@pytest.fixture
def hitl():
    return HITLManager()


@pytest.mark.parametrize("rec", ["Buy", "Sell"])
@pytest.mark.parametrize("conf", [60.0, 75.0, 99.0])
def test_confident_buy_sell_needs_approval(hitl, rec, conf):
    mode, reason = hitl.evaluate_hitl_requirement(rec, conf)
    assert mode == HITLMode.ACTIVE and "requires approval" in reason


@pytest.mark.parametrize("rec", ["Buy", "Sell"])
@pytest.mark.parametrize("conf", [0.0, 30.0, 59.9])
def test_low_confidence_buy_sell_is_flagged_not_silent(hitl, rec, conf):
    mode, reason = hitl.evaluate_hitl_requirement(rec, conf)
    assert mode == HITLMode.PASSIVE and reason.startswith(f"Low-confidence {rec}")
    note = hitl.create_notification(rec, conf, "AAPL")
    assert note.type == "warning"
    assert note.message.startswith(f"Low-confidence {rec}") and "Not a strong signal" in note.message


def test_buy_without_confidence_fails_closed(hitl):
    assert hitl.evaluate_hitl_requirement("Buy", None)[0] == HITLMode.ACTIVE


def test_low_confidence_can_require_approval_by_config():
    cfg = HitlConfig(**{**load_thresholds().hitl.model_dump(), "low_confidence_action": "approve"})
    hitl = HITLManager(cfg)
    mode, reason = hitl.evaluate_hitl_requirement("Buy", 45.0)
    assert mode == HITLMode.ACTIVE and "Low-confidence Buy" in reason
    challenge = hitl.create_step_up_challenge("Buy", 45.0, "AAPL")
    assert "Low-confidence Buy" in challenge.message


def test_hold_and_no_verdict_are_notifications(hitl):
    assert hitl.evaluate_hitl_requirement("Hold", None)[0] == HITLMode.PASSIVE
    hold = hitl.create_notification("Hold", None, "AAPL")
    assert hold.type == "info" and hold.confidence is None
    nv = hitl.create_notification("No Verdict", None, "AAPL", reason="TOKEN_LIMIT: budget exhausted")
    assert nv.type == "warning" and "TOKEN_LIMIT" in nv.message


@pytest.mark.parametrize("rec, conf", [("Buy", 10.0), ("Sell", 59.0), ("Hold", None), ("No Verdict", None)])
def test_no_notification_is_a_plain_success(hitl, rec, conf):
    mode, _ = hitl.evaluate_hitl_requirement(rec, conf)
    assert mode == HITLMode.PASSIVE
    assert hitl.create_notification(rec, conf, "AAPL").type != "success"
