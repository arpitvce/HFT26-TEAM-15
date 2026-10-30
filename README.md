# 🚀 CBIT Hacktoberfest '26 — Project Starter Repository

> **Official Public Starter Repository for CBIT Hacktoberfest '26**  
> *Chaitanya Bharathi Institute of Technology (CBIT), Hyderabad*

---

## 📋 Team Information

| Attribute | Details |
| :--- | :--- |
| **Team Name** | **NullPointerException** |
| **Team Number** | `TEAM-15` (`HFT26-TEAM-15`) |
| **Registration ID** | `HFEST26-6BX77XQA` |
| **Problem Track** | **Track Selection in Progress** |
| **Domain** | `General Track` |
| **Team Leader** | Nayan Reddy Rajidi (nayanreddyrajidi@gmail.com) |
| **Institution** | Vasavi College Of Engineering |

### 👥 Team Members

| # | Name | Email | Institution |
| :--- | :--- | :--- | :--- |
| 1 | Arpit Sahoo | anarpit12@outlook.com | Vasavi College Of Engineering |
| 2 | P Shyam Sourya Sanjay | psouryasanjay@gmail.com | Vasavi College Of Engineering |
| 3 | S Yashwant | yaswanth251107@gmail.com | Vasavi College Of Engineering |

---

## 🎯 Problem Statement

### **Track Selection in Progress**
*Domain:* **General Track**

Details regarding the chosen problem statement track and technical requirements.

---

## 🛠️ Contribution & Submission Workflow

All teams must follow the official fork-and-pull-request workflow:

1. **Fork this Repository**:
   - One team member should click the **Fork** button at the top right to fork this upstream repository to your personal/team GitHub account.
2. **Add Collaborators**:
   - In your forked repository, navigate to **Settings > Collaborators** and invite all your registered team members.
3. **Clone & Develop Locally**:
   ```bash
   git clone https://github.com/<your-username>/HFT26-TEAM-15.git
   cd HFT26-TEAM-15
   git checkout -b feature/prototype
   ```
4. **Commit & Push Regularly**:
   - Make atomic, descriptive commits throughout the hackathon.
   ```bash
   git add .
   git commit -m "feat: implement core solution architecture"
   git push origin feature/prototype
   ```
5. **Open Pull Request (PR) for Final Submission**:
   - When ready for evaluation, open a **Pull Request** from your fork's `main` branch back to this upstream repository's `main` branch.
   - **PR Title Format**: `[SUBMISSION] TEAM-15 - NullPointerException - Track Selection in Progress`
   - **PR Description Checklist**:
     - 📌 Solution Overview & Approach
     - 🏗️ Architecture & Tech Stack Details
     - 🚀 Local Setup & Installation Instructions
     - 🎥 Demo Video Link / Live Deployment URL
     - 📸 Key Screenshots & Visuals

---

## 📁 Recommended Project Structure

```
.
├── src/                  # Source code (Frontend / Backend / ML)
├── docs/                 # Documentation, architecture diagrams, slides
├── tests/                # Test suites & verification scripts
├── README.md             # Your project documentation & presentation
└── LICENSE               # Open-source license (MIT recommended)
```

---

## ⚠️ Important Rules & Code of Conduct
* **Upstream Protection**: Do not push directly to this repository's main branch. All evaluation will be conducted on your submitted Pull Request.
* **Original Work**: All code submitted must be developed during the hackathon timeline. Open-source libraries and frameworks are welcomed with proper attribution.
* **Code Freeze**: Final Pull Requests must be submitted before the designated hackathon submission deadline.


---

## Repository Explorer Backend

# Repository Explorer API

FastAPI backend for repository registration, safe ZIP extraction, recursive file scanning, metadata persistence, file exploration, and Tree-sitter structural parsing. Repository source is never executed.

## Run

```powershell
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/docs` for Swagger UI. Git must be installed and available on `PATH` for the optional GitHub clone route.

Repository files and `repository_metadata.sqlite3` default to `repositories/` beside `main.py`; set `REPOSITORY_STORAGE` to change that location.

## Register and scan a repository

Register a local directory visible to the API host:

