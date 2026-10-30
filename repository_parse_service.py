"""Repository-wide parsing workflow, safe source reads, and parse summaries."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import embedding_provider
from code_parser import GRAMMARS, parse_source
from repository_scanner import MAX_FILE_SIZE_BYTES, RepositoryScanError, resolve_repository_root
from repository_store import RepositoryStore
from search_index import build_search_documents


logger = logging.getLogger(__name__)
MAX_PARSE_FILE_BYTES = int(os.getenv("REPOSITORY_MAX_PARSE_FILE_BYTES", str(MAX_FILE_SIZE_BYTES)))
MAX_PARSE_FILES = int(os.getenv("REPOSITORY_MAX_PARSE_FILES", "10000"))
MAX_PARSE_TOTAL_BYTES = int(os.getenv("REPOSITORY_MAX_PARSE_TOTAL_BYTES", str(250 * 1024 * 1024)))


class RepositoryParseError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SourceReadError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _embedding_data(
    provider: embedding_provider.EmbeddingProvider | None,
    documents: list[dict[str, Any]],
) -> tuple[list[list[float]], str | None, str | None]:
    if provider is None or not documents:
        return [], None, None
    texts = [
        f"File: {item['file_path']}\nLanguage: {item['language']}\n"
        f"Symbols: {item['symbol_names']}\n{item['content']}"
        for item in documents
    ]
    vectors: list[list[float]] = []
    try:
        for offset in range(0, len(texts), 32):
            vectors.extend(provider.embed(texts[offset:offset + 32]))
        if len(vectors) != len(texts):
            raise embedding_provider.EmbeddingProviderError("Unexpected embedding count")
        if vectors and any(
            not vector
            or len(vector) != len(vectors[0])
            or any(not math.isfinite(float(value)) for value in vector)
            for vector in vectors
        ):
            raise embedding_provider.EmbeddingProviderError("Invalid or inconsistent embedding vectors")
    except Exception:
        logger.warning("Embedding generation failed; lexical and symbol search remain available")
        return [], None, None
    return vectors, provider.provider_id, provider.model_id


def _validate_indexed_path(raw_path: str) -> PurePosixPath:
    if not raw_path or "\x00" in raw_path or "\\" in raw_path:
        raise SourceReadError("invalid_repository_path", "Indexed file path is invalid")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SourceReadError("invalid_repository_path", "Indexed file path is invalid")
    return path


def _read_source(root_path: str, relative_path: str) -> bytes:
    """Read a bounded regular file without following links or leaving the root."""
    relative = _validate_indexed_path(relative_path)
    try:
        root = resolve_repository_root(root_path)
        candidate = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            is_junction = getattr(current, "is_junction", None)
            if current.is_symlink() or (is_junction is not None and is_junction()):
                raise SourceReadError("symbolic_link_not_followed", "Symbolic links are not parsed")

        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise SourceReadError("path_outside_repository", "File is outside the registered repository")
        expected_stat = resolved.stat(follow_symlinks=False)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        try:
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode) or not os.path.samestat(expected_stat, opened_stat):
                raise SourceReadError("file_changed_during_read", "File changed while it was being read")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                content = handle.read(MAX_PARSE_FILE_BYTES + 1)
                after_stat = os.fstat(handle.fileno())
                current_resolved = candidate.resolve(strict=True)
                current_path_stat = current_resolved.stat(follow_symlinks=False)
                if (
                    not _same_file_state(opened_stat, after_stat)
                    or current_resolved != resolved
                    or not os.path.samestat(opened_stat, current_path_stat)
                ):
                    raise SourceReadError("file_changed_during_read", "File changed while it was being read")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except SourceReadError:
        raise
    except (OSError, RepositoryScanError) as exc:
        raise SourceReadError("file_unavailable", "Source file is unavailable") from exc
    if len(content) > MAX_PARSE_FILE_BYTES:
        raise SourceReadError("file_size_limit", f"File exceeds the {MAX_PARSE_FILE_BYTES}-byte parse limit")
    return content


def _same_file_state(before: os.stat_result, after: os.stat_result) -> bool:
    """Detect replacement or metadata-visible changes during a bounded read."""
    return (
        os.path.samestat(before, after)
        and before.st_size == after.st_size
        and getattr(before, "st_mtime_ns", before.st_mtime) == getattr(after, "st_mtime_ns", after.st_mtime)
        and getattr(before, "st_ctime_ns", before.st_ctime) == getattr(after, "st_ctime_ns", after.st_ctime)
    )


def _repository_for_parse(repository_id: str, store: RepositoryStore) -> dict[str, Any]:
    repository = store.get(repository_id)
    if repository is None:
        raise RepositoryParseError(404, "Repository not found")
    if repository["status"] != "ready":
        raise RepositoryParseError(409, "Repository is not ready to parse")
    scan = store.get_scan(repository_id)
    if scan is None or scan["status"] != "complete":
        raise RepositoryParseError(409, "Repository has not been scanned; POST to its /scan endpoint first")
    repository["_scan_timestamp"] = scan["scanned_at"]
    return repository


def get_parsing_summary(repository_id: str, store: RepositoryStore) -> dict[str, Any]:
    repository = store.get(repository_id)
    if repository is None:
        raise RepositoryParseError(404, "Repository not found")
    parse = store.get_parse(repository_id)
    if parse is None:
        raise RepositoryParseError(409, "Repository has not been parsed")
    scan = store.get_scan(repository_id)
    scan_is_current = scan is not None and parse["scan_timestamp"] == scan["scanned_at"]
    return {
        "success": parse["status"] == "complete" and scan_is_current,
        "repository_id": repository_id,
        "repository_name": repository["name"],
        "status": parse["status"],
        "run_id": parse["run_id"],
        "scan_timestamp": parse["scan_timestamp"],
        "started_at": parse["started_at"],
        "completed_at": parse["completed_at"],
        "statistics": {
            "total_files": parse["total_files"],
            "parsed_files": parse["parsed_files"],
            "cached_files": parse["cached_files"],
            "skipped_files": parse["skipped_files"],
            "failed_files": parse["failed_files"],
            "symbol_count": parse["symbol_count"],
            "import_count": parse["import_count"],
        },
        "error_message": parse["error_message"] or (
            "Repository scan changed; parse the repository again" if not scan_is_current else None
        ),
        "errors": store.list_parse_errors(repository_id),
        "skipped": store.list_skipped_parse_files(repository_id),
    }


def parse_registered_repository(
    repository_id: str,
    store: RepositoryStore,
    force: bool = False,
) -> dict[str, Any]:
    repository = _repository_for_parse(repository_id, store)
    run_id = f"parse_{uuid.uuid4().hex}"
    started_at = _now()
    if not store.begin_parse(repository_id, run_id, repository["_scan_timestamp"], started_at):
        raise RepositoryParseError(409, "A parse operation is already in progress for this repository")

    counts = {
        "total_files": 0,
        "parsed_files": 0,
        "cached_files": 0,
        "skipped_files": 0,
        "failed_files": 0,
        "symbol_count": 0,
        "import_count": 0,
    }
    attempted_files = 0
    total_source_bytes = 0
    cached_paths: list[str] = []
    try:
        provider = embedding_provider.get_embedding_provider()
    except Exception:
        logger.warning("Embedding provider configuration is invalid; semantic search is disabled")
        provider = None
    try:
        entries = [entry for entry in store.get_entries(repository_id) if entry["type"] == "file"]
        previous_files = store.get_parsed_files(repository_id)
        counts["total_files"] = len(entries)
        for entry in entries:
            path = entry["path"]
            language = entry.get("language") or "unknown"
            existing = previous_files.get(path)

            skip_reason: str | None = None
            if not entry.get("is_included", False):
                skip_reason = f"excluded_by_scanner:{entry.get('exclusion_reason') or 'excluded'}"
            elif entry.get("is_binary"):
                skip_reason = "binary_file"
            elif entry.get("size_bytes", 0) > MAX_PARSE_FILE_BYTES:
                skip_reason = "file_size_limit"
            elif attempted_files >= MAX_PARSE_FILES:
                skip_reason = "parse_file_count_limit"
            elif total_source_bytes + (entry.get("size_bytes") or 0) > MAX_PARSE_TOTAL_BYTES:
                skip_reason = "parse_total_size_limit"
            elif language not in GRAMMARS:
                skip_reason = f"unsupported_language:{language}"

            if skip_reason is not None:
                if skip_reason.startswith("unsupported_language:"):
                    attempted_files += 1
                    try:
                        source = _read_source(repository["root_path"], path)
                        text = source.decode("utf-8", errors="strict")
                        if total_source_bytes + len(source) > MAX_PARSE_TOTAL_BYTES:
                            store.save_parsed_file(
                                repository_id, path, None, language, "skipped", "parse_total_size_limit",
                                run_id, _now(), [], [],
                            )
                            counts["skipped_files"] += 1
                            continue
                        content_hash = hashlib.sha256(source).hexdigest()
                        total_source_bytes += len(source)
                        if (
                            not force and existing is not None
                            and existing["content_hash"] == content_hash
                            and existing["language"] == language
                            and existing["status"] == "skipped"
                            and existing["reason"] == skip_reason
                        ):
                            if not store.search_index_is_current(
                                repository_id,
                                path,
                                content_hash,
                                provider.provider_id if provider else None,
                                provider.model_id if provider else None,
                            ):
                                documents = build_search_documents(text, [], path, language, content_hash)
                                vectors, provider_id, model_id = _embedding_data(provider, documents)
                                store.save_search_index_for_cached_file(
                                    repository_id, path, content_hash, documents,
                                    vectors, provider_id, model_id,
                                )
                            cached_paths.append(path)
                            counts["cached_files"] += 1
                            continue
                        documents = build_search_documents(text, [], path, language, content_hash)
                        vectors, provider_id, model_id = _embedding_data(provider, documents)
                        store.save_parsed_file(
                            repository_id, path, content_hash, language, "skipped", skip_reason,
                            run_id, _now(), [], [], None, documents, vectors, provider_id, model_id,
                        )
                        counts["skipped_files"] += 1
                    except SourceReadError as exc:
                        store.save_parsed_file(
                            repository_id, path, None, language, "failed", exc.reason,
                            run_id, _now(), [], [],
                            {"reason": exc.reason, "message": exc.message, "start_line": None},
                        )
                        counts["failed_files"] += 1
                    except UnicodeDecodeError:
                        reason = "unsupported_encoding"
                        store.save_parsed_file(
                            repository_id, path, None, language, "failed", reason,
                            run_id, _now(), [], [],
                            {"reason": reason, "message": "Source file is not valid UTF-8 text", "start_line": None},
                        )
                        counts["failed_files"] += 1
                    continue
                if (
                    not force and existing is not None and existing["status"] == "skipped"
                    and existing["reason"] == skip_reason and existing["language"] == language
                ):
                    cached_paths.append(path)
                    counts["cached_files"] += 1
                else:
                    store.save_parsed_file(
                        repository_id, path, None, language, "skipped", skip_reason,
                        run_id, _now(), [], [],
                    )
                    counts["skipped_files"] += 1
                continue

            attempted_files += 1
            try:
                source = _read_source(repository["root_path"], path)
                source.decode("utf-8", errors="strict")
                if total_source_bytes + len(source) > MAX_PARSE_TOTAL_BYTES:
                    reason = "parse_total_size_limit"
                    store.save_parsed_file(
                        repository_id, path, None, language, "skipped", reason,
                        run_id, _now(), [], [],
                    )
                    counts["skipped_files"] += 1
                    continue
                content_hash = hashlib.sha256(source).hexdigest()
                total_source_bytes += len(source)
                if (
                    not force and existing is not None
                    and existing["content_hash"] == content_hash
                    and existing["language"] == language
                    and existing["status"] in {"complete", "partial"}
                ):
                    if not store.search_index_is_current(
                        repository_id,
                        path,
                        content_hash,
                        provider.provider_id if provider else None,
                        provider.model_id if provider else None,
                    ):
                        symbols = store.get_symbols_for_file(repository_id, path)
                        documents = build_search_documents(
                            source.decode("utf-8"), symbols, path, language, content_hash,
                        )
                        vectors, provider_id, model_id = _embedding_data(provider, documents)
                        store.save_search_index_for_cached_file(
                            repository_id, path, content_hash, documents, vectors, provider_id, model_id,
                        )
                    cached_paths.append(path)
                    counts["cached_files"] += 1
                    counts["symbol_count"] += existing["symbol_count"]
                    counts["import_count"] += existing["import_count"]
                    continue

                parsed = parse_source(source, language, repository_id, path)
                documents = build_search_documents(
                    source.decode("utf-8"), parsed.symbols, path, language, content_hash,
                )
                vectors, provider_id, model_id = _embedding_data(provider, documents)
                status = "partial" if parsed.has_syntax_errors else "complete"
                error = None
                reason = None
                if parsed.has_syntax_errors:
                    reason = "syntax_error"
                    error = {
                        "reason": reason,
                        "message": f"Tree-sitter reported {parsed.syntax_error_count} syntax error(s); recovered structures may be incomplete.",
                        "start_line": None,
                    }
                store.save_parsed_file(
                    repository_id, path, content_hash, language, status, reason,
                    run_id, _now(), parsed.symbols, parsed.imports, error,
                    documents, vectors, provider_id, model_id,
                )
                counts["parsed_files"] += 1
                counts["symbol_count"] += len(parsed.symbols)
                counts["import_count"] += len(parsed.imports)
            except SourceReadError as exc:
                logger.info("Could not parse repository file %s: %s", path, exc.reason)
                store.save_parsed_file(
                    repository_id, path, None, language, "failed", exc.reason,
                    run_id, _now(), [], [],
                    {"reason": exc.reason, "message": exc.message, "start_line": None},
                )
                counts["failed_files"] += 1
            except UnicodeDecodeError:
                reason = "unsupported_encoding"
                store.save_parsed_file(
                    repository_id, path, None, language, "failed", reason,
                    run_id, _now(), [], [],
                    {"reason": reason, "message": "Source file is not valid UTF-8 text", "start_line": None},
                )
                counts["failed_files"] += 1
            except Exception as exc:  # Individual parser failures must not abort other files.
                logger.exception("Parser failed for repository file %s", path)
                reason = "parser_error"
                store.save_parsed_file(
                    repository_id, path, None, language, "failed", reason,
                    run_id, _now(), [], [],
                    {"reason": reason, "message": "The source file could not be parsed", "start_line": None},
                )
                counts["failed_files"] += 1

        completed_at = _now()
        store.mark_parsed_files_seen(repository_id, cached_paths, run_id, completed_at)
        store.complete_parse(repository_id, run_id, completed_at, counts)
    except RepositoryParseError:
        store.fail_parse(repository_id, "Parsing could not be completed", _now())
        raise
    except Exception as exc:
        logger.exception("Repository parse failed for %s", repository_id)
        store.fail_parse(repository_id, "Repository parsing failed", _now())
        raise RepositoryParseError(500, "Repository parsing failed") from exc

    return get_parsing_summary(repository_id, store)
