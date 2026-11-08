"""The current operation is independent of history pagination and selection."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.tests.test_durable_workflow import worker, enqueue_tracks, tick, write_audio


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "SPOTDL_CONFIG_DIR", str(tmp_path))
    return main.get_job_store(), TestClient(main.app)


def test_idle_and_finished_queues_have_no_current_job(queue):
    store, client = queue
    job = store.enqueue("download", {"navi_pass": "private"})
    assert client.get("/api/jobs").json()["current_job"] is None
    store.update(job["id"], status="done", stage="done")
    assert client.get("/api/jobs").json()["current_job"] is None


def test_current_job_is_selected_across_all_jobs_and_contains_only_safe_progress(queue):
    store, client = queue
    job = store.enqueue("download", {"playlist_name": "Moja playlista", "navi_pass": "private"})
    store.set_tracks(job["id"], [{"title": f"Song {index}"} for index in range(151)])
    for index in range(150):
        store.update_track(job["id"], index, status="imported")
    store.update(job["id"], status="running", stage="searching", result={
        "current_track": {"position": 150, "title": "Song 150", "artist": "Artist", "password": "private"},
        "search": {"query": "artist song", "attempt": 2, "total": 3, "api_key": "private"},
        "credentials": "private",
    })
    for _ in range(52):
        store.enqueue("import", {})
    response = client.get("/api/jobs").json()
    assert job["id"] not in {row["id"] for row in response["items"]}
    current = response["current_job"]
    assert current["id"] == job["id"]
    assert current["title"] == "Moja playlista"
    assert current["tracks_total"] == 151
    assert current["track_counts"] == {"imported": 150, "queued": 1}
    assert current["current_track"] == {"position": 150, "title": "Song 150", "artist": "Artist"}
    assert current["search"] == {"query": "artist song", "attempt": 2, "total": 3}
    assert "private" not in str(current)


def test_running_precedes_waiting_and_waiting_retains_real_stage(queue):
    store, client = queue
    waiting = store.enqueue("download", {})
    store.update(waiting["id"], status="waiting", stage="normalizing", next_attempt=time.time() + 5)
    running = store.enqueue("import", {})
    store.update(running["id"], status="running", stage="importing")
    assert client.get("/api/jobs").json()["current_job"]["id"] == running["id"]
    store.update(running["id"], status="done", stage="done")
    current = client.get("/api/jobs").json()["current_job"]
    assert current["id"] == waiting["id"]
    assert current["status"] == "waiting"
    assert current["stage"] == "normalizing"


def test_stage_changes_clear_obsolete_search_and_finalization_clears_track(queue):
    store, _ = queue
    job = store.enqueue("download", {})
    activity = {"current_track": {"position": 0, "title": "Song", "artist": "Artist"},
                "search": {"query": "artist song", "attempt": 1, "total": 3}}
    store.update(job["id"], status="running", stage="searching", result=activity)
    store.update(job["id"], stage="downloading")
    assert "search" not in store.get(job["id"])["result"]
    assert store.get(job["id"])["result"]["current_track"]["title"] == "Song"
    store.update(job["id"], stage="finalizing")
    assert "current_track" not in store.get(job["id"])["result"]


def test_search_progress_is_published_during_the_search(queue, monkeypatch):
    store, client = queue
    job = store.enqueue("download", {"tool": "soulseek", "url": "https://example.invalid"})
    store.set_tracks(job["id"], [{"title": "Song", "artist": "Artist"}])
    store.update(job["id"], status="running", stage="searching")
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda *_: {"server": "http://slskd"})
    from backend import soulseek_search

    def search(track, **kwargs):
        kwargs["progress_callback"]({"query": "artist song", "attempt": 2, "total": 3})
        current = client.get("/api/jobs").json()["current_job"]
        assert current["current_track"] == {"position": 0, "title": "Song", "artist": "Artist"}
        assert current["search"] == {"query": "artist song", "attempt": 2, "total": 3}
        return {"state": "not_found", "message": "No recording"}

    monkeypatch.setattr(soulseek_search, "search_and_queue", search)
    main.process_soulseek_track(store, job, store.tracks(job["id"])[0])


def test_polling_existing_soulseek_transfer_preserves_transferring_stage(queue, monkeypatch):
    store, client = queue
    job = store.enqueue("download", {"tool": "soulseek", "flags": {}})
    store.set_tracks(job["id"], [{"title": "Song", "artist": "Artist"}])
    store.update_track(job["id"], 0, status="transferring", result={
        "requested_at": time.time(), "selected": {"username": "peer", "filename": "song.flac"},
    })
    store.update(job["id"], status="running", stage="checking")
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda *_: {"server": "http://slskd"})
    monkeypatch.setattr(main.slskd_organizer, "find_file_in_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(main.slskd_organizer, "get_completed_album_directories", lambda *args: [])

    main.process_soulseek_track(store, job, store.tracks(job["id"])[0])

    current = client.get("/api/jobs").json()["current_job"]
    assert current["stage"] == "transferring"
    assert store.tracks(job["id"])[0]["status"] == "transferring"


def test_selecting_another_track_replaces_old_search_without_losing_job_results(queue):
    store, client = queue
    job = store.enqueue("download", {})
    store.update(job["id"], status="running", stage="searching", result={
        "current_track": {"position": 0, "title": "Old", "artist": "First"},
        "search": {"query": "old query", "attempt": 3, "total": 3},
        "imported_paths": ["/music/finished.flac"],
    })
    main.update_job_activity(store, job["id"], {"name": "Next", "artists": ["Second", "Third"]}, 1)
    current = client.get("/api/jobs").json()["current_job"]
    assert current["current_track"] == {"position": 1, "title": "Next", "artist": "Second, Third"}
    assert current["search"] is None
    assert store.get(job["id"])["result"]["imported_paths"] == ["/music/finished.flac"]


@pytest.mark.parametrize("status", ["done", "failed", "needs_review"])
def test_terminal_status_clears_progress_even_without_a_stage_update(queue, status):
    store, client = queue
    job = store.enqueue("download", {})
    store.update(job["id"], status="running", stage="searching", result={
        "current_track": {"position": 0, "title": "Old", "artist": "Artist"},
        "search": {"query": "old query", "attempt": 1, "total": 3},
        "imported_paths": ["/music/finished.flac"],
    })
    store.update(job["id"], status=status)
    assert store.get(job["id"])["result"] == {"imported_paths": ["/music/finished.flac"]}
    assert client.get("/api/jobs").json()["current_job"] is None


def test_completed_soulseek_transfer_publishes_importing_before_import(queue, monkeypatch):
    store, client = queue
    job = store.enqueue("download", {"tool": "soulseek", "flags": {}})
    store.set_tracks(job["id"], [{"title": "Song", "artist": "Artist"}])
    store.update_track(job["id"], 0, status="transferring", result={
        "requested_at": time.time(), "selected": {"username": "peer", "filename": "song.flac"},
    })
    store.update(job["id"], status="running", stage="checking")
    local = Path(main.SPOTDL_CONFIG_DIR) / "complete" / "song.flac"
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda *_: {"server": "http://slskd"})
    monkeypatch.setattr(main.slskd_organizer, "find_file_in_dir", lambda *args, **kwargs: str(local))
    monkeypatch.setattr(main.slskd_organizer, "get_completed_album_directories", lambda *args: [str(local.parent)])
    observed = []

    def import_ready_transfer(*args, **kwargs):
        observed.append(client.get("/api/jobs").json()["current_job"]["stage"])
        return {"paths": []}

    monkeypatch.setattr(main, "run_beets_import_task", import_ready_transfer)
    monkeypatch.setattr(main, "tracked_paths", lambda *_: ["/music/song.flac"])
    main.process_soulseek_track(store, job, store.tracks(job["id"])[0])
    assert observed == ["importing"]
    assert store.tracks(job["id"])[0]["status"] == "imported"


@pytest.mark.parametrize("tool", ["ytdl", "spotdl"])
def test_download_publishes_importing_before_beets_process(worker, monkeypatch, tool):
    store, job = enqueue_tracks([{"title": "Song", "url": "https://media.invalid/song", "source_tool": tool}], tool=tool)
    actual_import = main.run_beets_import_task
    observed = []

    def import_downloaded_track(*args, **kwargs):
        observed.append(TestClient(main.app).get("/api/jobs").json()["current_job"]["stage"])
        return actual_import(*args, **kwargs)

    monkeypatch.setattr(main, "run_beets_import_task", import_downloaded_track)
    tick(job["id"])
    assert observed == ["importing"]
    assert store.tracks(job["id"])[0]["status"] == "imported"


def test_recovered_ready_download_publishes_importing_before_beets_process(worker, monkeypatch):
    store, job = enqueue_tracks([{"title": "Song", "url": "https://media.invalid/song", "source_tool": "ytdl"}])
    source_task = job["id"] + "-0"
    source = write_audio(Path(main.BEETS_INBOX_DIR) / source_task / "song.wav")
    (source.parent / ".ready").touch()
    store.update_track(job["id"], 0, status="queued", result={"source_task": source_task})
    actual_import = main.run_beets_import_task
    observed = []

    def import_recovered_track(*args, **kwargs):
        observed.append(TestClient(main.app).get("/api/jobs").json()["current_job"]["stage"])
        return actual_import(*args, **kwargs)

    monkeypatch.setattr(main, "run_beets_import_task", import_recovered_track)
    tick(job["id"])
    assert observed == ["importing"]
    assert worker.downloads == []
    assert store.tracks(job["id"])[0]["status"] == "imported"
