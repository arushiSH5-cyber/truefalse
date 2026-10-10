"""Multilingual sentence embeddings for cross-language claim matching."""

import asyncio
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np


class Embedder(Protocol):
    dim: int

    async def embed_queries(self, texts: Sequence[str]) -> np.ndarray: ...

    async def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...


def normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


class SentenceTransformerEmbedder:
    """intfloat/multilingual-e5-base by default (100+ languages, 768-d).

    E5 models expect "query: " / "passage: " prefixes. Requires the `ml` extra.
    The model loads lazily and runs in a thread so the event loop is never blocked.
    """

    def __init__(self, model_name: str = "intfloat/multilingual-e5-base", dim: int = 768) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model: Any = None
        self._e5 = "e5" in model_name.lower()

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _encode(self, texts: list[str]) -> np.ndarray:
        out = self._load().encode(texts, normalize_embeddings=True, batch_size=32)
        return np.asarray(out, dtype=np.float32)

    async def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        prefix = "query: " if self._e5 else ""
        return await asyncio.to_thread(self._encode, [prefix + t for t in texts])

    async def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        prefix = "passage: " if self._e5 else ""
        return await asyncio.to_thread(self._encode, [prefix + t for t in texts])
