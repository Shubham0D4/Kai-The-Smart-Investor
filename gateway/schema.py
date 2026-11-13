"""
Response Schemas for Gateway API
Pydantic models for standardized JSON responses.

Where a model already exists in the backend it is reused instead of redefined, so the API cannot
drift from what the agents and the judge actually produce:
  agents.schema   AgentOutput, CritiqueOutput, FinalOutput, EvidenceRecord   (structured agent output)
  judge.verifier  AgentVerification                                          (claim-by-claim checks)
  judge.sigma_agent  QualityEvaluation, FinalRecommendation                  (Sigma's JSON verdict)
  judge.scoring   AgentIntegrity, Verdict
  quant.models    QuantReport                                                (deterministic statistics)
There is no authentication and no user id anywhere in this API (single-user local app).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from agents.schema import AgentOutput, CritiqueOutput, EvidenceRecord, FinalOutput
from judge.scoring import AgentIntegrity, Verdict
from judge.sigma_agent import FinalRecommendation, QualityEvaluation
from judge.verifier import AgentVerification
from quant.models import QuantReport


class PersonaType(str, Enum):
    """Risk persona types (select decision thresholds only)."""
    ZEN = "zen"
    BALANCED = "balanced"
    ALPHA = "alpha"


class RecommendationType(str, Enum):
    """Investment recommendation types."""
    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"
    NO_VERDICT = "No Verdict"


class AnalysisStatus(str, Enum):
    """Analysis session status."""
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class HITLMode(str, Enum):
    """HITL mode types."""
    PASSIVE = "passive"
    ACTIVE = "active"


# --------------------------------------------------------------------------- requests

class AnalysisRequest(BaseModel):
    """Request to start new analysis. Valuation inputs are fetched, never supplied."""
    ticker: str = Field(..., description="Stock ticker symbol", min_length=1, max_length=10,
                        pattern=r"^[A-Za-z0-9.\-]{1,10}$")
    persona: PersonaType = Field(default=PersonaType.ZEN, description="Risk persona")

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class ApprovalRequest(BaseModel):
    """Request to approve step-up challenge."""
    challenge_id: str = Field(..., description="Challenge ID to approve")
    user_confirmation: str = Field("approved", description="User confirmation (approved/rejected)")


class ChatRequest(BaseModel):
    """Chat request."""
    message: str = Field(..., min_length=1, max_length=4000, description="User message")


class ChatResponse(BaseModel):
    """One chat message."""
    response: str = Field(..., description="Message text")
    timestamp: str = Field(..., description="Timestamp")
    role: Literal["user", "assistant"] = Field("assistant", description="Who wrote it")


# --------------------------------------------------------------------------- verdict and packet

class QualityScores(BaseModel):
    """Debate quality scores (0-100)."""
    faithfulness: float = Field(..., description="Share of checkable claims verified against the fetched data")
    integrity: float = Field(..., description="Debate integrity: critiques answered, failed claims fixed")
    overall: float = Field(..., description="Mean of the two")


class DebateSummary(BaseModel):
    """Summary of debate execution."""
    rounds: int = Field(..., description="Number of rounds executed")
    agents: List[str] = Field(..., description="Agents that participated")
    quality: float = Field(..., description="Overall debate quality")


class AlphaPacket(BaseModel):
    """Signed Alpha Packet. quant_report stays an opaque dict: it is part of the signed payload."""
    packet_id: str
    mandate_id: str
    ticker: str
    persona: str
    recommendation: RecommendationType
    confidence: Optional[float] = Field(None, description="Only Buy and Sell carry a confidence")
    quality_scores: QualityScores
    debate_summary: DebateSummary
    key_risks: List[str]
    verdict_detail: Optional[Dict[str, Any]] = Field(None, description="Score, gates, supporting points, warnings")
    quant_report: Optional[Dict[str, Any]] = Field(None, description="Deterministic quant layer report (signed)")
    debate_log: str
    timestamp: str
    signature: str
    public_key: str
    verification_url: str
    version: str = "AP2-v1.0"


class Notification(BaseModel):
    """Passive mode notification."""
    type: str = Field(..., description="Notification type (info, warning, success)")
    message: str = Field(..., description="Notification message")
    recommendation: Optional[RecommendationType] = Field(None, description="Recommendation if available")
    confidence: Optional[float] = Field(None, description="Confidence level")


class StepUpChallenge(BaseModel):
    """Active mode step-up challenge. `status` is computed when the response is built."""
    challenge_id: str = Field(..., description="Unique challenge ID")
    message: str = Field(..., description="Challenge message for user")
    recommendation: RecommendationType = Field(..., description="Recommended action")
    confidence: Optional[float] = Field(None, description="Confidence level (0-100)")
    requires_approval: bool = Field(True, description="Whether approval is required")
    expires_at: str = Field(..., description="Challenge expiration timestamp")
    key_points: List[str] = Field(default_factory=list, description="Key supporting points")
    status: str = Field("pending", description="pending, approved, rejected or expired")
    decision: Optional[str] = None
    decided_at: Optional[str] = None


class SigmaView(BaseModel):
    """Sigma's validated verdict, as stored (without the verification and quant copies that are
    served separately in `debate`)."""
    debate_id: str
    ticker: str
    persona: str
    evaluation: QualityEvaluation
    final_recommendation: FinalRecommendation
    integrity: Dict[str, AgentIntegrity] = Field(default_factory=dict)
    verdicts_by_persona: Dict[str, Verdict] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- the debate, structured

class LogEvent(BaseModel):
    """One line of the debate log (system message, agent output or critique)."""
    timestamp: str
    agent: str
    message: str
    round: Optional[int] = None


AgentState = Literal["waiting", "analyzing", "done", "failed", "dropped"]


class AgentView(BaseModel):
    """Everything one agent said and what was checked, across the three rounds."""
    agent: str
    state: AgentState
    error: Optional[str] = Field(None, description="Why the agent failed (data source or invalid output)")
    dropped_reason: Optional[str] = Field(None, description="Why the circuit breaker dropped it")
    round1: Optional[AgentOutput] = None
    evidence: List[EvidenceRecord] = Field(default_factory=list, description="The records its claims may cite")
    verification: Optional[AgentVerification] = Field(None, description="Round-1 claims checked against the data")
    final_verification: Optional[AgentVerification] = Field(
        None, description="The same check on the round-3 claims (what Sigma scores)")
    round2: Optional[CritiqueOutput] = None
    round3: Optional[FinalOutput] = None


class DebateOutcome(BaseModel):
    status: Literal["COMPLETED", "NO_VERDICT"]
    reason: Optional[str] = Field(None, description="e.g. CIRCUIT_BREAKER_FAITHFULNESS, INSUFFICIENT_EVIDENCE, TOKEN_LIMIT")
    detail: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class LlmCall(BaseModel):
    agent: str
    round: int
    ok: bool
    total_tokens: int


class DebateView(BaseModel):
    """The persisted debate transcript, shaped for the UI. Built from the debate log file, so it
    is the same live or after the fact."""
    debate_id: str
    ticker: str
    persona: str
    rounds_started: int
    finished: bool
    outcome: Optional[DebateOutcome] = None
    total_tokens: int = 0
    token_limit: Optional[int] = None
    agents: Dict[str, AgentView]
    llm_calls: List[LlmCall] = Field(default_factory=list)
    events: List[LogEvent] = Field(default_factory=list)
    quant_report: Optional[QuantReport] = None


# --------------------------------------------------------------------------- session responses

class AnalysisResponse(BaseModel):
    """Response from the analysis request: the session to poll."""
    session_id: str = Field(..., description="Unique session ID")
    status: AnalysisStatus = Field(..., description="Current analysis status")
    ticker: str = Field(..., description="Stock ticker analyzed")
    persona: PersonaType = Field(..., description="Risk persona used")
    created_at: str = Field(..., description="Session creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")


class StatusResponse(BaseModel):
    """Full state of a session. Everything comes from the database and the persisted debate log."""
    session_id: str
    status: AnalysisStatus
    ticker: str
    persona: PersonaType
    created_at: str
    updated_at: str
    progress: Optional[str] = Field(None, description="Latest step of a running analysis")
    error: Optional[str] = Field(None, description="Why the session failed")
    hitl_mode: Optional[HITLMode] = None
    hitl_reason: Optional[str] = None
    approval_status: Optional[str] = None
    debate: Optional[DebateView] = None
    sigma: Optional[SigmaView] = None
    alpha_packet: Optional[AlphaPacket] = None
    notification: Optional[Notification] = None
    step_up_challenge: Optional[StepUpChallenge] = None


class ApprovalResponse(BaseModel):
    """Response from approval/rejection."""
    session_id: str
    status: AnalysisStatus
    approval_status: str = Field(..., description="approved or rejected")
    alpha_packet: Optional[AlphaPacket] = None
    ready_for_execution: bool = Field(False, description="Whether ready for portfolio execution")
    message: str = Field(..., description="Response message")


class HistoryItem(BaseModel):
    """Historical analysis item."""
    session_id: str
    ticker: str
    persona: PersonaType
    status: AnalysisStatus
    recommendation: Optional[RecommendationType] = None
    confidence: Optional[float] = None
    created_at: str
    alpha_packet_id: Optional[str] = None
    error: Optional[str] = None


class HistoryResponse(BaseModel):
    """Response from history request."""
    analyses: List[HistoryItem]
    total_count: int


# --------------------------------------------------------------------------- market data

class IndexQuote(BaseModel):
    name: str
    symbol: str
    available: bool
    value: Optional[str] = None
    change: Optional[str] = None
    positive: Optional[bool] = None
    error: Optional[str] = Field(None, description="Why this index could not be fetched")


class TickerQuote(BaseModel):
    symbol: str
    name: str
    price: str
    change: str
    is_positive: bool
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    dividend_yield: Optional[str] = None
    high_52w: Optional[str] = None
    low_52w: Optional[str] = None


class PricePoint(BaseModel):
    name: str
    price: float


class NewsArticle(BaseModel):
    id: str
    title: str
    url: Optional[str] = None
    source: Optional[str] = None
    published_at: Optional[str] = None
    summary: Optional[str] = None
    providers: List[str] = Field(default_factory=list)


class NewsResponse(BaseModel):
    articles: List[NewsArticle]
    providers_failed: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Error response."""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Detailed error information")
    session_id: Optional[str] = Field(None, description="Session ID if applicable")


if __name__ == "__main__":
    print(AnalysisRequest(ticker="aapl", persona=PersonaType.ZEN).model_dump())
    print(datetime.now().isoformat())
