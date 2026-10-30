from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import repository_ingestion
from main import app
from repository_store import RepositoryStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_repository_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(main, "REPOSITORY_STORAGE", tmp_path / "repository-storage")
    yield


def make_zip(entries: dict[str, bytes], filename: str = "project.zip") -> tuple[str, io.BytesIO]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    buffer.seek(0)
    return filename, buffer


def test_register_local_repository_and_persist_metadata(tmp_path: Path):
    local_repo = tmp_path / "local-project"
    (local_repo / "src").mkdir(parents=True)
    (local_repo / "src" / "app.py").write_text("print('safe')", encoding="utf-8")

    response = client.post("/repositories/local", json={"path": str(local_repo)})
    assert response.status_code == 201
    metadata = response.json()
    assert metadata["name"] == "local-project"
    assert metadata["root_path"] == str(local_repo.resolve())
    assert metadata["input_type"] == "local"
    assert metadata["status"] == "ready"

    details = client.get(f"/repositories/{metadata['id']}")
    assert details.status_code == 200
    assert details.json() == metadata
    # Reopening the store simulates a fresh process and confirms durable metadata.
    persisted = RepositoryStore(main.REPOSITORY_STORAGE / "repository_metadata.sqlite3")
    assert persisted.get(metadata["id"]) == metadata
    assert client.get("/repositories").json()["total"] == 1


def test_local_registration_rejects_missing_and_empty_paths(tmp_path: Path):
    missing = client.post("/repositories/local", json={"path": str(tmp_path / "missing")})
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    empty = client.post("/repositories/local", json={"path": str(empty_dir)})
    assert missing.status_code == 404
    assert empty.status_code == 422


def test_upload_valid_zip_extracts_nested_files_and_persists_status():
    filename, archive = make_zip({"demo/README.md": b"readme", "demo/src/main.py": b"pass"}, "demo.zip")
    response = client.post("/repositories/upload", files={"file": (filename, archive, "application/zip")})

    assert response.status_code == 201
    metadata = response.json()
    assert metadata["name"] == "demo"
    assert metadata["input_type"] == "zip"
    assert metadata["status"] == "ready"
    root = Path(metadata["root_path"])
    assert (root / "demo" / "README.md").read_bytes() == b"readme"
    assert (root / "demo" / "src" / "main.py").read_bytes() == b"pass"
    assert client.get(f"/repositories/{metadata['id']}").json()["status"] == "ready"


def test_upload_rejects_invalid_archive_and_records_failure():
    response = client.post(
        "/repositories/upload",
        files={"file": ("broken.zip", b"not a zip", "application/zip")},
    )
    assert response.status_code == 400
    failed = client.get("/repositories").json()["repositories"]
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"
    assert failed[0]["error_message"] == "Uploaded file is not a valid ZIP archive"


def test_zip_slip_entry_is_rejected_without_writing_outside_storage():
    filename, archive = make_zip({"../escape.txt": b"escape"})
    response = client.post("/repositories/upload", files={"file": (filename, archive, "application/zip")})
    assert response.status_code == 400
    assert not (main.REPOSITORY_STORAGE / "escape.txt").exists()
    assert all(item["status"] == "failed" for item in client.get("/repositories").json()["repositories"])


def test_zip_absolute_path_and_symlink_entries_are_rejected():
    for index, entry in enumerate(("/absolute.txt", "C:/drive.txt", "folder/./dot.txt")):
        filename, archive = make_zip({entry: b"unsafe"}, f"unsafe{index}.zip")
        response = client.post("/repositories/upload", files={"file": (filename, archive, "application/zip")})
        assert response.status_code == 400

    link = io.BytesIO()
    with zipfile.ZipFile(link, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../../target")
    link.seek(0)
    response = client.post("/repositories/upload", files={"file": ("link.zip", link, "application/zip")})
    assert response.status_code == 400


def test_zip_size_limit_is_enforced_and_partial_extract_is_removed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(repository_ingestion, "MAX_ZIP_EXTRACTED_BYTES", 4)
    filename, archive = make_zip({"large.txt": b"12345"})
    response = client.post("/repositories/upload", files={"file": (filename, archive, "application/zip")})
    assert response.status_code == 413
    rows = client.get("/repositories").json()["repositories"]
    assert rows[0]["status"] == "failed"
    assert not Path(rows[0]["root_path"]).exists()


def test_unsupported_upload_and_missing_metadata_errors():
    response = client.post("/repositories/upload", files={"file": ("project.tar", b"data", "application/octet-stream")})
    assert response.status_code == 415
    assert client.get("/repositories/repo_missing").status_code == 404


def test_repository_ids_are_unique_for_repeated_local_registration(tmp_path: Path):
    local_repo = tmp_path / "shared-project"
    local_repo.mkdir()
    (local_repo / "README.md").write_text("project", encoding="utf-8")
    first = client.post("/repositories/local", json={"path": str(local_repo)})
    second = client.post("/repositories/local", json={"path": str(local_repo)})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

