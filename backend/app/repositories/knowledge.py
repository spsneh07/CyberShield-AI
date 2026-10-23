import math
from dataclasses import dataclass

from sqlalchemy import Float, delete, func, select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument


@dataclass
class ChunkHit:
    chunk: KnowledgeChunk
    document: KnowledgeDocument
    score: float  # cosine similarity in [-1, 1]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class KnowledgeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    @property
    def _is_pg(self) -> bool:
        return self.db.get_bind().dialect.name == "postgresql"

    def get_by_path(self, source_path: str) -> KnowledgeDocument | None:
        return self.db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.source_path == source_path)).first()

    def upsert_document(self, meta: dict[str, str | None], source_path: str, content_hash: str,
                        chunks: list[tuple[str, list[float]]], embedding_identity: str = "") -> KnowledgeDocument:
        doc = self.get_by_path(source_path)
        if doc is None:
            doc = KnowledgeDocument(source_path=source_path)
            self.db.add(doc)
        else:
            self.db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
        doc.title = meta["title"] or source_path
        doc.organization = meta["organization"] or "Unknown"
        doc.url = meta["url"] or ""
        doc.category = meta["category"] or "general"
        doc.published_date = meta.get("date")
        doc.last_verified = meta.get("last_verified")
        doc.document_type = meta.get("document_type") or "document"
        doc.source_note = meta.get("source_note")
        doc.embedding_identity = embedding_identity
        doc.content_hash = content_hash
        self.db.flush()
        for i, (text, emb) in enumerate(chunks):
            self.db.add(KnowledgeChunk(document_id=doc.id, chunk_index=i, content=text, embedding=emb))
        self.db.commit()
        return doc

    def delete_missing(self, keep_paths: set[str]) -> int:
        docs = self.db.scalars(select(KnowledgeDocument)).all()
        removed = 0
        for d in docs:
            if d.source_path not in keep_paths:
                self.db.delete(d)
                removed += 1
        self.db.commit()
        return removed

    def embedding_identities(self) -> set[str]:
        return {i for i in self.db.scalars(select(KnowledgeDocument.embedding_identity).distinct()) if i}

    def count_chunks(self) -> int:
        return int(self.db.scalar(select(func.count()).select_from(KnowledgeChunk)) or 0)

    def search(self, embedding: list[float], top_k: int, category: str | None = None) -> list[ChunkHit]:
        if self._is_pg:
            distance = KnowledgeChunk.embedding.op("<=>", return_type=Float)(embedding)  # cosine distance
            stmt = (
                select(KnowledgeChunk, KnowledgeDocument, distance.label("distance"))
                .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
                .order_by(distance)
                .limit(top_k)
            )
            if category:
                stmt = stmt.where(KnowledgeDocument.category == category)
            return [ChunkHit(c, d, 1.0 - float(dist)) for c, d, dist in self.db.execute(stmt).all()]

        # Portable fallback (SQLite tests): exact cosine similarity in Python.
        stmt = select(KnowledgeChunk, KnowledgeDocument).join(
            KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id
        )
        if category:
            stmt = stmt.where(KnowledgeDocument.category == category)
        hits = [ChunkHit(c, d, _cosine(embedding, c.embedding)) for c, d in self.db.execute(stmt).all()]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
