from __future__ import annotations

import sqlite3
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import embedding_provider
import main
import repository_parse_service
import repository_scanner
import search_service
from main import app
from search_index import build_search_documents
from search_service import normalize_scores


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(main, "REPOSITORY_STORAGE", tmp_path / "storage")
    monkeypatch.setattr(embedding_provider, "get_embedding_provider", lambda: None)


def register_and_index(root: Path) -> str:
    registered = client.post("/repositories/local", json={"path": str(root)})
    assert registered.status_code == 201, registered.text
    repository_id = registered.json()["id"]
    scanned = client.post(f"/repositories/{repository_id}/scan")
    assert scanned.status_code == 200, scanned.text
    parsed = client.post(f"/repositories/{repository_id}/parse")
    assert parsed.status_code == 200, parsed.text
    return repository_id


def test_chunks_partition_source_exactly_and_prefer_symbol_boundaries():
    source = "".join(f"line {line}\n" for line in range(1, 13))
    symbols = [
        {"symbol_name": "first", "qualified_name": "first", "symbol_type": "function",
         "start_line": 1, "end_line": 4, "parent_symbol_id": None},
        {"symbol_name": "second", "qualified_name": "second", "symbol_type": "function",
         "start_line": 5, "end_line": 8, "parent_symbol_id": None},
    ]

    chunks = build_search_documents(source, symbols, "src/example.py", "Python", "hash", max_lines=4)

    assert [(chunk["start_line"], chunk["end_line"]) for chunk in chunks] == [(1, 4), (5, 8), (9, 12)]
    assert "".join(chunk["content"] for chunk in chunks) == source
    assert all(chunk["content"].splitlines()[0] == f"line {chunk['start_line']}" for chunk in chunks)
    assert chunks == build_search_documents(source, symbols, "src/example.py", "Python", "hash", max_lines=4)


def test_chunk_line_numbers_follow_lf_rows_and_preserve_crlf_bytes():
    source = "first\rsecond\r\nthird"
    chunks = build_search_documents(source, [], "raw.txt", "Text", "hash", max_lines=1)

    assert [(chunk["start_line"], chunk["end_line"]) for chunk in chunks] == [(1, 1), (2, 2)]
    assert "".join(chunk["content"] for chunk in chunks) == source


def test_lexical_and_symbol_search_share_one_exact_result(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = "def verify_password(password, candidate):\n    return password == candidate\n"
    (root / "auth.py").write_bytes(source.encode("utf-8"))
    repository_id = register_and_index(root)

    response = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "verify password", "retrieval_method": "hybrid"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["semantic_available"] is False
    assert body["available_methods"] == ["lexical", "symbol"]
    assert body["effective_weights"] == {"lexical": pytest.approx(2 / 3), "symbol": pytest.approx(1 / 3)}
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["repository_id"] == repository_id
    assert result["file_path"] == "auth.py"
    assert result["symbol_name"] == "verify_password"
    assert result["symbol_type"] == "function"
    assert result["snippet"] == source
    assert (result["start_line"], result["end_line"]) == (1, 2)
    assert result["retrieval_methods"] == ["lexical", "symbol"]
    assert result["bm25_score"] is not None
    assert result["symbol_score"] == 1.0
    assert result["final_score"] == pytest.approx(
        (0.4 * result["bm25_score"] + 0.2 * result["symbol_score"]) / 0.6
    )

    symbol_only = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "verify_password", "retrieval_method": "symbol"},
    )
    assert symbol_only.status_code == 200
    assert [item["symbol_name"] for item in symbol_only.json()["results"]] == ["verify_password"]


