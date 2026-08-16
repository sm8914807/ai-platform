"""Knowledge ingestion and RAG retrieval — durable SQL index (SQLite / Postgres)."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import KnowledgeSourceSpec, RetrievalChunk
from ai_platform.db.sql import SqlBackend, create_sql_backend
from ai_platform.knowledge.embeddings import EmbeddingProvider, build_embedding_provider


def chunk_text(text: str, max_tokens: int = 128) -> list[str]:
    """Simple sentence-aware chunker."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current: list[str] = []
    for s in sentences:
        current.append(s)
        if len(" ".join(current).split()) >= max_tokens:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks or [text]


def mock_embed(text: str, dims: int = 64) -> list[float]:
    """Backward-compatible sync mock embedding (prefer EmbeddingProvider)."""
    import hashlib

    h = hashlib.sha256(text.encode()).digest()
    vec = [((h[i % len(h)] / 255.0) - 0.5) for i in range(dims)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return sum(x * y for x, y in zip(a[:n], b[:n], strict=False))


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


def _as_float_list(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, list):
        return [float(x) for x in value]
    if isinstance(value, str):
        raw = json.loads(value) if value else []
        return [float(x) for x in raw]
    return []


class KnowledgeStore:
    """Chunk index with hybrid keyword + vector search.

    When ``sql`` is provided, chunks and embeddings persist across process restarts.
    Without SQL, behavior stays in-process (tests / ephemeral).
    """

    def __init__(
        self,
        embedder: EmbeddingProvider | None = None,
        db_path: str | None = None,
        *,
        sql: SqlBackend | None = None,
    ) -> None:
        self.embedder = embedder or build_embedding_provider()
        self.sql = sql
        if sql is None and db_path is not None:
            self.sql = create_sql_backend(db_path=db_path)
        self._chunks: list[RetrievalChunk] = []
        self._embeddings: dict[str, list[float]] = {}

    async def has_source(self, source_name: str) -> bool:
        if self.sql is not None:
            row = await self.sql.fetchone(
                "SELECT id FROM knowledge_chunks WHERE source_name = ? LIMIT 1",
                source_name,
            )
            return row is not None
        return any(c.source_id == source_name for c in self._chunks)

    async def ingest_source(self, source_name: str, spec: KnowledgeSourceSpec) -> int:
        count = 0
        chunk_size = spec.ingestion.get("chunking", {}).get("maxTokens", 128)
        pending_texts: list[str] = []
        pending_chunks: list[tuple[RetrievalChunk, int]] = []

        if self.sql is not None:
            await self.sql.execute(
                "DELETE FROM knowledge_chunks WHERE source_name = ?",
                source_name,
            )
        else:
            self._chunks = [c for c in self._chunks if c.source_id != source_name]
            keep_ids = {c.chunk_id for c in self._chunks}
            self._embeddings = {k: v for k, v in self._embeddings.items() if k in keep_ids}

        for doc in spec.documents:
            doc_id = doc.get("id", new_id("doc"))
            text = doc.get("text", "")
            meta = doc.get("metadata", {})
            for i, chunk_text_val in enumerate(chunk_text(text, chunk_size)):
                chunk_id = new_id("chunk")
                chunk = RetrievalChunk(
                    chunk_id=chunk_id,
                    source_id=source_name,
                    doc_id=doc_id,
                    text=chunk_text_val,
                    score=0.0,
                    metadata=meta,
                )
                pending_chunks.append((chunk, i))
                pending_texts.append(chunk_text_val)
                count += 1

        if not pending_texts:
            return 0

        vectors = await self.embedder.embed(pending_texts)
        now = datetime.now(timezone.utc).isoformat()
        for (chunk, chunk_index), vec in zip(pending_chunks, vectors, strict=True):
            if self.sql is not None:
                await self.sql.execute(
                    "INSERT INTO knowledge_chunks "
                    "(id, source_id, source_name, doc_id, chunk_index, text, "
                    "embedding_json, metadata_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    chunk.chunk_id,
                    chunk.source_id,
                    source_name,
                    chunk.doc_id,
                    chunk_index,
                    chunk.text,
                    json.dumps(vec),
                    json.dumps(chunk.metadata),
                    now,
                )
            else:
                self._chunks.append(chunk)
                self._embeddings[chunk.chunk_id] = vec
        return count

    async def _load_candidates(
        self, source_names: list[str] | None
    ) -> list[tuple[RetrievalChunk, list[float]]]:
        if self.sql is None:
            out: list[tuple[RetrievalChunk, list[float]]] = []
            for c in self._chunks:
                if source_names and c.source_id not in source_names:
                    continue
                out.append((c, self._embeddings.get(c.chunk_id, [])))
            return out

        if source_names:
            placeholders = ",".join("?" for _ in source_names)
            rows = await self.sql.fetchall(
                f"SELECT * FROM knowledge_chunks WHERE source_name IN ({placeholders})",
                *source_names,
            )
        else:
            rows = await self.sql.fetchall("SELECT * FROM knowledge_chunks")

        candidates: list[tuple[RetrievalChunk, list[float]]] = []
        for r in rows:
            chunk = RetrievalChunk(
                chunk_id=r["id"],
                source_id=r["source_name"],
                doc_id=r["doc_id"],
                text=r["text"],
                score=0.0,
                metadata=_as_dict(r.get("metadata_json")),
            )
            candidates.append((chunk, _as_float_list(r.get("embedding_json"))))
        return candidates

    async def retrieve(
        self, query: str, source_names: list[str] | None = None, top_k: int = 5
    ) -> list[RetrievalChunk]:
        q_lower = query.lower()
        q_vec = await self.embedder.embed_one(query)
        candidates = await self._load_candidates(source_names)

        scored: list[tuple[float, RetrievalChunk]] = []
        for c, vec in candidates:
            keyword = 1.0 if q_lower in c.text.lower() else 0.0
            sim = cosine_similarity(q_vec, vec) if vec else 0.0
            hybrid = 0.4 * keyword + 0.6 * sim
            scored.append(
                (
                    hybrid,
                    RetrievalChunk(
                        chunk_id=c.chunk_id,
                        source_id=c.source_id,
                        doc_id=c.doc_id,
                        text=c.text,
                        score=hybrid,
                        metadata=c.metadata,
                    ),
                )
            )

        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]

    def format_citations(self, chunks: list[RetrievalChunk]) -> str:
        lines = []
        for i, c in enumerate(chunks, 1):
            lines.append(f"[{i}] {c.source_id}/{c.doc_id}: {c.text[:200]}")
        return "\n".join(lines)

    @property
    def embedding_backend(self) -> str:
        return self.embedder.name