```powershell
$local = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/repositories/local `
  -ContentType 'application/json' -Body '{"path":"C:\\source\\my-project"}'
$scan = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/repositories/$($local.id)/scan"
```

Upload and scan a ZIP archive:

```powershell
$uploaded = curl.exe -s -X POST http://127.0.0.1:8000/repositories/upload `
  -F "file=@C:\source\my-project.zip" | ConvertFrom-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/repositories/$($uploaded.id)/scan"
```

Scanning is explicit for local/ZIP repositories. `POST /repositories/{repository_id}/scan` returns the stored snapshot when one exists; add `?force=true` to refresh it. GitHub cloning (`POST /repositories/clone`) continues to scan immediately.

## File explorer API

- `GET /repositories/{repository_id}/summary` — counts, byte totals, extensions, and language distribution.
- `GET /repositories/{repository_id}/tree` — nested directory tree with metadata and exclusion reasons.
- `GET /repositories/{repository_id}/files?offset=0&limit=100` — paginated file metadata; `path=src` filters to a directory, and `include_excluded=false` returns included files only.
- `GET /repositories/{repository_id}/file?path=src/main.py` — retrieve a bounded UTF-8 file.

The existing `GET /repositories/{repository_id}/files/{path}` content URL remains as a backward-compatible alias. Summary/tree/files return `409` until a scan has completed.

## Code parsing API

Run Phase 2 scanning before parsing a repository:

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/repositories/$repositoryId/parse"
```

Parsing is explicit; cloning and scanning do not automatically parse source files. The endpoint reads eligible files from the completed scan, uses their SHA-256 content hashes to reuse unchanged results, and returns counts for parsed, cached, skipped, and failed files. Use `?force=true` to parse supported files again even if their hashes match.

Supported Tree-sitter languages are Python, JavaScript, TypeScript/TSX, Java, C, C++, C#, Go, Rust, PHP, and Ruby. Other Phase 2 file types are skipped with a recorded reason. Malformed files are parsed on a best-effort basis; recovered symbols are returned and a syntax error is recorded. Imports describe source-level import statements only and do not prove runtime dependencies.

Endpoints:

- `POST /repositories/{repository_id}/parse?force=false` — parse the current scan snapshot.
- `GET /repositories/{repository_id}/parsing-summary` — parse status, statistics, errors, and skipped-file reasons.
- `GET /repositories/{repository_id}/symbols` — paginated symbols; filters include `name`, `symbol_type`, `language`, and exact `file_path`.
- `GET /repositories/{repository_id}/symbols/{symbol_id}` — retrieve a symbol by ID.
- `GET /repositories/{repository_id}/imports` — paginated imports; filters include `language`, exact `file_path`, and `module` substring.

Symbol locations use one-based line and character-column values. `end_line` is inclusive; `end_column` follows Tree-sitter's exclusive end position converted to a one-based character column. `parent_symbol_id` links nested methods/functions to their parent symbol, and `parent_symbol` provides the parent name. Snippets are limited to 32,768 characters; `snippet_truncated` indicates when a snippet was shortened.

Parsing uses individually pinned Tree-sitter grammar wheels (listed in `requirements.txt`) with `tree-sitter==0.26.0`. Parser limits default to the Phase 2 per-file size limit (1 MiB), 10,000 eligible files, and 250 MiB of source per parse run. Configure them with `REPOSITORY_MAX_PARSE_FILE_BYTES`, `REPOSITORY_MAX_PARSE_FILES`, and `REPOSITORY_MAX_PARSE_TOTAL_BYTES`. Binary and Phase 2-excluded files are not read by the parser. Symbol and import results are only served when they correspond to the latest completed scan; rescan, then parse, after repository changes.

## Hybrid search API

Search requires a completed scan and parse. `POST /repositories/{repository_id}/search` accepts:

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

Filters are optional. `file_path` selects one exact repository-relative path; `path_prefix` selects a directory subtree, and those two fields cannot be combined. `symbol_type` accepts `class`, `function`, or `method`. `retrieval_method` accepts `hybrid`, `lexical`, `symbol`, or `semantic`.

Example response:

