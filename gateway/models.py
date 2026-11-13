"""
Database models: analysis sessions (which also provide the history) and chat messages.
"""

from sqlalchemy import JSON, Column, Float, Integer, String, Text

from gateway.database import Base


class AnalysisSession(Base):
    """One analysis run. Everything needed to show it again after a server restart is stored here;
    the live debate transcript is the JSON log file at `debate_log`."""
    __tablename__ = "analysis_sessions"

    session_id = Column(String, primary_key=True)
    ticker = Column(String, index=True, nullable=False)
    persona = Column(String, nullable=False)
    status = Column(String, index=True, nullable=False)          # running | waiting_approval | completed | rejected | failed
    created_at = Column(String, index=True, nullable=False)      # ISO timestamps
    updated_at = Column(String, nullable=False)
    debate_log = Column(String)                                  # path of the persisted debate log
    hitl_mode = Column(String)
    hitl_reason = Column(Text)
    challenge = Column(JSON)                                     # step-up challenge, incl. its own status
    notification = Column(JSON)
    mandate = Column(JSON)
    evaluation = Column(JSON)                                    # Sigma's validated verdict
    alpha_packet = Column(JSON)
    alpha_packet_path = Column(String)
    recommendation = Column(String)                              # denormalised for the history list
    confidence = Column(Float)
    approval_status = Column(String)                             # approved | rejected
    error = Column(Text)


class ChatMessage(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)                         # always gateway.config.LOCAL_USER
    role = Column(String)
    message = Column(String)
    timestamp = Column(String)                                   # ISO string
