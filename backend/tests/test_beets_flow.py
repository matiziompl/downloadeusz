import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.tests.test_durable_workflow import write_audio


@pytest.fixture
def beets_app(tmp_path, monkeypatch):
    for setting in ("DOWNLOADS_DIR", "SLSKD_DOWNLOADS_DIR", "SPOTDL_CONFIG_DIR", "LOGS_DIR", "BEETS_INBOX_DIR"):
        directory = tmp_path / setting.lower()
        directory.mkdir()
        monkeypatch.setattr(main, setting, str(directory), raising=False)
    monkeypatch.setattr(main, "BEETS_ENABLED", True, raising=False)
    monkeypatch.setattr(main, "BEETS_AUTO_INTERVAL", 0, raising=False)
    monkeypatch.setattr(main, "BEETS_JOB_LOCK", threading.Lock(), raising=False)
    monkeypatch.setattr(main, "BEETS_STATE", {"last_task_id": None, "last_error": None}, raising=False)
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda config_dir: None)
    with patch("shutil.which", return_value="/usr/local/bin/beet"):
        yield TestClient(main.app)


def test_beets_status_reports_disabled_without_starting_import(beets_app, monkeypatch):
    monkeypatch.setattr(main, "BEETS_ENABLED", False)
    response = beets_app.get("/api/beets/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["running"] is False


@pytest.mark.parametrize("enabled,available", [(False, True), (True, False)])
def test_beets_import_requires_enabled_and_installed(beets_app, monkeypatch, enabled, available):
    monkeypatch.setattr(main, "BEETS_ENABLED", enabled)
    with patch("shutil.which", return_value="/usr/local/bin/beet" if available else None):
        response = beets_app.post("/api/beets/import", json={})
    assert response.status_code == 400
    assert not main.BEETS_JOB_LOCK.locked()


def test_beets_import_rejects_overlapping_job(beets_app):
    main.BEETS_JOB_LOCK.acquire()
    try:
        response = beets_app.post("/api/beets/import", json={})
        assert response.status_code == 409
    finally:
        main.BEETS_JOB_LOCK.release()


def test_beets_import_rejects_client_selected_filesystem_paths(beets_app):
    response = beets_app.post("/api/beets/import", json={"source_dir": "/etc", "paths": ["/etc/passwd"]})
    assert response.status_code == 422
    assert not main.BEETS_JOB_LOCK.locked()


@pytest.mark.parametrize("returncode", [0, 1])
def test_download_stages_audio_and_imports_only_successful_batches(beets_app, returncode):
    task_id = "download-qa"
    expected_batch = Path(main.BEETS_INBOX_DIR) / task_id
    observed = {}
    original_popen = main.subprocess.Popen

    def download_process(command, **kwargs):
        if command[0] == "ffmpeg":
            return original_popen(command, **kwargs)
        cwd = kwargs["cwd"]
        observed["cwd"] = Path(cwd)
        observed["audio"] = write_audio(Path(cwd) / "song.wav").read_bytes()
        return SimpleNamespace(stdout=iter([]), wait=lambda: returncode, returncode=returncode)

    def import_batch(*args, **kwargs):
        observed["ready_at_import"] = (expected_batch / ".ready").exists()

    with patch.object(main.subprocess, "Popen", side_effect=download_process), \
         patch.object(main, "fix_download_permissions"), \
         patch.object(main, "run_beets_import_task", side_effect=import_batch, create=True) as importer:
        main.run_download_task(task_id, main.DownloadRequest(
            tool="ytdl", url="https://youtube.com/watch?v=qa", flags={"audio_only": True}
        ))

    assert observed["cwd"] == expected_batch
    assert (expected_batch / "song.wav").read_bytes() == observed["audio"]
    assert (expected_batch / ".ready").exists() is (returncode == 0)
    if returncode == 0:
        assert observed["ready_at_import"] is True
    else:
        importer.assert_not_called()


def test_playlist_download_uses_the_same_staged_import_as_other_audio(beets_app):
    observed = {}
    original_popen = main.subprocess.Popen

    def download_process(command, **kwargs):
        if command[0] == "ffmpeg":
            return original_popen(command, **kwargs)
        cwd = kwargs["cwd"]
        observed["cwd"] = cwd
        write_audio(Path(cwd) / "song.wav")
        return SimpleNamespace(stdout=iter([]), wait=lambda: 0, returncode=0)

    with patch.object(main.subprocess, "Popen", side_effect=download_process), \
         patch.object(main, "fix_download_permissions"), \
         patch.object(main, "run_beets_import_task", create=True) as importer:
        main.run_download_task("playlist-qa", main.DownloadRequest(
            tool="spotdl", url="https://open.spotify.com/playlist/qa", flags={"generate_m3u": True}
        ))

    assert observed["cwd"] == str(Path(main.BEETS_INBOX_DIR) / "playlist-qa")
    assert (Path(main.BEETS_INBOX_DIR) / "playlist-qa" / ".ready").exists()
    importer.assert_called_once()


@pytest.mark.parametrize("tool,archive_flag,filename", [
    ("spotdl", "--archive", "spotdl-archive.txt"),
    ("ytdl", "--download-archive", "yt-dlp-archive.txt"),
])
def test_staged_downloads_share_persistent_archive(beets_app, tool, archive_flag, filename):
    commands = []

    def process(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=iter([]), wait=lambda: 0, returncode=0)

    with patch.object(main.subprocess, "Popen", side_effect=process), \
         patch.object(main, "fix_download_permissions"), \
         patch.object(main, "run_beets_import_task"):
        for task in ("first", "second"):
            main.run_download_task(task, main.DownloadRequest(
                tool=tool, url="https://example.com/song", flags={"audio_only": True, "skip_downloaded": True}))
    for command in commands:
        assert command[command.index(archive_flag) + 1] == str(Path(main.SPOTDL_CONFIG_DIR) / filename)


def test_slskd_beets_route_rejects_overlap_with_manual_import(beets_app):
    main.BEETS_JOB_LOCK.acquire()
    try:
        with patch.object(main.slskd_organizer, "organize_slskd_downloads", return_value={"organized": 0}), \
             patch.object(main, "fix_download_permissions"):
            response = beets_app.post("/api/slskd/organize", json={"slskd_url": "http://slskd:5030"})
        assert response.status_code == 409
    finally:
        main.BEETS_JOB_LOCK.release()


def test_slskd_multidisc_waits_for_pending_disc_without_local_audio(tmp_path, monkeypatch):
    album = tmp_path / "Artist - Album"
    first_disc = album / "CD1"
    first_disc.mkdir(parents=True)
    (first_disc / "01.flac").write_bytes(b"finished disc one")
    transfers = [
        {"filename": "Artist - Album/CD1/01.flac", "state": "Completed, Succeeded"},
        {"filename": "Artist - Album/CD2/01.flac", "state": "Queued, Locally"},
    ]
    monkeypatch.setattr(main.slskd_organizer, "fetch_slskd_transfer_files", lambda **kwargs: transfers)
    assert main.slskd_organizer.get_completed_album_directories("http://slskd", str(tmp_path)) == []


def test_beets_manual_import_selects_ready_batches_only(beets_app):
    ready = Path(main.BEETS_INBOX_DIR) / "completed"
    downloading = Path(main.BEETS_INBOX_DIR) / "in-progress"
    for directory in (ready, downloading):
        directory.mkdir()
        (directory / "song.mp3").write_bytes(b"original audio")
    (ready / ".ready").touch()

    with patch.object(main.beets_importer, "import_paths", return_value={"organized": 0, "skipped": 1, "errors": []}) as importer:
        response = beets_app.post("/api/beets/import", json={"retry": True})

    assert response.status_code == 200
    assert response.json()["status"] == "started"
    assert importer.call_args.args == ([str(ready)],)
    assert importer.call_args.kwargs["source_dir"] == main.BEETS_INBOX_DIR
    assert importer.call_args.kwargs["dest_dir"] == main.DOWNLOADS_DIR
    assert importer.call_args.kwargs["retry"] is True
    status = beets_app.get("/api/beets/status").json()
    assert status["last_task_id"] == response.json()["task_id"]
    assert status["running"] is False
    assert status["last_error"] is None
    assert (ready / "song.mp3").read_bytes() == b"original audio"
    assert (downloading / "song.mp3").read_bytes() == b"original audio"


def test_beets_ready_batch_splits_distinct_leaf_albums(beets_app):
    batch = Path(main.BEETS_INBOX_DIR) / "completed-playlist"
    first = batch / "Artist A" / "2020 - Album A"
    second = batch / "Artist B" / "2021 - Album B"
    for album in (first, second):
        album.mkdir(parents=True)
        (album / "01 Song.flac").write_bytes(b"one song from album")
    (batch / ".ready").touch()

    with patch.object(main.beets_importer, "import_paths", return_value={"organized": 0, "skipped": 2, "errors": []}) as importer:
        response = beets_app.post("/api/beets/import", json={})

    assert response.status_code == 200
    assert importer.call_args.args == ([str(first), str(second)],)


def test_beets_ready_batch_keeps_all_discs_in_one_album(beets_app):
    batch = Path(main.BEETS_INBOX_DIR) / "completed-album"
    album = batch / "Artist" / "2020 - Double Album"
    for disc in ("CD1", "CD2"):
        directory = album / disc
        directory.mkdir(parents=True)
        (directory / "01 Song.flac").write_bytes(b"song on one disc")
    (batch / ".ready").touch()

    with patch.object(main.beets_importer, "import_paths", return_value={"organized": 0, "skipped": 2, "errors": []}) as importer:
        response = beets_app.post("/api/beets/import", json={})

    assert response.status_code == 200
    assert importer.call_args.args == ([str(album)],)


def test_beets_import_failure_releases_lock_and_preserves_source(beets_app):
    batch = Path(main.BEETS_INBOX_DIR) / "completed"
    batch.mkdir()
    (batch / ".ready").touch()
    (batch / "song.flac").write_bytes(b"original lossless audio")

    with patch.object(main.beets_importer, "import_paths", side_effect=RuntimeError("metadata service unavailable")):
        response = beets_app.post("/api/beets/import", json={})

    assert response.status_code == 200
    status = beets_app.get("/api/beets/status").json()
    assert status["running"] is False
    assert "metadata service unavailable" in status["last_error"]
    assert (batch / "song.flac").read_bytes() == b"original lossless audio"
    assert (batch / ".ready").exists()


@pytest.mark.parametrize("organized", [0, 1])
def test_beets_scans_navidrome_only_when_library_changed(beets_app, organized):
    batch = Path(main.BEETS_INBOX_DIR) / "completed"
    batch.mkdir()
    (batch / ".ready").touch()
    (batch / "song.flac").write_bytes(b"audio")

    with patch.object(main.beets_importer, "import_paths", return_value={"organized": organized, "skipped": 1-organized, "errors": []}), \
         patch.object(main, "fix_download_permissions"), \
         patch.object(main.navidrome_dedup, "subsonic_request") as scan:
        response = beets_app.post("/api/beets/import", json={
            "navi_sync": True, "navi_url": "http://navidrome", "navi_user": "listener", "navi_pass": "secret"
        })

    assert response.status_code == 200
    if organized:
        assert scan.call_args.args[:4] == ("http://navidrome", "listener", "secret", "startScan.view")
    else:
        scan.assert_not_called()


def test_beets_sync_resolves_navidrome_account_by_username(beets_app):
    batch = Path(main.BEETS_INBOX_DIR) / "completed"
    batch.mkdir()
    (batch / ".ready").touch()
    (batch / "song.flac").write_bytes(b"audio")
    (Path(main.SPOTDL_CONFIG_DIR) / "navidrome_accounts.txt").write_text(
        'Server: "http://configured-navidrome:4533"\nuser: "listener"\npassword: "account-password"\n',
        encoding="utf-8",
    )

    with patch.object(main.beets_importer, "import_paths", return_value={"organized": 1, "skipped": 0, "errors": []}), \
         patch.object(main, "fix_download_permissions"), \
         patch.object(main.navidrome_dedup, "subsonic_request") as scan:
        response = beets_app.post("/api/beets/import", json={"navi_sync": True, "navi_user": "listener"})

    assert response.status_code == 200
    assert scan.call_args.args[:4] == (
        "http://configured-navidrome:4533", "listener", "account-password", "startScan.view"
    )


def test_beets_timer_persists_import_for_worker_and_stops_cleanly(beets_app, monkeypatch):
    monkeypatch.setattr(main, "BEETS_AUTO_INTERVAL", 0.001)
    stop = asyncio.Event()
    async def run_timer():
        timer = asyncio.create_task(main.beets_auto_loop(stop))
        try:
            for _ in range(100):
                if main.get_job_store().list():
                    break
                await asyncio.sleep(0.005)
        finally:
            stop.set()
            await timer

    asyncio.run(run_timer())

    assert stop.is_set()
    job, = main.get_job_store().list()
    assert job["kind"] == "import"
    assert job["status"] == "queued"
    assert not main.BEETS_JOB_LOCK.locked()
