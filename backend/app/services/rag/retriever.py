from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Citation
from app.schemas.incident import IncidentData
from app.services.ai.base import EmbeddingProvider
from app.services.classification.taxonomy import label
from app.services.rag.sanitize import safe_attr, strip_injections


def freshness(last_verified: str | None, max_age_days: int, today: date | None = None) -> str:
    """verified / stale from a recorded check date; unverified when none is recorded or it can't be parsed."""
    try:
        checked = date.fromisoformat((last_verified or "")[:10])
    except ValueError:
        return "unverified"
    return "verified" if ((today or date.today()) - checked).days <= max_age_days else "stale"


class Retriever:
    def __init__(self, db: Session, embeddings: EmbeddingProvider, top_k: int = 5, min_score: float = 0.05,
                 max_age_days: int = 180) -> None:
        self.repo = KnowledgeRepository(db)
        self.embeddings = embeddings
        self.top_k, self.min_score, self.max_age_days = top_k, min_score, max_age_days

    def _check_index_compatible(self) -> None:
        stored = self.repo.embedding_identities()
        if stored and stored != {self.embeddings.identity}:
            raise AppError(
                "embedding_mismatch",
                f"Knowledge base vectors were built with {sorted(stored)} but queries use "
                f"{self.embeddings.identity}. Re-run `python -m scripts.ingest_kb`.",
                503,
            )

    def search(self, query: str, top_k: int | None = None, category: str | None = None) -> list[Citation]:
        """Top-k documents (not chunks): chunks from the same document are merged into one citation."""
        k = top_k or self.top_k
        self._check_index_compatible()
        [vector] = self.embeddings.embed([query])
        hits = self.repo.search(vector, k * 3, category)  # over-fetch, then collapse per document
        by_doc: dict[str, Citation] = {}
        for h in hits:
            if h.score < self.min_score:
                continue
            excerpt, _ = strip_injections(h.chunk.content)  # defence in depth at query time
            existing = by_doc.get(h.document.id)
            if existing is not None:
                if len(existing.excerpt) < 2000:
                    existing.excerpt = f"{existing.excerpt}\n\n{excerpt}"
                continue
            if len(by_doc) >= k:
                continue
            by_doc[h.document.id] = Citation(
                id="",
                title=safe_attr(h.document.title),
                organization=safe_attr(h.document.organization),
                url=safe_attr(h.document.url),
                category=h.document.category,
                document_type=h.document.document_type,
                published_date=h.document.published_date,
                last_verified=h.document.last_verified,
                freshness=freshness(h.document.last_verified, self.max_age_days),
                source_note=h.document.source_note,
                score=round(h.score, 4),
                excerpt=excerpt,
            )
        out = list(by_doc.values())
        for i, c in enumerate(out, 1):
            c.id = f"S{i}"
        return out

    def for_incident(self, incident: IncidentData, classification: ClassificationResult) -> list[Citation]:
        """Topic retrieval plus one reporting/evidence chunk so guidance can cite real channels."""
        topic = f"{label(classification.category)} {classification.subtype or ''} {incident.platform or ''} {incident.description[:500]}"
        results = self.search(topic, top_k=self.top_k)
        need = "how to report cybercrime helpline portal" + (" bank unauthorised transaction" if incident.financial_loss else "")
        for extra in self.search(need, top_k=2):
            if all((extra.title, extra.url) != (r.title, r.url) for r in results):
                results.append(extra)
        for i, r in enumerate(results, 1):
            r.id = f"S{i}"
        return results
