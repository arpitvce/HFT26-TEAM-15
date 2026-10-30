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

Metadata includes relative paths, sizes, extensions, detected languages, UTC modification timestamps, hidden-file flags, inclusion status, binary/generated/unsupported/oversized flags, and exclusion reasons. The tree includes excluded entries so the UI can explain their status; excluded content is not removed from disk.

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

Tests cover the Phase 3 grammar extractors, exact locations, imports, malformed and empty files, hash caching, changed files, skip/error recording, path safety, API filters, and Phase 1/2 regressions.
