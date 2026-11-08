"""Durable worker regressions using SQLite and real importer state on disk."""

import fcntl
import hashlib
import json
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from backend import beets_importer, main, pipeline
from backend.job_store import JobStore


def write_audio(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 800)
    return path


@pytest.fixture
def worker(tmp_path, monkeypatch):
    for name in ("SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "LOGS_DIR", "BEETS_INBOX_DIR", "SLSKD_DOWNLOADS_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(main, name, str(directory))
    monkeypatch.setattr(main, "BEETS_ENABLED", True)
    monkeypatch.setattr(main, "NORMALIZATION_ENABLED", False)
    monkeypatch.setattr(main, "BEETS_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main, "PIPELINE_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main, "BEETS_STATE", {"last_task_id": None, "last_error": None})
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda *_: None)
    monkeypatch.setattr(main.accounts, "get_navidrome_account_by_id", lambda *_: None)
    monkeypatch.setattr(main, "fix_download_permissions", lambda *_: None)
    original_popen, original_run, original_which = subprocess.Popen, subprocess.run, shutil.which
    state = SimpleNamespace(downloads=[], copies=[], failures={}, review=False, partial_failure=False)

    def popen(command, **kwargs):
        if command[0] not in {"yt-dlp", "spotdl"}:
            return original_popen(command, **kwargs)
        url = command[1]
        state.downloads.append(url)
        code = 0
        if state.failures.get(url, 0):
            state.failures[url] -= 1
            code = 1
        if "-x" in command or command[0] == "spotdl":
            write_audio(Path(kwargs["cwd"]) / (url.rsplit("/", 1)[-1] + ".wav"))
        else:
            (Path(kwargs["cwd"]) / "video.webm").write_bytes(b"video payload")
        return SimpleNamespace(stdout=iter(["download finished\n"]), returncode=code, wait=lambda: code)

    def run(command, **kwargs):
        if Path(command[0]).name != "beet":
            return original_run(command, **kwargs)
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        if "list" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        assert "import" in command
        source = Path(command[-1])
        assert source.is_file()
        if state.review:
            report = {"event": "review", "source": str(source), "reason": "Needs a metadata decision"}
        else:
            destination = Path(main.DOWNLOADS_DIR) / "Artist" / source.name
            if "-C" not in command:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                state.copies.append(str(destination))
            else:
                destination = source
            report = {"event": "imported", "source": str(source), "destination": str(destination),
                      "metadata_confirmed": True, "release_confirmed": True, "reason": "Confirmed", "candidates": []}
        Path(kwargs["env"]["DOWNLOADEUSZ_BEETS_REPORT"]).write_text(json.dumps(report) + "\n")
        return subprocess.CompletedProcess(command, 2 if state.partial_failure else 0, "", "")

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/beet" if name == "beet" else original_which(name))
    return state


def enqueue_tracks(tracks, **payload):
    store = main.get_job_store()
    job = store.enqueue("download", {"tool": "ytdl", "url": "https://media.invalid/playlist",
                                     "flags": {"audio_only": True}, **payload})
    store.set_tracks(job["id"], tracks)
    return store, job


def tick(identity):
    store = main.get_job_store()
    store.update(identity, next_attempt=0)
    with store.connect() as db:
        db.execute("UPDATE tracks SET next_attempt=0 WHERE job_id=?", (identity,))
    main.run_pipeline_job(identity)
    return main.get_job_store().get(identity)


def finish(identity, limit=12):
    for _ in range(limit):
        current = tick(identity)
        if current["status"] in {"done", "needs_review", "failed"}:
            return current
    pytest.fail(f"Job did not settle: {current}")


def test_restart_retries_only_unfinished_audio_and_preserves_completed_copy(worker):
    first, second = "https://media.invalid/first", "https://media.invalid/second"
    store, job = enqueue_tracks([{"title": "First", "url": first, "source_tool": "ytdl"},
                                 {"title": "Second", "url": second, "source_tool": "ytdl"},
                                 {"title": "First", "url": first, "source_tool": "ytdl"}])
    worker.failures[second] = 1
    tick(job["id"])
    completed = Path(main.DOWNLOADS_DIR) / "Artist" / "first.wav"
    original = completed.read_bytes(), completed.stat().st_mtime_ns
    assert tick(job["id"])["status"] == "waiting"
    # A new connection reads only durable state, as after a worker restart.
    reopened = JobStore(store.path)
    assert reopened.tracks(job["id"])[0]["status"] == "imported"
    assert finish(job["id"])["status"] == "done"
    assert [row["status"] for row in reopened.tracks(job["id"])] == ["imported"] * 3
    assert (completed.read_bytes(), completed.stat().st_mtime_ns) == original
    assert len(list(Path(main.DOWNLOADS_DIR).rglob("*.wav"))) == 2
    assert worker.downloads == [first, second, second]
    assert len(worker.copies) == 2