```json
{
  "repository_id": "repo_example",
  "query": "Where is password verification implemented?",
  "retrieval_method": "hybrid",
  "semantic_available": false,
  "available_methods": ["lexical", "symbol"],
  "effective_weights": {"lexical": 0.6667, "symbol": 0.3333},
  "results": [
    {
      "repository_id": "repo_example",
      "file_path": "src/auth.py",
      "symbol_name": "verify_password",
      "symbol_type": "function",
      "language": "Python",
      "snippet": "def verify_password(password, candidate):\n    return password == candidate\n",
      "start_line": 1,
      "end_line": 2,
      "bm25_score": 1.0,
      "semantic_score": null,
      "symbol_score": 1.0,
      "final_score": 1.0,
      "retrieval_methods": ["lexical", "symbol"]
    }
  ]
}
```

Source chunks are created during parsing, partition source without overlapping line ranges, and preserve exact one-based inclusive locations. Lexical search uses SQLite FTS5 BM25 over source text, symbol names, language, and path. Symbol relevance comes from the Phase 3 parser index. Hybrid search normalizes each available signal to `[0, 1]`, applies initial weights `0.4K + 0.4S + 0.2Y`, then renormalizes the weights across methods available for that request. If semantic search is unavailable, the effective weights become `2/3` lexical and `1/3` symbol. The response returns those effective weights and individual scores.

### Optional semantic search

The default setup has no embedding provider; lexical and symbol retrieval work without credentials. To enable semantics, set `SEARCH_EMBEDDING_ENDPOINT` and `SEARCH_EMBEDDING_MODEL`, plus `SEARCH_EMBEDDING_API_KEY` if required. The HTTP adapter expects an OpenAI-compatible embeddings endpoint. It uses only the explicitly configured URL and does not assume Featherless provides embeddings. These settings can be placed in root `.env.secret` (ignored by Git). Tests inject a mock provider and make no external calls.

After enabling/changing a provider, call the parse endpoint to create or refresh vectors. Before returning results, search safely rereads each distinct candidate file and compares its SHA-256 with the indexed hash. If a candidate changed, was deleted, became excluded, or cannot be safely validated, the entire request returns `409` and instructs the caller to force a scan followed by parsing. New files are not searchable until that explicit refresh. Candidate validation does not execute repository code and does not hash every repository file.

Metadata includes relative paths, sizes, extensions, detected languages, UTC modification timestamps, hidden-file flags, inclusion status, binary/generated/unsupported/oversized flags, and exclusion reasons. The tree includes excluded entries so the UI can explain their status; excluded content is not removed from disk.

## AI explanation API

`POST /repositories/{repository_id}/ask` retrieves relevant Phase 4 chunks and returns an assessed explanation separately from its trusted source evidence. It requires the repository to be scanned and parsed. Configure backend-only `FEATHERLESS_API_KEY`/`FEATHERLESS_MODEL` and `MOONSHOT_API_KEY`/`MOONSHOT_MODEL`; both roles are required when claims are produced. This does not affect lexical or symbol search.

Provider timeouts are independently configurable with `FEATHERLESS_TIMEOUT_SECONDS` and `MOONSHOT_TIMEOUT_SECONDS` (each defaults to 45 seconds and must be greater than 0 and at most 600 seconds).

Example request:

```json
{
  "question": "How is password verification performed?",
  "limit": 8,
  "retrieval_method": "hybrid",
  "language": "Python",
  "path_prefix": "src"
}
```

Example response:

