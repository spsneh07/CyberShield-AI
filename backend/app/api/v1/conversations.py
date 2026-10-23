import json
import logging
import queue
import threading
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_pipeline
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.errors import NotFoundError
from app.repositories.conversations import ConversationRepository
from app.repositories.incidents import IncidentRepository
from app.schemas.conversation import (
    AssistantResult, ConversationCreate, ConversationOut, FeedbackIn, MessageIn, MessageOut,
)
from app.services.ai.factory import get_embeddings, get_llm
from app.services.conversation.orchestrator import Orchestrator
from app.services.pipeline import Pipeline

router = APIRouter(prefix="/conversations", tags=["conversations"])
log = logging.getLogger(__name__)


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationCreate | None = None, db: Session = Depends(get_db)) -> ConversationOut:
    conv = ConversationRepository(db).create(body.title if body else None)
    return ConversationOut(id=conv.id, title=conv.title, created_at=conv.created_at)


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> ConversationOut:
    repo = ConversationRepository(db)
    conv = repo.get(conversation_id)
    if conv is None:
        raise NotFoundError("Conversation")
    incident = IncidentRepository(db).latest_for_conversation(conversation_id)
    messages = [MessageOut.model_validate(m, from_attributes=True) for m in repo.list_messages(conversation_id, 200)]
    return ConversationOut(id=conv.id, title=conv.title, created_at=conv.created_at, messages=messages,
                           incident_id=incident.id if incident else None)


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)) -> None:
    """Permanently delete the conversation, its incident record and generated drafts (user-requested erasure)."""
    repo = ConversationRepository(db)
    conv = repo.get(conversation_id)
    if conv is None:
        raise NotFoundError("Conversation")
    repo.delete(conv)


@router.post("/{conversation_id}/messages", response_model=AssistantResult)
def post_message(conversation_id: str, body: MessageIn, db: Session = Depends(get_db),
                 pipeline: Pipeline = Depends(get_pipeline)) -> AssistantResult:
    return Orchestrator(db, pipeline).handle(conversation_id, body)


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/{conversation_id}/messages/stream")
def stream_message(conversation_id: str, body: MessageIn, db: Session = Depends(get_db)) -> StreamingResponse:
    """Server-Sent Events: one `stage` event as each pipeline step starts, then `result` or `error`."""
    if ConversationRepository(db).get(conversation_id) is None:
        raise NotFoundError("Conversation")
    events: queue.Queue[tuple[str, object] | None] = queue.Queue()

    def worker() -> None:
        with SessionLocal() as session:
            try:
                pipeline = Pipeline(session, get_llm(), get_embeddings(), get_settings())
                result = Orchestrator(session, pipeline).handle(
                    conversation_id, body, emit=lambda s: events.put(("stage", {"stage": s})))
                events.put(("result", result.model_dump(mode="json")))
            except Exception as exc:  # reported to the client as a safe error event
                log.exception("stream failed: %s", type(exc).__name__)
                events.put(("error", {"message": "Something went wrong while analysing your message."}))
            finally:
                events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen() -> Iterator[str]:
        while (item := events.get()) is not None:
            yield _sse(*item)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


feedback_router = APIRouter(tags=["feedback"])


@feedback_router.post("/feedback", status_code=201)
def post_feedback(body: FeedbackIn, db: Session = Depends(get_db)) -> dict:
    repo = ConversationRepository(db)
    if repo.get_message(body.message_id) is None:
        raise NotFoundError("Message")
    fb = repo.add_feedback(body.message_id, body.rating, body.comment)
    return {"id": fb.id}
