from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_explanation import AIExplanationError, ExplanationService
from repository_ingestion import IngestionError, ingest_zip_archive, register_local_repository
from repository_scanner import (
    RepositoryScanError,
    get_scan_summary,
    resolve_repository_root,
    scan_registered_repository,
)
from repository_service import MAX_FILE_READ_BYTES, clone_repository
from repository_parse_service import (
    RepositoryParseError,
    get_parsing_summary,
    parse_registered_repository,
)
from repository_store import RepositoryStore
from search_service import SearchUnavailableError, search_repository

logger = logging.getLogger(__name__)
app = FastAPI(title="GitHub Repository Explorer API", version="2.2.0")
REPOSITORY_STORAGE = Path(
    os.getenv("REPOSITORY_STORAGE", str(Path(__file__).resolve().parent / "repositories"))
).expanduser().resolve()
class GitHubURLRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    url: str = Field(..., description="HTTPS URL for a GitHub repository")


class Repository(BaseModel):
    owner: str
    name: str
    url: str


class GitHubURLResponse(BaseModel):
    success: bool
    message: str
    repository: Repository


class CloneRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    url: str = Field(..., description="HTTPS URL for a public GitHub repository")


class LocalRepositoryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    path: str = Field(..., min_length=1, max_length=4096, description="Path to a local repository directory")


class RepositoryMetadataResponse(BaseModel):
    id: str
    name: str
    root_path: str
    input_type: str
    created_at: str
    status: str
    error_message: str | None = None


class RepositoryListResponse(BaseModel):
    total: int
    repositories: list[RepositoryMetadataResponse]


class FileEntryResponse(BaseModel):
    path: str
    name: str
    type: str
    size_bytes: int | None = None
    extension: str | None = None
    language: str | None = None
    modified_at: str | None = None
    is_hidden: bool
    is_included: bool
    exclusion_reason: str | None = None
    is_binary: bool = False
    is_generated: bool = False
    is_unsupported: bool = False
    is_oversized: bool = False


class RepositoryTreeResponse(BaseModel):
    repository_id: str
    repository_name: str
    scan_status: str
    scanned_at: str
    tree: list[dict[str, Any]]


class RepositorySummaryResponse(BaseModel):
    success: bool = True
    repository_id: str
    repository_name: str
    scan_status: str
    scanned_at: str
    cached: bool = False
    statistics: dict[str, Any]
    languages: dict[str, int]
    extensions: dict[str, int]
    tree: list[dict[str, Any]]


class RepositoryFilesResponse(BaseModel):
    repository_id: str
    offset: int
    limit: int
    total: int
    has_more: bool
    files: list[FileEntryResponse]


class RepositoryFileResponse(BaseModel):
    repository_id: str
    path: str
    content: str


class ParsingStatistics(BaseModel):
    total_files: int
    parsed_files: int
    cached_files: int
    skipped_files: int
    failed_files: int
    symbol_count: int
    import_count: int


class ParsingErrorResponse(BaseModel):
    file_path: str
    language: str
    reason: str
    message: str
    start_line: int | None = None
    created_at: str


class ParsingSkipResponse(BaseModel):
    file_path: str
    language: str
    reason: str


class ParsingSummaryResponse(BaseModel):
    success: bool
    repository_id: str
    repository_name: str
    status: str
    run_id: str | None
    scan_timestamp: str | None
    started_at: str | None
    completed_at: str | None
    statistics: ParsingStatistics
    error_message: str | None
    errors: list[ParsingErrorResponse]
    skipped: list[ParsingSkipResponse]


class SymbolResponse(BaseModel):
    symbol_id: str
    repository_id: str
    file_path: str
    symbol_name: str
    qualified_name: str
    symbol_type: Literal["class", "function", "method"]
    language: str
    parent_symbol_id: str | None
    parent_symbol: str | None
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    parameters: list[str]
    snippet: str
    snippet_truncated: bool


class SymbolsResponse(BaseModel):
    repository_id: str
    offset: int
    limit: int
    total: int
    has_more: bool
    symbols: list[SymbolResponse]


class RepositoryImportResponse(BaseModel):
    import_id: int
    repository_id: str
    file_path: str
    language: str
    module: str
    imported_symbol: str | None
    alias: str | None
    import_type: str
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    source_snippet: str


