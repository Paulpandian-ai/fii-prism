"""Chat API for the Wealth Advisor.

- GET    /chat                     → list sessions (newest first)
- POST   /chat                     → create a new session, return session_id
- GET    /chat/{id}                → list messages on a session
- POST   /chat/{id}/message        → SSE: post a user message and stream the response
- DELETE /chat/{id}                → delete a session (and its messages via cascade)

The streaming endpoint persists both the user message and the assistant's final
message + tool_calls JSONB once the turn completes. Per-message cost rolls up to
the session's running totals.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fii_agents import (
    MODEL_SONNET,
    AdvisorToolContext,
    Model,
    run_analysis,
    stream_advisor_turn,
)
from fii_db import ChatMessage, ChatSession
from fii_db.models import SINGLE_USER_ID
from fii_db.session import session_scope
from pydantic import BaseModel, Field
from sqlalchemy import desc, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents_runtime import AgentsRuntime, get_runtime

log = structlog.get_logger(__name__)

# ruff: noqa: B008  # FastAPI Depends() in defaults is the dependency-injection idiom.
router = APIRouter(prefix="/chat", tags=["chat"])


# --- Request / response models ------------------------------------------------------------


class CreateSessionResponse(BaseModel):
    session_id: str


class SessionSummary(BaseModel):
    session_id: str
    title: str
    message_count: int
    total_cost_usd: float
    updated_at: datetime
    created_at: datetime


class MessageOut(BaseModel):
    message_id: str
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    referenced_analysis_ids: list[str] = Field(default_factory=list)
    cost_usd: float
    tokens_in: int
    tokens_out: int
    model_used: str | None = None
    created_at: datetime


class PostMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


# --- Routes -------------------------------------------------------------------------------


@router.get("", response_model=list[SessionSummary])
async def list_sessions(runtime: AgentsRuntime = Depends(get_runtime)) -> list[SessionSummary]:
    with session_scope(runtime.session_factory) as s:
        rows = (
            s.execute(
                select(ChatSession)
                .where(ChatSession.user_id == SINGLE_USER_ID)
                .order_by(desc(ChatSession.updated_at))
                .limit(50)
            )
            .scalars()
            .all()
        )
    return [_row_to_session_summary(r) for r in rows]


@router.post("", response_model=CreateSessionResponse, status_code=201)
async def create_session(
    runtime: AgentsRuntime = Depends(get_runtime),
) -> CreateSessionResponse:
    session_id = str(uuid.uuid4())
    with session_scope(runtime.session_factory) as s:
        s.execute(
            pg_insert(ChatSession)
            .values(session_id=session_id, user_id=SINGLE_USER_ID, title="New chat")
            .on_conflict_do_nothing(index_elements=[ChatSession.session_id])
        )
    return CreateSessionResponse(session_id=session_id)


@router.get("/{session_id}", response_model=list[MessageOut])
async def list_messages(
    session_id: str, runtime: AgentsRuntime = Depends(get_runtime)
) -> list[MessageOut]:
    with session_scope(runtime.session_factory) as s:
        sess = s.execute(
            select(ChatSession).where(ChatSession.session_id == session_id)
        ).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        rows = (
            s.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
            )
            .scalars()
            .all()
        )
    return [_row_to_message(r) for r in rows]


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, runtime: AgentsRuntime = Depends(get_runtime)) -> None:
    with session_scope(runtime.session_factory) as s:
        sess = s.execute(
            select(ChatSession).where(ChatSession.session_id == session_id)
        ).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        s.delete(sess)
    return None


@router.post("/{session_id}/message")
async def post_message(
    session_id: str,
    req: PostMessageRequest,
    runtime: AgentsRuntime = Depends(get_runtime),
) -> StreamingResponse:
    """SSE stream. Persists the user message immediately, then streams the assistant
    turn (tool_use_started / tool_result / message_complete frames). On
    message_complete we persist the assistant row + roll up session totals."""
    factory = runtime.session_factory
    with session_scope(factory) as s:
        sess = s.execute(
            select(ChatSession).where(ChatSession.session_id == session_id)
        ).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")

    # Load conversation history so the model has context.
    history = _build_anthropic_history(factory, session_id)
    user_message = req.content.strip()

    # Persist the user message first so it shows up in the GET endpoint immediately.
    user_msg_id = _persist_message(
        factory,
        session_id=session_id,
        role="user",
        content=user_message,
        tool_calls=[],
        referenced_analysis_ids=[],
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        model_used=None,
    )
    _bump_session(factory, session_id, delta_messages=1, title_seed=user_message)

    # Build the tool context. run_quick_analysis closes over the runtime so it can
    # invoke the LangGraph orchestrator.
    async def _run_quick(symbol: str, event_type: str | None) -> dict[str, Any]:
        analysis_id = str(uuid.uuid4())
        try:
            await run_analysis(
                symbol=symbol.upper(),
                analysis_id=analysis_id,
                user_id=SINGLE_USER_ID,
                database_url=runtime.database_url,
                factory=factory,
                embedder=runtime.embedder,
                raw_bucket=runtime.raw_bucket,
                budget=runtime.budget,
                extra_context={
                    "quick_refresh_event_type": event_type or "news_shock",
                    "model_tier": "haiku",
                },
            )
        except Exception as exc:
            return {"status": "failed", "symbol": symbol.upper(), "error": str(exc)}
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "analysis_id": analysis_id,
            "note": "Quick refresh complete; call get_latest_analysis for the full payload.",
        }

    tool_ctx = AdvisorToolContext(
        factory=factory, user_id=SINGLE_USER_ID, run_quick_analysis=_run_quick
    )
    model = Model(model_id=MODEL_SONNET)

    # Captured by event_source via closure — must exist before the generator runs.
    collected_tool_calls: list[dict[str, Any]] = []

    async def event_source() -> AsyncIterator[bytes]:
        yield _sse({"kind": "user_message_persisted", "message_id": user_msg_id})
        try:
            async for frame in stream_advisor_turn(
                model=model,
                history=history,
                user_message=user_message,
                tool_ctx=tool_ctx,
            ):
                payload = frame.to_dict()
                yield _sse(payload)
                if frame.kind == "message_complete":
                    # Persist + roll up totals.
                    assistant_id = _persist_message(
                        factory,
                        session_id=session_id,
                        role="assistant",
                        content=frame.final_text,
                        tool_calls=collected_tool_calls.copy(),
                        referenced_analysis_ids=frame.referenced_analysis_ids or [],
                        tokens_in=int(frame.tokens_in or 0),
                        tokens_out=int(frame.tokens_out or 0),
                        cost_usd=float(frame.cost_usd or 0.0),
                        model_used=model.model_id,
                    )
                    _bump_session(
                        factory,
                        session_id,
                        delta_messages=1,
                        delta_cost=float(frame.cost_usd or 0.0),
                        delta_tokens_in=int(frame.tokens_in or 0),
                        delta_tokens_out=int(frame.tokens_out or 0),
                    )
                    yield _sse({"kind": "assistant_message_persisted", "message_id": assistant_id})
                elif frame.kind == "tool_use_started":
                    collected_tool_calls.append(
                        {
                            "name": frame.tool_name,
                            "input": frame.tool_input,
                            "result": None,
                        }
                    )
                elif frame.kind == "tool_result" and collected_tool_calls:
                    collected_tool_calls[-1]["result"] = frame.tool_result
        except Exception as exc:
            log.exception("chat_stream_failed", session_id=session_id)
            yield _sse({"kind": "error", "text": str(exc)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Helpers ------------------------------------------------------------------------------


def _persist_message(
    factory,
    *,
    session_id: str,
    role: str,
    content: str | None,
    tool_calls: list[dict[str, Any]],
    referenced_analysis_ids: list[str],
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    model_used: str | None,
) -> str:
    msg_id = str(uuid.uuid4())
    with session_scope(factory) as s:
        s.execute(
            pg_insert(ChatMessage).values(
                message_id=msg_id,
                session_id=session_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
                referenced_analysis_ids=referenced_analysis_ids,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=Decimal(str(cost_usd)),
                model_used=model_used,
            )
        )
    return msg_id


def _bump_session(
    factory,
    session_id: str,
    *,
    delta_messages: int = 0,
    delta_cost: float = 0.0,
    delta_tokens_in: int = 0,
    delta_tokens_out: int = 0,
    title_seed: str | None = None,
) -> None:
    values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
    with session_scope(factory) as s:
        if title_seed and delta_messages > 0:
            sess = s.execute(
                select(ChatSession).where(ChatSession.session_id == session_id)
            ).scalar_one()
            if sess.title == "New chat":
                values["title"] = (title_seed[:60] + "…") if len(title_seed) > 60 else title_seed
        if delta_messages:
            values["message_count"] = ChatSession.message_count + delta_messages
        if delta_cost:
            values["total_cost_usd"] = ChatSession.total_cost_usd + Decimal(str(delta_cost))
        if delta_tokens_in:
            values["total_tokens_in"] = ChatSession.total_tokens_in + delta_tokens_in
        if delta_tokens_out:
            values["total_tokens_out"] = ChatSession.total_tokens_out + delta_tokens_out
        s.execute(update(ChatSession).where(ChatSession.session_id == session_id).values(**values))


def _build_anthropic_history(factory, session_id: str) -> list[dict[str, Any]]:
    """Reconstruct the Anthropic messages array from persisted rows. We replay user/assistant
    text and assistant tool_use + tool_result blocks so the model has full context across
    turns."""
    with session_scope(factory) as s:
        rows = (
            s.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
            )
            .scalars()
            .all()
        )
    history: list[dict[str, Any]] = []
    for r in rows:
        if r.role == "user":
            history.append({"role": "user", "content": [{"type": "text", "text": r.content or ""}]})
        elif r.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if r.content:
                blocks.append({"type": "text", "text": r.content})
            for tc in r.tool_calls or []:
                if "input" in tc and "name" in tc:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                            "name": tc["name"],
                            "input": tc["input"],
                        }
                    )
            if blocks:
                history.append({"role": "assistant", "content": blocks})
            # Tool results need to come back as a user message right after.
            tool_results = [tc for tc in (r.tool_calls or []) if tc.get("result") is not None]
            if tool_results:
                history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tc.get("id") or "toolu_unknown",
                                "content": json.dumps(tc["result"], default=str),
                            }
                            for tc in tool_results
                        ],
                    }
                )
    return history


def _row_to_session_summary(r: ChatSession) -> SessionSummary:
    return SessionSummary(
        session_id=str(r.session_id),
        title=r.title,
        message_count=r.message_count,
        total_cost_usd=float(r.total_cost_usd),
        updated_at=r.updated_at,
        created_at=r.created_at,
    )


def _row_to_message(r: ChatMessage) -> MessageOut:
    return MessageOut(
        message_id=str(r.message_id),
        role=r.role,
        content=r.content,
        tool_calls=r.tool_calls or [],
        referenced_analysis_ids=[str(x) for x in (r.referenced_analysis_ids or [])],
        cost_usd=float(r.cost_usd),
        tokens_in=r.tokens_in,
        tokens_out=r.tokens_out,
        model_used=r.model_used,
        created_at=r.created_at,
    )


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode()