@pytest.mark.parametrize("status", ["failed", "needs_review"])
def test_unresolved_track_never_turns_whole_job_done(worker, status):
    store, job = enqueue_tracks([{"title": "Unresolved", "url": "https://media.invalid/first"}])
    store.update_track(job["id"], 0, status=status, error="No verified audio")
    current = finish(job["id"])
    assert current["status"] == "needs_review"
    assert store.tracks(job["id"])[0]["status"] == status
    assert worker.downloads == []


def test_explicit_retry_reacquires_failed_song_without_copying_later_success(worker):
    first, second = "https://media.invalid/first", "https://media.invalid/second"
    store, job = enqueue_tracks([{"title": "First", "url": first, "source_tool": "ytdl"},
                                 {"title": "Second", "url": second, "source_tool": "ytdl"}])
    store.update_track(job["id"], 0, status="failed", error="Source was unavailable")
    assert finish(job["id"])["status"] == "needs_review"
    completed = Path(main.DOWNLOADS_DIR) / "Artist" / "second.wav"
    original = completed.read_bytes(), completed.stat().st_mtime_ns
    response = TestClient(main.app).post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 200
    assert finish(job["id"])["status"] == "done"
    assert [row["status"] for row in store.tracks(job["id"])] == ["imported", "imported"]
    assert (completed.read_bytes(), completed.stat().st_mtime_ns) == original
    assert worker.downloads == [second, first]
    assert len(worker.copies) == 2


