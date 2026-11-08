"""Synthetic album names must improve copies without inventing release evidence."""

import json
from pathlib import Path

import pytest
from mutagen.wave import WAVE

from backend import beets_importer
from backend.beetsplug import downloadeusz_policy as policy
from backend.tests.test_hybrid_importer import audio_file, import_one, offline_beets
from backend.tests.test_beets_write_safety import write_failure_catalog


@pytest.mark.parametrize("album", ["", "Unknown Album", "_Single", "Nieznany album"])
def test_missing_album_uses_clean_song_title_in_tags_and_path(tmp_path, offline_beets, album):
    source = audio_file(tmp_path / "incoming" / "song.wav", title="Local Song (Official Audio)", album=album)
    original = source.read_bytes()

    result = import_one(tmp_path, source)

    assert result == {"organized": 1, "skipped": 0, "errors": []}
    destination, = (tmp_path / "music").rglob("*.wav")
    assert destination.relative_to(tmp_path / "music").parts == ("Local Artist", "Local Song", "Local Song.wav")
    assert str(WAVE(destination).tags["TALB"]) == "Local Song"
    assert str(WAVE(destination).tags["TIT2"]) == "Local Song"
    assert source.read_bytes() == original
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    assert entry["album_source"] == "fallback"
    assert entry["release_confirmed"] is False
    assert entry["metadata_confirmed"] is False
    assert entry["next_attempt"] is not None


@pytest.mark.parametrize("album, directory", [("Genuine Album", "2001 - Genuine Album"), ("Single", "2001 - Single")])
def test_genuine_album_tags_and_layout_are_preserved(tmp_path, offline_beets, album, directory):
    source = audio_file(tmp_path / "incoming" / "song.wav", album=album)
    original = source.read_bytes()
    assert import_one(tmp_path, source)["organized"] == 1
    destination, = (tmp_path / "music").rglob("*.wav")
    assert destination.relative_to(tmp_path / "music").parts == ("Local Artist", directory, "01 Local Song!.wav")
    assert str(WAVE(destination).tags["TALB"]) == album
    assert source.read_bytes() == original


def test_synthetic_album_is_not_release_evidence_even_with_matching_year():
    source = {"album": "Local Song", "year": 2001, "album_source": "fallback"}
    assert not policy.release_agrees(source, {"album": "Local Song", "year": 2001})


