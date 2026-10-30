from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import repository_parse_service
from main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(main, "REPOSITORY_STORAGE", tmp_path / "storage")


def register_and_scan(root: Path) -> str:
    registered = client.post("/repositories/local", json={"path": str(root)})
    assert registered.status_code == 201
    repository_id = registered.json()["id"]
    scanned = client.post(f"/repositories/{repository_id}/scan")
    assert scanned.status_code == 200
    return repository_id


def test_parse_endpoint_persists_symbols_imports_and_summary(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "service.py").write_text(
        "from helpers import normalize\n"
        "class Service:\n"
        "    def run(self, value):\n"
        "        return normalize(value)\n"
        "def top():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    repository_id = register_and_scan(root)

    response = client.post(f"/repositories/{repository_id}/parse")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "complete"
    assert body["statistics"] == {
        "total_files": 1,
        "parsed_files": 1,
        "cached_files": 0,
        "skipped_files": 0,
        "failed_files": 0,
        "symbol_count": 3,
        "import_count": 1,
    }

    listed = client.get(f"/repositories/{repository_id}/symbols", params={"symbol_type": "method"})
    assert listed.status_code == 200
    method = listed.json()["symbols"][0]
    assert method["symbol_name"] == "run"
    assert method["qualified_name"] == "Service.run"
    assert method["parent_symbol"] == "Service"
    assert method["parent_symbol_id"]
    assert method["file_path"] == "service.py"
    assert method["start_line"] == 3
    assert method["end_line"] == 4
    filtered = client.get(f"/repositories/{repository_id}/symbols", params={"name": "run", "language": "Python"})
    assert filtered.status_code == 200
    assert [item["symbol_name"] for item in filtered.json()["symbols"]] == ["run"]

    detail = client.get(f"/repositories/{repository_id}/symbols/{method['symbol_id']}")
    assert detail.status_code == 200
    assert detail.json()["snippet"].startswith("def run")

    imports = client.get(f"/repositories/{repository_id}/imports").json()["imports"]
    assert len(imports) == 1
    assert imports[0]["module"] == "helpers"
    assert imports[0]["imported_symbol"] == "normalize"
    assert imports[0]["start_line"] == 1
    assert client.get(f"/repositories/{repository_id}/parsing-summary").status_code == 200

    with sqlite3.connect(main.REPOSITORY_STORAGE / "repository_metadata.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM repository_symbols WHERE repository_id=?", (repository_id,)
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM repository_imports WHERE repository_id=?", (repository_id,)
        ).fetchone()[0] == 1


def test_parse_is_cached_by_content_hash_and_changed_files_are_reparsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def old_name():\n    return 1\n", encoding="utf-8")
    repository_id = register_and_scan(root)

    calls = 0
    real_parser = repository_parse_service.parse_source

    def counted_parser(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_parser(*args, **kwargs)

    monkeypatch.setattr(repository_parse_service, "parse_source", counted_parser)
    first = client.post(f"/repositories/{repository_id}/parse")
    second = client.post(f"/repositories/{repository_id}/parse")
    assert first.status_code == second.status_code == 200
    assert calls == 1
    assert second.json()["statistics"]["cached_files"] == 1
    with sqlite3.connect(main.REPOSITORY_STORAGE / "repository_metadata.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM repository_symbols WHERE repository_id=?", (repository_id,)
        ).fetchone()[0] == 1

    source.write_text("def new_name():\n    return 2\n", encoding="utf-8")
    changed = client.post(f"/repositories/{repository_id}/parse")
    assert changed.status_code == 200
    assert calls == 2
    names = {item["symbol_name"] for item in client.get(f"/repositories/{repository_id}/symbols").json()["symbols"]}
    assert names == {"new_name"}

    assert client.post(f"/repositories/{repository_id}/scan", params={"force": "true"}).status_code == 200
    assert client.get(f"/repositories/{repository_id}/symbols").status_code == 409
    rescanned = client.post(f"/repositories/{repository_id}/parse")
    assert rescanned.status_code == 200
    assert rescanned.json()["statistics"]["cached_files"] == 1


def test_parse_records_skips_partial_syntax_and_empty_files(tmp_path: Path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / "src" / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (root / "src" / "empty.py").write_text("", encoding="utf-8")
    (root / "node_modules" / "excluded.py").write_text("def ignored(): pass\n", encoding="utf-8")
    (root / "index.html").write_text("<p>not a parser grammar</p>", encoding="utf-8")
    repository_id = register_and_scan(root)

    response = client.post(f"/repositories/{repository_id}/parse")
    assert response.status_code == 200, response.text
    stats = response.json()["statistics"]
    assert stats["total_files"] == 4
    assert stats["parsed_files"] == 2
    assert stats["skipped_files"] == 2
    assert stats["failed_files"] == 0
    with sqlite3.connect(main.REPOSITORY_STORAGE / "repository_metadata.sqlite3") as connection:
        statuses = dict(connection.execute(
            "SELECT file_path, status FROM parsed_files WHERE repository_id=?", (repository_id,)
        ).fetchall())
    assert statuses["src/broken.py"] == "partial"
    assert statuses["src/empty.py"] == "complete"
    assert statuses["node_modules/excluded.py"] == "skipped"
    assert statuses["index.html"] == "skipped"
    skipped = {item["file_path"]: item["reason"] for item in response.json()["skipped"]}
    assert skipped["node_modules/excluded.py"].startswith("excluded_by_scanner:")
    assert skipped["index.html"] == "unsupported_language:HTML"


def test_missing_repository_source_and_path_traversal_errors(tmp_path: Path):
    assert client.post("/repositories/missing/parse").status_code == 404

    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("def run(): pass\n", encoding="utf-8")
    repository_id = register_and_scan(root)
    assert client.get(f"/repositories/{repository_id}/parsing-summary").status_code == 409
    assert client.post(f"/repositories/{repository_id}/parse").status_code == 200
    assert client.get(f"/repositories/{repository_id}/symbols", params={"file_path": "../outside.py"}).status_code == 422
    assert client.get(f"/repositories/{repository_id}/symbols/missing").status_code == 404

    (root / "main.py").unlink()
    result = client.post(f"/repositories/{repository_id}/parse", params={"force": "true"})
    assert result.status_code == 200
    assert result.json()["statistics"]["failed_files"] == 1
    errors = client.get(f"/repositories/{repository_id}/parsing-summary").json()["errors"]
    assert errors[0]["reason"] == "file_unavailable"


def test_parse_requires_phase_two_scan(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("pass\n", encoding="utf-8")
    registered = client.post("/repositories/local", json={"path": str(root)})
    repository_id = registered.json()["id"]
    response = client.post(f"/repositories/{repository_id}/parse")
    assert response.status_code == 409


def test_parse_file_count_limit_is_reported_without_exceeding_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("def first(): pass\n", encoding="utf-8")
    (root / "b.py").write_text("def second(): pass\n", encoding="utf-8")
    repository_id = register_and_scan(root)
    monkeypatch.setattr(repository_parse_service, "MAX_PARSE_FILES", 1)

    response = client.post(f"/repositories/{repository_id}/parse")

    assert response.status_code == 200
    stats = response.json()["statistics"]
    assert stats["parsed_files"] == 1
    assert stats["skipped_files"] == 1
    assert response.json()["skipped"][0]["reason"] == "parse_file_count_limit"
