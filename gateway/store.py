"""
Persistent session store (FIXES.md B6).

Sessions, their step-up challenge, Sigma's verdict and the alpha packet live in SQLite, so a
server restart loses nothing. Every call opens its own short-lived SQLAlchemy session, so it is
safe to call from the request thread and from debate worker threads at the same time.
Rows come back as plain dicts; no ORM objects leave this module.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import sessionmaker

from gateway.models import AnalysisSession

RUNNING = "running"
INTERRUPTED_MESSAGE = "The server restarted while this analysis was running; start it again."

_COLUMNS = [c.name for c in AnalysisSession.__table__.columns]


def _now() -> str:
    return datetime.now().isoformat()


def _to_dict(row: AnalysisSession) -> Dict[str, Any]:
    return {name: getattr(row, name) for name in _COLUMNS}


class SessionStore:
    def __init__(self, session_factory: sessionmaker):
        self._factory = session_factory

    def create(self, session_id: str, ticker: str, persona: str) -> Dict[str, Any]:
        now = _now()
        row = AnalysisSession(session_id=session_id, ticker=ticker, persona=persona, status=RUNNING,
                              created_at=now, updated_at=now)
        with self._factory() as db:
            db.add(row)
            db.commit()
            return _to_dict(row)

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._factory() as db:
            row = db.get(AnalysisSession, session_id)
            return _to_dict(row) if row else None

    def update(self, session_id: str, **fields: Any) -> Dict[str, Any]:
        unknown = set(fields) - set(_COLUMNS)
        if unknown:
            raise KeyError(f"unknown session fields: {sorted(unknown)}")
        with self._factory() as db:
            row = db.get(AnalysisSession, session_id)
            if row is None:
                raise KeyError(session_id)
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = _now()
            db.commit()
            return _to_dict(row)

    def history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Newest first. Includes failed runs so they can be seen, not just completed ones."""
        with self._factory() as db:
            rows = (db.query(AnalysisSession).order_by(AnalysisSession.created_at.desc()).limit(limit).all())
            return [_to_dict(r) for r in rows]

    def count(self) -> int:
        with self._factory() as db:
            return db.query(AnalysisSession).count()

    def mark_interrupted(self) -> int:
        """Sessions still `running` belong to a process that no longer exists (called at startup)."""
        with self._factory() as db:
            rows = db.query(AnalysisSession).filter(AnalysisSession.status == RUNNING).all()
            for row in rows:
                row.status = "failed"
                row.error = INTERRUPTED_MESSAGE
                row.updated_at = _now()
            db.commit()
            return len(rows)
