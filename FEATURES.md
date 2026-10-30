# Feature Status

Status labels use the exact categories required by `AGENTS.md`. Verification refers to automated tests in the current suite.

## Phase 1 — Repository Ingestion

| Feature | Status | Details |
|---|---|---|
| Local repository directory registration | IMPLEMENTED AND VERIFIED | Validates and stores a canonical server-side path without copying. |
| ZIP archive upload and safe extraction | IMPLEMENTED AND VERIFIED | Validates entries, rejects traversal/symlinks/special types, applies extraction limits, and cleans failed staging data. |
| Unique repository IDs and metadata persistence | IMPLEMENTED AND VERIFIED | UUID-backed IDs and SQLite repository metadata with ingestion status. |
| Repository list/detail APIs | IMPLEMENTED AND VERIFIED | `GET /repositories` and `GET /repositories/{id}` read persisted rows. |

## Phase 2 — Repository Scanning and File Explorer

| Feature | Status | Details |
|---|---|---|
| Repository-ID scan operation | IMPLEMENTED AND VERIFIED | `POST /repositories/{id}/scan`; returns cached scan by default and supports `force=true`. GitHub clone route performs its scan immediately. |
| Recursive file and directory discovery | IMPLEMENTED AND VERIFIED | Stores relative POSIX paths, sizes, modification times, extensions, hidden status, and directory entries. Includes empty folders and excluded tree nodes. |
| Duplicate scan and record protection | IMPLEMENTED AND VERIFIED | Completed snapshots are reused unless forced; SQLite primary key and transactional replacement prevent duplicate rows. |
| Configurable directory exclusions | IMPLEMENTED AND VERIFIED | Defaults cover `.git`, `node_modules`, `venv`, `.venv`, `__pycache__`, `dist`, `build`, `coverage`, `.next`, `.cache`; configure with `REPOSITORY_EXCLUDED_DIRECTORIES`. |
| Generated file filtering | IMPLEMENTED AND VERIFIED | Configurable globs such as minified, map, bytecode, and generated-name patterns; reason is retained. |
| Binary/unsupported/oversized file handling | IMPLEMENTED AND VERIFIED | Binaries use extension and bounded-sample detection; unknown extensions and files over the configurable size are excluded with flags/reasons. |
| Language detection | IMPLEMENTED AND VERIFIED | Extension mappings cover Python, JavaScript, TypeScript, Java, C/C++, C#, Go, Rust, PHP, Ruby, HTML, CSS, JSON, YAML, Markdown, and SQL; unknown is explicit. |
| Repository statistics | IMPLEMENTED AND VERIFIED | File/directory totals, included/excluded counts, repository/included byte totals, languages, extensions, and source/binary/generated/unsupported/oversized counts. |
| Hierarchical file tree | IMPLEMENTED AND VERIFIED | Stable folder-first alphabetical ordering, nested paths, metadata, hidden flags, and exclusion reasons. |
| Paginated file metadata listing | IMPLEMENTED AND VERIFIED | `GET /repositories/{id}/files` supports offset/limit, path prefix, and included-only filtering. |
| Safe file content retrieval | IMPLEMENTED AND VERIFIED | `GET /repositories/{id}/file?path=...` validates scanned relative paths, symlink/junction components, root containment, UTF-8, and response size. Existing `/files/{path}` remains a compatibility alias. |
| Symlink/junction handling | IMPLEMENTED AND VERIFIED | Links are represented as excluded entries and never traversed; external junction test passes on Windows. |

## Phase 3 — Code Parsing and Structural Indexing

| Feature | Status | Details |
|---|---|---|
| Tree-sitter grammar integration | IMPLEMENTED AND VERIFIED | Individually pinned parser and grammar wheels are configured for Python, JavaScript, TypeScript/TSX, Java, C, C++, C#, Go, Rust, PHP, and Ruby; tested against each configured grammar. |
| Class/function/method extraction | IMPLEMENTED AND VERIFIED | Extracts supported syntax declarations, nested symbols, parameters, parent symbol IDs, and qualified names. Go receiver methods and Rust `impl` methods link to local type symbols when available. |
| Import extraction | IMPLEMENTED AND VERIFIED | Stores source-level import/include/use statements and their locations; tests cover all configured languages. These are not runtime dependency claims. |
| Source locations and snippets | IMPLEMENTED AND VERIFIED | Uses Tree-sitter byte ranges, reports one-based lines and character columns, stores relative paths and bounded snippets. |
| Parse status, error and unsupported-file persistence | IMPLEMENTED AND VERIFIED | Persists per-run summary, per-file parse status/reason, recoverable syntax errors, and skipped-language reasons in SQLite. |
| Hash-based incremental parse | IMPLEMENTED AND VERIFIED | Reuses results when SHA-256 content hash and language match; `force=true` reparses unchanged files. Results are aligned to the latest scan timestamp. |
| Structural indexing APIs | IMPLEMENTED AND VERIFIED | Adds parse, symbol listing/detail, imports, and parsing-summary endpoints with filters and pagination. |
| Parsing safety and resource limits | IMPLEMENTED AND VERIFIED | Reads only scanner-indexed eligible files, rejects symlinks/path traversal, caps per-file bytes, file count, and parse-run bytes; repository code is never executed. |

## Existing GitHub and later phases

| Feature | Status | Details |
|---|---|---|
| GitHub URL validation | IMPLEMENTED AND VERIFIED | `POST /github` validates and normalizes HTTPS GitHub repository URLs. |
| GitHub clone | IMPLEMENTED, NOT FULLY VERIFIED | Uses Git CLI with argument list and timeout; no live network clone was part of Phase 2 verification. |
| Phase 4 search and retrieval | PLANNED | Keyword, lexical, semantic, and hybrid retrieval are absent. |
| Phase 5 AI explanation | PLANNED | Featherless and evidence-grounded explanation are absent. |
| Phase 6 frontend/dashboard | PLANNED | No frontend exists. |
| Phase 7 evaluation/integration | PLANNED | No retrieval benchmark or full-system evaluation exists. |
| Phase 8 agentic investigation | PLANNED | No orchestrator, tool loop, evidence verification, or investigation history exists. |

No Phase 3–8 functionality is claimed as complete.
