from __future__ import annotations

import io
import os
import sqlite3
import zipfile
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import repository_scanner
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(main, "REPOSITORY_STORAGE", tmp_path / "storage")
    yield


@pytest.fixture
def sample_repository(tmp_path: Path) -> Path:
    root = tmp_path / "sample-project"
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "dist").mkdir()
    (root / "empty").mkdir()
    (root / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    (root / "src" / ".env").write_text("KEY=value", encoding="utf-8")
    (root / "src" / "app.min.js").write_text("minified();", encoding="utf-8")
    (root / "src" / "image.png").write_bytes(b"\x89PNG\x00binary")
    (root / "src" / "large.py").write_text("x" * 30, encoding="utf-8")
    (root / "node_modules" / "pkg" / "index.js").write_text("module", encoding="utf-8")
    (root / ".git" / "config").write_text("[core]", encoding="utf-8")
    (root / "dist" / "bundle.js").write_text("bundle", encoding="utf-8")
    (root / "README.md").write_text("docs", encoding="utf-8")
    (root / "data.json").write_text("{}", encoding="utf-8")
    (root / "config.yaml").write_text("name: app", encoding="utf-8")
    (root / "unknown.xyz").write_text("unknown text", encoding="utf-8")
    return root


def register_repository(root: Path) -> str:
    response = client.post("/repositories/local", json={"path": str(root)})
    assert response.status_code == 201
    return response.json()["id"]


def run_scan(repository_id: str, force: bool = False):
    return client.post(f"/repositories/{repository_id}/scan", params={"force": str(force).lower()})


def test_recursive_scan_counts_languages_exclusions_and_metadata(
    sample_repository: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(repository_scanner, "MAX_FILE_SIZE_BYTES", 20)
    repository_id = register_repository(sample_repository)

    before = client.get(f"/repositories/{repository_id}/summary")
    assert before.status_code == 409
    response = run_scan(repository_id)
    assert response.status_code == 200
    body = response.json()
    stats = body["statistics"]

    assert stats["total_files"] == 12
    assert stats["total_directories"] == 6
    assert stats["included_files"] == 4
    assert stats["excluded_files"] == 8
    assert stats["total_repository_size_bytes"] == sum(
        path.stat().st_size for path in sample_repository.rglob("*") if path.is_file()
    )
    assert stats["included_file_size_bytes"] == sum(
        (sample_repository / relative).stat().st_size
        for relative in ("src/main.py", "README.md", "data.json", "config.yaml")
    )
    assert stats["source_files"] == 1
    assert stats["hidden_directories"] == 1
    assert stats["binary_files"] == 1
    assert stats["generated_files"] == 1
    assert stats["oversized_files"] == 1
    assert stats["unsupported_files"] >= 2
    assert body["languages"] == {"JSON": 1, "Markdown": 1, "Python": 1, "YAML": 1}
    assert body["extensions"][".py"] == 2
    assert body["extensions"]["(no extension)"] == 2

    file_metadata = client.get(f"/repositories/{repository_id}/files", params={"path": "src"}).json()["files"]
    main_file = next(item for item in file_metadata if item["path"] == "src/main.py")
    assert main_file["size_bytes"] == (sample_repository / "src/main.py").stat().st_size
    assert main_file["modified_at"]
    assert (sample_repository / "src/.env").name.startswith(".")
    assert next(item for item in file_metadata if item["name"] == ".env")["is_hidden"] is True
    assert next(item for item in file_metadata if item["name"] == "app.min.js")["exclusion_reason"] == "generated_file"
    assert next(item for item in file_metadata if item["name"] == "large.py")["is_oversized"] is True


def test_tree_is_nested_sorted_and_keeps_exclusion_reason(sample_repository: Path):
    repository_id = register_repository(sample_repository)
    assert run_scan(repository_id).status_code == 200
    tree_response = client.get(f"/repositories/{repository_id}/tree")
    assert tree_response.status_code == 200
    tree = tree_response.json()["tree"]
    root_names = [item["name"] for item in tree]
    assert root_names == [".git", "dist", "empty", "node_modules", "src", "config.yaml", "data.json", "README.md", "unknown.xyz"]
    src = next(item for item in tree if item["name"] == "src")
    assert src["path"] == "src"
    assert [item["name"] for item in src["children"]] == [
        ".env", "app.min.js", "image.png", "large.py", "main.py"
    ]
    node_modules = next(item for item in tree if item["name"] == "node_modules")
    assert node_modules["is_included"] is False
    assert node_modules["exclusion_reason"] == "excluded_directory:node_modules"
    assert node_modules["children"][0]["children"][0]["exclusion_reason"] == "excluded_directory:node_modules"
    assert next(item for item in tree if item["name"] == "empty")["children"] == []


def test_supported_language_map_and_unknown_extensions():
    expected = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".java": "Java",
        ".c": "C", ".cpp": "C++", ".cs": "C#", ".go": "Go", ".rs": "Rust",
        ".php": "PHP", ".rb": "Ruby", ".html": "HTML", ".css": "CSS", ".json": "JSON",
        ".yaml": "YAML", ".md": "Markdown", ".sql": "SQL", ".mystery": "unknown",
    }
    assert {extension: repository_scanner.detect_language(extension) for extension in expected} == expected


def test_default_excluded_directories_and_configurable_exclusions(sample_repository: Path, monkeypatch):
    monkeypatch.setattr(repository_scanner, "EXCLUDED_DIRECTORY_NAMES", {"custom-cache"})
    custom = sample_repository / "custom-cache"
    custom.mkdir()
    (custom / "skip.py").write_text("skip", encoding="utf-8")
    entries, stats, _, _ = repository_scanner.scan_repository_root(sample_repository)
    skipped = next(item for item in entries if item["path"] == "custom-cache/skip.py")
    assert skipped["is_included"] is False
    assert skipped["exclusion_reason"] == "excluded_directory:custom-cache"
    assert stats["total_files"] == 13


def test_scan_is_cached_and_force_scan_replaces_records_without_duplicates(
    sample_repository: Path, monkeypatch: pytest.MonkeyPatch
):
    repository_id = register_repository(sample_repository)
    original = repository_scanner.scan_repository_root
    calls = 0

    def counted_scan(root: Path):
        nonlocal calls
        calls += 1
        return original(root)

    monkeypatch.setattr(repository_scanner, "scan_repository_root", counted_scan)
    first = run_scan(repository_id)
    second = run_scan(repository_id)
    assert first.status_code == second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert calls == 1

    database = main.REPOSITORY_STORAGE / "repository_metadata.sqlite3"
    with sqlite3.connect(database) as connection:
        initial_count = connection.execute(
            "SELECT COUNT(*) FROM repository_files WHERE repository_id = ?", (repository_id,)
        ).fetchone()[0]
    (sample_repository / "new.py").write_text("pass", encoding="utf-8")
    refreshed = run_scan(repository_id, force=True)
    assert refreshed.json()["cached"] is False
    assert calls == 2
    with sqlite3.connect(database) as connection:
        refreshed_count = connection.execute(
            "SELECT COUNT(*) FROM repository_files WHERE repository_id = ?", (repository_id,)
        ).fetchone()[0]
    assert refreshed_count == initial_count + 1
    assert client.get(f"/repositories/{repository_id}/files").json()["total"] == 13


def test_file_listing_pagination_and_content_routes(sample_repository: Path):
    repository_id = register_repository(sample_repository)
    run_scan(repository_id)
    page = client.get(f"/repositories/{repository_id}/files", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    assert len(page.json()["files"]) == 2
    assert page.json()["has_more"] is True
    included = client.get(
        f"/repositories/{repository_id}/files", params={"path": "src", "include_excluded": "false"}
    )
    assert [item["path"] for item in included.json()["files"]] == ["src/large.py", "src/main.py"]

    response = client.get(f"/repositories/{repository_id}/file", params={"path": "src/main.py"})
    assert response.status_code == 200
    assert response.json() == {"repository_id": repository_id, "path": "src/main.py", "content": "print('hi')"}
    legacy = client.get(f"/repositories/{repository_id}/files/src/main.py")
    assert legacy.status_code == 200
    assert legacy.json() == {"path": "src/main.py", "content": "print('hi')"}
    traversal = client.get(f"/repositories/{repository_id}/file", params={"path": "../outside.txt"})
    assert traversal.status_code == 422
    missing = client.get(f"/repositories/{repository_id}/file", params={"path": "missing.py"})
    assert missing.status_code == 404


def test_missing_repository_returns_consistent_not_found():
    missing_id = "repo_missing"
    assert run_scan(missing_id).status_code == 404
    assert client.get(f"/repositories/{missing_id}/summary").status_code == 404
    assert client.get(f"/repositories/{missing_id}/tree").status_code == 404
    assert client.get(f"/repositories/{missing_id}/files").status_code == 404
    assert client.get(f"/repositories/{missing_id}/file", params={"path": "x.py"}).status_code == 404


def test_symlink_to_external_directory_is_listed_but_never_followed(sample_repository: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret", encoding="utf-8")
    link = sample_repository / "external-link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        if os.name != "nt":
            pytest.skip(f"symlink creation is unavailable: {exc}")
        junction = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if junction.returncode:
            pytest.skip(f"symlink/junction creation is unavailable: {exc}; {junction.stderr.strip()}")

    repository_id = register_repository(sample_repository)
    result = run_scan(repository_id)
    assert result.status_code == 200
    assert result.json()["statistics"]["symlink_files"] == 1
    assert "external-link/secret.py" not in {item["path"] for item in main.get_store().get_entries(repository_id)}
    tree = client.get(f"/repositories/{repository_id}/tree").json()["tree"]
    link_node = next(item for item in tree if item["name"] == "external-link")
    assert link_node["type"] == "symlink"
    assert link_node["exclusion_reason"] == "symbolic_link_not_followed"
    assert client.get(f"/repositories/{repository_id}/file", params={"path": "external-link/secret.py"}).status_code == 404



def test_phase1_zip_repository_scans_by_repository_id():
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zip-project/main.py", "print('zip')")
        archive.writestr("zip-project/README.md", "zip docs")
    archive_buffer.seek(0)
    uploaded = client.post(
        "/repositories/upload",
        files={"file": ("zip-project.zip", archive_buffer, "application/zip")},
    )
    assert uploaded.status_code == 201
    repository_id = uploaded.json()["id"]
    scanned = run_scan(repository_id)
    assert scanned.status_code == 200
    assert scanned.json()["statistics"]["included_files"] == 2
    assert client.get(f"/repositories/{repository_id}/tree").status_code == 200


def test_scan_entry_limit_returns_413_and_persists_failed_state(
    sample_repository: Path, monkeypatch: pytest.MonkeyPatch
):
    repository_id = register_repository(sample_repository)
    monkeypatch.setattr(repository_scanner, "MAX_SCAN_ENTRIES", 3)
    response = run_scan(repository_id)
    assert response.status_code == 413
    assert "scan entry limit" in response.json()["detail"]
    assert main.get_store().get_scan(repository_id)["status"] == "failed"
