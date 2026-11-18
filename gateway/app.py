"""
Gateway API - FastAPI application for the single-user local Kai app.

- No authentication (FIXES.md B2-B4 dropped). By default it listens on 127.0.0.1 and accepts
  browser requests only from the Vite dev origin; both are set by environment variables
  (gateway/config.py). Add auth before any public deployment.
- Debates run in a thread pool (B5), never on the event loop, so /status answers while one runs.
- Sessions, history and chat are stored in SQLite (B6). The coordinator object is never kept:
  live logs and structure come from the persisted debate log (gateway/views.py).
- Market data failures are errors, not made-up numbers (B9).

All state lives on the app instance (`create_app`), so tests can build an app against a temporary
database and "restart" the server by building a second one on the same file.
"""

import logging
import os
import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import yfinance as yf
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import sessionmaker

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from coordinator.llm_config import load_llm_config
from coordinator.personas import RiskPersona
from coordinator.server import DebateCoordinator
from gateway import models
from gateway.config import LOCAL_USER, GatewaySettings, load_settings
from gateway.database import Base, make_engine, make_session_factory
from gateway.hitl_manager import HITLManager
from gateway.schema import (
    AlphaPacket, AnalysisRequest, AnalysisResponse, AnalysisStatus, ApprovalRequest, ApprovalResponse,
    ChatRequest, ChatResponse, HistoryItem, HistoryResponse, HITLMode, IndexQuote, LogEvent,
    NewsArticle, NewsResponse, PricePoint, StatusResponse, TickerQuote,
)
from gateway.store import SessionStore
from gateway.views import build_events, read_log, status_response
from judge.ap2_handler import AP2Handler
from judge.sigma_agent import SigmaAgent
from mcp_server.tools.news_stream import get_news_stream

logger = logging.getLogger(__name__)

INDICES = {"S&P 500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI", "VIX": "^VIX"}
HISTORY_PERIODS = {"1d": "1m", "5d": "15m", "1mo": "60m", "ytd": "1d", "1y": "1d"}
MARKET_NEWS_SYMBOL = "SPY"      # broad-market news: coverage of the S&P 500 ETF
# Chat uses the first Groq model of the "chat" role in config/llm.yaml
CHAT_MODEL = next(m.model for m in load_llm_config().chain("chat") if m.provider == "groq")
CHAT_SYSTEM_PROMPT = ("You are Kai, an advanced AI investment assistant. Be concise, professional, and "
                      "data-driven. You have no access to live data in this chat; say so if asked for prices.")


# --------------------------------------------------------------------------- app context

@dataclass
class AppContext:
    settings: GatewaySettings
    session_factory: sessionmaker
    store: SessionStore
    hitl: HITLManager
    ap2: AP2Handler
    sigma: SigmaAgent
    executor: ThreadPoolExecutor
    coordinator_factory: Callable[[str, RiskPersona], DebateCoordinator]
    groq: Optional[Any] = None


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _now() -> str:
    return datetime.now().isoformat()


# --------------------------------------------------------------------------- the analysis workflow

