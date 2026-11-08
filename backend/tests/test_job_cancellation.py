"""Cancellation remains durable even when an in-flight worker finishes late."""

import concurrent.futures
import fcntl
import hashlib
import io
import json
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import acquisition, beets_importer, job_store, main
from backend.job_store import JobStore


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name in ("SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "LOGS_DIR", "BEETS_INBOX_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(main, name, str(directory))
    monkeypatch.setattr(main, "PIPELINE_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", False)
    return main.get_job_store()


def test_stop_all_cancels_active_jobs_and_preserves_completed_tracks(isolated):
    store = isolated
    completed_audio = Path(main.DOWNLOADS_DIR) / "kept.flac"
    completed_audio.write_bytes(b"existing recording")
    active = [store.enqueue("download", {}) for _ in range(3)]
    store.claim(active[1]["id"])
    store.update(active[2]["id"], status="waiting", next_attempt=12345678900)
    finished = store.enqueue("import", {})
    store.update(finished["id"], status="done")
    store.set_tracks(active[1]["id"], [{"title": "Finished"}, {"title": "Pending"}])
    store.update_track(active[1]["id"], 0, status="imported", result={"paths": ["kept.flac"]})
    response = TestClient(main.app).post("/api/jobs/cancel")
    assert response.status_code == 200
    assert response.json()["cancelled"] == 3
    assert response.json()["status"] == "cancelled"
    assert [store.get(item["id"])["status"] for item in active] == ["cancelled"] * 3
    assert store.get(finished["id"])["status"] == "done"
    tracks = store.tracks(active[1]["id"])
    assert [track["status"] for track in tracks] == ["imported", "cancelled"]
    assert tracks[0]["result"] == {"paths": ["kept.flac"]}
    assert completed_audio.read_bytes() == b"existing recording"
    reopened = JobStore(store.path)
    reopened.recover()
    assert reopened.claim() is None
    assert TestClient(main.app).post("/api/jobs/cancel").json()["cancelled"] == 0


def test_cancel_one_does_not_stop_other_jobs(isolated):
    store = isolated
    selected = store.enqueue("download", {})
    other = store.enqueue("download", {})
    client = TestClient(main.app)
    response = client.post(f"/api/jobs/{selected['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["cancelled"] == 1
    assert store.get(selected["id"])["status"] == "cancelled"
    assert store.claim()["id"] == other["id"]
    assert client.post("/api/jobs/missing/cancel").status_code == 404
    assert client.post(f"/api/jobs/{selected['id']}/cancel").status_code == 409


@pytest.mark.parametrize("status", ["done", "failed", "needs_review"])
def test_cancelling_terminal_job_is_rejected_without_changing_history(isolated, status):
    store = isolated
    item = store.enqueue("download", {})
    store.update(item["id"], status=status, result={"paths": ["kept.flac"]})
    response = TestClient(main.app).post(f"/api/jobs/{item['id']}/cancel")
    assert response.status_code == 409
    saved = store.get(item["id"])
    assert saved["status"] == status
    assert saved["result"] == {"paths": ["kept.flac"]}


@pytest.mark.parametrize("operation", ["update", "update_track", "fail_track"])
def test_late_worker_cannot_resurrect_cancelled_job(isolated, operation):
    store = isolated
    item = store.enqueue("download", {})
    store.set_tracks(item["id"], [{"title": "Pending"}])
    store.cancel(item["id"])
    cancelled_error = getattr(job_store, "JobCancelled", ())
    try:
        if operation == "update":
            store.update(item["id"], status="waiting", next_attempt=0)
        elif operation == "update_track":
            store.update_track(item["id"], 0, status="imported", result={"paths": ["late.flac"]})
        else:
            store.fail_track(item["id"], 0, "late error")
    except cancelled_error:
        pass
    store.recover()
    assert store.get(item["id"])["status"] == "cancelled"
    assert store.tracks(item["id"])[0]["status"] == "cancelled"
    assert store.claim(item["id"]) is None


def test_explicit_retry_requeues_cancelled_tracks_without_repeating_completed_ones(isolated, monkeypatch):
    store = isolated
    item = store.enqueue("download", {})
    store.set_tracks(item["id"], [{"title": "Completed"}, {"title": "Pending"}])
    store.update_track(item["id"], 0, status="imported", result={"paths": ["kept.flac"]})
    store.cancel(item["id"])
    monkeypatch.setattr(main, "run_pipeline_job", lambda *_: None)
    response = TestClient(main.app).post(f"/api/jobs/{item['id']}/retry")
    assert response.status_code == 200
    assert store.get(item["id"])["status"] == "queued"
    tracks = store.tracks(item["id"])
    assert [track["status"] for track in tracks] == ["imported", "queued"]
    assert tracks[0]["result"] == {"paths": ["kept.flac"]}
    assert (tracks[1]["failures"], tracks[1]["next_attempt"]) == (0, 0)


def test_cancellation_checkpoint_does_not_become_a_retryable_error(isolated):
    store = isolated
    item = store.enqueue("download", {})
    with job_store.cancellation_scope(store, item["id"]):
        job_store.check_cancelled()
        store.cancel(item["id"])
        with pytest.raises(job_store.JobCancelled):
            job_store.check_cancelled()
    job_store.check_cancelled()
    assert not issubclass(job_store.JobCancelled, Exception)


@pytest.mark.parametrize("return_error", [False, True])
def test_worker_finishing_after_cancel_does_not_finalize_or_schedule_retry(isolated, monkeypatch, return_error):
    store = isolated
    monkeypatch.setattr(main, "BEETS_ENABLED", False)
    item = store.enqueue("download", {"tool": "ytdl", "url": "https://media.invalid/song", "flags": {}})

    def download(*args, **kwargs):
        store.cancel(item["id"])
        if return_error:
            raise RuntimeError("download exited after cancellation")
        return {"returncode": 0}

    monkeypatch.setattr(main, "run_download_task", download)
    main.run_pipeline_job(item["id"])
    saved = store.get(item["id"])
    assert saved["status"] == "cancelled"
    assert saved["failures"] == 0
    assert store.claim(item["id"]) is None


def test_stop_all_pauses_automatic_enqueue_until_manual_request(isolated):
    store = isolated
    store.enqueue("import", {}, automatic=True)
    store.cancel()
    reopened = JobStore(store.path)
    assert reopened.enqueue("import", {}, automatic=True) is None
    assert len(reopened.list()) == 1
    manual = reopened.enqueue("download", {})
    assert manual["status"] == "queued"
    assert reopened.enqueue("import", {}, automatic=True)["status"] == "queued"


def test_single_cancel_keeps_automatic_enqueue_available(isolated):
    item = isolated.enqueue("download", {})
    isolated.cancel(item["id"])
    assert isolated.enqueue("import", {}, automatic=True)["status"] == "queued"


def test_retry_waits_for_cancelled_execution_to_leave_worker_lock(isolated):
    item = isolated.enqueue("download", {})
    isolated.set_tracks(item["id"], [{"title": "Pending"}])
    isolated.cancel(item["id"])
    with main.PIPELINE_JOB_LOCK:
        response = TestClient(main.app).post(f"/api/jobs/{item['id']}/retry")
    assert response.status_code == 409
    assert isolated.get(item["id"])["status"] == "cancelled"
    assert isolated.tracks(item["id"])[0]["status"] == "cancelled"


def test_retry_rejects_execution_owned_by_another_worker_process(isolated):
    item = isolated.enqueue("download", {})
    isolated.set_tracks(item["id"], [{"title": "Pending"}])
    isolated.cancel(item["id"])
    with open(str(isolated.path) + ".worker.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = TestClient(main.app).post(f"/api/jobs/{item['id']}/retry")
    assert response.status_code == 409
    assert isolated.get(item["id"])["status"] == "cancelled"
    assert isolated.tracks(item["id"])[0]["status"] == "cancelled"


def test_cancelling_silent_downloader_terminates_its_process_group(isolated, tmp_path):
    scope = job_store.cancellation_scope
    item = isolated.enqueue("download", {})
    pidfile = tmp_path / "child.pid"
    child_script = (
        "import os,signal,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(60)"
    )
    script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child_script!r}]); time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, start_new_session=True)

    def monitor():
        with scope(isolated, item["id"]):
            with pytest.raises(job_store.JobCancelled):
                acquisition.log_process(process, io.StringIO(), timeout=30)

    with concurrent.futures.ThreadPoolExecutor(1) as executor:
        future = executor.submit(monitor)
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert pidfile.exists(), "Child never reached its silent blocking state"
            isolated.cancel(item["id"])
            future.result(timeout=5)
        finally:
            acquisition.terminate_process(process)
    assert process.poll() is not None
    child_state = Path("/proc") / pidfile.read_text() / "stat"
    assert not child_state.exists() or child_state.read_text().split()[2] == "Z"


