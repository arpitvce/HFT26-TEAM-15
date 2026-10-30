from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class RepositoryStore:
    """SQLite persistence for repository metadata and scan snapshots."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS repositories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    input_type TEXT NOT NULL CHECK(input_type IN ('local', 'zip', 'github')),
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('processing', 'ready', 'failed')),
                    error_message TEXT
                );
                CREATE TABLE IF NOT EXISTS repository_scans (
                    repository_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('scanning', 'complete', 'failed')),
                    scanned_at TEXT,
                    statistics_json TEXT,
                    languages_json TEXT,
                    extensions_json TEXT,
                    error_message TEXT
                );
                CREATE TABLE IF NOT EXISTS repository_files (
                    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    entry_type TEXT NOT NULL CHECK(entry_type IN ('directory', 'file', 'symlink')),
                    size_bytes INTEGER,
                    extension TEXT,
                    language TEXT,
                    modified_at TEXT,
                    is_hidden INTEGER NOT NULL DEFAULT 0,
                    is_included INTEGER NOT NULL DEFAULT 1,
                    exclusion_reason TEXT,
                    is_binary INTEGER NOT NULL DEFAULT 0,
                    is_generated INTEGER NOT NULL DEFAULT 0,
                    is_unsupported INTEGER NOT NULL DEFAULT 0,
                    is_oversized INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (repository_id, relative_path)
                );
                CREATE INDEX IF NOT EXISTS idx_repository_files_listing
                    ON repository_files(repository_id, entry_type, relative_path);
                CREATE TABLE IF NOT EXISTS repository_parses (
                    repository_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('scanning', 'complete', 'failed')),
                    run_id TEXT,
                    scan_timestamp TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    total_files INTEGER NOT NULL DEFAULT 0,
                    parsed_files INTEGER NOT NULL DEFAULT 0,
                    cached_files INTEGER NOT NULL DEFAULT 0,
                    skipped_files INTEGER NOT NULL DEFAULT 0,
                    failed_files INTEGER NOT NULL DEFAULT 0,
                    symbol_count INTEGER NOT NULL DEFAULT 0,
                    import_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                );
                CREATE TABLE IF NOT EXISTS parsed_files (
                    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                    file_path TEXT NOT NULL,
                    content_hash TEXT,
                    language TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('complete', 'partial', 'skipped', 'failed')),
                    reason TEXT,
                    run_id TEXT NOT NULL,
                    parsed_at TEXT NOT NULL,
                    PRIMARY KEY(repository_id, file_path)
                );
                CREATE TABLE IF NOT EXISTS repository_symbols (
                    symbol_id TEXT PRIMARY KEY,
                    repository_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    symbol_name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    symbol_type TEXT NOT NULL,
                    language TEXT NOT NULL,
                    parent_symbol_id TEXT REFERENCES repository_symbols(symbol_id) ON DELETE CASCADE,
                    parent_symbol TEXT,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    start_column INTEGER NOT NULL,
                    end_column INTEGER NOT NULL,
                    parameters_json TEXT NOT NULL,
                    source_snippet TEXT NOT NULL,
                    snippet_truncated INTEGER NOT NULL DEFAULT 0,
                    start_byte INTEGER NOT NULL,
                    end_byte INTEGER NOT NULL,
                    FOREIGN KEY(repository_id, file_path)
                        REFERENCES parsed_files(repository_id, file_path) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_symbols_repository ON repository_symbols(repository_id);
                CREATE INDEX IF NOT EXISTS idx_symbols_file_path ON repository_symbols(repository_id, file_path);
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON repository_symbols(symbol_name);
                CREATE INDEX IF NOT EXISTS idx_symbols_type ON repository_symbols(symbol_type);
                CREATE TABLE IF NOT EXISTS repository_imports (
                    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repository_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    module TEXT NOT NULL,
                    imported_symbol TEXT,
                    alias TEXT,
                    import_type TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    start_column INTEGER NOT NULL,
                    end_column INTEGER NOT NULL,
                    source_snippet TEXT NOT NULL,
                    FOREIGN KEY(repository_id, file_path)
                        REFERENCES parsed_files(repository_id, file_path) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_imports_repository ON repository_imports(repository_id);
                CREATE INDEX IF NOT EXISTS idx_imports_file_path ON repository_imports(repository_id, file_path);
                CREATE INDEX IF NOT EXISTS idx_imports_module ON repository_imports(module);
                CREATE TABLE IF NOT EXISTS repository_parse_errors (
                    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repository_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    message TEXT NOT NULL,
                    start_line INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(repository_id, file_path)
                        REFERENCES parsed_files(repository_id, file_path) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_parse_errors_repository
                    ON repository_parse_errors(repository_id);
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create(
        self,
        repository_id: str,
        name: str,
        root_path: Path,
        input_type: str,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO repositories (id, name, root_path, input_type, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'processing')",
                (repository_id, name, str(root_path), input_type, created_at),
            )

    def set_status(self, repository_id: str, status: str, error_message: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE repositories SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, repository_id),
            )

    def get(self, repository_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, name, root_path, input_type, created_at, status, error_message "
                "FROM repositories WHERE id = ?",
                (repository_id,),
            ).fetchone()
        return self._as_dict(row)

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, root_path, input_type, created_at, status, error_message "
                "FROM repositories ORDER BY created_at DESC, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_scan(self, repository_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, scanned_at, statistics_json, languages_json, extensions_json, error_message "
                "FROM repository_scans WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["statistics"] = json.loads(result.pop("statistics_json")) if result["statistics_json"] else None
        result["languages"] = json.loads(result.pop("languages_json")) if result["languages_json"] else {}
        result["extensions"] = json.loads(result.pop("extensions_json")) if result["extensions_json"] else {}
        return result

    def begin_scan(self, repository_id: str) -> bool:
        """Atomically claim a scan; return False when another scan is active."""
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT status FROM repository_scans WHERE repository_id = ?", (repository_id,)
            ).fetchone()
            if exists is None:
                connection.execute(
                    "INSERT INTO repository_scans(repository_id, status) VALUES (?, 'scanning')",
                    (repository_id,),
                )
                return True
            cursor = connection.execute(
                "UPDATE repository_scans SET status = 'scanning', error_message = NULL "
                "WHERE repository_id = ? AND status != 'scanning'",
                (repository_id,),
            )
            return cursor.rowcount == 1

    def fail_scan(self, repository_id: str, detail: str) -> None:
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT scanned_at FROM repository_scans WHERE repository_id = ?", (repository_id,)
            ).fetchone()
            if previous and previous["scanned_at"]:
                connection.execute(
                    "UPDATE repository_scans SET status = 'complete', error_message = ? "
                    "WHERE repository_id = ?",
                    (detail, repository_id),
                )
            else:
                connection.execute(
                    "UPDATE repository_scans SET status = 'failed', error_message = ? "
                    "WHERE repository_id = ?",
                    (detail, repository_id),
                )

    def save_scan(
        self,
        repository_id: str,
        scanned_at: str,
        statistics: dict[str, Any],
        languages: dict[str, int],
        extensions: dict[str, int],
        entries: list[dict[str, Any]],
    ) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM repository_files WHERE repository_id = ?", (repository_id,))
            connection.executemany(
                """INSERT INTO repository_files (
                    repository_id, relative_path, name, entry_type, size_bytes, extension, language,
                    modified_at, is_hidden, is_included, exclusion_reason, is_binary, is_generated,
                    is_unsupported, is_oversized
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        repository_id, entry["path"], entry["name"], entry["type"], entry.get("size_bytes"),
                        entry.get("extension"), entry.get("language"), entry.get("modified_at"),
                        int(entry.get("is_hidden", False)), int(entry.get("is_included", True)),
                        entry.get("exclusion_reason"), int(entry.get("is_binary", False)),
                        int(entry.get("is_generated", False)), int(entry.get("is_unsupported", False)),
                        int(entry.get("is_oversized", False)),
                    )
                    for entry in entries
                ],
            )
            connection.execute(
                """INSERT INTO repository_scans (
                    repository_id, status, scanned_at, statistics_json, languages_json, extensions_json, error_message
                ) VALUES (?, 'complete', ?, ?, ?, ?, NULL)
                ON CONFLICT(repository_id) DO UPDATE SET
                    status='complete', scanned_at=excluded.scanned_at,
                    statistics_json=excluded.statistics_json, languages_json=excluded.languages_json,
                    extensions_json=excluded.extensions_json, error_message=NULL""",
                (
                    repository_id, scanned_at, json.dumps(statistics, sort_keys=True),
                    json.dumps(languages, sort_keys=True), json.dumps(extensions, sort_keys=True),
                ),
            )

    def get_entries(self, repository_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT relative_path AS path, name, entry_type AS type, size_bytes, extension, language, modified_at, "
                "is_hidden, is_included, exclusion_reason, is_binary, is_generated, is_unsupported, is_oversized "
                "FROM repository_files WHERE repository_id = ? ORDER BY relative_path COLLATE NOCASE, relative_path",
                (repository_id,),
            ).fetchall()
        return [self._decode_entry(row) for row in rows]

    def get_file_entries(
        self,
        repository_id: str,
        offset: int,
        limit: int,
        path_prefix: str | None,
        include_excluded: bool,
    ) -> tuple[int, list[dict[str, Any]]]:
        clauses = ["repository_id = ?", "entry_type IN ('file', 'symlink')"]
        params: list[Any] = [repository_id]
        if not include_excluded:
            clauses.append("is_included = 1")
        if path_prefix:
            prefix = path_prefix + "/"
            clauses.append("(relative_path = ? OR substr(relative_path, 1, ?) = ?)")
            params.extend([path_prefix, len(prefix), prefix])
        where = " AND ".join(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM repository_files WHERE {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT relative_path AS path, name, entry_type AS type, size_bytes, extension, language, modified_at, "
                "is_hidden, is_included, exclusion_reason, is_binary, is_generated, is_unsupported, is_oversized "
                f"FROM repository_files WHERE {where} ORDER BY relative_path COLLATE NOCASE, relative_path LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return total, [self._decode_entry(row) for row in rows]

    def get_file_entry(self, repository_id: str, relative_path: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT relative_path AS path, name, entry_type AS type, size_bytes, extension, language, modified_at, "
                "is_hidden, is_included, exclusion_reason, is_binary, is_generated, is_unsupported, is_oversized "
                "FROM repository_files WHERE repository_id = ? AND relative_path = ?",
                (repository_id, relative_path),
            ).fetchone()
        return self._decode_entry(row) if row is not None else None

    def get_parse(self, repository_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repository_parses WHERE repository_id = ?", (repository_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def begin_parse(self, repository_id: str, run_id: str, scan_timestamp: str, started_at: str) -> bool:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT status FROM repository_parses WHERE repository_id = ?", (repository_id,)
            ).fetchone()
            if exists is None:
                connection.execute(
                    "INSERT INTO repository_parses(repository_id, status, run_id, scan_timestamp, started_at) "
                    "VALUES (?, 'scanning', ?, ?, ?)",
                    (repository_id, run_id, scan_timestamp, started_at),
                )
                return True
            cursor = connection.execute(
                "UPDATE repository_parses SET status='scanning', run_id=?, scan_timestamp=?, started_at=?, "
                "completed_at=NULL, error_message=NULL WHERE repository_id=? AND status!='scanning'",
                (run_id, scan_timestamp, started_at, repository_id),
            )
            return cursor.rowcount == 1

    def get_parsed_files(self, repository_id: str) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT parsed_files.*,
                    (SELECT COUNT(*) FROM repository_symbols AS symbols
                     WHERE symbols.repository_id=parsed_files.repository_id
                       AND symbols.file_path=parsed_files.file_path) AS symbol_count,
                    (SELECT COUNT(*) FROM repository_imports AS imports
                     WHERE imports.repository_id=parsed_files.repository_id
                       AND imports.file_path=parsed_files.file_path) AS import_count
                    FROM parsed_files WHERE repository_id=?""",
                (repository_id,),
            ).fetchall()
        return {row["file_path"]: dict(row) for row in rows}

    def mark_parsed_files_seen(
        self, repository_id: str, file_paths: list[str], run_id: str, parsed_at: str
    ) -> None:
        if not file_paths:
            return
        with self._connect() as connection:
            connection.executemany(
                "UPDATE parsed_files SET run_id=?, parsed_at=? WHERE repository_id=? AND file_path=?",
                [(run_id, parsed_at, repository_id, path) for path in file_paths],
            )

    def save_parsed_file(
        self,
        repository_id: str,
        file_path: str,
        content_hash: str | None,
        language: str,
        status: str,
        reason: str | None,
        run_id: str,
        parsed_at: str,
        symbols: list[dict[str, Any]],
        imports: list[dict[str, Any]],
        error: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM parsed_files WHERE repository_id=? AND file_path=?",
                (repository_id, file_path),
            )
            connection.execute(
                "INSERT INTO parsed_files(repository_id, file_path, content_hash, language, status, reason, run_id, parsed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (repository_id, file_path, content_hash, language, status, reason, run_id, parsed_at),
            )
            pending_symbols = list(symbols)
            ordered_symbols: list[dict[str, Any]] = []
            inserted_symbol_ids: set[str] = set()
            while pending_symbols:
                ready = [
                    item for item in pending_symbols
                    if item.get("parent_symbol_id") is None
                    or item["parent_symbol_id"] in inserted_symbol_ids
                ]
                if not ready:
                    raise ValueError("Parsed symbols contain an unresolved parent relationship")
                for item in ready:
                    ordered_symbols.append(item)
                    inserted_symbol_ids.add(item["symbol_id"])
                    pending_symbols.remove(item)
            connection.executemany(
                """INSERT INTO repository_symbols (
                    symbol_id, repository_id, file_path, symbol_name, qualified_name, symbol_type,
                    language, parent_symbol_id, parent_symbol, start_line, end_line, start_column,
                    end_column, parameters_json, source_snippet, snippet_truncated, start_byte, end_byte
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["symbol_id"], repository_id, file_path, item["symbol_name"],
                        item["qualified_name"], item["symbol_type"], language,
                        item.get("parent_symbol_id"), item.get("parent_symbol"), item["start_line"],
                        item["end_line"], item["start_column"], item["end_column"],
                        json.dumps(item["parameters"], ensure_ascii=False), item["snippet"],
                        int(item["snippet_truncated"]), item["_start_byte"], item["_end_byte"],
                    )
                    for item in ordered_symbols
                ],
            )
            connection.executemany(
                """INSERT INTO repository_imports (
                    repository_id, file_path, language, module, imported_symbol, alias, import_type,
                    start_line, end_line, start_column, end_column, source_snippet
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        repository_id, file_path, language, item["module"], item.get("imported_symbol"),
                        item.get("alias"), item["import_type"], item["start_line"], item["end_line"],
                        item["start_column"], item["end_column"], item["snippet"],
                    )
                    for item in imports
                ],
            )
            if error is not None:
                connection.execute(
                    """INSERT INTO repository_parse_errors (
                        repository_id, file_path, language, reason, message, start_line, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        repository_id, file_path, language, error["reason"], error["message"],
                        error.get("start_line"), parsed_at,
                    ),
                )

    def complete_parse(
        self,
        repository_id: str,
        run_id: str,
        completed_at: str,
        counts: dict[str, int],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM parsed_files WHERE repository_id=? AND run_id!=?",
                (repository_id, run_id),
            )
            connection.execute(
                """UPDATE repository_parses SET status='complete', completed_at=?, total_files=?,
                    parsed_files=?, cached_files=?, skipped_files=?, failed_files=?, symbol_count=?,
                    import_count=?, error_message=NULL WHERE repository_id=? AND run_id=?""",
                (
                    completed_at, counts["total_files"], counts["parsed_files"], counts["cached_files"],
                    counts["skipped_files"], counts["failed_files"], counts["symbol_count"],
                    counts["import_count"], repository_id, run_id,
                ),
            )

    def fail_parse(self, repository_id: str, error_message: str, completed_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE repository_parses SET status='failed', completed_at=?, error_message=? "
                "WHERE repository_id=?",
                (completed_at, error_message, repository_id),
            )

    def list_symbols(
        self,
        repository_id: str,
        name: str | None,
        symbol_type: str | None,
        language: str | None,
        file_path: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        clauses = ["repository_id=?"]
        params: list[Any] = [repository_id]
        if name:
            clauses.append("symbol_name LIKE ? ESCAPE '\\'")
            escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        if symbol_type:
            clauses.append("symbol_type=?")
            params.append(symbol_type)
        if language:
            clauses.append("language=?")
            params.append(language)
        if file_path:
            clauses.append("file_path=?")
            params.append(file_path)
        where = " AND ".join(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM repository_symbols WHERE {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT * FROM repository_symbols WHERE " + where +
                " ORDER BY file_path COLLATE NOCASE, start_line, symbol_name COLLATE NOCASE LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return total, [self._decode_symbol(row) for row in rows]

    def get_symbol(self, repository_id: str, symbol_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repository_symbols WHERE repository_id=? AND symbol_id=?",
                (repository_id, symbol_id),
            ).fetchone()
        return self._decode_symbol(row) if row is not None else None

    def list_imports(
        self,
        repository_id: str,
        language: str | None,
        file_path: str | None,
        module: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        clauses = ["repository_id=?"]
        params: list[Any] = [repository_id]
        if language:
            clauses.append("language=?")
            params.append(language)
        if file_path:
            clauses.append("file_path=?")
            params.append(file_path)
        if module:
            clauses.append("module LIKE ? ESCAPE '\\'")
            escaped = module.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        where = " AND ".join(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM repository_imports WHERE {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT * FROM repository_imports WHERE " + where +
                " ORDER BY file_path COLLATE NOCASE, start_line LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return total, [dict(row) for row in rows]

    def list_parse_errors(self, repository_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT file_path, language, reason, message, start_line, created_at "
                "FROM repository_parse_errors WHERE repository_id=? "
                "ORDER BY error_id DESC LIMIT ?",
                (repository_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_skipped_parse_files(self, repository_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT file_path, language, reason FROM parsed_files "
                "WHERE repository_id=? AND status='skipped' "
                "ORDER BY file_path COLLATE NOCASE, file_path LIMIT ?",
                (repository_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode_symbol(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["parameters"] = json.loads(item.pop("parameters_json"))
        item["snippet"] = item.pop("source_snippet")
        item["snippet_truncated"] = bool(item["snippet_truncated"])
        item.pop("start_byte", None)
        item.pop("end_byte", None)
        return item

    @staticmethod
    def _decode_entry(row: sqlite3.Row) -> dict[str, Any]:
        entry = dict(row)
        for key in ("is_hidden", "is_included", "is_binary", "is_generated", "is_unsupported", "is_oversized"):
            entry[key] = bool(entry[key])
        return entry
