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
| Phase 4 search and retrieval | IMPLEMENTED AND VERIFIED | Repository-scoped BM25 lexical search, symbol relevance, optional embeddings, and normalized hybrid ranking are available through `POST /repositories/{id}/search`; all 83 tests pass. |
| Phase 5 AI explanation | PLANNED | Featherless and evidence-grounded explanation are absent. |
| Phase 6 frontend/dashboard | PLANNED | No frontend exists. |
| Phase 7 evaluation/integration | PLANNED | No retrieval benchmark or full-system evaluation exists. |
| Phase 8 agentic investigation | PLANNED | No orchestrator, tool loop, evidence verification, or investigation history exists. |

Phase 3 is implemented and verified. Phase 4 is implemented and its targeted and regression tests are reported with the delivery. Phases 5–8 remain planned.

## Phase 4 — Hybrid Search and Retrieval

| Feature | Status | Details |
|---|---|---|
| Deterministic source chunks | IMPLEMENTED AND VERIFIED | Parser-indexed symbols guide non-overlapping line-preserving chunks; exact one-based inclusive ranges are stored. Included text files without a Tree-sitter grammar are indexed lexically. |
| Lexical BM25 search | IMPLEMENTED AND VERIFIED | SQLite FTS5 ranks file paths, symbol names, language, and source text. Query syntax is safely tokenized and technical identifiers remain searchable. |
| Symbol relevance | IMPLEMENTED AND VERIFIED | Uses names and qualified names extracted by the Phase 3 parser; it does not generate symbol guesses. |
| Optional semantic retrieval | IMPLEMENTED AND VERIFIED | A replaceable embedding provider interface is disabled by default. An explicitly configured OpenAI-compatible HTTP adapter can index and query vectors; tests use a mock provider. |
| Hybrid ranking | IMPLEMENTED AND VERIFIED | Independently min-max normalizes available component scores, uses initial `0.4/0.4/0.2` weights, and renormalizes across available methods. Effective weights and per-result component scores are returned. |
| Search filters and isolation | IMPLEMENTED AND VERIFIED | Supports language, exact file path or path prefix, symbol type, and retrieval method. Database queries always include repository ID and current parse/scan guards. |
| Search index consistency | IMPLEMENTED AND VERIFIED | Candidate files are safely reread and SHA-256 checked once per search request before any results are returned. Changed, deleted, excluded, unreadable, unsafe, and stale-scan candidates fail closed with `409`. |
| Duplicate result handling | IMPLEMENTED AND VERIFIED | Retrieval methods merge on a single document ID; same path and inclusive line ranges are collapsed before returning results. |

### Phase 4 API

`POST /repositories/{repository_id}/search` accepts a JSON object such as:

```json
{
  "query": "Where is password verification implemented?",
  "limit": 10,
  "language": "Python",
  "path_prefix": "src",
  "symbol_type": "function",
  "retrieval_method": "hybrid"
}
```

`file_path` (exact) and `path_prefix` are mutually exclusive. The response contains `semantic_available`, `available_methods`, `effective_weights`, and ranked results with repository ID, path, optional symbol name/type, language, source snippet, exact start/end lines, BM25/semantic/symbol/final scores, and matched methods.

Hybrid results use the initial normalized weighted sum `0.4K + 0.4S + 0.2Y`. If semantic retrieval is unavailable, its weight is removed and the remaining weights are renormalized to `2/3` lexical and `1/3` symbol. A method unavailable because its provider is disabled does not lower the other methods' weights. These initial weights are not benchmark tuned. Reciprocal Rank Fusion was not selected as the primary ranker because it discards score magnitude and cannot express the requested component weights directly.

### Embedding provider configuration

Set `SEARCH_EMBEDDING_ENDPOINT` and `SEARCH_EMBEDDING_MODEL`; set `SEARCH_EMBEDDING_API_KEY` when the endpoint requires bearer authentication. These values may be stored in the ignored root `.env.secret`. The built-in adapter expects an OpenAI-compatible embeddings API, but does not assume any particular vendor endpoint. A different API format can be supported by replacing the provider adapter. No provider or credentials are required for lexical/symbol search or the test suite.

After configuring or changing a provider/model, run the repository parse endpoint again. Hash-matched files refresh their embedding index without Tree-sitter reparsing. Search validates candidate-file hashes before returning results. If a candidate changed, was deleted, became excluded, or cannot be safely verified, search returns `409` with instructions to run a forced scan followed by parsing. New files are not discovered until that refresh.

### Phase 4 limitations

- Semantic search currently compares stored vectors in Python and is intended for modest repository indexes; a vector extension/index is a future scaling option.
- Semantic availability is reported unavailable if any in-scope indexed chunk lacks a vector for the configured provider/model.
- Search uses a bounded candidate set per retrieval method and returns at most 100 results.
- Candidate-file validation is not an atomic filesystem snapshot. It detects content changes by hashing bytes read and uses before/after file metadata to detect changes during the read where possible; a mutation after validation and before response remains a filesystem race.
- Search validates candidate files only, not every repository file. Therefore, a newly added file is not searchable until the user explicitly forces a scan and parses again.
- External embedding calls transmit the indexed chunk text to the explicitly configured service.
