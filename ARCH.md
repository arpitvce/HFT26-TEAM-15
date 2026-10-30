# Architecture

## Current architecture

Repository Explorer currently has a modular FastAPI backend, SQLite persistence for repository metadata and scan snapshots, and Tree-sitter parsing with structural symbol/import indexes. It has no frontend, retrieval engine, or AI/agent components.

```mermaid
flowchart LR
    Client[API client / Swagger UI] --> API[FastAPI: main.py]
    API --> Ingest[repository_ingestion.py]
    API --> Scanner[repository_scanner.py]
    API --> ParseService[repository_parse_service.py]
    ParseService --> Parser[code_parser.py: Tree-sitter]
    API --> Store[repository_store.py]
    API --> Git[repository_service.py]
    Ingest --> Root[Local path or extracted ZIP root]
    Git --> Root
    Scanner --> Root
    Scanner --> Store
    ParseService --> SafeRead[Bounded root-contained file reads]
    SafeRead --> Root
    Parser --> ParseService
    ParseService --> Store
    Store --> DB[(SQLite metadata, scan, file tables)]
    Store --> ParseDB[(SQLite parse, symbol, import, error tables)]
    Scanner --> Tree[Persisted tree and statistics]
    Parser --> Symbols[Symbols, imports, parse errors]
    API --> Explorer[Tree / summary / paginated files / safe file read]
```

## Project structure

- `main.py`: FastAPI app, schemas, URL validation, ingestion/scanning routes, file listing and content route validation.
- `repository_ingestion.py`: Phase 1 local path validation and safe bounded ZIP extraction.
- `repository_service.py`: Git clone and a compatibility wrapper for the common scanner.
- `repository_scanner.py`: filesystem walk, filtering/classification, language detection, scan cache workflow, and tree construction.
- `code_parser.py`: language grammar registry, Tree-sitter traversal, language-specific symbol/import extraction, and source-location normalization.
- `repository_parse_service.py`: scan-state checks, safe bounded source reads, content-hash caching, parse limits, result/error handling, and parse summaries.
- `repository_store.py`: SQLite schema and operations for repository/scan/file metadata and parser status, symbols, imports, and errors.
- `tests/`: Phase 1/2 regression tests and Phase 3 parser/API tests.
- `requirements.txt`: Python dependencies.
- `README.md`: startup, API, and scanning configuration.
- `SPEC.md`, `AGENTS.md`, `FEATURES.md`, `IMPLEMENTATION_REPORT.md`: specification and engineering documentation.

Repository metadata, scan statistics, and file entries live in `repository_metadata.sqlite3` under `REPOSITORY_STORAGE`. ZIP uploads and Git clones are stored in unique ID-named directories. Local paths are registered in place.

## Phase 2 scanning flow

1. The client registers a local directory or ZIP using Phase 1. GitHub cloning retains its immediate scan behavior.
2. For local/ZIP inputs, `POST /repositories/{id}/scan` resolves the repository ID through SQLite and verifies its ingestion status and root path.
3. `repository_scanner.py` walks entries with `os.scandir`, sorting each directory deterministically. It stores relative POSIX paths, sizes, extensions, language, UTC modification time, hidden status, inclusion status, category flags, and an exclusion reason.
4. The scanner never follows symlinks or Windows junctions. It marks them as excluded entries. It detects binary content using known binary extensions or at most an 8 KiB sample for eligible files.
5. Excluded directories are represented in the tree and traversed for file/folder totals and explanatory metadata; their contents inherit the directory exclusion reason and are not content-sniffed.
6. One SQLite transaction replaces the repository's prior file snapshot and writes summary statistics, language counts, extension counts, and completion time. A primary key on `(repository_id, relative_path)` prevents duplicate records.
7. Repeated scan requests return the saved snapshot unless `force=true`. A failed forced scan leaves the prior completed snapshot intact.
8. Tree, summary, paginated file metadata, and content endpoints read the persisted scan. File content is read only after validating the relative path, confirming the scanned file row, rejecting symlink/junction components, resolving under the registered root, and enforcing a read-size limit.

## Phase 3 parsing flow

1. A client calls `POST /repositories/{id}/parse` after a completed Phase 2 scan. Parsing is not automatically triggered by clone or scan.
2. `repository_parse_service.py` verifies the repository is ready and its scan is complete, then atomically claims a parse run. It iterates the Phase 2 file metadata and skips excluded, binary, oversized, or unsupported-language entries without opening them.
3. For eligible files, the parser validates the stored relative path, rejects symlink/junction components, resolves the path under the registered root, opens a regular file without following the final link, and reads at most the configured parse-size limit.
4. UTF-8 content is hashed with SHA-256. A matching prior hash and language reuses the saved symbols/imports; changed content (or `force=true`) is parsed.
5. `code_parser.py` uses the grammar selected by Phase 2's language and extension metadata. It traverses syntax trees to extract class-like declarations, functions/methods, parameters, imports, source snippets, and source positions. Tree-sitter error nodes produce a partial result plus a recorded syntax error.
6. Each file's current parser state, symbols, imports, and error rows are replaced in a SQLite transaction. A completed run removes parser records for paths no longer present in the latest scan and stores summary counts. A changed Phase 2 scan invalidates parser API reads until parsing is run again.
7. Symbol, import, and parsing-summary routes return persisted results. Imports are source-level statements and do not establish runtime dependencies.