```json
{
  "repository_id": "repo_example",
  "question": "How is password verification performed?",
  "answer": "The candidate password is compared with the stored value [src/auth.py:10-12].",
  "insufficient_evidence": false,
  "claims": [
    {
      "claim_id": "C1",
      "text": "The candidate password is compared with the stored value.",
      "evidence_ids": ["E1"],
      "assessment_evidence_ids": ["E1"],
      "verification": "model_assessed_supported"
    }
  ],
  "model": "configured-model",
  "generator_model": "configured-featherless-model",
  "verifier_model": "configured-moonshot-model",
  "request_id": "5a1237a0-91cd-4ac2-bc12-5b74f7ad1249",
  "usage": [
    {
      "role": "answer_generator",
      "provider": "featherless",
      "model": "configured-featherless-model",
      "input_tokens": 740,
      "output_tokens": 118,
      "total_tokens": 858,
      "usage_available": true,
      "cost": null,
      "cost_kind": "unavailable",
      "cost_available": false,
      "pricing_basis": null,
      "status": "succeeded"
    },
    {
      "role": "evidence_verifier",
      "provider": "moonshot",
      "model": "configured-moonshot-model",
      "input_tokens": 825,
      "output_tokens": 64,
      "total_tokens": 889,
      "usage_available": true,
      "cost": null,
      "cost_kind": "unavailable",
      "cost_available": false,
      "pricing_basis": null,
      "status": "succeeded"
    }
  ],
  "evidence": [
    {
      "evidence_id": "E1",
      "file_path": "src/auth.py",
      "start_line": 10,
      "end_line": 12,
      "snippet": "def verify_password(password, candidate):\\n    return password == candidate\\n",
      "symbol_name": "verify_password",
      "symbol_type": "function",
      "language": "Python",
      "retrieval_methods": ["lexical", "symbol"],
      "snippet_truncated": false
    }
  ]
}
```

Only selected excerpts are sent to the providers (up to five, 4,000 characters each and 20,000 characters total context); the repository is never sent wholesale. Featherless generates structured claims; Moonshot independently assesses every claim as `supported`, `contradicted`, `not_established`, or `ambiguous`. Non-supported claims are explicitly labeled and not stated as facts. These model assessments can still be wrong and do not guarantee factual correctness. Trusted paths and line ranges remain in the separate retrieved-evidence list and are used for generated answer citations. Usage metadata is recorded separately for every provider call; costs remain unavailable unless operator-supplied per-million input/output rates are configured. Usage persistence failures do not fail otherwise successful explanations. No search hits produce an explicit insufficient-evidence response without a model call. The generator may request one validated repository search; Phase 5 does not run an autonomous tool loop or persist conversation history. See [Featherless chat completions](https://featherless.ai/docs/completions) and [Moonshot chat API](https://platform.moonshot.ai/docs/api/chat).

## Filtering and limits

Default excluded directory names: `.git`, `node_modules`, `venv`, `.venv`, `__pycache__`, `dist`, `build`, `coverage`, `.next`, `.cache`.

Defaults can be changed with comma-separated `REPOSITORY_EXCLUDED_DIRECTORIES` and `REPOSITORY_GENERATED_PATTERNS`. `REPOSITORY_MAX_FILE_SIZE_BYTES` defaults to 1 MiB; `REPOSITORY_MAX_SCAN_FILES` defaults to 10,000; `REPOSITORY_MAX_SCAN_ENTRIES` defaults to 50,000. ZIP ingestion limits are configured with `REPOSITORY_MAX_ZIP_UPLOAD_BYTES`, `REPOSITORY_MAX_ZIP_EXTRACTED_BYTES`, `REPOSITORY_MAX_ZIP_FILES`, and `REPOSITORY_MAX_ZIP_ENTRIES`.

Binary detection uses known binary extensions plus a bounded 8 KiB sample for eligible files. Language detection is extension based. Unknown extensions are marked `unknown` and excluded from inclusion statistics. A completed scan is reused until explicitly refreshed; changed repository contents or changed filter settings require `force=true`.

## Other routes and tests

- `POST /repositories/local`, `POST /repositories/upload`, `GET /repositories`, and `GET /repositories/{repository_id}` manage Phase 1 ingestion metadata.
- `POST /github` validates a GitHub URL.
- `POST /repositories/clone` clones a public GitHub repository and returns its initial scan summary.

Run tests with:

```powershell
python -m pytest -q
```

Tests cover the Phase 3 grammar extractors, exact locations, imports, malformed and empty files, hash caching, changed files, skip/error recording, path safety, API filters, and Phase 1/2 regressions. Phase 4 tests cover BM25, symbol and mocked semantic retrieval, hybrid score normalization, repository isolation, filters, duplicate removal, chunk line accuracy, cached index refresh, candidate hash freshness (including direct modifications/deletions), fail-closed validation, and candidate-only validation cost.
