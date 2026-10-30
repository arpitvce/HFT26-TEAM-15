from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_valid_github_url():
    response = client.post("/github", json={"url": "https://github.com/username/repository"})
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "GitHub repository URL received successfully",
        "repository": {
            "owner": "username",
            "name": "repository",
            "url": "https://github.com/username/repository",
        },
    }


def test_trailing_slash_is_normalized():
    response = client.post("/github", json={"url": "https://github.com/username/repository/"})
    assert response.status_code == 200
    assert response.json()["repository"]["url"] == "https://github.com/username/repository"


def test_git_suffix_is_normalized():
    response = client.post("/github", json={"url": "https://github.com/username/repository.git"})
    assert response.status_code == 200
    assert response.json()["repository"] == {
        "owner": "username",
        "name": "repository",
        "url": "https://github.com/username/repository",
    }


def test_invalid_url():
    response = client.post("/github", json={"url": "not a url"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Only HTTPS URLs are supported"


def test_gitlab_url_is_rejected():
    response = client.post("/github", json={"url": "https://gitlab.com/username/repository"})
    assert response.status_code == 422
    assert response.json()["detail"] == "URL must belong to github.com"


def test_empty_url_is_rejected():
    response = client.post("/github", json={"url": "   "})
    assert response.status_code == 422
    assert response.json()["detail"] == "URL must not be empty"


def test_missing_repository_name_is_rejected():
    response = client.post("/github", json={"url": "https://github.com/username"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Missing repository name"


def test_credentials_are_rejected():
    response = client.post("/github", json={"url": "https://user:token@github.com/username/repository"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Credentials are not allowed in the URL"


def test_unexpected_port_is_rejected():
    response = client.post("/github", json={"url": "https://github.com:8443/username/repository"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Ports are not allowed in the URL"


def test_malformed_repository_path_is_rejected():
    response = client.post("/github", json={"url": "https://github.com/username/repository/tree/main"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid repository path"


def test_missing_owner_is_rejected():
    response = client.post("/github", json={"url": "https://github.com//repository"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Missing username"