def test_cancel_during_failed_in_place_write_restores_audio_before_stopping_next_file(isolated, monkeypatch, tmp_path):
    from backend.tests.test_durable_workflow import write_audio

    store = isolated
    monkeypatch.setattr(main, "BEETS_ENABLED", True)
    monkeypatch.setattr(main, "BEETS_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main, "BEETS_STATE", {"last_task_id": None, "last_error": None})
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda *_: None)
    monkeypatch.setattr(beets_importer, "audio_packet_signature", lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest())
    state_path = Path(main.SPOTDL_CONFIG_DIR) / "beets/library-state.json"
    state_path.parent.mkdir()
    state = {"version": 1, "tracks": {}}
    destinations, originals, entries = [], [], []
    for name in ("first", "second"):
        source = write_audio(tmp_path / "incoming" / f"{name}.wav")
        destination = Path(main.DOWNLOADS_DIR) / source.name
        shutil.copy2(source, destination)
        entries.append(beets_importer._save_track(state, state_path, source, {
            "event": "imported", "destination": str(destination),
            "metadata_confirmed": False, "release_confirmed": False,
            "reason": "Awaiting metadata recognition",
        }, Path(main.DOWNLOADS_DIR)))
        destinations.append(destination)
        originals.append(destination.read_bytes())
        source.unlink()
    item = store.enqueue("import", {})
    original_run, original_which = subprocess.run, shutil.which
    written = []

    def run(command, **kwargs):
        if Path(command[0]).name != "beet":
            return original_run(command, **kwargs)
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        assert "import" in command
        assert "-C" in command
        destination = Path(command[-1])
        written.append(destination)
        destination.write_bytes(b"interrupted metadata write")
        Path(kwargs["env"]["DOWNLOADEUSZ_BEETS_REPORT"]).write_text(json.dumps({
            "event": "review", "source": str(destination), "reason": "simulated metadata write failure",
        }) + "\n")
        store.cancel(item["id"])
        return subprocess.CompletedProcess(command, 2, "metadata writer failed\n", "write error\n")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/beet" if name == "beet" else original_which(name))
    cancelled = False
    try:
        with job_store.cancellation_scope(store, item["id"]):
            main.run_beets_import_task(item["id"], main.BeetsImportRequest(retry=True), defer_finalize=True)
    except job_store.JobCancelled:
        cancelled = True

    assert destinations[0].read_bytes() == originals[0]
    assert destinations[1].read_bytes() == originals[1]
    assert written == [destinations[0]]
    saved = beets_importer._load_library_state(main.SPOTDL_CONFIG_DIR)["tracks"]
    assert saved[entries[0]["id"]]["metadata_confirmed"] is False
    assert "metadata write failure" in saved[entries[0]["id"]]["reason"]
    assert store.get(item["id"])["status"] == "cancelled"
    assert cancelled, "Import must stop at the first safe boundary after cancellation"


