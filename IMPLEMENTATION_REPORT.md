# Repository Explorer Implementation Report — Phases 1 and 2

## Documents and scope

Read `SPEC.md` and `AGENTS.md` before implementation. At inspection time, `ARCH.md` and `FEATURES.md` did not exist; they have now been created to document the actual implementation and verified status. `SPEC.md` was not changed because no requirement or design decision was changed.

The overall goal in `SPEC.md` is an agentic code exploration platform that ingests repositories, parses and indexes source code, retrieves evidence, investigates through bounded tool calls, verifies findings, and produces evidence-grounded explanations. The specification divides delivery into eight phases. Phase 1 and Phase 2 are now implemented; Phases 3–8 remain planned.

## Inspected project structure

Before changes, the project contained:

- `main.py`: FastAPI API and GitHub URL validation, clone, summary/tree and file retrieval routes.
- `repository_service.py`: Git CLI clone and filesystem scan/statistics.
- `tests/test_main.py` and `tests/test_repository_summary.py`: 17 baseline tests.
- `requirements.txt`, `README.md`, `SPEC.md`, and `AGENTS.md`.
- No `ARCH.md` or `FEATURES.md`.
- Two local virtual-environment directories (`.venv` and `.venv-1`); they are environment artifacts, not application modules.

The directory is not a Git repository, so `git status` is unavailable. There was no frontend, database schema/model layer, or persistent metadata storage before this change. Baseline verification before Phase 1 changes: **17 tests passed**.

## Existing implementation and status

| Feature | Status at inspection | Notes |
|---|---|---|
| GitHub URL validation (`POST /github`) | IMPLEMENTED AND VERIFIED | Validates HTTPS GitHub host/path and normalizes URLs. |
| GitHub clone and scan (`POST /repositories/clone`) | PARTIALLY IMPLEMENTED | Clone plus scan existed; metadata was not persisted. Clone itself was mock-tested rather than live-network tested. |
| Summary/tree endpoints | PARTIALLY IMPLEMENTED | Available for clone records held in process memory only. |
| File-content endpoint | PARTIALLY IMPLEMENTED | Bounded UTF-8 retrieval for clone records. |
| Local repository registration | NOT IMPLEMENTED | No route or validation. |
| ZIP upload/extraction | NOT IMPLEMENTED | No multipart dependency, route, or Zip Slip protections. |
| Persistent metadata/status | NOT IMPLEMENTED | Repository records/summaries were process-local; no database. |
| Frontend, parsing, indexing, search, AI, agent loop | PLANNED | No corresponding modules were present. |

Existing code uses Python, FastAPI, Pydantic, Uvicorn, pytest, and HTTPX. The scanner was limited to GitHub clones and excluded `.git`; it did not implement the full Phase 2 exclusion policy. GitHub URL parsing is kept in `main.py`; no database model duplication existed. The two virtual environments are redundant-looking local setup artifacts and were left untouched.


## Technical problems found during inspection

- Repository information and GitHub scan snapshots were held only in memory; a process restart lost records.
- Local paths and ZIP uploads were not supported, so Phase 1 acceptance criteria were unmet.
- ZIP safety and archive-size/count limits were absent.
- The existing scan exclusions covered `.git` only and did not meet Phase 2’s default filtering requirements.
- The summary/tree/file endpoints only worked for GitHub clones, not all ingestion types.
- `ARCH.md` and `FEATURES.md` were missing; no database models or schemas existed.
- No frontend or Phase 3–8 modules were present. No source-level duplicate implementations were found. Two virtual environment directories are workspace artifacts and were left unchanged.

## Dependencies

The existing project already declared FastAPI, Uvicorn, Pydantic through FastAPI, pytest, and HTTPX. Phase 1 ZIP form upload requires `python-multipart`, which was added to `requirements.txt` and installed in `.venv`. SQLite is provided by the Python standard library, so no database dependency was added. No Tree-sitter, embedding, LLM, or frontend dependencies are needed for Phase 1.

## Recommended implementation sequence

1. **Phase 1 — Repository Ingestion:** local path validation, bounded safe ZIP extraction, unique IDs, durable metadata/status, and error handling (implemented here).
2. **Phase 2 — Scanning and File Explorer:** configurable filters, language detection, scan all registered roots, and safe tree/file routes (implemented in this update).
3. **Phase 3 — Parsing and Structural Indexing:** supported Tree-sitter grammars, symbols/imports/snippets with exact locations, and indexing schema.
4. **Phase 4 — Search and Retrieval:** keyword/symbol retrieval, lexical ranking, then optional semantic and hybrid retrieval.
5. **Phase 5 — AI Explanation:** backend-only Featherless integration with bounded retrieved evidence and citations.
6. **Phase 6 — Frontend:** ingestion, repository dashboard, tree/file viewer, search, and investigation views.
7. **Phase 7 — Evaluation and Integration:** regression/security coverage, retrieval question set, agent behavior evaluation, and system integration.
8. **Phase 8 — Agentic Investigation:** bounded orchestrator/tool loop, evidence verification, execution limits, and history.

Do not begin later phases until their inputs and API boundaries are stable; do not combine parsing, retrieval, and AI work into Phase 1.

