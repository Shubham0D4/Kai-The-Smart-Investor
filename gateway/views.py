"""
Turn persisted state into API responses.

The debate log file is the persisted transcript (coordinator/debate_logger.py writes it
atomically after every agent step), so the same code builds the view while a debate is running
and long after it finished or the server restarted. Nothing here touches in-memory state.
"""

import json
import os
from typing import Any, Dict, List, Optional

from coordinator.config import load_thresholds
from coordinator.transcript import AGENT_ORDER, Transcript
from gateway.hitl_manager import HITLManager
from gateway.schema import (
    AgentView, AlphaPacket, DebateOutcome, DebateView, LlmCall, LogEvent, Notification,
    SigmaView, StatusResponse, StepUpChallenge,
)
from judge.verifier import verify_output
from quant.models import QuantReport

LATEST_STEP_MAX_CHARS = 160


def read_log(path: Optional[str]) -> Optional[Dict[str, Any]]:
    """The persisted debate log, or None if there is none (yet) or it cannot be read."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_events(log: Dict[str, Any]) -> List[LogEvent]:
    """Flat, time-ordered list of everything the debate log recorded."""
    events: List[Dict[str, Any]] = [
        {**e, "round": None} for e in log.get("pre_round_logs", [])
    ]
    for i, rnd in enumerate(log.get("rounds", []), start=1):
        number = rnd.get("round_number", i)
        events += [{**e, "round": number} for e in rnd.get("system_logs", [])]
        for name, entry in rnd.get("agents", {}).items():
            if "error" in entry:
                events.append({"timestamp": entry["timestamp"], "agent": "system", "round": number,
                               "message": f"ERROR ({name.capitalize()}): {entry['error']}"})
            else:
                events.append({"timestamp": entry["timestamp"], "agent": name, "round": number,
                               "message": entry.get("analysis", "")})
    events.sort(key=lambda e: e["timestamp"])
    return [LogEvent(**e) for e in events]


def _agent_errors(log: Dict[str, Any]) -> Dict[str, str]:
    """Latest error message per agent, from the log's per-round agent entries."""
    errors: Dict[str, str] = {}
    for rnd in log.get("rounds", []):
        for name, entry in rnd.get("agents", {}).items():
            if "error" in entry:
                errors[name] = entry["error"]
    return errors


def build_debate_view(log: Dict[str, Any]) -> DebateView:
    t = Transcript.from_dict(log.get("transcript") or {}, log.get("ticker", ""))
    outcome = DebateOutcome(**log["outcome"]) if log.get("outcome") else None
    finished = outcome is not None or "end_time" in log
    errors = _agent_errors(log)
    meta = log.get("metadata", {})

    rounds_started = t.rounds_started
    current = {1: t.round1, 2: t.round2, 3: t.round3}.get(rounds_started, {})

    quant = QuantReport.model_validate(log["quant_report"]) if log.get("quant_report") else None
    verification_cfg = load_thresholds().verification

    agents: Dict[str, AgentView] = {}
    analyzing_assigned = False
    for name in AGENT_ORDER:
        error = errors.get(name)
        dropped = t.dropped.get(name)
        if dropped:
            state = "dropped"
        elif error:
            state = "failed"
        elif name in current and name in t.round1:
            state = "done"
        elif finished:
            # The debate ended without this agent producing the current round's output.
            state = "done" if name in t.round1 else "failed"
            if state == "failed":
                error = f"did not run: {outcome.detail}" if outcome and outcome.detail else "did not run"
        elif not analyzing_assigned and rounds_started >= 1 and not (name in t.dropped):
            state, analyzing_assigned = "analyzing", True          # agents run one after another
        else:
            state = "waiting"
        agents[name] = AgentView(
            agent=name, state=state, error=error, dropped_reason=dropped,
            round1=t.round1.get(name), evidence=t.evidence.get(name, []),
            verification=t.verification.get(name),
            final_verification=(verify_output(t.round3[name], t.evidence.get(name, []), verification_cfg, quant)
                                if name in t.round3 else None),
            round2=t.round2.get(name), round3=t.round3.get(name),
        )

    return DebateView(
        debate_id=log.get("debate_id", ""), ticker=log.get("ticker", t.ticker), persona=log.get("persona", ""),
        rounds_started=rounds_started, finished=finished, outcome=outcome,
        total_tokens=meta.get("total_tokens", 0), token_limit=meta.get("token_limit"),
        agents=agents, llm_calls=[LlmCall(**c) for c in t.calls], token_budget=meta.get("token_budget"),
        market_snapshot=log.get("market_snapshot"),
        events=build_events(log), quant_report=quant,
    )


def latest_step(view: Optional[DebateView]) -> Optional[str]:
    """Most recent system message of a running debate, for a one-line progress indicator."""
    if view is None:
        return None
    system = [e for e in view.events if e.agent == "system"]
    if not system:
        return None
    msg = system[-1].message
    return msg if len(msg) <= LATEST_STEP_MAX_CHARS else msg[:LATEST_STEP_MAX_CHARS] + "..."


def sigma_view(evaluation: Optional[Dict[str, Any]]) -> Optional[SigmaView]:
    return SigmaView.model_validate(evaluation) if evaluation else None


def status_response(row: Dict[str, Any], hitl: HITLManager) -> StatusResponse:
    log = read_log(row.get("debate_log"))
    debate = build_debate_view(log) if log else None
    challenge = None
    if row.get("challenge"):
        ch = row["challenge"]
        challenge = StepUpChallenge(**{**ch, "status": hitl.challenge_status(ch)})
    return StatusResponse(
        session_id=row["session_id"], status=row["status"], ticker=row["ticker"], persona=row["persona"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        progress=latest_step(debate) if row["status"] == "running" else None,
        error=row.get("error"), hitl_mode=row.get("hitl_mode"), hitl_reason=row.get("hitl_reason"),
        approval_status=row.get("approval_status"), debate=debate, sigma=sigma_view(row.get("evaluation")),
        alpha_packet=AlphaPacket(**row["alpha_packet"]) if row.get("alpha_packet") else None,
        notification=Notification(**row["notification"]) if row.get("notification") else None,
        step_up_challenge=challenge,
    )