@pytest.mark.parametrize("local_status", ["failed", "cancelled"])
@pytest.mark.parametrize("remote_directory", ["pending", "processing"])
def test_normalizer_retry_cannot_duplicate_unfinished_remote_operation(isolated, monkeypatch, tmp_path, local_status, remote_directory):
    from backend.tests.test_normalizer_api import connected_queue

    monkeypatch.setattr(main, "NORMALIZATION_QUEUE_DIR", str(tmp_path / "normalization"))
    queue = connected_queue()
    identity, _ = main.enqueue_normalizer("scan_library")
    if remote_directory == "processing":
        (queue / "pending" / f"{identity}.json").rename(queue / "processing" / f"{identity}.json")
    if local_status == "cancelled":
        isolated.cancel(identity)
    else:
        isolated.update(identity, status="failed", error="Local tracking error")

    response = TestClient(main.app).post(f"/api/jobs/{identity}/retry")

    assert response.status_code == 409
    assert len(isolated.list()) == 1
    requests = list((queue / "pending").glob("*.json")) + list((queue / "processing").glob("*.json"))
    assert [path.stem for path in requests] == [identity]
    assert isolated.get(identity)["status"] == local_status


@pytest.mark.parametrize("remote_status", ["done", "failed"])
def test_cancelled_normalizer_can_retry_after_remote_acknowledgement(isolated, monkeypatch, tmp_path, remote_status):
    from backend.tests.test_normalizer_api import connected_queue, put_request

    monkeypatch.setattr(main, "NORMALIZATION_QUEUE_DIR", str(tmp_path / "normalization"))
    queue = connected_queue()
    identity, _ = main.enqueue_normalizer("scan_library")
    isolated.cancel(identity)
    (queue / "pending" / f"{identity}.json").unlink()
    put_request(queue, "done", identity=identity, operation="scan_library", status=remote_status)

    response = TestClient(main.app).post(f"/api/jobs/{identity}/retry")

    assert response.status_code == 200
    new_identity = response.json()["task_id"]
    assert new_identity != identity
    assert (queue / "pending" / f"{new_identity}.json").is_file()
    assert isolated.get(identity)["status"] == "cancelled"


