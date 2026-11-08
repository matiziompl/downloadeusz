import pytest

from backend import navidrome_dedup
from backend.job_store import JobStore
from backend.pipeline import finalize


@pytest.mark.parametrize("server_path", [
    "Artist/Album/Song.flac",
    "/music/Artist/Album/Song.flac",
    r"C:\Music\Artist\Album\Song.flac",
])
def test_final_path_selects_imported_candidate_before_recording_ambiguity(monkeypatch, server_path):
    songs = [
        {"id": "old", "artist": "Artist", "title": "Song", "duration": 180,
         "path": "Artist/Album/Song.mp3", "musicBrainzId": "11111111-1111-4111-8111-111111111111"},
        {"id": "imported", "artist": "Artist", "title": "Song", "duration": 180,
         "path": server_path, "musicBrainzId": "22222222-2222-4222-8222-222222222222"},
    ]
    monkeypatch.setattr(navidrome_dedup, "subsonic_request", lambda *args, **kwargs:
                        {"subsonic-response": {"status": "ok", "searchResult3": {"song": songs}}})

    song = navidrome_dedup.search_navidrome_song(
        "http://navidrome", "user", "password", "Artist", "Song", length=180,
        required_paths=["Artist/Album/Song.flac"])

    assert song["id"] == "imported"


@pytest.mark.parametrize("overrides", [
    {"path": "OtherArtist/Album/Song.flac"},
    {"path": "Artist/Album/Song.flac.bak"},
    {"path": ""},
    {"artist": "Different Artist"},
    {"title": "Song (Live)"},
    {"duration": 250},
])
def test_final_path_does_not_bypass_path_boundary_or_recording_checks(monkeypatch, overrides):
    song = {"id": "wrong", "artist": "Artist", "title": "Song", "duration": 180,
            "path": "Artist/Album/Song.flac", **overrides}
    monkeypatch.setattr(navidrome_dedup, "subsonic_request", lambda *args, **kwargs:
                        {"subsonic-response": {"status": "ok", "searchResult3": {"song": [song]}}})

    assert navidrome_dedup.search_navidrome_song(
        "http://navidrome", "user", "password", "Artist", "Song", length=180,
        required_paths=["Artist/Album/Song.flac"]) is None


@pytest.mark.parametrize("linked_directory", ["pending", "processing", "done", "queue"])
def test_normalization_producer_rejects_symlinked_queue_directories(tmp_path, linked_directory):
    music = tmp_path / "music"
    music.mkdir()
    (music / "song.flac").write_bytes(b"audio")
    queue, outside = tmp_path / "queue", tmp_path / "outside"
    outside.mkdir()
    if linked_directory == "queue":
        queue.symlink_to(outside, target_is_directory=True)
    else:
        queue.mkdir()
        (queue / linked_directory).symlink_to(outside, target_is_directory=True)
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("import", {})
    store.update(job["id"], result={"paths": ["song.flac"]})

    with pytest.raises(ValueError):
        finalize(store, store.get(job["id"]), library_dir=music, normalization_dir=queue)

    assert list(outside.iterdir()) == []
    assert store.get(job["id"])["status"] == "queued"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_library_write_lock_preserves_files_and_rejects_non_lock_nodes(tmp_path, kind):
    import os
    from backend.audio_identity import library_write_lock
    library = tmp_path / "music"
    library.mkdir()
    original = tmp_path / "recording.flac"
    original.write_bytes(b"preserved audio")
    lock = library / ".downloadeusz-write.lock"
    if kind == "symlink":
        lock.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, lock)
    else:
        os.mkfifo(lock)
    with pytest.raises((OSError, ValueError)):
        with library_write_lock(library):
            pytest.fail("A non-lock filesystem node was accepted")
    assert original.read_bytes() == b"preserved audio"
