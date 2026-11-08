import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from backend import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "LOGS_DIR", "BEETS_INBOX_DIR", "SLSKD_DOWNLOADS_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(main, name, str(directory))
    monkeypatch.setattr(main, "SLSKD_WEBHOOK_SECRET", "test-secret", raising=False)
    monkeypatch.setattr(main, "BEETS_ENABLED", True)
    with patch.object(main, "run_pipeline_job", create=True):
        yield TestClient(main.app)


def test_webhook_authenticates_and_deduplicates_without_trusting_filename(client):
    event = {"id": "event-1", "type": "DownloadFileComplete", "localFilename": "/private/secret"}
    assert client.post("/api/slskd/events", json=event).status_code == 401
    headers = {"X-Webhook-Key": "test-secret"}
    first = client.post("/api/slskd/events", json=event, headers=headers)
    second = client.post("/api/slskd/events", json=event, headers=headers)
    assert first.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    job = main.get_job_store().get(first.json()["task_id"])
    assert job["kind"] == "import"
    assert "/private/secret" not in str(job["payload"])


def test_download_is_durable_and_public_status_hides_credentials(client):
    response = client.post("/api/download", json={"tool": "ytdl", "url": "https://example.invalid/song", "navi_pass": "private"})
    assert response.status_code == 200
    identity = response.json()["task_id"]
    saved = main.get_job_store().get(identity)
    assert saved["kind"] == "download"
    status = client.get("/api/jobs/" + identity)
    assert status.status_code == 200
    assert "private" not in str(status.json())
    assert client.get("/api/version").json()["version"] == main.APP_VERSION


def test_retry_reuses_job_and_keeps_finished_tracks(client):
    store = main.get_job_store()
    job = store.enqueue("download", {})
    store.set_tracks(job["id"], [{"title": "First"}, {"title": "Second"}])
    store.update_track(job["id"], 0, status="imported", result={"paths": []})
    store.update_track(job["id"], 1, status="failed", error="offline")
    store.update(job["id"], status="failed")
    assert client.post("/api/jobs/" + job["id"] + "/retry").status_code == 200
    assert [item["status"] for item in store.tracks(job["id"])] == ["imported", "queued"]


def test_large_playlist_details_are_paginated(client):
    store = main.get_job_store()
    job = store.enqueue("download", {})
    store.set_tracks(job["id"], [{"title": f"Song {index}"} for index in range(1000)])
    page = client.get("/api/jobs/" + job["id"] + "?offset=100&limit=100").json()
    assert page["tracks_total"] == 1000
    assert len(page["tracks"]) == 100
    assert page["tracks"][0]["position"] == 100
    assert page["track_counts"] == {"queued": 1000}
    assert client.get("/api/jobs/" + job["id"] + "?limit=10000").status_code == 422