class ImportsResponse(BaseModel):
    repository_id: str
    offset: int
    limit: int
    total: int
    has_more: bool
    imports: list[RepositoryImportResponse]


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(10, ge=1, le=100)
    language: str | None = Field(None, min_length=1, max_length=50)
    file_path: str | None = Field(None, min_length=1, max_length=4096)
    path_prefix: str | None = Field(None, min_length=1, max_length=4096)
    symbol_type: Literal["class", "function", "method"] | None = None
    retrieval_method: Literal["hybrid", "lexical", "semantic", "symbol"] = "hybrid"

    @model_validator(mode="after")
    def validate_path_filter(self) -> SearchRequest:
        if self.file_path is not None and self.path_prefix is not None:
            raise ValueError("file_path and path_prefix cannot be used together")
        return self


class SearchResultResponse(BaseModel):
    repository_id: str
    file_path: str
    symbol_name: str | None
    symbol_type: Literal["class", "function", "method"] | None
    language: str
    snippet: str
    start_line: int
    end_line: int
    bm25_score: float | None
    semantic_score: float | None
    symbol_score: float
    final_score: float
    retrieval_methods: list[Literal["lexical", "semantic", "symbol"]]


class SearchResponse(BaseModel):
    repository_id: str
    query: str
    retrieval_method: Literal["hybrid", "lexical", "semantic", "symbol"]
    semantic_available: bool
    available_methods: list[Literal["lexical", "semantic", "symbol"]]
    effective_weights: dict[str, float]
    results: list[SearchResultResponse]


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(8, ge=1, le=20)
    retrieval_method: Literal["hybrid", "lexical", "semantic", "symbol"] = "hybrid"
    language: str | None = Field(None, min_length=1, max_length=50)
    file_path: str | None = Field(None, min_length=1, max_length=4096)
    path_prefix: str | None = Field(None, min_length=1, max_length=4096)
    symbol_type: Literal["class", "function", "method"] | None = None

    @model_validator(mode="after")
    def validate_path_filter(self) -> AskRequest:
        if self.file_path is not None and self.path_prefix is not None:
            raise ValueError("file_path and path_prefix cannot be used together")
        return self


class AskEvidenceResponse(BaseModel):
    evidence_id: str
    file_path: str
    symbol_name: str | None
    symbol_type: Literal["class", "function", "method"] | None
    language: str
    snippet: str
    start_line: int
    end_line: int
    snippet_truncated: bool
    retrieval_methods: list[Literal["lexical", "semantic", "symbol"]]


class AskClaimResponse(BaseModel):
    claim_id: str
    text: str
    evidence_ids: list[str]
    assessment_evidence_ids: list[str]
    verification: Literal[
        "model_assessed_supported", "model_assessed_contradicted",
        "model_assessed_not_established", "model_assessed_ambiguous",
    ]


class AskUsageResponse(BaseModel):
    role: Literal["answer_generator", "evidence_verifier"]
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    usage_available: bool
    cost: float | None
    cost_kind: Literal["estimated", "unavailable"]
    cost_available: bool
    pricing_basis: str | None
    status: Literal["succeeded", "failed"]


class AskResponse(BaseModel):
    repository_id: str
    question: str
    answer: str
    insufficient_evidence: bool
    claims: list[AskClaimResponse]
    evidence: list[AskEvidenceResponse]
    model: str | None
    generator_model: str | None
    verifier_model: str | None
    request_id: str
    usage: list[AskUsageResponse]


def get_store() -> RepositoryStore:
    return RepositoryStore(REPOSITORY_STORAGE / "repository_metadata.sqlite3")


@app.exception_handler(sqlite3.Error)
async def handle_database_error(_request: Any, exc: sqlite3.Error) -> JSONResponse:
    logger.exception("Repository metadata database operation failed", exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "Repository metadata is temporarily unavailable"})


