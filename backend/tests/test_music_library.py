"""The music browser indexes real library files without trusting paths or old beets state."""

import json
import wave
from pathlib import Path

import pytest
import mutagen
from mutagen.id3 import TALB, TIT2, TPE1, TPE2, TDRC, TRCK, TPOS
from mutagen.wave import WAVE

from backend.job_store import JobStore


def write_track(path, *, title="Song", artist="Artist", album="Album"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 800)
    track = WAVE(path)
    track.add_tags()
    for frame in [TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=[artist]),
                  TALB(encoding=3, text=[album]), TPE2(encoding=3, text=["Album Artist"]),
                  TDRC(encoding=3, text=["2024"]), TRCK(encoding=3, text=["3/10"]),
                  TPOS(encoding=3, text=["1/2"])]:
        track.tags.add(frame)
    track.save()
    return path


@pytest.fixture
def library(tmp_path):
    from backend.music_library import MusicLibrary

    config, music = tmp_path / "config", tmp_path / "music"
    config.mkdir()
    music.mkdir()
    return MusicLibrary(config, music), config, music


def test_untracked_real_audio_is_indexed_with_tags_and_public_paths(library):
    index, _, music = library
    write_track(music / "Artist" / "song.wav", title="Real Song")
    (music / "not-audio.flac").write_text("not an audio file")
    summary = index.refresh()
    page = index.list_tracks()
    assert summary["total"] == page["total"] == 1
    row, = page["items"]
    assert row["title"] == "Real Song"
    assert row["artist"] == "Artist"
    assert row["album"] == "Album"
    assert row["albumartist"] == "Album Artist"
    assert str(row["year"]) == "2024"
    assert str(row["track"]) == "3"
    assert str(row["disc"]) == "1"
    assert 0.09 < row["duration"] < 0.11
    assert row["format"].lower() in {"wav", "wave"}
    assert row["relative_path"] == "Artist/song.wav"
    assert row["needs_album"] is False
    assert not row["incomplete_reason"]
    assert Path(index.path(row["id"])) == music / "Artist" / "song.wav"
    assert index.get_track(row["id"])["title"] == "Real Song"
    assert str(music) not in json.dumps(row)


def test_unicode_search_matches_title_artist_and_album_with_pagination(library):
    index, _, music = library
    write_track(music / "a.wav", title="Łódź", artist="Björk", album="Żółć")
    write_track(music / "b.wav", title="Other", artist="BJÖRK", album="Collection")
    write_track(music / "c.wav", title="Third", artist="Else", album="Collection")
    index.refresh()
    assert index.list_tracks(query="lodz")["total"] == 1
    assert index.list_tracks(query="zolc")["total"] == 1
    assert index.list_tracks(query="BJORK")["total"] == 2
    first = index.list_tracks(query="collection", offset=0, limit=1)
    second = index.list_tracks(query="collection", offset=1, limit=1)
    assert first["total"] == second["total"] == 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["offset"] == 0 and second["offset"] == 1
    assert first["limit"] == second["limit"] == 1
    assert index.list_tracks(offset=50)["items"] == []


def test_incomplete_filter_includes_empty_unknown_and_unconfirmed_fallback_albums(library):
    index, config, music = library
    write_track(music / "complete.wav", album="Confirmed")
    write_track(music / "empty.wav", album="")
    write_track(music / "unknown.wav", album="Unknown Album")
    fallback = write_track(music / "fallback.wav", album="Song")
    state = config / "beets" / "library-state.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"version": 1, "tracks": {"legacy": {
        "id": "legacy", "source": str(config / "inbox" / "fallback.wav"),
        "destination": str(fallback), "imported": True, "album_source": "fallback",
        "metadata_confirmed": True, "release_confirmed": False,
    }}}))
    summary = index.refresh()
    missing = index.list_tracks(incomplete=True)
    assert summary["incomplete"] == missing["total"] == 3
    assert {row["relative_path"] for row in missing["items"]} == {"empty.wav", "unknown.wav", "fallback.wav"}
    assert all(row["needs_album"] and row["incomplete_reason"] for row in missing["items"])


