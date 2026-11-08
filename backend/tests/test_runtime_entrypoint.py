"""The container imports main from backend/, unlike pytest's package imports."""

import json
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest

from backend import main
from backend.tests.test_durable_workflow import worker, enqueue_tracks, write_audio, tick


def test_soulseek_worker_imports_from_container_working_directory(tmp_path):
    code = '''
import main
import soulseek_search
main.accounts.get_active_slskd_account = lambda *_: {"server": "http://slskd"}
soulseek_search.search_and_queue = lambda *args, **kwargs: {"state": "not_found", "message": "No recording"}
class Store:
    def update(self, *args, **kwargs): pass
    def update_track(self, *args, **kwargs):
        assert kwargs["status"] == "needs_review"
main.process_soulseek_track(Store(), {"id": "test", "payload": {}},
    {"position": 0, "payload": {"title": "Song", "artist": "Artist"}, "result": {}, "status": "queued"})
'''
    env = {**os.environ, "PYTHONPATH": "", "DOWNLOADS_DIR": str(tmp_path / "music"),
           "SPOTDL_CONFIG_DIR": str(tmp_path / "config"), "WALLPAPERS_DIR": str(tmp_path / "wallpapers")}
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(main.__file__).parent,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_ready_review_retries_once_then_requires_user_decision(tmp_path, monkeypatch):
    config = tmp_path / "config"
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(main, "SPOTDL_CONFIG_DIR", str(config))
    monkeypatch.setattr(main, "LOGS_DIR", str(logs))
    state_path = config / "beets" / "library-state.json"
    state_path.parent.mkdir(parents=True)
    source = str(tmp_path / "ready" / "song.flac")
    item_id = "a" * 64
    state_path.write_text(json.dumps({"version": 1, "tracks": {item_id: {
        "id": item_id, "source": source, "imported": False,
        "reason": "Nagranie pozostaje niejednoznaczne",
    }}}))
    store = main.get_job_store()
    job = store.enqueue("import", {"review_item_id": item_id, "retry": True})
    outcome = {"paths": [], "errors": [], "organized": 0, "updated": 0, "ready_sources": [source]}
    with patch.object(main, "run_beets_import_task", return_value=outcome):
        main.run_pipeline_job(job["id"])
    current = store.get(job["id"])
    assert current["status"] == "needs_review"
    assert "niejednoznaczne" in current["error"]


def test_download_owns_normalization_of_all_files_changed_during_its_import(worker, tmp_path, monkeypatch):
    other = write_audio(Path(main.BEETS_INBOX_DIR) / "manual" / "other.wav")
    (other.parent / ".ready").touch()
    queue = tmp_path / "normalization"
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", True)
    monkeypatch.setattr(main, "NORMALIZATION_QUEUE_DIR", str(queue))
    store, job = enqueue_tracks([{"url": "https://media.invalid/current", "source_tool": "ytdl"}])
    tick(job["id"])
    tick(job["id"])
    copies = list(Path(main.DOWNLOADS_DIR).rglob("*.wav"))
    assert len(copies) == 2
    request, = (queue / "pending").glob("*.json")
    assert set(json.loads(request.read_text())["paths"]) == {"Artist/current.wav", "Artist/other.wav"}


def test_queued_manual_import_does_not_reserve_beets_while_downloader_runs(worker):
    main.PIPELINE_JOB_LOCK.acquire()
    try:
        response = TestClient(main.app).post("/api/beets/import", json={})
        assert response.status_code == 200
        assert not main.BEETS_JOB_LOCK.locked()
    finally:
        main.PIPELINE_JOB_LOCK.release()
        if main.BEETS_JOB_LOCK.locked():
            main.BEETS_JOB_LOCK.release()


def test_worker_recovers_after_transient_storage_failure(monkeypatch):
    stop = asyncio.Event()
    attempts = []

    def run():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("Configuration mount temporarily unavailable")
        stop.set()

    monkeypatch.setattr(main, "run_pipeline_job", run)
    asyncio.run(main.pipeline_worker(stop))
    assert len(attempts) == 2


def test_restart_recovers_normalization_after_import_committed_before_job_result(worker, tmp_path, monkeypatch):
    source = write_audio(Path(main.BEETS_INBOX_DIR) / "manual" / "committed.wav")
    (source.parent / ".ready").touch()
    queue = tmp_path / "normalization"
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", True)
    monkeypatch.setattr(main, "NORMALIZATION_QUEUE_DIR", str(queue))
    store = main.get_job_store()
    job = store.enqueue("import", {})
    actual_import = main.run_beets_import_task

    def interrupted(*args, **kwargs):
        actual_import(*args, **kwargs)
        raise SystemExit("Process interrupted after beets committed")

    with patch.object(main, "run_beets_import_task", interrupted), pytest.raises(SystemExit):
        main.run_pipeline_job(job["id"])
    assert (Path(main.DOWNLOADS_DIR) / "Artist/committed.wav").is_file()
    tick(job["id"])
    assert store.get(job["id"])["status"] == "waiting"
    request, = (queue / "pending").glob("*.json")
    assert json.loads(request.read_text())["paths"] == ["Artist/committed.wav"]
