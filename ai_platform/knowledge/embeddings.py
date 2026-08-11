"""Embedding providers for RAG — OpenAI, local deterministic, optional Voyage."""

from __future__ import annotations

import hashlib
import math
import os
from abc import ABC, abstractmethod
from typing import Any


class EmbeddingProvider(ABC):
    name: str
    dimensions: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class LocalHashEmbedding(EmbeddingProvider):
    """Deterministic local embeddings for offline/dev (not semantic quality)."""

    name = "local"
    dimensions = 256

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def _embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        # Expand with multiple hashes for more dimensions
        raw = h
        while len(raw) < self.dimensions:
            raw += hashlib.sha256(raw).digest()
        vec = [((raw[i] / 255.0) - 0.5) for i in range(self.dimensions)]
        # Add bag-of-words signal for better local relevance
        tokens = set(text.lower().split())
        for i, tok in enumerate(list(tokens)[:64]):
            th = int(hashlib.md5(tok.encode()).hexdigest()[:8], 16)
            idx = th % self.dimensions
            vec[idx] += 0.15
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class OpenAIEmbedding(EmbeddingProvider):
    name = "openai"
    dimensions = 1536

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("PLATFORM_EMBEDDING_MODEL", "text-embedding-3-small")
        self.base_url = base_url.rstrip("/")
        if "3-large" in self.model:
            self.dimensions = 3072
        elif "ada-002" in self.model:
            self.dimensions = 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY required for OpenAI embeddings")
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        # Sort by index to preserve order
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]


def build_embedding_provider(prefer: str | None = None) -> EmbeddingProvider:
    """Pick best available embedding backend."""
    prefer = prefer or os.getenv("PLATFORM_EMBEDDING_PROVIDER", "auto")
    if prefer == "local":
        return LocalHashEmbedding()
    if prefer == "openai" or (prefer == "auto" and os.getenv("OPENAI_API_KEY")):
        try:
            return OpenAIEmbedding()
        except Exception:
            if prefer == "openai":
                raise
    return LocalHashEmbedding()
