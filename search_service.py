"""Repository-scoped lexical, symbol, semantic, and hybrid retrieval."""

from __future__ import annotations

import heapq
import hashlib
import json
import math
import re
from typing import Any

import embedding_provider
import repository_scanner
from repository_parse_service import SourceReadError, _read_source
from repository_store import RepositoryStore


WEIGHTS = {"lexical": 0.4, "semantic": 0.4, "symbol": 0.2}
MAX_QUERY_TERMS = 64
MAX_CANDIDATES = 500
STALE_INDEX_DETAIL = (
    "Repository source changed or could not be safely verified; run a forced scan followed by parsing"
)


class SearchUnavailableError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[^\W_]+(?:_[^\W_]+)*", query.casefold(), flags=re.UNICODE)
    return list(dict.fromkeys(terms))[:MAX_QUERY_TERMS]


def _fts_query(terms: list[str]) -> str:
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def normalize_scores(scores: dict[int, float]) -> dict[int, float]:
    """Min-max normalize one retrieval component; ties share the maximum relevance."""
    if not scores:
        return {}
    low = min(scores.values())
    high = max(scores.values())
    if math.isclose(low, high, rel_tol=1e-12, abs_tol=1e-12):
        return {document_id: 1.0 for document_id in scores}
    return {
        document_id: min(1.0, max(0.0, (score - low) / (high - low)))
        for document_id, score in scores.items()
    }


def _symbol_relevance(symbol_names: str, terms: list[str]) -> float:
    if not symbol_names or not terms:
        return 0.0
    name_terms = set(re.findall(r"[^\W_]+", symbol_names.casefold(), flags=re.UNICODE))
    query_parts = [part for term in terms for part in term.split("_")]
    matched = sum(term in name_terms for term in query_parts)
    if not matched:
        return 0.0
    if matched == len(query_parts):
        return 1.0
    return 0.75 * matched / len(query_parts)


def _cosine(left: list[float], right: list[float]) -> float | None:
    if not left or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return dot / (left_norm * right_norm)


def _embedding_text(provider: embedding_provider.EmbeddingProvider, query: str) -> list[float]:
    try:
        vectors = provider.embed([query])
    except Exception as exc:
        raise SearchUnavailableError(503, "Semantic search is temporarily unavailable") from exc
    if len(vectors) != 1 or not vectors[0]:
        raise SearchUnavailableError(503, "Semantic search is temporarily unavailable")
    return vectors[0]


def _validate_candidate_files(
    repository_root: str,
    candidate_rows: list[dict[str, Any]],
) -> None:
    """Verify each distinct candidate file's current contents against its index hash."""
    expected_hashes: dict[str, str] = {}
    for row in candidate_rows:
        file_path = str(row["file_path"])
        content_hash = str(row["content_hash"])
        previous_hash = expected_hashes.setdefault(file_path, content_hash)
        if previous_hash != content_hash:
            raise SearchUnavailableError(409, STALE_INDEX_DETAIL)

    for file_path, expected_hash in expected_hashes.items():
        try:
            content = _read_source(repository_root, file_path)
        except (SourceReadError, OSError, ValueError):
            raise SearchUnavailableError(409, STALE_INDEX_DETAIL) from None
        if repository_scanner.current_file_exclusion_reason(file_path, content) is not None:
            raise SearchUnavailableError(409, STALE_INDEX_DETAIL)
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise SearchUnavailableError(409, STALE_INDEX_DETAIL)