def test_cancel_waits_for_atomic_retry_then_cancels_whole_job(isolated, monkeypatch):
    store = isolated
    item = store.enqueue("download", {})
    store.set_tracks(item["id"], [{"title": "Completed"}, {"title": "First pending"}, {"title": "Second pending"}])
    store.update_track(item["id"], 0, status="imported", result={"paths": ["kept.wav"]})
    store.cancel()
    observer = JobStore(store.path)
    original_connect = store.connect
    retry_writing, release_retry, cancellation_started = threading.Event(), threading.Event(), threading.Event()
    barrier_timed_out = threading.Event()

    @contextmanager
    def paused_connection():
        with original_connect() as db:
            def trace(statement):
                if statement.startswith("UPDATE tracks"):
                    retry_writing.set()
                    if not release_retry.wait(5):
                        barrier_timed_out.set()
            db.set_trace_callback(trace)
            yield db

    def cancel():
        cancellation_started.set()
        return observer.cancel()

    monkeypatch.setattr(store, "connect", paused_connection)
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        retry_future = executor.submit(store.retry, item["id"])
        try:
            assert retry_writing.wait(5), "Retry never reached its first track update"
            cancel_future = executor.submit(cancel)
            assert cancellation_started.wait(5)
            assert observer.get(item["id"])["status"] == "cancelled"
            assert [track["status"] for track in observer.tracks(item["id"])] == ["imported", "cancelled", "cancelled"]
        finally:
            release_retry.set()
        retry_future.result(timeout=5)
        assert cancel_future.result(timeout=5) == 1

    assert not barrier_timed_out.is_set(), "Retry transaction barrier was not released"
    assert observer.get(item["id"])["status"] == "cancelled"
    assert [track["status"] for track in observer.tracks(item["id"])] == ["imported", "cancelled", "cancelled"]
    assert observer.enqueue("import", {}, automatic=True) is None
