from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import conversations, evidence, incidents, rag, scamcheck
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.repositories.knowledge import KnowledgeRepository
from app.services.ai.prompts.common import DISCLAIMER
from app.services.classification.taxonomy import CATEGORIES
from app.services.language import LANGUAGE_NAMES

settings = get_settings()
setup_logging(settings.log_level)

app = FastAPI(title="AI Cyber Crime Complaint & Awareness Assistant", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Session-Token"],
)
register_error_handlers(app)

api = APIRouter(prefix="/api/v1")
api.include_router(conversations.router)
api.include_router(conversations.feedback_router)
api.include_router(incidents.router)
api.include_router(rag.router)
api.include_router(scamcheck.router)
api.include_router(evidence.router)


@api.get("/meta", tags=["meta"])
def meta() -> dict:
    return {
        "disclaimer": DISCLAIMER,
        "categories": [{"id": c.id, "label": c.label} for c in CATEGORIES],
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "languages": [{"id": k, "label": v} for k, v in LANGUAGE_NAMES.items()],
    }


app.include_router(api)


@app.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": db.get_bind().dialect.name,
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "knowledge_chunks": KnowledgeRepository(db).count_chunks(),
        "knowledge_embedding": sorted(KnowledgeRepository(db).embedding_identities()),
    }