def test_semantic_search_uses_mock_provider_without_network_or_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class MockProvider:
        provider_id = "mock"
        model_id = "test-v1"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] if "secure" in text.casefold() or "password" in text.casefold()
                     else [0.0, 1.0] for text in texts]

    provider = MockProvider()
    monkeypatch.setattr(embedding_provider, "get_embedding_provider", lambda: provider)
    root = tmp_path / "project"
    root.mkdir()
    (root / "security.py").write_text(
        "def check_secret(secret):\n    secure_match = secret == 'x'\n    return secure_match\n",
        encoding="utf-8",
    )
    (root / "maths.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    repository_id = register_and_index(root)

    response = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "Where is password verification handled?", "retrieval_method": "semantic"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["semantic_available"] is True
    assert body["available_methods"] == ["lexical", "symbol", "semantic"]
    assert body["results"][0]["file_path"] == "security.py"
    assert body["results"][0]["semantic_score"] == 1.0
    hybrid = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "check_secret secure", "retrieval_method": "hybrid"},
    )
    assert hybrid.status_code == 200
    combined = hybrid.json()
    assert combined["effective_weights"] == {"lexical": 0.4, "semantic": 0.4, "symbol": 0.2}
    result = combined["results"][0]
    assert result["final_score"] == pytest.approx(
        0.4 * result["bm25_score"]
        + 0.4 * result["semantic_score"]
        + 0.2 * result["symbol_score"]
    )


def test_semantic_unavailable_does_not_break_other_methods(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "auth.py").write_text("def verify_password(value):\n    return bool(value)\n", encoding="utf-8")
    repository_id = register_and_index(root)

    hybrid = client.post(f"/repositories/{repository_id}/search", json={"query": "verify password"})
    semantic = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "verify password", "retrieval_method": "semantic"},
    )

    assert hybrid.status_code == 200
    assert hybrid.json()["results"]
    assert hybrid.json()["semantic_available"] is False
    assert semantic.status_code == 503


def test_search_validates_unchanged_candidate_files_once_per_distinct_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()
    source = "\n".join(
        ["def freshness_marker_one():", "    return 'freshness_marker_two'"]
        + [f"# filler {line}" for line in range(1, 90)]
    ) + "\n"
    (root / "main.py").write_text(source, encoding="utf-8")
    repository_id = register_and_index(root)

    checked: list[str] = []
    original_reader = search_service._read_source

    def counted_reader(root_path: str, file_path: str) -> bytes:
        checked.append(file_path)
        return original_reader(root_path, file_path)

    monkeypatch.setattr(search_service, "_read_source", counted_reader)
    response = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "freshness_marker", "retrieval_method": "lexical", "limit": 10},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["results"]) >= 1
    assert checked == ["main.py"]