def run_analysis(ctx: AppContext, session_id: str, ticker: str, persona: str) -> None:
    """
    Whole analysis for one session. Runs in a worker thread (never on the event loop).
    Progress is visible to /status through the debate log file the coordinator writes; this
    function only records the outcome. Any exception becomes a `failed` session with its message.
    """
    store = ctx.store
    try:
        coordinator = ctx.coordinator_factory(ticker, RiskPersona(persona))
        coordinator.logger.log_system_message(
            f"Investment mandate created for {ticker}. Analysis session {session_id} started.")
        coordinator.logger.flush()
        # Absolute, so the session still finds its log if the server restarts from another directory
        store.update(session_id, debate_log=os.path.abspath(coordinator.logger.log_path))

        mandate = ctx.ap2.create_intent_mandate(user_id=LOCAL_USER, ticker=ticker, persona=persona)
        store.update(session_id, mandate=mandate)

        debate = coordinator.run_debate(rounds=3)

        # Sigma gets the quant report directly as a structured input, separate from the transcript
        evaluation = ctx.sigma.evaluate_debate(debate["log_file"], persona=persona,
                                               quant_report=coordinator.quant_report)
        packet = ctx.ap2.generate_alpha_packet(mandate=mandate, sigma_evaluation=evaluation,
                                               debate_log_path=debate["log_file"])
        packet_path = ctx.ap2.save_alpha_packet(packet)

        final = evaluation["final_recommendation"]
        recommendation, confidence = final["recommendation"], final["confidence"]
        mode, reason = ctx.hitl.evaluate_hitl_requirement(recommendation, confidence)
        fields: Dict[str, Any] = dict(
            debate_log=os.path.abspath(debate["log_file"]), evaluation=evaluation, alpha_packet=packet,
            alpha_packet_path=packet_path, recommendation=recommendation, confidence=confidence,
            hitl_mode=mode.value, hitl_reason=reason,
        )
        if mode == HITLMode.PASSIVE:
            fields["notification"] = ctx.hitl.create_notification(
                recommendation, confidence, ticker, reason=final.get("reason")).model_dump(mode="json")
            fields["status"] = AnalysisStatus.COMPLETED.value
        else:
            challenge = ctx.hitl.create_step_up_challenge(
                recommendation, confidence, ticker, key_points=final.get("supporting_points") or None)
            fields["challenge"] = challenge.model_dump(mode="json")
            fields["status"] = AnalysisStatus.WAITING_APPROVAL.value
        store.update(session_id, **fields)
    except Exception as e:
        logger.error("Analysis %s failed: %s\n%s", session_id, e, traceback.format_exc())
        store.update(session_id, status=AnalysisStatus.FAILED.value, error=f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- routes

router = APIRouter()


@router.get("/")
def root():
    return {"service": "Kai - Economist Gateway API", "version": "2.0.0", "status": "operational"}


@router.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": _now()}


# ---- market data (yfinance). A failure is reported as an error, never replaced by made-up numbers.

def _provider_error(what: str, e: Exception) -> HTTPException:
    logger.error("%s failed: %s", what, e)
    return HTTPException(status_code=502, detail=f"Market data provider (yfinance) failed for {what}: {e}")


def _number(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and v == v else None


@router.get("/api/v1/market/indices", response_model=List[IndexQuote], tags=["Market Data"])
def get_market_indices():
    """Live data for major indices. Each index reports its own availability; if none can be
    fetched the request fails with 502."""
    results, errors = [], []
    for name, symbol in INDICES.items():
        try:
            fi = yf.Ticker(symbol).fast_info
            price, prev = _number(fi.last_price), _number(fi.previous_close)
            if price is None or not prev:
                raise ValueError("no price returned")
            change = (price - prev) / prev * 100
            results.append(IndexQuote(name=name, symbol=symbol, available=True, value=f"{price:,.2f}",
                                      change=f"{change:+.2f}%", positive=price >= prev))
        except Exception as e:
            errors.append(f"{name}: {e}")
            results.append(IndexQuote(name=name, symbol=symbol, available=False, error=str(e)))
    if not any(r.available for r in results):
        raise _provider_error("market indices", Exception("; ".join(errors)))
    return results


@router.get("/api/v1/ticker/{symbol}/quote", response_model=TickerQuote, tags=["Market Data"])
def get_ticker_quote(symbol: str):
    """Quote and key statistics for a ticker."""
    try:
        tk = yf.Ticker(symbol)
        fi = tk.fast_info
        price, prev = _number(fi.last_price), _number(fi.previous_close)
    except Exception as e:
        raise _provider_error(f"quote {symbol.upper()}", e)
    if price is None or not prev:
        raise HTTPException(status_code=404, detail=f"No quote available for {symbol.upper()}")
    try:
        info = tk.info or {}
    except Exception as e:             # statistics are optional; the price above is what matters
        logger.warning("info for %s unavailable: %s", symbol, e)
        info = {}
    change = (price - prev) / prev * 100
    dy, hi, lo = _number(info.get("dividendYield")), _number(info.get("fiftyTwoWeekHigh")), _number(info.get("fiftyTwoWeekLow"))
    return TickerQuote(
        symbol=symbol.upper(), name=info.get("shortName") or symbol.upper(), price=f"{price:,.2f}",
        change=f"{change:+.2f}%", is_positive=price >= prev,
        market_cap=_number(info.get("marketCap")), pe_ratio=_number(info.get("trailingPE")),
        dividend_yield=f"{dy:.2f}%" if dy else None,          # yfinance reports this already in percent
        high_52w=f"${hi:,.2f}" if hi else None, low_52w=f"${lo:,.2f}" if lo else None,
    )


@router.get("/api/v1/ticker/{symbol}/history", response_model=List[PricePoint], tags=["Market Data"])
def get_ticker_history(symbol: str, period: str = "1d"):
    """Closing prices for charts. Periods: 1d, 5d, 1mo, ytd, 1y."""
    if period not in HISTORY_PERIODS:
        raise HTTPException(status_code=422, detail=f"period must be one of {sorted(HISTORY_PERIODS)}")
    try:
        hist = yf.Ticker(symbol).history(period=period, interval=HISTORY_PERIODS[period])
        points = [PricePoint(name=(idx.strftime("%H:%M") if period == "1d" else
                                   idx.strftime("%m-%d %H:%M") if period == "5d" else idx.strftime("%Y-%m-%d")),
                             price=round(float(row["Close"]), 2))
                  for idx, row in hist.iterrows() if row["Close"] == row["Close"]]
    except Exception as e:
        raise _provider_error(f"history {symbol.upper()}", e)
    if not points:
        raise HTTPException(status_code=404, detail=f"No price history for {symbol.upper()} ({period})")
    return points


def _news_response(symbol: str, days: int, limit: int) -> NewsResponse:
    news = get_news_stream(symbol, days=days, max_articles=limit)
    if not news.get("success"):
        failed = "; ".join(f"{p}: {m}" for p, m in news.get("providers_failed", {}).items())
        raise HTTPException(status_code=502, detail=f"All news providers failed for {symbol}: {failed or news.get('error')}")
    return NewsResponse(
        articles=[NewsArticle(id=a["id"], title=a["title"], url=a.get("url"), source=a.get("source"),
                              published_at=a.get("published_at"), summary=a.get("summary"),
                              providers=a.get("providers", [])) for a in news["articles"]],
        providers_failed=news.get("providers_failed", {}), providers_skipped=news.get("providers_skipped", {}),
        provider_counts=news.get("provider_counts", {}), warnings=news.get("warnings", []))


@router.get("/api/v1/ticker/{symbol}/news", response_model=NewsResponse, tags=["Market Data"])
def get_ticker_news(symbol: str, days: int = 7, limit: int = 8):
    return _news_response(symbol.upper(), max(1, min(days, 30)), max(1, min(limit, 20)))


@router.get("/api/v1/market/news", response_model=NewsResponse, tags=["Market Data"])
def get_market_news(limit: int = 6):
    return _news_response(MARKET_NEWS_SYMBOL, 2, max(1, min(limit, 20)))


# ---- analysis sessions

@router.post("/api/v1/analyze", response_model=AnalysisResponse)
def analyze_ticker(request: AnalysisRequest, ctx: AppContext = Depends(get_ctx)):
    """Start an analysis. Returns immediately; the debate runs in the thread pool. Poll /status."""
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    row = ctx.store.create(session_id, request.ticker, request.persona.value)
    ctx.executor.submit(run_analysis, ctx, session_id, request.ticker, request.persona.value)
    return AnalysisResponse(session_id=session_id, status=row["status"], ticker=row["ticker"],
                            persona=row["persona"], created_at=row["created_at"], updated_at=row["updated_at"])


def _session_or_404(ctx: AppContext, session_id: str) -> Dict[str, Any]:
    row = ctx.store.get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


@router.get("/api/v1/status/{session_id}", response_model=StatusResponse)
def get_status(session_id: str, ctx: AppContext = Depends(get_ctx)):
    """Full state of a session, built from the database and the persisted debate log."""
    return status_response(_session_or_404(ctx, session_id), ctx.hitl)


@router.get("/api/v1/sessions/{session_id}/logs", response_model=List[LogEvent])
def get_session_logs(session_id: str, ctx: AppContext = Depends(get_ctx)):
    """The debate's log lines so far, read from the persisted debate log."""
    row = _session_or_404(ctx, session_id)
    log = read_log(row.get("debate_log"))
    return build_events(log) if log else []


def _decide(ctx: AppContext, session_id: str, challenge_id: Optional[str], decision: str) -> Dict[str, Any]:
    row = _session_or_404(ctx, session_id)
    if row["status"] != AnalysisStatus.WAITING_APPROVAL.value:
        raise HTTPException(status_code=400, detail="Session not waiting for approval")
    challenge, err = ctx.hitl.resolve_challenge(row.get("challenge"), challenge_id or "", decision)
    if err:
        raise HTTPException(status_code=400, detail=err)
    approved = decision == "approved"
    return ctx.store.update(
        session_id, challenge=challenge, approval_status=decision,
        status=(AnalysisStatus.COMPLETED if approved else AnalysisStatus.REJECTED).value)


@router.post("/api/v1/approve/{session_id}", response_model=ApprovalResponse)
def approve_recommendation(session_id: str, request: ApprovalRequest, ctx: AppContext = Depends(get_ctx)):
    """Approve the step-up challenge of a session waiting for approval."""
    row = _decide(ctx, session_id, request.challenge_id, "approved")
    return ApprovalResponse(session_id=session_id, status=AnalysisStatus.COMPLETED, approval_status="approved",
                            alpha_packet=AlphaPacket(**row["alpha_packet"]), ready_for_execution=True,
                            message="Recommendation approved. Ready for portfolio execution.")


@router.post("/api/v1/reject/{session_id}", response_model=ApprovalResponse)
def reject_recommendation(session_id: str, ctx: AppContext = Depends(get_ctx)):
    """Reject the step-up challenge of a session waiting for approval."""
    row = _session_or_404(ctx, session_id)
    challenge_id = (row.get("challenge") or {}).get("challenge_id")
    _decide(ctx, session_id, challenge_id, "rejected")
    return ApprovalResponse(session_id=session_id, status=AnalysisStatus.REJECTED, approval_status="rejected",
                            ready_for_execution=False, message="Recommendation rejected by user.")


@router.get("/api/v1/history", response_model=HistoryResponse)
def get_history(limit: int = 50, ctx: AppContext = Depends(get_ctx)):
    """Past analyses, newest first (persisted in SQLite)."""
    rows = ctx.store.history(max(1, min(limit, 200)))
    items = [HistoryItem(session_id=r["session_id"], ticker=r["ticker"], persona=r["persona"], status=r["status"],
                         recommendation=r["recommendation"], confidence=r["confidence"], created_at=r["created_at"],
                         alpha_packet_id=(r["alpha_packet"] or {}).get("packet_id"), error=r["error"])
             for r in rows]
    return HistoryResponse(analyses=items, total_count=ctx.store.count())


# ---- chat (persisted in SQLite)

@router.get("/api/v1/chat/history", response_model=List[ChatResponse])
def get_chat_history(limit: int = 50, ctx: AppContext = Depends(get_ctx)):
    """The most recent chat messages, oldest first."""
    with ctx.session_factory() as db:
        rows = (db.query(models.ChatMessage).filter(models.ChatMessage.user_id == LOCAL_USER)
                .order_by(models.ChatMessage.id.desc()).limit(max(1, min(limit, 200))).all())
        return [ChatResponse(response=m.message, timestamp=m.timestamp, role=m.role) for m in reversed(rows)]


@router.post("/api/v1/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest, ctx: AppContext = Depends(get_ctx)):
    """Chat with the Kai assistant. Fails with 503 when no chat model is configured; no canned
    reply is invented or stored."""
    if ctx.groq is None:
        raise HTTPException(status_code=503, detail="Chat is unavailable: GROQ_API_KEY is not set.")
    with ctx.session_factory() as db:
        db.add(models.ChatMessage(user_id=LOCAL_USER, role="user", message=request.message, timestamp=_now()))
        db.commit()
        recent = (db.query(models.ChatMessage).filter(models.ChatMessage.user_id == LOCAL_USER)
                  .order_by(models.ChatMessage.id.desc()).limit(5).all())
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}] + [
        {"role": m.role, "content": m.message} for m in reversed(recent)]
    try:
        completion = ctx.groq.chat.completions.create(messages=messages, model=CHAT_MODEL)
        answer = completion.choices[0].message.content
    except Exception as e:
        logger.error("Chat model call failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Chat model call failed: {e}")
    stamp = _now()
    with ctx.session_factory() as db:
        db.add(models.ChatMessage(user_id=LOCAL_USER, role="assistant", message=answer, timestamp=stamp))
        db.commit()
    return ChatResponse(response=answer, timestamp=stamp, role="assistant")


# --------------------------------------------------------------------------- app factory

def create_app(db_url: Optional[str] = None, *, coordinator_factory=None, sigma: Optional[SigmaAgent] = None,
               ap2: Optional[AP2Handler] = None, settings: Optional[GatewaySettings] = None,
               groq_client: Optional[Any] = None) -> FastAPI:
    """
    Build a gateway instance. Nothing is created on disk until the app starts (lifespan).

    Args:
        db_url: SQLAlchemy URL (default: KAI_DB_URL or sqlite:///./kai.db)
        coordinator_factory: (ticker, RiskPersona) -> DebateCoordinator; injectable for tests
        sigma, ap2: injectable judge and signer
        settings: override environment settings
        groq_client: chat model client; default is created from GROQ_API_KEY if set
    """
    load_dotenv()
    cfg = settings or load_settings()
    engine = make_engine(db_url or cfg.db_url)
    factory = make_session_factory(engine)
    if groq_client is None and os.getenv("GROQ_API_KEY"):
        from groq import Groq
        groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    ctx = AppContext(
        settings=cfg, session_factory=factory, store=SessionStore(factory), hitl=HITLManager(),
        ap2=ap2 or AP2Handler(), sigma=sigma or SigmaAgent(),
        executor=ThreadPoolExecutor(max_workers=cfg.max_concurrent_debates, thread_name_prefix="kai-debate"),
        coordinator_factory=coordinator_factory or (lambda ticker, persona: DebateCoordinator(ticker, persona)),
        groq=groq_client,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(bind=engine)
        interrupted = ctx.store.mark_interrupted()
        if interrupted:
            logger.warning("Marked %d analysis session(s) left running by a previous process as failed.", interrupted)
        yield
        ctx.executor.shutdown(wait=False, cancel_futures=True)
        engine.dispose()

    app = FastAPI(title="Kai - Economist Gateway API", description="Local API for the explainable investing copilot",
                  version="2.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=cfg.cors_origins, allow_credentials=False,
        allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
    )
    app.state.ctx = ctx
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = load_settings()
    print(f"Starting Kai - Economist Gateway API on {settings.host}:{settings.port} (no authentication)")
    uvicorn.run(app, host=settings.host, port=settings.port)
