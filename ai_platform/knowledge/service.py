"""Knowledge ingestion and RAG retrieval."""

import math
import re
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import KnowledgeSourceSpec, RetrievalChunk
from ai_platform.knowledge.embeddings import EmbeddingProvider, build_embedding_provider


def chunk_text(text: str, max_tokens: int = 128) -> list[str]:
    """Simple sentence-aware chunker (Phase 2)."""
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


class KnowledgeStore:
    """In-memory chunk index with hybrid keyword + vector search."""

    def __init__(self, embedder: EmbeddingProvider | None = None) -> None:
        self.embedder = embedder or build_embedding_provider()
        self._chunks: list[RetrievalChunk] = []
        self._embeddings: dict[str, list[float]] = {}

    async def ingest_source(self, source_name: str, spec: KnowledgeSourceSpec) -> int:
        count = 0
        chunk_size = spec.ingestion.get("chunking", {}).get("maxTokens", 128)
        pending_texts: list[str] = []
        pending_chunks: list[RetrievalChunk] = []
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
                pending_chunks.append(chunk)
                pending_texts.append(chunk_text_val)
                count += 1

        if pending_texts:
            vectors = await self.embedder.embed(pending_texts)
            for chunk, vec in zip(pending_chunks, vectors, strict=True):
                self._chunks.append(chunk)
                self._embeddings[chunk.chunk_id] = vec
        return count

    async def retrieve(
        self, query: str, source_names: list[str] | None = None, top_k: int = 5
    ) -> list[RetrievalChunk]:
        q_lower = query.lower()
        q_vec = await self.embedder.embed_one(query)
        candidates = self._chunks
        if source_names:
            candidates = [c for c in candidates if c.source_id in source_names]

        scored: list[tuple[float, RetrievalChunk]] = []
        for c in candidates:
            keyword = 1.0 if q_lower in c.text.lower() else 0.0
            vec = self._embeddings.get(c.chunk_id, [])
            sim = cosine_similarity(q_vec, vec) if vec else 0.0
            hybrid = 0.4 * keyword + 0.6 * sim
            scored.append((hybrid, RetrievalChunk(
                chunk_id=c.chunk_id,
                source_id=c.source_id,
                doc_id=c.doc_id,
                text=c.text,
                score=hybrid,
                metadata=c.metadata,
            )))

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
    ) -> None:
        self.store = store or KnowledgeStore(embedder=embedder)
        self._loaded: set[str] = set()

    async def ensure_source(self, source_name: str, spec: KnowledgeSourceSpec) -> None:
        if source_name in self._loaded:
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
                from ai_platform.core.models import KnowledgeSourceSpec

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