class KnowledgeService:
    def __init__(
        self,
        store: KnowledgeStore | None = None,
        embedder: EmbeddingProvider | None = None,
        db_path: str | None = None,
        *,
        sql: SqlBackend | None = None,
    ) -> None:
        if store is not None:
            self.store = store
        else:
            self.store = KnowledgeStore(embedder=embedder, db_path=db_path, sql=sql)
        self._loaded: set[str] = set()

    @classmethod
    def durable(
        cls,
        db_path: str | None = None,
        *,
        sql: SqlBackend | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> KnowledgeService:
        return cls(store=KnowledgeStore(embedder=embedder, db_path=db_path, sql=sql))

    async def ensure_source(self, source_name: str, spec: KnowledgeSourceSpec) -> None:
        if source_name in self._loaded:
            return
        if await self.store.has_source(source_name):
            self._loaded.add(source_name)
            return
        await self.store.ingest_source(source_name, spec)
        self._loaded.add(source_name)

    async def retrieve_for_agent(
        self,
        query: str,
        knowledge_refs: list[str],
        bundle: dict[str, dict],
        top_k: int = 5,
    ) -> list[RetrievalChunk]:
        source_names: list[str] = []
        for ref in knowledge_refs:
            parts = ref.split("/", 1)
            if len(parts) != 2:
                continue
            name = parts[1]
            doc = bundle.get(f"KnowledgeSource:{name}")
            if doc:
                spec = KnowledgeSourceSpec.model_validate(doc["spec"])
                await self.ensure_source(name, spec)
                source_names.append(name)

        top_k_val = top_k
        if knowledge_refs:
            first = knowledge_refs[0].split("/", 1)
            if len(first) == 2:
                doc = bundle.get(f"KnowledgeSource:{first[1]}")
                if doc:
                    top_k_val = doc["spec"].get("retrieval", {}).get("topK", top_k)

        return await self.store.retrieve(query, source_names, top_k_val)
