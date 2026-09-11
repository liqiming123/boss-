import hashlib
import json

from conftest import login


def test_extension_release_metadata_and_download(client, session, tmp_path, monkeypatch):
    package = tmp_path / "latest.zip"
    payload = b"zip-payload"
    package.write_bytes(payload)
    metadata = tmp_path / "release.json"
    metadata.write_text(json.dumps({"version": "0.11.5", "published_at": "2026-09-04T00:00:00+08:00", "sha256": hashlib.sha256(payload).hexdigest()}))
    settings = __import__("recruitment_collab.api.routes", fromlist=["get_settings"]).get_settings()
    monkeypatch.setattr("recruitment_collab.api.routes.get_settings", lambda: settings.model_copy(update={"extension_package_path": str(package), "extension_release_metadata_path": str(metadata)}))
    headers = login(client, "admin@example.com")
    info = client.get("/api/v1/admin/extension-release", headers=headers)
    assert info.status_code == 200
    assert info.headers["cache-control"] == "no-store, max-age=0"
    assert info.json()["version"] == "0.11.5"
    response = client.get("/api/v1/admin/extension-release/download", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["content-type"] == "application/zip"
    assert response.content == payload


def test_extension_release_requires_auth(client):
    assert client.get("/api/v1/admin/extension-release").status_code == 401