Parent-child relationships use `repository_symbols.parent_symbol_id` (self-reference). Nested declarations point to the nearest enclosing class, function, or method. Go receiver methods and Rust `impl` methods link to a same-file type declaration when one is present. Each `file_path` is a repository-relative POSIX path. Lines are one-based and end lines inclusive; columns are one-based character columns with an exclusive end column. Snippets are capped at 32,768 characters and indicate truncation.

## SQLite schema

- `repositories`: Phase 1 ID, name, root path, input type, creation time, ingestion status, and safe error text.
- `repository_scans`: scan status, completion timestamp, statistics JSON, language and extension distributions, and scan error text.
- `repository_files`: one unique row per repository-relative directory, regular file, or symlink; stores type, size, extension, language, modification time, hidden/included flags, classification flags, and exclusion reason.
- `repository_parses`: last parse run status, run ID, scan snapshot timestamp, timing, counts, and operation-level error.
- `parsed_files`: per-path content hash, language, parsing status/reason, last-seen parse run, and parse timestamp. This is the parent for per-file parse records.
- `repository_symbols`: symbol ID, repository-relative file path, name/qualified name, type, language, parent symbol, one-based locations, parameters JSON, source snippet, truncation flag, and byte offsets. Foreign keys link to the repository and `parsed_files`; a self-reference links parent symbols.
- `repository_imports`: source-level imported module/symbol/alias/type, path, language, snippet, and one-based source range. Foreign keys link to the repository and `parsed_files`.
- `repository_parse_errors`: parse or file-read errors with relative path, language, reason, safe message, optional line, and timestamp. Foreign keys link to the repository and `parsed_files`.

There is no migration framework in the project. Tables and indexes are created with `CREATE TABLE/INDEX IF NOT EXISTS`.

## API architecture

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/repositories/local` | Register a non-empty local directory. |
| `POST` | `/repositories/upload` | Validate and safely extract a ZIP archive. |
| `GET` | `/repositories` | List persisted repository metadata. |
| `GET` | `/repositories/{repository_id}` | Retrieve ingestion status and location metadata. |
| `POST` | `/repositories/{repository_id}/scan` | Scan once, return cached scan, or force refresh. |
| `POST` | `/repositories/{repository_id}/parse` | Parse eligible files from the completed scan; `force=true` reparses unchanged files. |
| `GET` | `/repositories/{repository_id}/symbols` | Paginated/filterable symbols (`name`, type, language, exact path). |
| `GET` | `/repositories/{repository_id}/symbols/{symbol_id}` | Retrieve a symbol and its source evidence. |
| `GET` | `/repositories/{repository_id}/imports` | Paginated/filterable source import records. |
| `GET` | `/repositories/{repository_id}/parsing-summary` | Parse run status, counts, errors, and skipped reasons. |
| `GET` | `/repositories/{repository_id}/tree` | Return the persisted hierarchical tree. |
| `GET` | `/repositories/{repository_id}/summary` | Return file, directory, size, language, and exclusion statistics. |
| `GET` | `/repositories/{repository_id}/files` | Paginate file/symlink metadata with optional path and exclusion filters. |
| `GET` | `/repositories/{repository_id}/file?path=...` | Return bounded UTF-8 content for a scanned file. |
| `GET` | `/repositories/{repository_id}/files/{path}` | Backward-compatible content alias, hidden from generated OpenAPI docs. |
| `POST` | `/github` | Validate and normalize a GitHub URL. |
| `POST` | `/repositories/clone` | Clone and scan a public GitHub repository immediately. |

## Configuration

- `REPOSITORY_STORAGE`: filesystem location for ZIP extraction, Git clones, and SQLite.
- `REPOSITORY_EXCLUDED_DIRECTORIES`: comma-separated directory-name exclusions.
- `REPOSITORY_GENERATED_PATTERNS`: comma-separated filename/path globs.
- `REPOSITORY_MAX_FILE_SIZE_BYTES`: per-file inclusion limit, default 1 MiB.
- `REPOSITORY_MAX_SCAN_FILES`: maximum file-like entries, default 10,000.
- `REPOSITORY_MAX_SCAN_ENTRIES`: maximum total metadata entries, default 50,000.
- `REPOSITORY_MAX_FILE_READ_BYTES`: file content response limit, default 1 MiB.
- `REPOSITORY_MAX_PARSE_FILE_BYTES`: maximum bytes read for one parse file, default follows the Phase 2 file-size limit (1 MiB).
- `REPOSITORY_MAX_PARSE_FILES`: maximum eligible files considered per parse run, default 10,000.
- `REPOSITORY_MAX_PARSE_TOTAL_BYTES`: total source bytes accepted by a parse run, default 250 MiB.

ZIP upload/extraction configuration is documented in `README.md`.

## Current limitations

- Language detection is extension based. Binary detection uses a fixed extension set plus a bounded byte sample.
- Scanning is synchronous and limited by configurable entry/file counts.
- A cached scan is not automatically invalidated when local files or environment filter settings change; clients must request `force=true`.
- File contents are limited to UTF-8 text and the configured response-size cap.
- No authentication/authorization is present; use only in a trusted local environment.
- Tree-sitter supports Python, JavaScript, TypeScript/TSX, Java, C, C++, C#, Go, Rust, PHP, and Ruby. HTML, CSS, SQL, and other Phase 2 languages are recorded as skipped because no structural extractor grammar is configured.
- Parsing is synchronous. The current pinned Python binding does not expose an assignable parser timeout, so file-count and byte limits bound work; very complex files near the size limit may still take longer than usual.
- Parser snippets are truncated at 32,768 characters. Unsupported grammar constructs may not yield symbols even when their file language has a configured grammar.
- No semantic index, search, AI provider, agent loop, or frontend is implemented.
