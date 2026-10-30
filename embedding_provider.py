"""Optional, replaceable embedding-provider interface and HTTP adapter."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().with_name(".env.secret"), override=False)


class EmbeddingProviderError(RuntimeError):
    """Raised when a configured embedding provider cannot return valid vectors."""


class EmbeddingProvider(Protocol):
    provider_id: str
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one finite, non-empty vector for each input text, in input order."""


class OpenAICompatibleEmbeddingProvider:
    """Adapter for an explicitly configured OpenAI-compatible embeddings endpoint."""

    provider_id = "openai-compatible-http"

    def __init__(self, endpoint: str, model_id: str, api_key: str | None = None) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EmbeddingProviderError("Embedding endpoint must be an HTTP or HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise EmbeddingProviderError("Embedding endpoint must not contain credentials")
        if not model_id.strip():
            raise EmbeddingProviderError("Embedding model must not be empty")
        self.endpoint = endpoint
        self.model_id = model_id
        self.api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = httpx.post(
                self.endpoint,
                json={"model": self.model_id, "input": texts},
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload["data"]
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in ordered]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise EmbeddingProviderError("Embedding provider request failed") from exc
        if len(vectors) != len(texts):
            raise EmbeddingProviderError("Embedding provider returned an unexpected vector count")
        dimensions: int | None = None
        validated: list[list[float]] = []
        for vector in vectors:
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingProviderError("Embedding provider returned an invalid vector") from exc
            if not values or not all(math.isfinite(value) for value in values):
                raise EmbeddingProviderError("Embedding provider returned an invalid vector")
            if dimensions is None:
                dimensions = len(values)
            elif len(values) != dimensions:
                raise EmbeddingProviderError("Embedding provider returned inconsistent vector dimensions")
            validated.append(values)
        return validated


def get_embedding_provider() -> EmbeddingProvider | None:
    """Create the configured adapter, or return None when semantic search is disabled."""
    endpoint = os.getenv("SEARCH_EMBEDDING_ENDPOINT", "").strip()
    model_id = os.getenv("SEARCH_EMBEDDING_MODEL", "").strip()
    if not endpoint and not model_id:
        return None
    if not endpoint or not model_id:
        return None
    return OpenAICompatibleEmbeddingProvider(
        endpoint,
        model_id,
        os.getenv("SEARCH_EMBEDDING_API_KEY") or None,
    )
