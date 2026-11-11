"""The browser switch is a durable server setting, including while offline."""

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.job_store import JobStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "LOGS_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(main, name, str(directory))
    monkeypatch.setattr(main, "NORMALIZATION_QUEUE_DIR", str(tmp_path / "normalization"))
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", True)
    monkeypatch.setattr(main, "PIPELINE_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main.accounts, "get_navidrome_account_by_id", lambda *_: None)
    return TestClient(main.app)


def test_offline_switch_persists_across_clients_and_environment_defaults(client, monkeypatch):
    assert client.get("/api/normalizer").json()["enabled"] is True
    response = client.put("/api/normalizer/settings", json={"enabled": False})
    assert response.status_code == 200
    assert response.json() == {"enabled": False}
    assert not Path(main.NORMALIZATION_QUEUE_DIR).exists()
    panel = TestClient(main.app).get("/api/normalizer").json()
    assert panel["enabled"] is False
    assert panel["available"] is False
    assert main.get_job_store().normalization_enabled(True) is False

    assert client.put("/api/normalizer/settings", json={"enabled": True}).json() == {"enabled": True}
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", False)
    assert TestClient(main.app).get("/api/normalizer").json()["enabled"] is True
    assert main.get_job_store().normalization_enabled(False) is True


@pytest.mark.parametrize("default", [False, True])
def test_environment_remains_default_until_browser_saves(client, monkeypatch, default):
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", default)
    assert client.get("/api/normalizer").json()["enabled"] is default


@pytest.mark.parametrize("payload", [
    {}, {"enabled": "false"}, {"enabled": "true"}, {"enabled": 0},
    {"enabled": 1}, {"enabled": None}, {"enabled": []},
    {"enabled": False, "operation": "scan_library"},
])
def test_switch_requires_boolean_and_rejects_unknown_fields(client, payload):
    response = client.put("/api/normalizer/settings", json=payload)
    assert response.status_code == 422
    assert client.get("/api/normalizer").json()["enabled"] is True


@pytest.mark.parametrize("error", [OSError("/private/config/settings"), sqlite3.OperationalError("secret db path")])
def test_failed_save_is_reported_without_overriding_current_setting(client, monkeypatch, error):
    def fail(*_):
        raise error

    with monkeypatch.context() as patcher:
        patcher.setattr(JobStore, "set_normalization_enabled", fail)
        response = client.put("/api/normalizer/settings", json={"enabled": False})
    assert response.status_code == 503
    assert isinstance(response.json()["detail"], str)
    assert str(error) not in response.text
    assert client.get("/api/normalizer").json()["enabled"] is True


@pytest.mark.parametrize("error", [OSError("/private/config/settings"), sqlite3.OperationalError("secret db path")])
def test_failed_setting_read_does_not_report_environment_as_current_state(client, monkeypatch, error):
    def fail(*_):
        raise error

    with monkeypatch.context() as patcher:
        patcher.setattr(JobStore, "normalization_enabled", fail)
        response = client.get("/api/normalizer")
    assert response.status_code == 503
    assert isinstance(response.json()["detail"], str)
    assert str(error) not in response.text
    assert client.get("/api/normalizer").json()["enabled"] is True


@pytest.mark.parametrize("kind", ["download", "import"])
def test_switch_releases_waiting_automatic_job_without_removing_published_request(client, monkeypatch, kind):
    calls = []

    def navi(endpoint, params=None):
        calls.append((endpoint, params))
        data = {"status": "ok"}
        if endpoint == "getScanStatus.view":
            data["scanStatus"] = {"scanning": False}
        elif endpoint == "getPlaylists.view":
            data["playlists"] = {"playlist": []}
        elif endpoint == "createPlaylist.view":
            data["playlist"] = {"id": "created"}
        return {"subsonic-response": data}

    monkeypatch.setattr(main, "navi_services", lambda _: (navi, None))
    music = Path(main.DOWNLOADS_DIR) / "song.mp3"
    music.write_bytes(b"already imported audio")
    store = main.get_job_store()
    job = store.enqueue(kind, {"navi_sync": True, "flags": {"generate_m3u": True}})
    store.set_tracks(job["id"], [{"title": "Song"}])
    store.update_track(job["id"], 0, status="imported",
                       result={"paths": [str(music)], "navidrome_id": "song"})
    store.update(job["id"], stage="normalizing", result={"library_changed": True})

    # Saved ON must win over an OFF environment, not just update the panel.
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", False)
    assert client.put("/api/normalizer/settings", json={"enabled": True}).status_code == 200
    main.run_pipeline_job(job["id"])
    waiting = store.get(job["id"])
    assert (waiting["status"], waiting["stage"]) == ("waiting", "normalizing")
    assert not calls
    pending, = (Path(main.NORMALIZATION_QUEUE_DIR) / "pending").glob("*.json")
    published = pending.read_bytes()

    # Saved OFF must win over an ON environment for the already waiting job.
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", True)
    assert client.put("/api/normalizer/settings", json={"enabled": False}).status_code == 200
    store.update(job["id"], next_attempt=0)
    main.run_pipeline_job(job["id"])
    assert store.get(job["id"])["status"] == "done"
    assert pending.read_bytes() == published
    assert [name for name, _ in calls][:2] == ["startScan.view", "getScanStatus.view"]
    assert next(params for name, params in calls if name == "createPlaylist.view")["songId"] == ["song"]


def test_switch_does_not_disable_manual_normalizer_operations(client):
    queue = Path(main.NORMALIZATION_QUEUE_DIR)
    for name in ("pending", "processing", "done", "progress"):
        (queue / name).mkdir(parents=True)
    (queue / "heartbeat.json").write_text(json.dumps({
        "protocol_version": 2, "version": "2.3", "updated_at": time.time(),
        "capabilities": ["preview_albums", "repair_albums", "scan_library", "normalize"],
    }))
    assert client.put("/api/normalizer/settings", json={"enabled": False}).status_code == 200
    response = client.post("/api/normalizer/jobs", json={"operation": "preview_albums"})
    assert response.status_code == 202
    identity = response.json()["id"]
    main.run_pipeline_job(identity)
    current = main.get_job_store().get(identity)
    assert (current["status"], current["stage"]) == ("waiting", "normalizing")
    assert (queue / "pending" / f"{identity}.json").is_file()


@pytest.mark.parametrize("enabled", [False, True])
def test_legacy_direct_import_uses_saved_setting_for_immediate_scan(client, monkeypatch, enabled):
    calls = []
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", not enabled)
    assert client.put("/api/normalizer/settings", json={"enabled": enabled}).status_code == 200
    monkeypatch.setattr(main, "BEETS_ENABLED", True)
    monkeypatch.setattr(main, "BEETS_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main, "BEETS_STATE", {"last_task_id": None, "last_error": None})
    monkeypatch.setattr(main.shutil, "which", lambda _: "/unused/beet")
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda *_: None)
    monkeypatch.setattr(main, "get_ready_download_paths", lambda: [])
    monkeypatch.setattr(main.beets_importer, "enrich_pending", lambda **_: {
        "organized": 1, "updated": 0, "skipped": 0, "errors": [],
    })
    monkeypatch.setattr(main, "fix_download_permissions", lambda *_: None)
    monkeypatch.setattr(main.navidrome_dedup, "subsonic_request", lambda *args: calls.append(args[-1]))
    result = main.run_beets_import_task("legacy", main.BeetsImportRequest(
        navi_sync=True, navi_url="http://navidrome.invalid", navi_user="user", navi_pass="secret",
    ), log_callback=lambda _: None)
    assert result["errors"] == []
    assert calls == ([] if enabled else ["startScan.view"])


def test_enabling_during_scan_starts_fresh_scan_after_normalization_ack(client, monkeypatch):
    calls = []
    scanning = [True]

    def navi(endpoint, params=None):
        calls.append(endpoint)
        return {"subsonic-response": {"status": "ok", "scanStatus": {"scanning": scanning[0]}}}

    monkeypatch.setattr(main, "navi_services", lambda _: (navi, None))
    music = Path(main.DOWNLOADS_DIR) / "song.mp3"
    music.write_bytes(b"already imported audio")
    store = main.get_job_store()
    job = store.enqueue("download", {"navi_sync": True})
    store.set_tracks(job["id"], [{"title": "Song"}])
    store.update_track(job["id"], 0, status="imported", result={"paths": [str(music)]})
    store.update(job["id"], stage="normalizing")

    assert client.put("/api/normalizer/settings", json={"enabled": False}).status_code == 200
    main.run_pipeline_job(job["id"])
    assert store.get(job["id"])["stage"] == "scanning"
    assert calls == ["startScan.view", "getScanStatus.view"]

    assert client.put("/api/normalizer/settings", json={"enabled": True}).status_code == 200
    store.update(job["id"], next_attempt=0)
    main.run_pipeline_job(job["id"])
    assert store.get(job["id"])["stage"] == "normalizing"
    assert calls == ["startScan.view", "getScanStatus.view"]

    queue = Path(main.NORMALIZATION_QUEUE_DIR)
    pending, = (queue / "pending").glob("*.json")
    request = json.loads(pending.read_text())
    (queue / "done").mkdir()
    (queue / "done" / pending.name).write_text(json.dumps({"id": request["id"], "status": "done"}))
    scanning[0] = False
    store.update(job["id"], next_attempt=0)
    main.run_pipeline_job(job["id"])
    assert calls == ["startScan.view", "getScanStatus.view", "startScan.view", "getScanStatus.view"]
    assert store.get(job["id"])["status"] == "done"