def test_refresh_persists_ids_updates_changed_tags_and_removes_deleted_files(library):
    index, config, music = library
    source = write_track(music / "one.wav", title="Before")
    index.refresh()
    before, = index.list_tracks()["items"]
    tags = WAVE(source)
    tags.tags.add(TIT2(encoding=3, text=["After"]))
    tags.save()
    with pytest.raises(ValueError):
        index.get_track(before["id"])
    index.refresh()
    after, = index.list_tracks()["items"]
    assert after["id"] == before["id"]
    assert after["title"] == "After"
    assert after["version"] != before["version"]
    reopened = type(index)(config, music)
    assert reopened.get_track(before["id"])["title"] == "After"
    source.unlink()
    reopened.refresh()
    assert reopened.list_tracks()["total"] == 0
    with pytest.raises(KeyError):
        reopened.get_track(before["id"])


def test_refresh_does_not_reread_unchanged_audio_tags_or_portable_fallback_markers(library, monkeypatch):
    index, _, music = library
    first = write_track(music / "first.wav")
    write_track(music / "second.wav")
    original = mutagen.File
    reads = []

    def read(path, *args, **kwargs):
        reads.append(Path(getattr(path, "filename", path)))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(mutagen, "File", read)
    index.refresh()
    reads.clear()
    index.refresh()
    assert reads == []
    tags = WAVE(first)
    tags.tags.add(TIT2(encoding=3, text=["Changed"]))
    tags.save()
    index.refresh()
    assert first in reads
    assert music / "second.wav" not in reads


def test_index_skips_symlinked_files_and_directories_and_rejects_substitution(library, tmp_path):
    index, _, music = library
    outside = write_track(tmp_path / "private" / "outside.wav", title="Private")
    (music / "linked.wav").symlink_to(outside)
    (music / "linked-directory").symlink_to(outside.parent, target_is_directory=True)
    local = write_track(music / "inside.wav", title="Public")
    index.refresh()
    row, = index.list_tracks()["items"]
    assert row["title"] == "Public"
    local.unlink()
    local.symlink_to(outside)
    with pytest.raises(ValueError):
        index.path(row["id"])


def test_library_move_keeps_public_id_and_manual_album_acceptance_after_refresh(library):
    index, config, music = library
    old = write_track(music / "old.wav", album="Song")
    index.refresh()
    row, = index.list_tracks()["items"]
    destination = music / "Artist" / "Song" / "new.wav"
    destination.parent.mkdir(parents=True)
    old.rename(destination)
    index.update_path(row["id"], "Artist/Song/new.wav", album_accepted=True, album_source="fallback")
    index.refresh()
    moved, = type(index)(config, music).list_tracks()["items"]
    assert moved["id"] == row["id"]
    assert moved["relative_path"] == "Artist/Song/new.wav"
    assert moved["album_accepted"] is True
    assert moved["album_source"] == "fallback"
    assert moved["needs_album"] is False
    assert Path(index.path(row["id"])) == destination
    with pytest.raises(ValueError):
        index.update_path(row["id"], "../../outside.wav")


def test_remapping_library_path_updates_remembered_sources_and_nested_progress(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    music = tmp_path / "music"
    music.mkdir()
    old, new = "Artist/old.wav", "Artist/Album/new.wav"
    old_abs, new_abs = str(music / old), str(music / new)
    store.remember_source("ytdl", "source", [{"path": old, "audio_signature": "same-audio"}])
    job = store.enqueue("download", {})
    store.set_tracks(job["id"], [{"title": "Song"}])
    store.update(job["id"], result={"paths": [old], "nested": {"destination": old_abs}, "message": f"See {old}"})
    store.update_track(job["id"], 0, status="imported", result={"paths": [old], "nested": {"destination": old_abs}})
    store.remap_library_path(old, new, music)
    remembered, = store.source_files("ytdl", "source")
    assert remembered == {"path": new, "audio_signature": "same-audio"}
    for result in [store.get(job["id"])["result"], store.tracks(job["id"])[0]["result"]]:
        assert result["paths"] == [new]
        assert result["nested"]["destination"] == new_abs
    assert store.get(job["id"])["result"]["message"] == f"See {old}"