def search_repository(
    repository_id: str,
    query: str,
    limit: int,
    store: RepositoryStore,
    retrieval_method: str = "hybrid",
    language: str | None = None,
    file_path: str | None = None,
    path_prefix: str | None = None,
    symbol_type: str | None = None,
) -> dict[str, Any]:
    terms = _query_terms(query)
    if not terms:
        raise SearchUnavailableError(422, "Query must contain at least one searchable character")

    provider: embedding_provider.EmbeddingProvider | None = None
    if retrieval_method in {"hybrid", "semantic"}:
        try:
            provider = embedding_provider.get_embedding_provider()
        except Exception:
            provider = None

    semantic_available = bool(
        provider
        and store.semantic_index_is_ready(
            repository_id, provider.provider_id, provider.model_id,
            language, file_path, path_prefix, symbol_type,
        )
    )
    if retrieval_method == "semantic" and not semantic_available:
        raise SearchUnavailableError(503, "Semantic search is unavailable for this repository or filter")

    candidate_limit = min(MAX_CANDIDATES, max(50, limit * 20))
    lexical_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    semantic_rows: list[tuple[float, dict[str, Any]]] = []

    if retrieval_method in {"hybrid", "lexical"}:
        lexical_rows = store.search_lexical_documents(
            repository_id, _fts_query(terms), candidate_limit,
            language, file_path, path_prefix, symbol_type,
        )
    if retrieval_method in {"hybrid", "symbol"}:
        symbol_rows = store.search_symbol_documents(
            repository_id, terms, candidate_limit,
            language, file_path, path_prefix, symbol_type,
        )
    if retrieval_method in {"hybrid", "semantic"} and semantic_available and provider is not None:
        try:
            query_vector = _embedding_text(provider, query)
            best: list[tuple[float, int, dict[str, Any]]] = []
            for index, row in enumerate(store.iter_semantic_documents(
                repository_id, provider.provider_id, provider.model_id,
                language, file_path, path_prefix, symbol_type,
            )):
                try:
                    vector = json.loads(row["vector_json"])
                except (TypeError, ValueError):
                    semantic_available = False
                    best.clear()
                    break
                similarity = _cosine(query_vector, vector)
                if similarity is None:
                    semantic_available = False
                    best.clear()
                    break
                item = (similarity, index, row)
                if len(best) < candidate_limit:
                    heapq.heappush(best, item)
                elif similarity > best[0][0]:
                    heapq.heapreplace(best, item)
            semantic_rows = [(score, row) for score, _, row in sorted(best, reverse=True)]
        except SearchUnavailableError:
            semantic_available = False
            if retrieval_method == "semantic":
                raise
        if retrieval_method == "semantic" and not semantic_available:
            raise SearchUnavailableError(503, "Semantic search is unavailable for this repository index")

    candidate_rows = [*lexical_rows, *symbol_rows, *(row for _, row in semantic_rows)]
    if candidate_rows:
        repository = store.get(repository_id)
        if repository is None:
            raise SearchUnavailableError(404, "Repository not found")
        _validate_candidate_files(repository["root_path"], candidate_rows)

    by_document: dict[int, dict[str, Any]] = {}
    lexical_raw: dict[int, float] = {}
    symbol_raw: dict[int, float] = {}
    semantic_raw: dict[int, float] = {}

    for row in lexical_rows:
        document_id = int(row["document_id"])
        by_document[document_id] = row
        lexical_raw[document_id] = max(0.0, -float(row["bm25_raw"]))
    for row in symbol_rows:
        document_id = int(row["document_id"])
        by_document.setdefault(document_id, row)
        symbol_raw[document_id] = _symbol_relevance(row["symbol_names"], terms)
    for score, row in semantic_rows:
        document_id = int(row["document_id"])
        by_document.setdefault(document_id, row)
        semantic_raw[document_id] = score

    lexical_scores = normalize_scores(lexical_raw)
    symbol_scores = normalize_scores(symbol_raw)
    semantic_scores = normalize_scores(semantic_raw) if semantic_available else {}
    active_components: list[str]
    if retrieval_method == "lexical":
        active_components = ["lexical"]
    elif retrieval_method == "symbol":
        active_components = ["symbol"]
    elif retrieval_method == "semantic":
        active_components = ["semantic"]
    else:
        active_components = ["lexical", "symbol"]
        if semantic_available:
            active_components.append("semantic")
    weight_total = sum(WEIGHTS[name] for name in active_components)
    effective_weights = {
        name: WEIGHTS[name] / weight_total for name in active_components
    }

    results: list[dict[str, Any]] = []
    for document_id, row in by_document.items():
        component_scores = {
            "lexical": lexical_scores.get(document_id, 0.0),
            "symbol": symbol_scores.get(document_id, 0.0),
            "semantic": semantic_scores.get(document_id, 0.0),
        }
        final_score = sum(
            WEIGHTS[name] * component_scores[name] for name in active_components
        ) / weight_total
        matched = []
        if document_id in lexical_raw:
            matched.append("lexical")
        if document_id in symbol_raw:
            matched.append("symbol")
        if document_id in semantic_raw:
            matched.append("semantic")
        results.append({
            "document_id": document_id,
            "repository_id": repository_id,
            "file_path": row["file_path"],
            "symbol_name": row["symbol_name"],
            "symbol_type": row["symbol_type"],
            "language": row["language"],
            "snippet": row["content"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "bm25_score": lexical_scores.get(document_id) if document_id in lexical_raw else None,
            "semantic_score": semantic_scores.get(document_id) if document_id in semantic_raw else None,
            "symbol_score": symbol_scores.get(document_id, 0.0),
            "final_score": final_score,
            "retrieval_methods": matched,
        })

    # A document is unique per indexed source region; this guard also collapses
    # same-range rows if an older or externally migrated index contains duplicates.
    unique: dict[tuple[str, int, int], dict[str, Any]] = {}
    for result in results:
        key = (result["file_path"], result["start_line"], result["end_line"])
        previous = unique.get(key)
        if previous is None:
            unique[key] = result
            continue
        if result["final_score"] > previous["final_score"]:
            unique[key] = result
        else:
            previous["retrieval_methods"] = sorted(
                set(previous["retrieval_methods"]) | set(result["retrieval_methods"])
            )

    ranked = sorted(
        unique.values(),
        key=lambda item: (-item["final_score"], item["file_path"].casefold(), item["start_line"]),
    )[:limit]
    for item in ranked:
        item.pop("document_id", None)
    available = ["lexical", "symbol"]
    if semantic_available:
        available.append("semantic")
    return {
        "repository_id": repository_id,
        "query": query,
        "retrieval_method": retrieval_method,
        "semantic_available": semantic_available,
        "available_methods": available,
        "effective_weights": effective_weights,
        "results": ranked,
    }
