"""Music-library routes use local files and enqueue edits through the durable worker."""

import io
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import APIC
from mutagen.wave import WAVE
from PIL import Image

from backend import main
from backend.tests.test_music_library import write_track


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "LOGS_DIR", "NORMALIZATION_QUEUE_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(main, name, str(directory))
    return TestClient(main.app)


def test_refresh_indexes_real_untracked_music_and_lists_searchable_pages(client):
    write_track(Path(main.DOWNLOADS_DIR) / "one.wav", title="Łódź", artist="Björk")
    write_track(Path(main.DOWNLOADS_DIR) / "two.wav", title="Second", album="")
    refreshed = client.post("/api/music/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["total"] == 2
    assert refreshed.json()["incomplete"] == 1
    page = client.get("/api/music/tracks", params={"q": "LODZ", "limit": 1})
    assert page.status_code == 200
    data = page.json()
    assert data["total"] == 1
    assert data["offset"] == 0 and data["limit"] == 1
    assert data["indexed_at"]
    row, = data["items"]
    assert row["title"] == "Łódź"
    assert row["relative_path"] == "one.wav"
    assert str(main.DOWNLOADS_DIR) not in page.text
    missing = client.get("/api/music/tracks", params={"incomplete": True}).json()
    assert missing["total"] == 1
    assert missing["items"][0]["relative_path"] == "two.wav"
    assert client.get("/api/music/tracks", params={"offset": 20}).json()["items"] == []


@pytest.mark.parametrize("params", [{"offset": -1}, {"limit": 0}, {"limit": 501}, {"incomplete": "invalid"}])
def test_invalid_library_pagination_and_filter_do_not_start_a_scan(client, params):
    assert client.get("/api/music/tracks", params=params).status_code == 422


def test_empty_library_and_missing_track_covers_are_normal_states(client):
    response = client.get("/api/music/tracks")
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []
    assert client.get("/api/music/tracks/missing-id/cover").status_code == 404


def test_embedded_cover_is_returned_as_bounded_local_thumbnail(client):
    source = write_track(Path(main.DOWNLOADS_DIR) / "art.wav")
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 1200), "blue").save(buffer, format="JPEG")
    tags = WAVE(source)
    tags.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=buffer.getvalue()))
    tags.save()
    assert client.post("/api/music/refresh").status_code == 200
    row, = client.get("/api/music/tracks").json()["items"]
    assert row["cover_url"].startswith("/api/music/tracks/")
    response = client.get(row["cover_url"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    with Image.open(io.BytesIO(response.content)) as image:
        assert max(image.size) <= 512


def test_cover_route_does_not_follow_library_file_or_adjacent_cover_symlinks(client, tmp_path):
    source = write_track(Path(main.DOWNLOADS_DIR) / "plain.wav")
    outside = write_track(tmp_path / "outside.wav", title="Private")
    private_cover = tmp_path / "private.jpg"
    Image.new("RGB", (20, 20), "red").save(private_cover)
    (source.parent / "cover.jpg").symlink_to(private_cover)
    assert client.post("/api/music/refresh").status_code == 200
    row, = client.get("/api/music/tracks").json()["items"]
    endpoint = f"/api/music/tracks/{row['id']}/cover"
    assert client.get(endpoint, params={"path": str(private_cover), "url": "https://example.invalid/private.jpg"}).status_code == 404
    source.unlink()
    source.symlink_to(outside)
    assert client.get(endpoint).status_code in {404, 409}


@pytest.mark.parametrize("payload", [
    {"action": "delete"}, {"action": "title", "path": "/outside.wav"},
    {"action": "musicbrainz", "url": 42}, {},
])
def test_preview_rejects_unknown_actions_and_arbitrary_paths(client, payload):
    assert client.post("/api/music/tracks/unknown/preview", json=payload).status_code == 422


@pytest.mark.parametrize("payload", [
    {}, {"preview_token": 3}, {"preview_token": "saved", "path": "/outside.wav"},
])
def test_apply_requires_saved_preview_and_rejects_edit_injection(client, payload):
    assert client.post("/api/music/tracks/unknown/apply", json=payload).status_code == 422


def test_preview_and_apply_use_saved_server_decision_and_enqueue_without_editing(client, monkeypatch):
    source = write_track(Path(main.DOWNLOADS_DIR) / "pending.wav", album="")
    original = source.read_bytes()
    assert client.post("/api/music/refresh").status_code == 200
    row, = client.get("/api/music/tracks").json()["items"]
    calls = []

    class Editor:
        def preview(self, track_id, *, action, url="", candidate_key=""):
            calls.append(("preview", track_id, action, url, candidate_key))
            return {"preview_token": "server-decision", "before": {"album": ""}, "after": {"album": "Song"}}

        def validate_preview(self, track_id, preview_token):
            calls.append(("validate", track_id, preview_token))
            assert preview_token == "server-decision"
            return {"track_id": track_id, "preview_token": preview_token}

    monkeypatch.setattr(main, "get_music_editor", lambda: Editor())
    base = f"/api/music/tracks/{row['id']}"
    preview = client.post(base + "/preview", json={"action": "title"})
    assert preview.status_code == 200
    assert preview.json()["preview_token"] == "server-decision"
    assert calls[0] == ("preview", row["id"], "title", "", "")
    response = client.post(base + "/apply", json={
        "preview_token": "server-decision", "navi_account_id": "selected-account", "navi_sync": True,
    })
    assert response.status_code in {200, 202}
    assert calls[-1] == ("validate", row["id"], "server-decision")
    job = main.get_job_store().get(response.json()["task_id"])
    assert job["kind"] == "music_edit"
    assert job["status"] == "queued"
    assert job["payload"]["track_id"] == row["id"]
    assert job["payload"]["preview_token"] == "server-decision"
    assert job["payload"]["navi_account_id"] == "selected-account"
    assert job["payload"]["navi_sync"] is True
    assert source.read_bytes() == original


def test_rejected_stale_preview_never_creates_an_edit_job(client, monkeypatch):
    write_track(Path(main.DOWNLOADS_DIR) / "stale.wav")
    assert client.post("/api/music/refresh").status_code == 200
    row, = client.get("/api/music/tracks").json()["items"]

    class Editor:
        def validate_preview(self, track_id, preview_token):
            raise ValueError("File version changed; generate a new preview")

    monkeypatch.setattr(main, "get_music_editor", lambda: Editor())
    response = client.post(f"/api/music/tracks/{row['id']}/apply", json={"preview_token": "stale-token"})
    assert response.status_code == 409
    assert not main.get_job_store().list()


def test_unknown_preview_token_cannot_create_an_edit_job(client, monkeypatch):
    write_track(Path(main.DOWNLOADS_DIR) / "plain.wav")
    assert client.post("/api/music/refresh").status_code == 200
    row, = client.get("/api/music/tracks").json()["items"]

    class Editor:
        def validate_preview(self, track_id, preview_token):
            raise KeyError(preview_token)

    monkeypatch.setattr(main, "get_music_editor", lambda: Editor())
    response = client.post(f"/api/music/tracks/{row['id']}/apply", json={"preview_token": "unknown-token"})
    assert response.status_code in {404, 409}
    assert not main.get_job_store().list()


def test_music_edit_worker_normalizes_then_scans_once_without_reapplying_tags(client, monkeypatch):
    write_track(Path(main.DOWNLOADS_DIR) / "edited.wav")
    monkeypatch.setattr(main, "PIPELINE_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", True)
    applied = []
    scans = []

    class Editor:
        def apply(self, track_id, preview_token, *, store, job_id):
            current = store.get(job_id)
            assert current["status"] == "running"
            assert current["stage"] == "importing"
            applied.append((track_id, preview_token, job_id))
            return {"paths": ["edited.wav"], "library_changed": True}

    def navidrome_services(payload):
        assert payload["navi_account_id"] == "selected-account"
        assert payload["navi_sync"] is True

        def call(endpoint, params=None):
            scans.append(endpoint)
            return {"subsonic-response": {"status": "ok", "scanStatus": {"scanning": False}}}

        return call, None

    monkeypatch.setattr(main, "get_music_editor", lambda: Editor())
    monkeypatch.setattr(main, "navi_services", navidrome_services)
    store = main.get_job_store()
    job = store.enqueue("music_edit", {
        "track_id": "track-id", "preview_token": "stored-decision",
        "navi_account_id": "selected-account", "navi_sync": True,
    })
    main.run_pipeline_job(job["id"])
    waiting = store.get(job["id"])
    assert waiting["status"] == "waiting"
    assert waiting["stage"] == "normalizing"
    assert waiting["result"]["paths"] == ["edited.wav"]
    assert waiting["result"]["library_changed"] is True
    assert scans == []
    pending, = (Path(main.NORMALIZATION_QUEUE_DIR) / "pending").glob("*.json")
    request = json.loads(pending.read_text())
    assert request["paths"] == ["edited.wav"]
    done = Path(main.NORMALIZATION_QUEUE_DIR) / "done"
    done.mkdir()
    (done / pending.name).write_text(json.dumps({"id": request["id"], "status": "done"}))
    store.update(job["id"], next_attempt=0)
    main.run_pipeline_job(job["id"])
    finished = store.get(job["id"])
    assert finished["status"] == finished["stage"] == "done"
    assert finished["result"]["normalized"] is True
    assert applied == [("track-id", "stored-decision", job["id"])]
    assert scans == ["startScan.view", "getScanStatus.view"]


@pytest.mark.parametrize("error, expected_status, expected_failures", [
    (ValueError("Preview became stale"), "needs_review", 0),
    (RuntimeError("Temporary disk failure"), "waiting", 1),
])
def test_music_edit_worker_distinguishes_stale_decision_from_transient_failure(client, monkeypatch, error, expected_status, expected_failures):
    monkeypatch.setattr(main, "PIPELINE_JOB_LOCK", threading.Lock())

    class Editor:
        def apply(self, *_args, **_kwargs):
            raise error

    monkeypatch.setattr(main, "get_music_editor", lambda: Editor())
    store = main.get_job_store()
    job = store.enqueue("music_edit", {"track_id": "track", "preview_token": "token"})
    main.run_pipeline_job(job["id"])
    failed = store.get(job["id"])
    assert failed["status"] == expected_status
    assert failed["failures"] == expected_failures
    assert str(error) in failed["error"]
    if expected_status == "waiting":
        assert failed["next_attempt"] > failed["updated"]