def test_retry_does_not_verify_matching_release_using_synthetic_album(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav", title="Local Album", album="")
    assert import_one(tmp_path, source)["organized"] == 1
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    destination = Path(entry["destination"])
    # A release-year tag can exist without a genuine album. It must not make
    # a future matching synthetic title prove an edition.
    from mediafile import MediaFile
    audio = MediaFile(destination)
    audio.title = "Local Song!"
    audio.year = 2001
    audio.save()
    offline_beets.write_text("recognized")

    result = beets_importer.enrich_pending(config_dir=str(tmp_path / "config"),
                                         dest_dir=str(tmp_path / "music"), retry=True, item_id=entry["id"])

    assert result["errors"] == []
    current, = beets_importer.list_review(str(tmp_path / "config"))
    assert current["metadata_confirmed"] is True
    assert current["release_confirmed"] is False
    assert current["album_source"] == "fallback"
    assert str(WAVE(destination).tags["TALB"]) == "Local Album"


def test_explicit_retry_can_add_fallback_to_legacy_copy_without_source(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav", album="")
    destination = tmp_path / "music" / "Local Artist" / "_Single" / "Local Song!.wav"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(source.read_bytes())
    state_dir = tmp_path / "config" / "beets"
    state_dir.mkdir(parents=True)
    state = {"version": 1, "tracks": {}}
    entry = beets_importer._save_track(state, state_dir / "library-state.json", source,
                                     {"event": "imported", "destination": str(destination)}, tmp_path / "music")
    source.unlink()

    result = beets_importer.enrich_pending(config_dir=str(tmp_path / "config"),
                                         dest_dir=str(tmp_path / "music"), retry=True, item_id=entry["id"])

    assert result["errors"] == []
    assert result["updated"] == 1
    assert str(WAVE(destination).tags["TALB"]) == "Local Song!"
    current, = beets_importer.list_review(str(tmp_path / "config"))
    assert current["album_source"] == "fallback"
    assert current["release_confirmed"] is False
    assert list((tmp_path / "music").rglob("*.wav")) == [destination]


@pytest.mark.parametrize("album", ["Unknown Album", "Nieznany album", "_Single"])
def test_placeholder_source_album_cannot_confirm_release(album):
    assert not policy.release_agrees({"album": album, "year": 2001}, {"album": album, "year": 2001})


@pytest.mark.parametrize("failure", ["fail-write", "partial-write"])
def test_failed_fallback_write_restores_copied_audio_without_confirming_album(tmp_path, write_failure_catalog, failure):
    source = audio_file(tmp_path / "incoming" / "song.wav", title="Entirely Different Song", album="")
    original = source.read_bytes()
    write_failure_catalog.write_text(failure, encoding="utf-8")

    result = import_one(tmp_path, source)

    assert result["errors"]
    assert result["organized"] == 1
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    assert entry["album_source"] != "fallback"
    assert entry["release_confirmed"] is False
    assert entry["metadata_confirmed"] is False
    assert Path(entry["destination"]).read_bytes() == original
    assert source.read_bytes() == original


def test_scheduled_enrichment_does_not_migrate_legacy_album_tags(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav", album="")
    destination = tmp_path / "music" / "legacy.wav"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    before = destination.read_bytes()
    state_dir = tmp_path / "config" / "beets"
    state_dir.mkdir(parents=True)
    state = {"version": 1, "tracks": {}}
    beets_importer._save_track(state, state_dir / "library-state.json", source,
                              {"event": "imported", "destination": str(destination)}, tmp_path / "music")
    state["tracks"][next(iter(state["tracks"]))]["next_attempt"] = 0
    (state_dir / "library-state.json").write_text(json.dumps(state))

    result = beets_importer.enrich_pending(config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"))

    assert result["errors"] == []
    assert result["updated"] == 0
    assert destination.read_bytes() == before


def test_confirmed_album_match_can_replace_synthetic_album(tmp_path):
    import os
    from types import SimpleNamespace
    from beets import config
    from beets.autotag.distance import Distance
    from beets.autotag.hooks import AlbumInfo, TrackInfo
    from beets.autotag.match import AlbumMatch
    from beets.importer import ImportTask
    from beets.library import Item, Library
    from backend.beetsplug.downloadeusz import DownloadeuszPlugin

    path = audio_file(tmp_path / "music" / "song.wav", album="Local Song!")
    library = Library(str(tmp_path / "library.db"), directory=os.fsencode(path.parent))
    item = Item.from_path(os.fsencode(path))
    item["downloadeusz_album_source"] = "fallback"
    library.add(item)
    track = TrackInfo(title="Local Song!", artist="Local Artist", track_id="recording-a", length=1.0, index=1)
    album = AlbumInfo(album="Confirmed Album", artist="Local Artist", album_id="release-a", tracks=[track], year=2024)
    task = ImportTask(None, [os.fsencode(path)], [item])
    task.set_choice(AlbumMatch(Distance(), album, {item: track}))
    helper = DownloadeuszPlugin()
    helper.review_choice(None, task)
    task.apply_metadata()
    old_copy, old_move = config["import"]["copy"].get(), config["import"]["move"].get()
    try:
        config["import"]["copy"].set(False)
        config["import"]["move"].set(False)
        helper.protect_destinations(SimpleNamespace(lib=library, config=config["import"]), task)
    finally:
        config["import"]["copy"].set(old_copy)
        config["import"]["move"].set(old_move)

    assert item.album == "Confirmed Album"
    assert item["downloadeusz_album_source"] == "confirmed"
    assert helper.decisions[str(path)]["release_confirmed"] is True


def test_beets_plugin_fallback_loads_outside_project_working_directory(tmp_path, offline_beets, monkeypatch):
    source = audio_file(tmp_path / "incoming" / "song.wav", album="")
    monkeypatch.chdir(tmp_path)
    assert import_one(tmp_path, source) == {"organized": 1, "skipped": 0, "errors": []}
    destination, = (tmp_path / "music").rglob("*.wav")
    assert str(WAVE(destination).tags["TALB"]) == "Local Song!"