def parse_github_repository_url(raw_url: str) -> Repository:
    if not raw_url:
        raise HTTPException(status_code=422, detail="URL must not be empty")
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid URL format") from exc
    if parsed.scheme.lower() != "https":
        raise HTTPException(status_code=422, detail="Only HTTPS URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(status_code=422, detail="Credentials are not allowed in the URL")
    if port is not None:
        raise HTTPException(status_code=422, detail="Ports are not allowed in the URL")
    if parsed.hostname is None or parsed.hostname.lower() != "github.com":
        raise HTTPException(status_code=422, detail="URL must belong to github.com")
    if parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="Query strings and fragments are not allowed")
    path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
    if path.endswith("/"):
        path = path[:-1]
    segments = path.split("/")
    if len(segments) != 2 or not all(segments):
        if segments and not segments[0]:
            detail = "Missing username"
        elif len(segments) == 1 or (len(segments) == 2 and not segments[1]):
            detail = "Missing repository name"
        else:
            detail = "Invalid repository path"
        raise HTTPException(status_code=422, detail=detail)
    owner, name = segments
    if name.endswith(".git"):
        name = name[:-4]
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner):
        raise HTTPException(status_code=422, detail="Invalid repository owner")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name.startswith(".") or name in {".", ".."}:
        raise HTTPException(status_code=422, detail="Invalid repository name")
    return Repository(owner=owner, name=name, url=f"https://github.com/{owner}/{name}")