## Phase 1 implementation plan (historical)

1. Add a metadata store with unique IDs and durable `processing`, `ready`, and `failed` statuses.
2. Add local repository validation and registration without copying or executing user code.
3. Add multipart ZIP upload with a private staging directory, strict path/type validation, bounded streaming extraction, atomic placement, and cleanup on failure.
4. Add list/detail APIs so persisted metadata and failures can be inspected after restart.
5. Preserve the current GitHub/scan API behavior while recording its metadata in the new store.
6. Add unit/API tests for success, invalid paths/archives, traversal, archive limits, unique IDs, cleanup, and persistence; run the app and verify actual HTTP requests.

## Phase 1 implementation

Implemented local directory registration and ZIP uploads, including request/archive validation, UUID repository IDs, extraction limits, traversal and symlink protections, safe streaming extraction, staging cleanup, and persistent processing metadata. SQLite is used only for Phase 1 repository metadata; no file/symbol indexing tables were added. Added list and detail routes to inspect persisted status. Existing GitHub routes remain available and now also persist basic metadata.

At the end of the Phase 1 work, scanning was GitHub-only. Phase 2 now adds persisted scans for registered local, ZIP, and GitHub repositories. No Tree-sitter, AST parsing, symbol extraction, search, AI, agent, or frontend work has been added.

## Files created or modified

Created:

- `repository_ingestion.py`
- `repository_store.py`
- `tests/test_repository_ingestion.py`
- `ARCH.md`
- `FEATURES.md`
- `IMPLEMENTATION_REPORT.md`

Modified:

- `main.py`
- `requirements.txt` — added `python-multipart` for FastAPI multipart upload handling.
- `README.md`

`SPEC.md` and `AGENTS.md` were read and left unchanged.

## Endpoints

- `POST /repositories/local` — register an existing server-side directory.
- `POST /repositories/upload` — upload and safely extract a ZIP archive.
- `GET /repositories` — list persisted metadata.
- `GET /repositories/{repository_id}` — retrieve persisted metadata/status.

Existing GitHub validation, clone, summary, tree, and file routes remain. Ingestion metadata includes ID, name, root path, input type, UTC timestamp, status, and optional safe error message.

## Dependencies

Added `python-multipart>=0.0.9,<1.0` to `requirements.txt` and installed it into the project `.venv` for integration verification. No database package was added; SQLite is from Python’s standard library.

## Verification results

- Phase 1 verification: `python -m pytest -q` recorded **26 passed**, plus a Starlette deprecation warning from FastAPI TestClient using HTTPX. A local directory and ZIP were exercised through Uvicorn, and metadata was verified after process restart.
- Phase 2 verification: `python -m pytest -q` — **36 passed**, with one Windows junction test exercising external symlink protection and one Starlette deprecation warning. Coverage includes recursive metadata scans, filters, languages, tree/list/content APIs, cache/force behavior, traversal protection, missing resources, and Phase 1 regression tests.
- Started the FastAPI app with Uvicorn on `127.0.0.1:8765` and a temporary storage directory.
- HTTP `POST /repositories/local` returned `201`; metadata reported `input_type=local` and `status=ready`.
- HTTP `POST /repositories/upload` returned `201`; extracted nested ZIP files were present and metadata reported `input_type=zip` and `status=ready`.
- Stopped and restarted the API using the same metadata database; `GET /repositories/{id}` returned both records, confirming persistence across process restart.
- Automated tests verify invalid archives, ZIP traversal/absolute/Windows/symlink entries, extraction limits, failure cleanup, and metadata persistence.

## Known limitations

- The API is synchronous and intended for a trusted local environment; authentication/authorization is absent.
- Local repositories are registered in place and must be accessible from the API server.
- Ingestion metadata and scan/file metadata persist in SQLite. File contents remain on their source filesystem. ZIP and cloned roots are stored in the configured repository storage.
- Scan snapshots are cached until a client requests `force=true`; local content/filter changes do not automatically invalidate cached scans.
- Scanning is synchronous and bounded by file/entry limits.
- Language detection is extension based and file content retrieval is UTF-8 only with a size limit.
- No live GitHub clone was performed for this task.

## Phase 2 implementation update

Added `repository_scanner.py` and extended `repository_store.py` with scan, statistics, and per-path metadata tables. Scan requests use repository IDs, persist relative paths and file metadata, classify languages and exclusions, and replace scan rows transactionally. Repeated scans reuse completed records unless `force=true`; GitHub clone responses still include an immediate scan. Added paginated file metadata and query-based content routes while retaining the legacy content URL.

Phase 2 files created or modified:

- Created `repository_scanner.py` and `tests/test_repository_scanner.py`.
- Modified `main.py`, `repository_store.py`, `repository_service.py`, `tests/test_repository_summary.py`, `README.md`, `ARCH.md`, and `FEATURES.md`.

The scanner uses configurable exclusion directories, generated-file patterns, file-size and scan-count limits. It records excluded entries and reasons without deleting files, classifies binary files with a bounded sample, and marks unknown extensions as unsupported. Symlinks and Windows junctions are recorded but never traversed. File content reads validate scanned relative paths and root containment, reject link components, enforce the response size limit, and require UTF-8.
