import json
from pathlib import Path

from backend.job_store import JobStore
from backend.pipeline import finalize


def test_normalization_ack_precedes_scan_and_playlist_preserves_order(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    (music / "song.mp3").write_bytes(b"audio")
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("download", {"flags": {"generate_m3u": True, "m3u_name": "My playlist"}, "navi_sync": True})
    store.set_tracks(job["id"], [{"title": "First"}, {"title": "Second"}, {"title": "First"}])
    for index, navidrome_id in enumerate(("one", "two", "one")):
        store.update_track(job["id"], index, status="imported", result={"paths": ["song.mp3"], "navidrome_id": navidrome_id})
    calls = []

    def navi(endpoint, params=None):
        calls.append((endpoint, params))
        data = {"status": "ok"}
        if endpoint == "getScanStatus.view":
            data["scanStatus"] = {"scanning": False}
        if endpoint == "createPlaylist.view":
            data["playlist"] = {"id": "created"}
        return {"subsonic-response": data}

    queue = tmp_path / "normalization"
    assert not finalize(store, job, library_dir=music, normalization_dir=queue, navi_call=navi)
    assert not calls
    pending, = (queue / "pending").glob("*.json")
    request = json.loads(pending.read_text())
    assert request["paths"] == ["song.mp3"]
    # The app may run as root while the sidecar drops to PUID:PGID.
    assert pending.stat().st_mode & 0o004
    (queue / "done").mkdir()
    (queue / "done" / pending.name).write_text(json.dumps({"id": request["id"], "status": "done"}))
    assert finalize(store, store.get(job["id"]), library_dir=music, normalization_dir=queue, navi_call=navi)
    assert next(params for name, params in calls if name == "createPlaylist.view")["songId"] == ["one", "two", "one"]
    assert store.get(job["id"])["result"]["playlist_id"] == "created"


def test_waits_for_actual_scan_completion_and_retry_does_not_recreate_playlist(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("download", {"navi_sync": True})
    scanning = [True]
    calls = []

    def navi(endpoint, params=None):
        calls.append(endpoint)
        return {"subsonic-response": {"status": "ok", "scanStatus": {"scanning": scanning[0]}}}

    assert not finalize(store, job, library_dir=tmp_path, navi_call=navi)
    scanning[0] = False
    assert finalize(store, store.get(job["id"]), library_dir=tmp_path, navi_call=navi)
    assert calls.count("startScan.view") == 1


def test_rejects_normalization_path_outside_library(tmp_path):
    import pytest
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("download", {})
    store.set_tracks(job["id"], [{}])
    store.update_track(job["id"], 0, status="imported", result={"paths": ["../private.mp3"]})
    with pytest.raises(ValueError):
        finalize(store, job, library_dir=tmp_path / "music", normalization_dir=tmp_path / "queue")
