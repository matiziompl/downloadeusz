"""Normalizeusz integration uses a shared queue, never an in-process tag worker."""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "LOGS_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(main, name, str(directory))
    monkeypatch.setattr(main, "NORMALIZATION_QUEUE_DIR", str(tmp_path / "queue"))
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", False)
    monkeypatch.setattr(main, "PIPELINE_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main.accounts, "get_navidrome_account_by_id", lambda *_: None)
    return TestClient(main.app)


def test_missing_queue_is_offline_without_creating_the_mount(client):
    response = client.get("/api/normalizer")
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False
    assert data["enabled"] is False
    assert data["requests"] == []
    assert not Path(main.NORMALIZATION_QUEUE_DIR).exists()
    assert client.post("/api/normalizer/jobs", json={"operation": "repair_albums"}).status_code == 503
    assert not Path(main.NORMALIZATION_QUEUE_DIR).exists()


def test_crash_before_request_publication_is_recoverable_without_automatic_replay(client, monkeypatch):
    queue = connected_queue()

    def crash(*args):
        raise SystemExit("interrupted before publication")

    monkeypatch.setattr(main.normalizer_bridge, "publish", crash)
    with pytest.raises(SystemExit):
        main.enqueue_normalizer("repair_albums")
    store = main.get_job_store()
    job, = store.list()
    assert job["status"] == "waiting"
    main.run_pipeline_job(job["id"])
    assert store.get(job["id"])["attempts"] == 0
    store.update(job["id"], next_attempt=0)
    main.run_pipeline_job(job["id"])
    current = store.get(job["id"])
    assert current["status"] == "needs_review"
    assert current["error"]
    assert list((queue / "pending").iterdir()) == []


def connected_queue(*, age=0, capabilities=None):
    queue = Path(main.NORMALIZATION_QUEUE_DIR)
    for name in ("pending", "processing", "done", "progress"):
        (queue / name).mkdir(parents=True, exist_ok=True)
    (queue / "heartbeat.json").write_text(json.dumps({
        "protocol_version": 2, "version": "2.3", "updated_at": time.time() - age,
        "capabilities": capabilities if capabilities is not None else [
            "normalize", "preview_albums", "repair_albums", "scan_library"],
    }))
    return queue


def put_request(queue, directory, *, operation="repair_albums", identity=None, **fields):
    identity = identity or str(uuid.uuid4())
    (queue / directory / f"{identity}.json").write_text(json.dumps({
        "id": identity, "operation": operation, "created_at": time.time(), **fields,
    }))
    return identity


def test_healthy_worker_is_available_when_automatic_normalization_is_disabled(client):
    connected_queue()
    data = client.get("/api/normalizer").json()
    assert data["enabled"] is False
    assert data["available"] is True
    assert data["version"] == "2.3"
    assert "repair_albums" in data["capabilities"]
    assert data["counts"] == {"pending": 0, "processing": 0}


@pytest.mark.parametrize("age", [31, 3600])
def test_stale_heartbeat_blocks_manual_request_without_queuing(client, age):
    queue = connected_queue(age=age)
    assert client.get("/api/normalizer").json()["available"] is False
    assert client.post("/api/normalizer/jobs", json={"operation": "repair_albums"}).status_code == 503
    assert list((queue / "pending").iterdir()) == []


def test_unsupported_capability_blocks_only_that_operation(client):
    queue = connected_queue(capabilities=["normalize", "preview_albums"])
    assert client.post("/api/normalizer/jobs", json={"operation": "repair_albums"}).status_code == 503
    assert list((queue / "pending").iterdir()) == []
    assert client.post("/api/normalizer/jobs", json={"operation": "preview_albums"}).status_code == 202


@pytest.mark.parametrize("operation", ["preview_albums", "repair_albums", "scan_library"])
def test_manual_operation_is_queued_and_tracked_durably(client, operation):
    queue = connected_queue()
    response = client.post("/api/normalizer/jobs", json={"operation": operation})
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    request_path = queue / "pending" / f"{data['id']}.json"
    queued = json.loads(request_path.read_text())
    assert queued["id"] == data["id"]
    assert queued["operation"] == operation
    assert not queued.get("paths")
    jobs = main.get_job_store().list()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "normalizer"
    assert jobs[0]["payload"]["operation"] == operation
    panel = client.get("/api/normalizer").json()
    assert panel["counts"]["pending"] == 1
    assert panel["requests"][0]["id"] == data["id"]


@pytest.mark.parametrize("payload", [
    {}, {"operation": "erase_library"}, {"operation": "repair_albums", "paths": ["/etc/passwd"]},
])
def test_invalid_manual_request_cannot_choose_arbitrary_paths(client, payload):
    queue = connected_queue()
    response = client.post("/api/normalizer/jobs", json=payload)
    assert response.status_code == 422
    assert list((queue / "pending").iterdir()) == []


def test_ack_of_another_operation_cannot_confirm_the_requested_work(client):
    queue = connected_queue()
    response = client.post("/api/normalizer/jobs", json={"operation": "preview_albums"})
    identity = response.json()["id"]
    (queue / "pending" / f"{identity}.json").unlink()
    put_request(queue, "done", identity=identity, operation="repair_albums", status="done", result={})
    main.run_pipeline_job(identity)
    job = main.get_job_store().get(identity)
    assert job["status"] == "failed"
    assert "potwierdzenie" in job["error"]


def test_panel_reports_processing_progress_and_finished_results(client):
    queue = connected_queue()
    active = put_request(queue, "processing", operation="scan_library")
    (queue / "progress" / f"{active}.json").write_text(json.dumps({
        "id": active, "operation": "scan_library", "updated_at": time.time(),
        "processed": 4, "total": 9, "changed": 2, "skipped": 1, "errors": 1,
        "current_path": "Artist/Album/Song.flac", "private_token": "never-expose-me",
    }))
    finished = put_request(queue, "done", status="done", completed_at=time.time(), result={
        "processed": 3, "changed": 1, "skipped": 2, "errors": 0,
        "samples": [{"path": "Artist/Song.flac", "title": "Song", "album": "Album"}],
        "error_samples": [], "private_token": "never-expose-me",
    })
    response = client.get("/api/normalizer")
    assert response.status_code == 200
    data = response.json()
    rows = {row["id"]: row for row in data["requests"]}
    assert data["counts"] == {"pending": 0, "processing": 1}
    assert rows[active]["status"] == "processing"
    assert rows[active]["progress"]["processed"] == 4
    assert rows[active]["progress"]["total"] == 9
    assert rows[active]["progress"]["current_path"] == "Artist/Album/Song.flac"
    assert rows[finished]["status"] == "done"
    assert rows[finished]["result"]["changed"] == 1
    assert rows[finished]["result"]["samples"] == [
        {"path": "Artist/Song.flac", "title": "Song", "album": "Album"}]
    assert "never-expose-me" not in response.text


def test_panel_includes_worker_failure_and_per_file_errors(client):
    queue = connected_queue()
    identity = put_request(queue, "done", status="failed", error="Brak zapisu tagów", result={
        "processed": 1, "changed": 0, "skipped": 0, "errors": 1, "samples": [],
        "error_samples": [{"path": "Artist/Song.flac", "error": "Tag jest tylko do odczytu"}],
    })
    rows = client.get("/api/normalizer").json()["requests"]
    assert rows[0]["id"] == identity
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "Brak zapisu tagów"
    assert rows[0]["result"]["error_samples"] == [
        {"path": "Artist/Song.flac", "error": "Tag jest tylko do odczytu"}]


def test_panel_bounds_history_and_keeps_newest_requests(client):
    queue = connected_queue()
    identities = []
    for index in range(25):
        identities.append(put_request(queue, "done", created_at=1000 + index,
                                      completed_at=1000 + index, status="done", result={}))
        os.utime(queue / "done" / f"{identities[-1]}.json", (1000 + index, 1000 + index))
    rows = client.get("/api/normalizer").json()["requests"]
    assert len(rows) == 20
    assert [row["id"] for row in rows] == list(reversed(identities[5:]))


def test_broken_rows_do_not_hide_valid_requests_or_expose_unknown_fields(client):
    queue = connected_queue()
    valid = put_request(queue, "pending", private_token="never-expose-me")
    (queue / "pending" / f"{uuid.uuid4()}.json").write_text("{malformed")
    (queue / "done" / f"{uuid.uuid4()}.json").write_text("[]")
    put_request(queue, "done", identity="unsafe", status="done", result={})
    put_request(queue, "done", id="../traversal", status="done", result={})
    response = client.get("/api/normalizer")
    assert response.status_code == 200
    assert valid in {row["id"] for row in response.json()["requests"]}
    assert "../traversal" not in response.text
    assert "never-expose-me" not in response.text


@pytest.mark.parametrize("name", ["heartbeat.json", "pending", "processing", "done", "progress"])
def test_symlinked_queue_component_is_rejected(client, tmp_path, name):
    queue = connected_queue()
    original = queue / name
    outside = tmp_path / ("outside-" + name)
    original.rename(outside)
    original.symlink_to(outside, target_is_directory=outside.is_dir())
    assert client.get("/api/normalizer").json()["available"] is False
    assert client.post("/api/normalizer/jobs", json={"operation": "repair_albums"}).status_code == 503


def test_symlinked_queue_root_is_rejected(client, tmp_path):
    queue = connected_queue()
    outside = tmp_path / "outside-queue"
    queue.rename(outside)
    queue.symlink_to(outside, target_is_directory=True)
    assert client.get("/api/normalizer").json()["available"] is False
    assert client.post("/api/normalizer/jobs", json={"operation": "repair_albums"}).status_code == 503


def test_symlinked_reports_are_not_read(client, tmp_path):
    queue = connected_queue()
    for directory in ("done", "progress"):
        identity = put_request(queue, "processing") if directory == "progress" else str(uuid.uuid4())
        outside = tmp_path / f"secret-{directory}.json"
        outside.write_text(json.dumps({"id": identity, "operation": "repair_albums",
            "status": "failed", "error": "never-expose-me", "current_path": "never-expose-me"}))
        (queue / directory / f"{identity}.json").symlink_to(outside)
    response = client.get("/api/normalizer")
    assert response.status_code == 200
    assert "never-expose-me" not in response.text


def test_untrusted_report_paths_cannot_expose_absolute_or_parent_paths(client):
    queue = connected_queue()
    identity = put_request(queue, "processing")
    (queue / "progress" / f"{identity}.json").write_text(json.dumps({
        "id": identity, "current_path": "/private/host-secret.flac", "processed": 1, "total": 1,
    }))
    put_request(queue, "done", status="done", result={"samples": [
        {"path": "/private/host-secret.flac", "title": "Secret", "album": "Album"},
        {"path": "../host-secret.flac", "title": "Secret", "album": "Album"},
        {"path": "Song\u0000.flac", "title": "Song", "album": "Album"},
        {"path": "Artist/Song.flac", "title": "Song", "album": "Album"},
    ]})
    response = client.get("/api/normalizer")
    assert response.status_code == 200
    assert "/private/host-secret.flac" not in response.text
    assert "../host-secret.flac" not in response.text
    assert "\\u0000" not in response.text
    assert "Artist/Song.flac" in response.text


@pytest.mark.parametrize("raw", ["{broken", "[]", '{"protocol_version":2,"updated_at":"invalid"}',
    json.dumps({"protocol_version": 2, "updated_at": time.time(), "padding": "x" * 2_000_000})],
    ids=["invalid-json", "array", "invalid-timestamp", "oversized"])
def test_malformed_or_oversized_heartbeat_is_offline(client, raw):
    queue = connected_queue()
    (queue / "heartbeat.json").write_text(raw)
    response = client.get("/api/normalizer")
    assert response.status_code == 200
    assert response.json()["available"] is False


def test_oversized_report_does_not_break_panel_or_hide_other_requests(client):
    queue = connected_queue()
    valid = put_request(queue, "pending")
    put_request(queue, "done", status="done", padding="x" * 2_000_000)
    response = client.get("/api/normalizer")
    assert response.status_code == 200
    assert valid in {row["id"] for row in response.json()["requests"]}
    assert len(response.content) < 100_000


@pytest.mark.parametrize("operation", ["preview_albums", "repair_albums", "scan_library"])
def test_worker_waits_for_ack_and_scans_selected_navidrome_after_changes(client, monkeypatch, operation):
    queue = connected_queue()
    calls = []
    account = {"id": "navi", "server": "http://navidrome.invalid", "username": "user", "password": "secret"}
    monkeypatch.setattr(main.accounts, "get_navidrome_account_by_id", lambda identity, *_: account if identity == "navi" else None)

    def subsonic(server, user, password, endpoint, params=None):
        assert (server, user, password) == ("http://navidrome.invalid", "user", "secret")
        calls.append(endpoint)
        assert (queue / "done" / f"{request_id}.json").is_file()
        assert endpoint in {"startScan.view", "getScanStatus.view"}
        return {"subsonic-response": {"status": "ok", "scanStatus": {"scanning": False}}}

    monkeypatch.setattr(main.navidrome_dedup, "subsonic_request", subsonic)
    response = client.post("/api/normalizer/jobs", json={"operation": operation, "navi_account_id": "navi"})
    assert response.status_code == 202
    request_id = response.json()["id"]
    store = main.get_job_store()
    job, = store.list()
    main.run_pipeline_job(job["id"])
    assert store.get(job["id"])["status"] == "waiting"
    assert calls == []
    (queue / "pending" / f"{request_id}.json").unlink()
    put_request(queue, "done", identity=request_id, operation=operation, status="done", result={
        "processed": 4, "changed": 0 if operation == "scan_library" else 2,
        "skipped": 2, "errors": 0, "samples": [], "error_samples": [],
    })
    store.update(job["id"], next_attempt=0)
    main.run_pipeline_job(job["id"])
    assert store.get(job["id"])["status"] == "done"
    assert calls == ([] if operation == "preview_albums" else ["startScan.view", "getScanStatus.view"])
    assert list((queue / "pending").iterdir()) == []


def test_failed_worker_ack_stops_job_without_bulk_retry(client):
    queue = connected_queue()
    response = client.post("/api/normalizer/jobs", json={"operation": "repair_albums"})
    assert response.status_code == 202
    request_id = response.json()["id"]
    store = main.get_job_store()
    job, = store.list()
    (queue / "pending" / f"{request_id}.json").unlink()
    put_request(queue, "done", identity=request_id, status="failed", error="Brak zapisu tagów", result={
        "processed": 1, "changed": 0, "skipped": 0, "errors": 1,
    })
    store.update(job["id"], next_attempt=0)
    main.run_pipeline_job(job["id"])
    failed = store.get(job["id"])
    assert failed["status"] == "failed"
    assert "Brak zapisu tagów" in failed["error"]
    main.run_pipeline_job(job["id"])
    assert store.get(job["id"])["attempts"] == failed["attempts"]
    assert list((queue / "pending").iterdir()) == []


@pytest.mark.parametrize("worker_already_claimed", [False, True])
def test_directory_flush_failure_keeps_published_request_tracked(client, monkeypatch, worker_already_claimed):
    queue = connected_queue()
    original_fsync = os.fsync
    calls = 0

    def fail_after_publish(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            if worker_already_claimed:
                request, = (queue / "pending").glob("*.json")
                request.rename(queue / "processing" / request.name)
            raise OSError("Directory flush temporarily unavailable")
        return original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_after_publish)
    response = client.post("/api/normalizer/jobs", json={"operation": "repair_albums"})
    monkeypatch.setattr(os, "fsync", original_fsync)
    assert response.status_code == 202
    data = response.json()
    assert data.get("warning")
    identity = data["id"]
    store = main.get_job_store()
    assert store.get(identity)["status"] == "waiting"
    directory = "processing" if worker_already_claimed else "pending"
    request = queue / directory / f"{identity}.json"
    assert json.loads(request.read_text())["id"] == identity
    before = request.read_bytes()
    store.update(identity, next_attempt=0)
    main.run_pipeline_job(identity)
    assert store.get(identity)["status"] == "waiting"
    assert request.read_bytes() == before
    assert len(list((queue / "pending").glob("*.json"))) + len(list((queue / "processing").glob("*.json"))) == 1
    request.unlink()
    put_request(queue, "done", identity=identity, status="done", result={
        "processed": 1, "changed": 1, "skipped": 0, "errors": 0,
    })
    store.update(identity, next_attempt=0)
    main.run_pipeline_job(identity)
    assert store.get(identity)["status"] == "done"


def test_file_flush_failure_before_publish_rejects_and_cleans_temporary_request(client, monkeypatch):
    queue = connected_queue()

    def fail_before_publish(_descriptor):
        raise OSError("File flush temporarily unavailable")

    monkeypatch.setattr(os, "fsync", fail_before_publish)
    response = client.post("/api/normalizer/jobs", json={"operation": "repair_albums"})
    assert response.status_code == 503
    job, = main.get_job_store().list()
    assert job["status"] == "failed"
    assert list((queue / "pending").iterdir()) == []


def test_preview_round_trip_through_separate_normalizeusz_watcher(client, tmp_path):
    normalizeusz = Path(main.__file__).resolve().parents[2] / "normalizeusz"
    watcher, helper = normalizeusz / "watcher.sh", normalizeusz / "album_tags.py"
    if not watcher.is_file() or not helper.is_file():
        pytest.skip("Separate Normalizeusz sources are unavailable for the queue integration test")
    queue = connected_queue()
    response = client.post("/api/normalizer/jobs", json={"operation": "preview_albums"})
    assert response.status_code == 202
    identity = response.json()["id"]
    environment = {**os.environ, "TEST_MODE": "true", "INITIAL_SCAN": "false",
        "MUSIC_DIR": main.DOWNLOADS_DIR, "NORMALIZATION_QUEUE_DIR": str(queue),
        "QUEUE_DIR": str(tmp_path / "watcher-queue"), "SCANNED_DIR": str(tmp_path / "watcher-scanned"),
        "ALBUM_TAG_HELPER": str(helper), "PYTHON_BIN": sys.executable, "DISCORD_WEBHOOK_URL": ""}
    completed = subprocess.run(["bash", "-c", 'source "$1"\ninitialize_normalization_queue\nprocess_normalization_requests',
                                "normalizer-api-test", str(watcher)],
                               env=environment, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    ack = json.loads((queue / "done" / f"{identity}.json").read_text())
    assert ack["status"] == "done"
    assert ack["operation"] == "preview_albums"
    rows = client.get("/api/normalizer").json()["requests"]
    report, = [row for row in rows if row["id"] == identity]
    assert report["operation"] == "preview_albums"
    assert report["status"] == "done"
    assert report["result"]["processed"] == 0