def test_external_worker_lock_prevents_claim_and_recovery(worker):
    store, job = enqueue_tracks([{"url": "https://media.invalid/first", "source_tool": "ytdl"}])
    interrupted = store.enqueue("import", {})
    store.claim(interrupted["id"])
    with open(str(store.path) + ".worker.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main.run_pipeline_job(job["id"])
        assert store.get(job["id"])["status"] == "queued"
        assert store.get(job["id"])["attempts"] == 0
        assert store.get(interrupted["id"])["status"] == "running"
        assert worker.downloads == []
    assert finish(job["id"])["status"] == "done"


def test_downloaded_review_retry_imports_ready_original_without_redownloading(worker):
    worker.review = True
    store, job = enqueue_tracks([{"title": "First", "url": "https://media.invalid/first", "source_tool": "ytdl"}])
    assert finish(job["id"])["status"] == "needs_review"
    entry, = beets_importer.list_review(main.SPOTDL_CONFIG_DIR)
    assert entry["imported"] is False
    source = Path(entry["source"])
    original = source.read_bytes()
    worker.review = False
    response = TestClient(main.app).post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 200
    assert finish(job["id"])["status"] == "done"
    assert store.tracks(job["id"])[0]["status"] == "imported"
    assert source.read_bytes() == original
    assert len(list(Path(main.DOWNLOADS_DIR).rglob("*.wav"))) == 1
    assert worker.downloads == ["https://media.invalid/first"]


def test_manual_review_job_waits_for_source_readiness_before_copy(worker):
    source = write_audio(Path(main.BEETS_INBOX_DIR) / "batch" / "first.wav")
    item_id = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    state_path = Path(main.SPOTDL_CONFIG_DIR) / "beets" / "library-state.json"
    state_path.parent.mkdir()
    state_path.write_text(json.dumps({"version": 1, "tracks": {item_id: {
        "id": item_id, "source": str(source), "status": "needs_review", "imported": False,
        "metadata_confirmed": False, "release_confirmed": False, "next_attempt": time.time() + 86400,
    }}}))
    store = main.get_job_store()
    job = store.enqueue("import", {"review_item_id": item_id, "retry": True})
    main.run_pipeline_job(job["id"])
    assert not list(Path(main.DOWNLOADS_DIR).rglob("*.wav"))
    assert store.get(job["id"])["status"] != "done"
    (source.parent / ".ready").touch()
    response = TestClient(main.app).post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 200
    assert finish(job["id"])["status"] == "done"
    assert len(list(Path(main.DOWNLOADS_DIR).rglob("*.wav"))) == 1
    assert source.is_file()


def test_partial_import_error_retains_real_copy_for_finalization(worker):
    worker.partial_failure = True
    store, job = enqueue_tracks([{"url": "https://media.invalid/first", "source_tool": "ytdl"}])
    tick(job["id"])
    destination, = Path(main.DOWNLOADS_DIR).rglob("*.wav")
    original = destination.read_bytes(), destination.stat().st_mtime_ns
    worker.partial_failure = False
    assert finish(job["id"])["status"] == "done"
    assert store.tracks(job["id"])[0]["result"]["paths"] == ["Artist/first.wav"]
    assert (destination.read_bytes(), destination.stat().st_mtime_ns) == original
    assert len(worker.copies) == 1


def test_video_download_preserves_video_mode_with_beets_enabled(worker):
    store = main.get_job_store()
    job = store.enqueue("download", {"tool": "ytdl", "url": "https://media.invalid/video", "flags": {}})
    assert finish(job["id"])["status"] == "done"
    assert (Path(main.DOWNLOADS_DIR) / "video.webm").read_bytes() == b"video payload"
    assert not list(Path(main.BEETS_INBOX_DIR).iterdir())
    assert not list(Path(main.DOWNLOADS_DIR).rglob("*.wav"))
    assert worker.copies == []


def test_normalization_ack_and_delayed_indexing_gate_ordered_native_playlist(tmp_path):
    music, queue = tmp_path / "music", tmp_path / "queue"
    write_audio(music / "first.wav")
    write_audio(music / "second.wav")
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("download", {"navi_sync": True, "flags": {
        "generate_m3u": True, "m3u_name": "Ordered playlist", "public_playlist": True,
    }})
    store.set_tracks(job["id"], [{"title": "First"}, {"title": "Second"}, {"title": "First"}])
    for position, path in enumerate(("first.wav", "second.wav", "first.wav")):
        store.update_track(job["id"], position, status="imported", result={"paths": [path]})
    server = {"allowed": False, "scanning": True, "indexed": False, "playlists": {}, "scans": 0}

    def navi(endpoint, params=None):
        assert server["allowed"], "Navidrome was contacted before normalization acknowledgement"
        response = {"status": "ok"}
        if endpoint == "startScan.view":
            server["scans"] += 1
        elif endpoint == "getScanStatus.view":
            response["scanStatus"] = {"scanning": server["scanning"], "count": 2}
        elif endpoint == "getPlaylists.view":
            response["playlists"] = {"playlist": list(server["playlists"].values())}
        elif endpoint == "createPlaylist.view":
            identity = params.get("playlistId", "native-1")
            server["playlists"][identity] = {"id": identity, **params}
            response["playlist"] = {"id": identity}
        elif endpoint == "updatePlaylist.view":
            server["playlists"][params["playlistId"]].update(params)
        else:
            pytest.fail(f"Unexpected endpoint: {endpoint}")
        return {"subsonic-response": response}

    def lookup(track, paths):
        assert paths == (["first.wav"] if track["title"] == "First" else ["second.wav"])
        return {"id": "one"} if track["title"] == "First" else {"id": "two"} if server["indexed"] else None

    def finalize():
        return pipeline.finalize(store, store.get(job["id"]), library_dir=music,
                                 normalization_dir=queue, navi_call=navi, lookup=lookup)

    assert finalize() is False
    request, = (queue / "pending").glob("*.json")
    assert json.loads(request.read_text())["paths"] == ["first.wav", "second.wav"]
    (queue / "processing").mkdir()
    request.rename(queue / "processing" / request.name)
    assert finalize() is False
    assert store.get(job["id"])["stage"] == "normalizing"
    (queue / "done").mkdir()
    (queue / "done" / request.name).write_text(json.dumps({"id": request.stem, "status": "done"}))
    server["allowed"] = True
    assert finalize() is False
    assert store.get(job["id"])["stage"] == "scanning"
    assert server["playlists"] == {}
    server["scanning"] = False
    assert finalize() is False
    assert store.get(job["id"])["status"] == "waiting"
    assert store.get(job["id"])["result"]["missing_tracks"] == [1]
    server["indexed"] = True
    assert finalize() is True
    assert store.get(job["id"])["status"] == "done"
    playlist, = server["playlists"].values()
    assert playlist["songId"] == ["one", "two", "one"]
    assert playlist["name"] == "Ordered playlist"
    assert playlist["public"] == "true"
    assert server["scans"] == 1
    assert list(music.rglob("*.m3u")) == []


def test_all_existing_songs_create_native_playlist_without_downloading(worker, monkeypatch):
    monkeypatch.setattr(main.accounts, "get_navidrome_account_by_id", lambda *_: {
        "server": "http://navidrome", "username": "listener", "password": "secret",
    })
    monkeypatch.setattr(main.navidrome_dedup, "search_navidrome_song", lambda url, user, password, artist, title, **kwargs: {
        "id": {"First": "one", "Second": "two"}[title], "title": title, "artist": artist,
        "suffix": "flac", "duration": 180, "path": f"Artist/{title}.flac",
    })
    playlists = {}

    def navi(url, user, password, endpoint, params=None):
        response = {"status": "ok"}
        if endpoint == "getScanStatus.view":
            response["scanStatus"] = {"scanning": False}
        elif endpoint == "getPlaylists.view":
            response["playlists"] = {"playlist": list(playlists.values())}
        elif endpoint == "createPlaylist.view":
            playlists["native-1"] = {"id": "native-1", **params}
            response["playlist"] = {"id": "native-1"}
        elif endpoint == "updatePlaylist.view":
            playlists[params["playlistId"]].update(params)
        else:
            assert endpoint == "startScan.view"
        return {"subsonic-response": response}

    monkeypatch.setattr(main.navidrome_dedup, "subsonic_request", navi)
    store, job = enqueue_tracks([{"title": "First", "artist": "Artist", "url": "https://media.invalid/first"},
                                 {"title": "Second", "artist": "Artist", "url": "https://media.invalid/second"},
                                 {"title": "First", "artist": "Artist", "url": "https://media.invalid/first"}],
                                navi_sync=True, navi_account_id="listener", flags={
                                    "audio_only": True, "check_navidrome": True,
                                    "navidrome_skip_mode": "all", "generate_m3u": True, "m3u_name": "Existing songs",
                                })
    assert finish(job["id"])["status"] == "done"
    assert [row["status"] for row in store.tracks(job["id"])] == ["existing"] * 3
    assert playlists["native-1"]["songId"] == ["one", "two", "one"]
    assert playlists["native-1"]["name"] == "Existing songs"
    assert not list(Path(main.DOWNLOADS_DIR).rglob("*"))
    assert worker.downloads == worker.copies == []


def test_ambiguous_soulseek_recordings_leave_durable_review_without_fallback(worker, monkeypatch):
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda *_: {"server": "http://slskd"})
    peers = [{"username": username, "hasFreeUploadSlot": True, "uploadSpeed": 500000,
              "queueLength": 0, "fileCount": 1, "lockedFileCount": 0, "lockedFiles": [],
              "files": [{"filename": "Artist/Album/01 - Song.flac", "size": 42000000,
                         "length": length, "extension": "flac", "bitDepth": 16,
                         "sampleRate": 44100, "bitRate": 900, "isLocked": False}]}
             for username, length in (("short-recording", 180), ("long-recording", 250))]
    search_id = "5ca4302a-26be-49ad-b223-cfb452da1570"
    responses = iter([{"id": search_id, "isComplete": False, "state": "InProgress", "responses": []},
                      {"id": search_id, "isComplete": True, "state": "Completed",
                       "endedAt": "2026-11-07T12:00:05Z", "responses": peers}] * 2)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(next(responses)).encode()

    def urlopen(request, timeout):
        assert "/transfers/" not in urlparse(request.full_url).path
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    store, job = enqueue_tracks([{"title": "Song", "artist": "Artist", "length": None,
                                 "url": "https://media.invalid/song", "source_tool": "ytdl"}],
                                tool="soulseek", flags={"youtube_fallback": True})
    assert finish(job["id"])["status"] == "needs_review"
    row, = JobStore(store.path).tracks(job["id"])
    assert row["status"] == "needs_review"
    assert row["result"]["state"] == "needs_review"
    assert "Kilka nagrań" in row["error"]
    assert worker.downloads == worker.copies == []