def test_search_rejects_modified_source_without_scan_even_if_size_and_mtime_match(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def old_marker():\n    return 'old'\n", encoding="utf-8")
    repository_id = register_and_index(root)
    original_stat = source.stat()
    source.write_text("def old_marker():\n    return 'new'\n", encoding="utf-8")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert source.stat().st_size == original_stat.st_size

    response = client.post(
        f"/repositories/{repository_id}/search", json={"query": "old_marker"}
    )

    assert response.status_code == 409
    assert "forced scan followed by parsing" in response.json()["detail"]
    assert "results" not in response.json()


def test_search_rejects_deleted_candidate_without_scan(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def deleted_marker():\n    return True\n", encoding="utf-8")
    repository_id = register_and_index(root)
    source.unlink()

    response = client.post(
        f"/repositories/{repository_id}/search", json={"query": "deleted_marker"}
    )

    assert response.status_code == 409
    assert "forced scan followed by parsing" in response.json()["detail"]


def test_search_rejects_candidate_that_becomes_excluded_without_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "generated.py").write_text("def excluded_marker():\n    return True\n", encoding="utf-8")
    repository_id = register_and_index(root)
    monkeypatch.setattr(repository_scanner, "GENERATED_FILE_PATTERNS", ("generated.py",))

    response = client.post(
        f"/repositories/{repository_id}/search", json={"query": "excluded_marker"}
    )

    assert response.status_code == 409
    assert "forced scan followed by parsing" in response.json()["detail"]


def test_new_source_is_searchable_after_explicit_refresh(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "existing.py").write_text("def existing_marker():\n    return True\n", encoding="utf-8")
    repository_id = register_and_index(root)
    (root / "new_file.py").write_text("def newly_added_marker():\n    return True\n", encoding="utf-8")

    before_refresh = client.post(
        f"/repositories/{repository_id}/search", json={"query": "newly_added_marker"}
    )
    assert before_refresh.status_code == 200
    assert before_refresh.json()["results"] == []

    scanned = client.post(f"/repositories/{repository_id}/scan", params={"force": "true"})
    assert scanned.status_code == 200
    assert client.post(
        f"/repositories/{repository_id}/search", json={"query": "existing_marker"}
    ).status_code == 409
    assert client.post(f"/repositories/{repository_id}/parse").status_code == 200
    refreshed = client.post(
        f"/repositories/{repository_id}/search", json={"query": "newly_added_marker"}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["results"][0]["file_path"] == "new_file.py"


def test_search_fails_closed_when_candidate_validation_cannot_read_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("def protected_marker():\n    return True\n", encoding="utf-8")
    repository_id = register_and_index(root)

    def denied_reader(*_args, **_kwargs):
        raise repository_parse_service.SourceReadError("file_unavailable", "unavailable")

    monkeypatch.setattr(search_service, "_read_source", denied_reader)
    response = client.post(
        f"/repositories/{repository_id}/search", json={"query": "protected_marker"}
    )

    assert response.status_code == 409
    assert "results" not in response.json()


def test_search_returns_no_partial_results_when_any_candidate_is_stale(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("def shared_marker_a():\n    return True\n", encoding="utf-8")
    stale_file = root / "b.py"
    stale_file.write_text("def shared_marker_b():\n    return True\n", encoding="utf-8")
    repository_id = register_and_index(root)
    stale_file.write_text("def shared_marker_b():\n    return False\n", encoding="utf-8")

    response = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "shared_marker", "limit": 10},
    )

    assert response.status_code == 409
    assert "results" not in response.json()


def test_forced_scan_and_parse_restore_search_after_candidate_change(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def previous_marker():\n    return True\n", encoding="utf-8")
    repository_id = register_and_index(root)
    source.write_text("def current_marker():\n    return True\n", encoding="utf-8")

    assert client.post(
        f"/repositories/{repository_id}/search", json={"query": "previous_marker"}
    ).status_code == 409
    assert client.post(
        f"/repositories/{repository_id}/scan", params={"force": "true"}
    ).status_code == 200
    assert client.post(
        f"/repositories/{repository_id}/parse"
    ).status_code == 200

    old = client.post(
        f"/repositories/{repository_id}/search", json={"query": "previous_marker"}
    )
    current = client.post(
        f"/repositories/{repository_id}/search", json={"query": "current_marker"}
    )
    assert old.status_code == current.status_code == 200
    assert old.json()["results"] == []
    assert current.json()["results"][0]["file_path"] == "main.py"


def test_candidate_validation_cost_tracks_matching_files_not_repository_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()
    for index in range(75):
        (root / f"unrelated_{index:03}.py").write_text(
            f"def routine_{index}():\n    return {index}\n", encoding="utf-8"
        )
    (root / "target.py").write_text(
        "def scalable_marker_first():\n    return 'scalable_marker_second'\n", encoding="utf-8"
    )
    repository_id = register_and_index(root)
    read_paths: list[str] = []
    original_reader = search_service._read_source

    def counted_reader(root_path: str, file_path: str) -> bytes:
        read_paths.append(file_path)
        return original_reader(root_path, file_path)

    monkeypatch.setattr(search_service, "_read_source", counted_reader)
    response = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "scalable_marker", "retrieval_method": "lexical"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["results"]
    assert read_paths == ["target.py"]


def test_lexical_index_includes_scanned_text_without_a_tree_sitter_grammar(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = '<main>rare_authentication_marker</main>\n'
    (root / "index.html").write_bytes(source.encode("utf-8"))
    repository_id = register_and_index(root)

    response = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "rare_authentication_marker", "retrieval_method": "lexical"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["file_path"] == "index.html"
    assert response.json()["results"][0]["snippet"] == source
    assert response.json()["results"][0]["start_line"] == 1
    assert response.json()["results"][0]["end_line"] == 1


def test_repository_isolation_and_search_filters(tmp_path: Path):
    first_root = tmp_path / "first"
    (first_root / "src").mkdir(parents=True)
    (first_root / "tools").mkdir()
    (first_root / "src" / "auth.py").write_text(
        "class Auth:\n    def verify(self, token):\n        return bool(token)\n", encoding="utf-8"
    )
    (first_root / "tools" / "auth.py").write_text(
        "def verify_token(token):\n    return token is not None\n", encoding="utf-8"
    )
    first_id = register_and_index(first_root)

    second_root = tmp_path / "second"
    second_root.mkdir()
    (second_root / "private.py").write_text("def verify_token():\n    return 'other repo'\n", encoding="utf-8")
    second_id = register_and_index(second_root)

    isolated = client.post(
        f"/repositories/{first_id}/search", json={"query": "verify token", "limit": 10}
    ).json()["results"]
    assert isolated
    assert {item["repository_id"] for item in isolated} == {first_id}
    assert all(item["file_path"] != "private.py" for item in isolated)

    prefix = client.post(
        f"/repositories/{first_id}/search",
        json={"query": "verify token", "path_prefix": "src", "symbol_type": "method"},
    ).json()["results"]
    assert prefix
    assert all(item["file_path"].startswith("src/") and item["symbol_type"] == "method" for item in prefix)

    language = client.post(
        f"/repositories/{second_id}/search",
        json={"query": "verify token", "language": "Python", "file_path": "private.py"},
    ).json()["results"]
    assert language and all(item["language"] == "Python" for item in language)


def test_changed_source_refreshes_index_without_stale_terms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def old_marker():\n    return 1\n", encoding="utf-8")
    repository_id = register_and_index(root)

    calls = 0
    real_parser = repository_parse_service.parse_source

    def counted_parser(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_parser(*args, **kwargs)

    monkeypatch.setattr(repository_parse_service, "parse_source", counted_parser)
    source.write_text("def new_marker():\n    return 2\n", encoding="utf-8")
    rescanned = client.post(f"/repositories/{repository_id}/scan", params={"force": "true"})
    assert rescanned.status_code == 200
    assert client.post(
        f"/repositories/{repository_id}/search", json={"query": "old_marker"}
    ).status_code == 409
    updated = client.post(f"/repositories/{repository_id}/parse")
    assert updated.status_code == 200
    assert calls == 1
    assert client.post(f"/repositories/{repository_id}/search", json={"query": "old_marker"}).json()["results"] == []
    new_results = client.post(
        f"/repositories/{repository_id}/search", json={"query": "new_marker"}
    ).json()["results"]
    assert new_results and new_results[0]["snippet"].startswith("def new_marker")


def test_deleted_or_failed_source_cannot_leave_search_documents(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def obsolete_marker():\n    return True\n", encoding="utf-8")
    repository_id = register_and_index(root)
    source.unlink()
    assert client.post(
        f"/repositories/{repository_id}/scan", params={"force": "true"}
    ).status_code == 200
    assert client.post(
        f"/repositories/{repository_id}/search", json={"query": "obsolete_marker"}
    ).status_code == 409

    parsed = client.post(f"/repositories/{repository_id}/parse")
    assert parsed.status_code == 200
    assert parsed.json()["statistics"]["failed_files"] == 0
    results = client.post(
        f"/repositories/{repository_id}/search", json={"query": "obsolete_marker"}
    ).json()["results"]
    assert results == []
    with sqlite3.connect(main.REPOSITORY_STORAGE / "repository_metadata.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM search_documents WHERE repository_id=?", (repository_id,)
        ).fetchone()[0] == 0


def test_file_that_fails_utf8_parsing_loses_old_search_documents(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def stable_marker():\n    return True\n", encoding="utf-8")
    repository_id = register_and_index(root)
    source.write_bytes(b"\xff\xfe\xfd\n")

    parsed = client.post(f"/repositories/{repository_id}/parse")

    assert parsed.status_code == 200
    assert parsed.json()["statistics"]["failed_files"] == 1
    assert client.post(
        f"/repositories/{repository_id}/search", json={"query": "stable_marker"}
    ).json()["results"] == []


def test_excluded_source_is_removed_after_rescan_and_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    private = root / "private"
    private.mkdir(parents=True)
    (private / "secret.py").write_text("def secret_marker():\n    return 'hidden'\n", encoding="utf-8")
    repository_id = register_and_index(root)
    monkeypatch.setattr(repository_scanner, "EXCLUDED_DIRECTORY_NAMES", {
        *repository_scanner.EXCLUDED_DIRECTORY_NAMES, "private",
    })

    scanned = client.post(f"/repositories/{repository_id}/scan", params={"force": "true"})
    assert scanned.status_code == 200
    assert client.post(f"/repositories/{repository_id}/search", json={"query": "secret_marker"}).status_code == 409
    parsed = client.post(f"/repositories/{repository_id}/parse")
    assert parsed.status_code == 200
    assert client.post(f"/repositories/{repository_id}/search", json={"query": "secret_marker"}).json()["results"] == []


def test_cached_parse_backfills_search_index_without_tree_sitter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("def cached_marker():\n    return 5\n", encoding="utf-8")
    repository_id = register_and_index(root)
    database = main.REPOSITORY_STORAGE / "repository_metadata.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM search_index_files WHERE repository_id=?", (repository_id,))

    def unexpected_parser(*_args, **_kwargs):
        raise AssertionError("cached source must not be reparsed")

    monkeypatch.setattr(repository_parse_service, "parse_source", unexpected_parser)
    refreshed = client.post(f"/repositories/{repository_id}/parse")

    assert refreshed.status_code == 200
    assert refreshed.json()["statistics"]["cached_files"] == 1
    results = client.post(
        f"/repositories/{repository_id}/search", json={"query": "cached_marker"}
    ).json()["results"]
    assert results and results[0]["symbol_name"] == "cached_marker"

    class MockProvider:
        provider_id = "mock-refresh"
        model_id = "test-v1"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embedding_provider, "get_embedding_provider", lambda: MockProvider())
    refreshed_embeddings = client.post(f"/repositories/{repository_id}/parse")
    assert refreshed_embeddings.status_code == 200
    assert refreshed_embeddings.json()["statistics"]["cached_files"] == 1
    semantic = client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "cached marker", "retrieval_method": "semantic"},
    )
    assert semantic.status_code == 200, semantic.text
    assert semantic.json()["semantic_available"] is True


def test_score_normalization_and_invalid_search_requests(tmp_path: Path):
    assert normalize_scores({10: 1.0, 11: 2.0, 12: 3.0}) == {10: 0.0, 11: 0.5, 12: 1.0}
    assert normalize_scores({10: 4.0, 11: 4.0}) == {10: 1.0, 11: 1.0}
    assert normalize_scores({10: -0.2, 11: -0.2}) == {10: 1.0, 11: 1.0}
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("pass\n", encoding="utf-8")
    repository_id = register_and_index(root)

    assert client.post(f"/repositories/{repository_id}/search", json={"query": "   "}).status_code == 422
    assert client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "pass", "file_path": "../outside.py"},
    ).status_code == 422
    assert client.post(
        f"/repositories/{repository_id}/search",
        json={"query": "pass", "file_path": "main.py", "path_prefix": "src"},
    ).status_code == 422
