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
"""

import os
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

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
        self.pending_approvals: Dict[str, Dict] = {}

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
        icon = ("🚀" if recommendation == "Buy" else "⚠️") if strong else "❓"
        message = (f"{icon} {label} {recommendation} signal for {ticker} ({_fmt_conf(confidence)}). "
                   f"Tap to approve and proceed.")
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
        self.pending_approvals[challenge_id] = {
            "challenge": challenge,
            "created_at": datetime.now(),
            "status": "pending",
        }
        return challenge

    def check_approval_status(self, challenge_id: str) -> Optional[str]:
        """Status: "pending", "approved", "rejected", "expired", or None."""
        if challenge_id not in self.pending_approvals:
            return None
        approval_data = self.pending_approvals[challenge_id]
        if approval_data["status"] == "pending":
            expires_at = datetime.fromisoformat(approval_data["challenge"].expires_at)
            if datetime.now() > expires_at:
                approval_data["status"] = "expired"
        return approval_data["status"]

    def approve_challenge(self, challenge_id: str) -> bool:
        if self.check_approval_status(challenge_id) != "pending":
            return False
        self.pending_approvals[challenge_id]["status"] = "approved"
        self.pending_approvals[challenge_id]["approved_at"] = datetime.now()
        return True

    def reject_challenge(self, challenge_id: str) -> bool:
        if self.check_approval_status(challenge_id) != "pending":
            return False
        self.pending_approvals[challenge_id]["status"] = "rejected"
        self.pending_approvals[challenge_id]["rejected_at"] = datetime.now()
        return True

    def record_decision(self, session_id: str, challenge_id: str, decision: str, user_id: str) -> Dict:
        """Record user's approval/rejection decision."""
        record = {
            "session_id": session_id,
            "challenge_id": challenge_id,
            "decision": decision,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
        }
        print(f"[HITL] Decision recorded: {decision} for challenge {challenge_id}")
        return record
