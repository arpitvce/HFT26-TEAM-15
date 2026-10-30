from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

import main
from main import app
from repository_service import scan_repository

client = TestClient(app)


@pytest.fixture
def known_repository(tmp_path: Path) -> Path:
    root = tmp_path / "express"
    (root / "lib" / "middleware").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "lib" / "application.js").write_bytes(b"abc")
    (root / "lib" / "middleware" / "init.js").write_bytes(b"12345")
    (root / "README.md").write_bytes(b"readme")
    (root / "LICENSE").write_bytes(b"xx")
    (root / ".git").mkdir()
    (root / ".git" / "ignored.bin").write_bytes(b"ignored")
    return root


def test_scan_counts_size_extensions_and_exclusions(known_repository: Path):
    result = scan_repository(known_repository)
    assert result["statistics"]["total_files"] == 5
    assert result["statistics"]["total_directories"] == 4
    assert result["statistics"]["source_code_files"] == 2
    assert result["statistics"]["total_excluded_files"] == 2
    assert result["statistics"]["total_size_bytes"] == 23
    assert result["extensions"] == {"(no extension)": 1, ".bin": 1, ".js": 2, ".md": 1}


def test_scan_preserves_paths_and_sorts_nested_tree(known_repository: Path):
    tree = scan_repository(known_repository)["tree"]
    assert [node["name"] for node in tree] == [".git", "empty", "lib", "LICENSE", "README.md"]
    assert tree[0]["children"][0]["path"] == ".git/ignored.bin"
    lib = tree[2]
    assert lib["path"] == "lib"
    assert [node["name"] for node in lib["children"]] == ["middleware", "application.js"]
    middleware = lib["children"][0]
    assert middleware["children"][0]["path"] == "lib/middleware/init.js"
    assert tree[1]["children"] == []


def test_clone_response_contains_immediate_summary_and_cached_endpoints(monkeypatch, known_repository: Path, tmp_path: Path):
    monkeypatch.setattr(main, "REPOSITORY_STORAGE", tmp_path / "clones")

    def fake_clone(_url: str, destination: Path) -> Path:
        shutil.copytree(known_repository, destination)
        return destination

    monkeypatch.setattr(main, "clone_repository", fake_clone)

    response = client.post("/repositories/clone", json={"url": "https://github.com/example/express"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["statistics"]["total_files"] == 5
    repository_id = data["repository_id"]

    summary = client.get(f"/repositories/{repository_id}/summary")
    tree = client.get(f"/repositories/{repository_id}/tree")
    assert summary.status_code == tree.status_code == 200
    assert summary.json()["statistics"] == data["statistics"]
    assert tree.json()["tree"] == data["tree"]


def test_clone_failure_does_not_register_scan(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(main, "REPOSITORY_STORAGE", tmp_path / "clones")

    def fail_clone(_url: str, _destination: Path) -> Path:
        raise RuntimeError("not found")

    monkeypatch.setattr(main, "clone_repository", fail_clone)
    response = client.post("/repositories/clone", json={"url": "https://github.com/example/missing"})
    assert response.status_code == 502
    repositories = client.get("/repositories").json()["repositories"]
    assert len(repositories) == 1
    assert repositories[0]["status"] == "failed"


def test_file_content_endpoint_serves_repository_text(monkeypatch, known_repository: Path, tmp_path: Path):
    monkeypatch.setattr(main, "REPOSITORY_STORAGE", tmp_path / "clones")

    def fake_clone(_url: str, destination: Path) -> Path:
        shutil.copytree(known_repository, destination)
        return destination

    monkeypatch.setattr(main, "clone_repository", fake_clone)
    result = client.post("/repositories/clone", json={"url": "https://github.com/example/express"}).json()
    response = client.get(f"/repositories/{result['repository_id']}/files/lib/application.js")
    assert response.status_code == 200
    assert response.json() == {"path": "lib/application.js", "content": "abc"}


def test_file_endpoint_rejects_path_traversal(monkeypatch, known_repository: Path, tmp_path: Path):
    monkeypatch.setattr(main, "REPOSITORY_STORAGE", tmp_path / "clones")

    def fake_clone(_url: str, destination: Path) -> Path:
        shutil.copytree(known_repository, destination)
        return destination

    monkeypatch.setattr(main, "clone_repository", fake_clone)
    result = client.post("/repositories/clone", json={"url": "https://github.com/example/express"}).json()
    response = client.get(f"/repositories/{result['repository_id']}/files/../README.md")
    assert response.status_code == 404