@pytest.mark.parametrize("fallback", [False, True])
def test_exhausted_soulseek_peers_need_review_or_use_explicit_fallback(worker, monkeypatch, fallback):
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda *_: {"server": "http://slskd"})
    monkeypatch.setattr(main.slskd_organizer, "fetch_slskd_transfer_files", lambda **kwargs: [])

    def no_search(*args, **kwargs):
        pytest.fail("Exhausted peers triggered another external search")

    monkeypatch.setattr("urllib.request.urlopen", no_search)
    store, job = enqueue_tracks([{"title": "Song", "artist": "Artist", "length": 180,
                                 "url": "https://media.invalid/song", "source_tool": "ytdl"}],
                                tool="soulseek", flags={"youtube_fallback": fallback})
    store.update_track(job["id"], 0, status="transferring", result={
        "selected": {"username": "third-peer", "filename": "Artist/Album/01 - Song.flac"},
        "excluded_usernames": ["first-peer", "second-peer"], "requested_at": time.time() - 901,
    })
    assert finish(job["id"])["status"] == ("done" if fallback else "needs_review")
    row, = store.tracks(job["id"])
    if fallback:
        assert row["status"] == "imported"
        assert row["result"]["fallback"] is True
        assert (Path(main.DOWNLOADS_DIR) / "Artist" / "song.wav").is_file()
        assert worker.downloads == ["https://media.invalid/song"]
    else:
        assert row["status"] == "needs_review"
        assert row["result"]["excluded_usernames"] == ["first-peer", "second-peer", "third-peer"]
        assert worker.downloads == worker.copies == []


