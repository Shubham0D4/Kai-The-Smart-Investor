"""
Human-in-the-Loop (HITL) Manager
Decides whether a verdict needs explicit human approval or a notification.

Rule (thresholds in config/thresholds.yaml, `hitl`):
  Buy/Sell, confidence >= approval_confidence  -> ACTIVE: approval required
  Buy/Sell, confidence <  approval_confidence  -> never passed silently:
        low_confidence_action "warn"    -> PASSIVE, explicit low-confidence WARNING notification
        low_confidence_action "approve" -> ACTIVE: approval required
  Buy/Sell with no confidence                  -> ACTIVE (fail closed)
  Hold                                         -> PASSIVE info notification (no confidence)
  No Verdict                                   -> PASSIVE warning notification with the reason

The manager keeps no state. A step-up challenge is a plain dict stored in the session row
(gateway/store.py), so a pending approval survives a server restart; resolve_challenge()
validates a decision against that stored dict.
"""

import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Import schemas
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from coordinator.config import HitlConfig, load_thresholds
from gateway.schema import (
    RecommendationType, HITLMode, Notification, StepUpChallenge
)


def _fmt_conf(confidence: Optional[float]) -> str:
    return f"{confidence:.0f}%" if confidence is not None else "no confidence"


class HITLManager:
    """
    Manages Human-in-the-Loop workflow.
    Determines when to notify vs. when to require approval.
    """

    def __init__(self, cfg: Optional[HitlConfig] = None):
        self.cfg = cfg or load_thresholds().hitl
        self.approval_confidence = self.cfg.approval_confidence
        self.approval_timeout_minutes = self.cfg.approval_timeout_minutes

    def evaluate_hitl_requirement(self, recommendation: str,
                                  confidence: Optional[float]) -> Tuple[HITLMode, str]:
        """
        Determine if HITL approval is required. Returns (mode, reason).
        """
        if recommendation in ("Buy", "Sell"):
            if confidence is None:
                return (HITLMode.ACTIVE, f"{recommendation} without a confidence score requires approval")
            if confidence >= self.approval_confidence:
                return (HITLMode.ACTIVE, f"{recommendation} at {_fmt_conf(confidence)} confidence "
                                         f"(>= {self.approval_confidence:.0f}) requires approval")
            if self.cfg.low_confidence_action == "approve":
                return (HITLMode.ACTIVE, f"Low-confidence {recommendation} ({_fmt_conf(confidence)}) "
                                         f"requires approval")
            return (HITLMode.PASSIVE, f"Low-confidence {recommendation} ({_fmt_conf(confidence)} "
                                      f"< {self.approval_confidence:.0f}): flagged as a warning, "
                                      f"not a strong signal")
        if recommendation == "Hold":
            return (HITLMode.PASSIVE, "Hold recommendation - notification only")
        return (HITLMode.PASSIVE, "No verdict - notification only")

    def create_notification(self, recommendation: str, confidence: Optional[float], ticker: str,
                            reason: Optional[str] = None) -> Notification:
        """
        Create a passive mode notification. A Buy/Sell only reaches this point when its
        confidence is below the approval threshold, so it is always a warning.
        """
        if recommendation == "No Verdict":
            notif_type = "warning"
            message = f"No verdict for {ticker}: {reason or 'the debate did not reach a decision'}"
        elif recommendation == "Hold":
            notif_type = "info"
            message = f"Analysis complete for {ticker}. Recommendation: Hold"
        else:
            notif_type = "warning"
            message = (f"Low-confidence {recommendation} for {ticker} ({_fmt_conf(confidence)}). "
                       f"Not a strong signal: review the verdict gates and risks before acting.")
        return Notification(
            type=notif_type,
            message=message,
            recommendation=RecommendationType(recommendation),
            confidence=confidence,
        )

    def create_step_up_challenge(self, recommendation: str, confidence: Optional[float], ticker: str,
                                 key_points: Optional[List[str]] = None) -> StepUpChallenge:
        """Create an active mode step-up challenge."""
        challenge_id = f"chal_{uuid.uuid4().hex[:16]}"
        strong = confidence is not None and confidence >= self.approval_confidence
        label = "Strong" if strong else "Low-confidence"
        message = (f"{label} {recommendation} signal for {ticker} ({_fmt_conf(confidence)}). "
                   f"Review the reasoning, then approve to proceed or reject to discard.")
        expires_at = (datetime.now() + timedelta(minutes=self.approval_timeout_minutes)).isoformat()
        if not key_points:
            key_points = [f"{label} recommendation ({_fmt_conf(confidence)})",
                          "Based on multi-agent debate analysis",
                          "Requires your approval to proceed"]

        challenge = StepUpChallenge(
            challenge_id=challenge_id,
            message=message,
            recommendation=RecommendationType(recommendation),
            confidence=confidence,
            requires_approval=True,
            expires_at=expires_at,
            key_points=key_points,
        )
        return challenge

    @staticmethod
    def challenge_status(challenge: Dict[str, Any], now: Optional[datetime] = None) -> str:
        """"pending", "approved", "rejected" or "expired" for a stored challenge dict."""
        decided = challenge.get("decision")
        if decided:
            return decided
        expires_at = datetime.fromisoformat(challenge["expires_at"])
        return "expired" if (now or datetime.now()) > expires_at else "pending"

    def resolve_challenge(self, challenge: Optional[Dict[str, Any]], challenge_id: str, decision: str,
                          now: Optional[datetime] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Validate an approve/reject decision against a stored challenge dict.
        Returns (updated_challenge, None) on success or (None, reason) when it cannot be applied.
        """
        if decision not in ("approved", "rejected"):
            return None, f"unknown decision {decision!r}"
        if not challenge:
            return None, "no step-up challenge is pending for this session"
        if challenge["challenge_id"] != challenge_id:
            return None, "challenge id does not match this session's challenge"
        status = self.challenge_status(challenge, now)
        if status != "pending":
            return None, f"challenge is {status}, not pending"
        stamp = (now or datetime.now()).isoformat()
        return {**challenge, "decision": decision, "decided_at": stamp}, None

    def record_decision(self, session_id: str, challenge_id: str, decision: str, user_id: str) -> Dict:
        """Build the decision record (the session row stores it)."""
        return {
            "session_id": session_id,
            "challenge_id": challenge_id,
            "decision": decision,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
        }