def _raise_ingestion_http_error(exc: IngestionError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _raise_scan_http_error(exc: RepositoryScanError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _raise_parse_http_error(exc: RepositoryParseError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _normalize_relative_path(raw_path: str, allow_root: bool = False) -> str:
    if allow_root and raw_path == "":
        return ""
    if not raw_path or len(raw_path) > 4096 or "\x00" in raw_path or "\\" in raw_path:
        raise HTTPException(status_code=422, detail="Invalid repository-relative path")
    if raw_path.startswith("/") or re.match(r"^[A-Za-z]:", raw_path):
        raise HTTPException(status_code=422, detail="Repository path must be relative")
    parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=422, detail="Invalid repository-relative path")
    return PurePosixPath(*parts).as_posix()


def _repository_for_scan(repository_id: str, store: RepositoryStore) -> dict[str, Any]:
    repository = store.get(repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    if repository["status"] != "ready":
        raise HTTPException(status_code=409, detail="Repository is not ready")
    scan = store.get_scan(repository_id)
    if scan is None or scan["status"] != "complete":
        raise HTTPException(status_code=409, detail="Repository has not been scanned; POST to its /scan endpoint first")
    return repository


def _repository_for_current_parse(repository_id: str, store: RepositoryStore) -> None:
    _repository_for_scan(repository_id, store)
    parse = store.get_parse(repository_id)
    scan = store.get_scan(repository_id)
    if parse is None or parse["status"] != "complete":
        raise HTTPException(status_code=409, detail="Repository has not been parsed")
    if scan is None or parse["scan_timestamp"] != scan["scanned_at"]:
        raise HTTPException(status_code=409, detail="Repository scan changed; parse the repository again")


def _read_repository_file(repository_id: str, raw_path: str, store: RepositoryStore) -> dict[str, Any]:
    repository = _repository_for_scan(repository_id, store)
    relative_path = _normalize_relative_path(raw_path)
    entry = store.get_file_entry(repository_id, relative_path)
    if entry is None or entry["type"] != "file":
        raise HTTPException(status_code=404, detail="Repository file not found")
    if entry["exclusion_reason"] and entry["exclusion_reason"].casefold() == "excluded_directory:.git":
        raise HTTPException(status_code=404, detail="Repository file not found")

    root = resolve_repository_root(repository["root_path"])
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        is_junction = getattr(current, "is_junction", None)
        if current.is_symlink() or (is_junction and is_junction()):
            raise HTTPException(status_code=404, detail="Repository file not found")
    descriptor: int | None = None
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Repository file not found")
        expected_stat = resolved.stat(follow_symlinks=False)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or not os.path.samestat(expected_stat, opened_stat):
            raise HTTPException(status_code=404, detail="Repository file not found")
        with os.fdopen(descriptor, "rb") as file_handle:
            descriptor = None
            content_bytes = file_handle.read(MAX_FILE_READ_BYTES + 1)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Repository file not found") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(content_bytes) > MAX_FILE_READ_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the configured content retrieval limit")
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="File is not UTF-8 text") from exc
    return {"repository_id": repository_id, "path": relative_path, "content": content}


@app.post("/github", response_model=GitHubURLResponse)
def receive_github_url(request: GitHubURLRequest) -> GitHubURLResponse:
    return GitHubURLResponse(
        success=True,
        message="GitHub repository URL received successfully",
        repository=parse_github_repository_url(request.url),
    )


@app.post("/repositories/local", response_model=RepositoryMetadataResponse, status_code=201)
def register_local(request: LocalRepositoryRequest) -> dict[str, Any]:
    try:
        return register_local_repository(request.path, get_store())
    except IngestionError as exc:
        _raise_ingestion_http_error(exc)


@app.post("/repositories/upload", response_model=RepositoryMetadataResponse, status_code=201)
def upload_repository(file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        return ingest_zip_archive(file.filename, file.file, REPOSITORY_STORAGE, get_store())
    except IngestionError as exc:
        _raise_ingestion_http_error(exc)
    finally:
        file.file.close()


@app.get("/repositories", response_model=RepositoryListResponse)
def list_repositories() -> dict[str, Any]:
    repositories = get_store().list()
    return {"total": len(repositories), "repositories": repositories}


@app.get("/repositories/{repository_id}", response_model=RepositoryMetadataResponse)
def repository_metadata(repository_id: str) -> dict[str, Any]:
    record = get_store().get(repository_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return record


@app.post("/repositories/{repository_id}/scan", response_model=RepositorySummaryResponse)
def scan_repository_endpoint(
    repository_id: str,
    force: bool = Query(False, description="Rescan the filesystem instead of returning the saved scan"),
) -> dict[str, Any]:
    try:
        return scan_registered_repository(repository_id, get_store(), force=force)
    except RepositoryScanError as exc:
        _raise_scan_http_error(exc)


@app.post("/repositories/{repository_id}/parse", response_model=ParsingSummaryResponse)
def parse_repository_endpoint(
    repository_id: str,
    force: bool = Query(False, description="Reparse source files even when their content hashes are unchanged"),
) -> dict[str, Any]:
    try:
        return parse_registered_repository(repository_id, get_store(), force=force)
    except RepositoryParseError as exc:
        _raise_parse_http_error(exc)


@app.get("/repositories/{repository_id}/symbols", response_model=SymbolsResponse)
def repository_symbols(
    repository_id: str,
    name: str | None = Query(None, max_length=200, description="Case-insensitive substring of the symbol name"),
    symbol_type: Literal["class", "function", "method"] | None = Query(None),
    language: str | None = Query(None, max_length=50),
    file_path: str | None = Query(None, max_length=4096),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    store = get_store()
    _repository_for_current_parse(repository_id, store)
    normalized_path = _normalize_relative_path(file_path) if file_path is not None else None
    total, symbols = store.list_symbols(
        repository_id, name, symbol_type, language, normalized_path, offset, limit
    )
    return {
        "repository_id": repository_id,
        "offset": offset,
        "limit": limit,
        "total": total,
        "has_more": offset + len(symbols) < total,
        "symbols": symbols,
    }


@app.get("/repositories/{repository_id}/symbols/{symbol_id}", response_model=SymbolResponse)
def repository_symbol(repository_id: str, symbol_id: str) -> dict[str, Any]:
    store = get_store()
    _repository_for_current_parse(repository_id, store)
    symbol = store.get_symbol(repository_id, symbol_id)
    if symbol is None:
        raise HTTPException(status_code=404, detail="Repository symbol not found")
    return symbol


@app.get("/repositories/{repository_id}/imports", response_model=ImportsResponse)
def repository_imports(
    repository_id: str,
    language: str | None = Query(None, max_length=50),
    file_path: str | None = Query(None, max_length=4096),
    module: str | None = Query(None, max_length=500),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    store = get_store()
    _repository_for_current_parse(repository_id, store)
    normalized_path = _normalize_relative_path(file_path) if file_path is not None else None
    total, imports = store.list_imports(repository_id, language, normalized_path, module, offset, limit)
    return {
        "repository_id": repository_id,
        "offset": offset,
        "limit": limit,
        "total": total,
        "has_more": offset + len(imports) < total,
        "imports": imports,
    }


@app.post("/repositories/{repository_id}/search", response_model=SearchResponse)
def search_repository_endpoint(repository_id: str, request: SearchRequest) -> dict[str, Any]:
    store = get_store()
    _repository_for_current_parse(repository_id, store)
    normalized_file_path = _normalize_relative_path(request.file_path) if request.file_path else None
    normalized_path_prefix = _normalize_relative_path(request.path_prefix) if request.path_prefix else None
    try:
        return search_repository(
            repository_id,
            request.query,
            request.limit,
            store,
            request.retrieval_method,
            request.language,
            normalized_file_path,
            normalized_path_prefix,
            request.symbol_type,
        )
    except SearchUnavailableError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.post("/repositories/{repository_id}/ask", response_model=AskResponse)
def ask_repository_endpoint(repository_id: str, request: AskRequest) -> dict[str, Any]:
    store = get_store()
    _repository_for_current_parse(repository_id, store)
    normalized_file_path = _normalize_relative_path(request.file_path) if request.file_path else None
    normalized_path_prefix = _normalize_relative_path(request.path_prefix) if request.path_prefix else None
    try:
        return ExplanationService().explain(
            repository_id,
            request.question,
            request.limit,
            store,
            request.retrieval_method,
            request.language,
            normalized_file_path,
            normalized_path_prefix,
            request.symbol_type,
        )
    except AIExplanationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except SearchUnavailableError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/repositories/{repository_id}/parsing-summary", response_model=ParsingSummaryResponse)
def repository_parsing_summary(repository_id: str) -> dict[str, Any]:
    try:
        return get_parsing_summary(repository_id, get_store())
    except RepositoryParseError as exc:
        _raise_parse_http_error(exc)


@app.post("/repositories/clone", response_model=RepositorySummaryResponse)
def clone_and_summarize(request: CloneRequest) -> dict[str, Any]:
    repository = parse_github_repository_url(request.url)
    repository_id = f"repo_{uuid.uuid4().hex}"
    destination = REPOSITORY_STORAGE / repository_id
    store = get_store()
    store.create(repository_id, repository.name, destination, "github", datetime.now(timezone.utc).isoformat())
    try:
        clone_repository(repository.url, destination)
        store.set_status(repository_id, "ready")
    except (RuntimeError, OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        store.set_status(repository_id, "failed", "Repository clone failed")
        logger.warning("Repository clone failed for %s", repository_id, exc_info=True)
        raise HTTPException(status_code=502, detail="Repository clone failed") from exc
    try:
        result = scan_registered_repository(repository_id, store, force=True)
    except RepositoryScanError as exc:
        _raise_scan_http_error(exc)
    return result


@app.get("/repositories/{repository_id}/summary", response_model=RepositorySummaryResponse)
def repository_summary(repository_id: str) -> dict[str, Any]:
    repository = _repository_for_scan(repository_id, get_store())
    try:
        return get_scan_summary(repository_id, repository["name"], get_store())
    except RepositoryScanError as exc:
        _raise_scan_http_error(exc)


@app.get("/repositories/{repository_id}/tree", response_model=RepositoryTreeResponse)
def repository_tree(repository_id: str) -> dict[str, Any]:
    repository = _repository_for_scan(repository_id, get_store())
    try:
        summary = get_scan_summary(repository_id, repository["name"], get_store())
    except RepositoryScanError as exc:
        _raise_scan_http_error(exc)
    return {key: summary[key] for key in ("repository_id", "repository_name", "scan_status", "scanned_at", "tree")}


@app.get("/repositories/{repository_id}/files", response_model=RepositoryFilesResponse)
def repository_files(
    repository_id: str,
    path: str | None = Query(None, max_length=4096, description="Optional relative directory path prefix"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    include_excluded: bool = Query(True),
) -> dict[str, Any]:
    _repository_for_scan(repository_id, get_store())
    path_prefix = _normalize_relative_path(path, allow_root=True) if path is not None else None
    if path_prefix:
        directory = get_store().get_file_entry(repository_id, path_prefix)
        if directory is None or directory["type"] != "directory":
            raise HTTPException(status_code=404, detail="Repository directory not found")
    total, files = get_store().get_file_entries(repository_id, offset, limit, path_prefix, include_excluded)
    return {
        "repository_id": repository_id,
        "offset": offset,
        "limit": limit,
        "total": total,
        "has_more": offset + len(files) < total,
        "files": files,
    }


@app.get("/repositories/{repository_id}/file", response_model=RepositoryFileResponse)
def repository_file_by_query(
    repository_id: str,
    path: str = Query(..., min_length=1, max_length=4096),
) -> dict[str, Any]:
    return _read_repository_file(repository_id, path, get_store())


@app.get("/repositories/{repository_id}/files/{file_path:path}", response_model=None, include_in_schema=False)
def repository_file_legacy(repository_id: str, file_path: str) -> dict[str, Any]:
    """Backward-compatible alias for clients using the original file route."""
    result = _read_repository_file(repository_id, file_path, get_store())
    return {"path": result["path"], "content": result["content"]}