def test_scan_polling_does_not_consume_failure_budget(worker, monkeypatch):
    monkeypatch.setattr(main.accounts, "get_navidrome_account_by_id", lambda *_: {
        "server": "http://navidrome", "username": "listener", "password": "secret",
    })
    server = {"scanning": True, "scans": 0}

    def navi(url, user, password, endpoint, params=None):
        assert endpoint in {"startScan.view", "getScanStatus.view"}
        server["scans"] += endpoint == "startScan.view"
        return {"subsonic-response": {"status": "ok", "scanStatus": {"scanning": server["scanning"]}}}

    monkeypatch.setattr(main.navidrome_dedup, "subsonic_request", navi)
    store, job = enqueue_tracks([{"title": "Existing"}], navi_sync=True, navi_account_id="listener")
    store.update_track(job["id"], 0, status="existing", result={"navidrome_id": "one"})
    for _ in range(12):
        assert tick(job["id"])["status"] == "waiting"
    current = store.get(job["id"])
    assert current["attempts"] >= 12
    assert current["failures"] == 0
    server["scanning"] = False
    assert finish(job["id"])["status"] == "done"
    assert server["scans"] == 1
    assert worker.downloads == worker.copies == []


def test_unresolved_playlist_id_becomes_partial_review_after_index_timeout(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(pipeline.time, "time", lambda: now[0])
    music = tmp_path / "music"
    write_audio(music / "first.wav")
    write_audio(music / "second.wav")
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("download", {"navi_sync": True, "flags": {"generate_m3u": True}})
    store.set_tracks(job["id"], [{"title": "First"}, {"title": "Missing"}])
    store.update_track(job["id"], 0, status="imported", result={"paths": ["first.wav"], "navidrome_id": "one"})
    store.update_track(job["id"], 1, status="imported", result={"paths": ["second.wav"]})
    playlist = {}

    def navi(endpoint, params=None):
        response = {"status": "ok"}
        if endpoint == "getScanStatus.view":
            response["scanStatus"] = {"scanning": False}
        elif endpoint == "getPlaylists.view":
            response["playlists"] = {"playlist": []}
        elif endpoint == "createPlaylist.view":
            playlist.update(params)
            response["playlist"] = {"id": "partial-1"}
        elif endpoint == "updatePlaylist.view":
            playlist.update(params)
        else:
            assert endpoint == "startScan.view"
        return {"subsonic-response": response}

    def finalize():
        return pipeline.finalize(store, store.get(job["id"]), library_dir=music,
                                 navi_call=navi, lookup=lambda *_: None)

    assert finalize() is False
    assert store.get(job["id"])["status"] == "waiting"
    now[0] += 601
    assert finalize() is True
    current = store.get(job["id"])
    assert current["status"] == "needs_review"
    assert current["result"]["missing_tracks"] == [1]
    assert current["result"]["playlist_id"] == "partial-1"
    assert playlist["songId"] == ["one"]
