"""Dense embedding providers for Bailian and deterministic local smoke tests."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import math
import re
from typing import Protocol

from langchain_openai import OpenAIEmbeddings


class EmbeddingDimensionError(RuntimeError):
    """The configured, returned, and stored vector dimensions disagree."""


class EmbeddingProvider(Protocol):
    """The small seam needed by ingestion and retrieval."""

    @property
    def dimension(self) -> int: ...

    @property
    def model_id(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class BailianEmbeddingProvider:
    """Alibaba Bailian's OpenAI-compatible Embeddings API."""

    def __init__(self, *, api_key: str, base_url: str, model: str, dimension: int) -> None:
        self._dimension = dimension
        self._model_id = model
        self._client = OpenAIEmbeddings(
            api_key=api_key,
            base_url=base_url,
            model=model,
            dimensions=dimension,
            chunk_size=10,
            check_embedding_ctx_length=False,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str:
        return self._model_id

    def _validate(self, vectors: Sequence[Sequence[float]]) -> list[list[float]]:
        result = [list(vector) for vector in vectors]
        if any(len(vector) != self.dimension for vector in result):
            actual = sorted({len(vector) for vector in result})
            raise EmbeddingDimensionError(
                f"Bailian returned vector dimensions {actual}; configured dimension is {self.dimension}"
            )
        return result

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._validate(self._client.embed_documents(list(texts)))

    def embed_query(self, text: str) -> list[float]:
        return self._validate([self._client.embed_query(text)])[0]


class DeterministicEmbeddingProvider:
    """Dependency-free vectors for local storage smoke tests, never production inference."""

    def __init__(self, dimension: int = 32) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str:
        return "oncall-deterministic-sha256-v1"

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = _TOKEN_FOR_EMBEDDING.findall(text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


_TOKEN_FOR_EMBEDDING = re.compile(r"[\u3400-\u9fff]|[a-z0-9_./:@+-]+", re.UNICODE)
