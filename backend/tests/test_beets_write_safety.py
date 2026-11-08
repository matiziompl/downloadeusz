"""Actual beets must not confirm failed metadata writes or lose its only copy."""

import hashlib
import json
import shutil
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.id3 import TALB, TDRC, TIT2, TPE1, TRCK
from mutagen.wave import WAVE

from backend import beets_importer


@pytest.fixture
def write_failure_catalog(tmp_path):
    if not shutil.which("beet"):
        pytest.skip("beet CLI required")
    mode = tmp_path / "catalog-mode"
    mode.write_text("unknown", encoding="utf-8")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "writetest.py").write_text('''
import os
from pathlib import Path
from beets.metadata_plugins import MetadataSourcePlugin
from beets.autotag.hooks import TrackInfo
from beets.library import Item, WriteError

MODE = Path(_MODE_)

class WriteTestPlugin(MetadataSourcePlugin):
    def __init__(self):
        super().__init__()
        if MODE.read_text() != "unknown":
            def failing_write(item, *args, **kwargs):
                if MODE.read_text() == "partial-write":
                    Path(os.fsdecode(item.path)).write_bytes(b"incomplete metadata save")
                raise WriteError(item.path, OSError("simulated metadata write failure"))
            Item.write = failing_write
    def album_for_id(self, value): return None
    def track_for_id(self, value): return None
    def candidates(self, *args): return []
    def item_candidates(self, item, artist, title):
        if MODE.read_text() == "unknown": return []
        return [TrackInfo(title="Local Song", artist="Local Artist", artists=["Local Artist"],
                          album="Local Album", year=2001, index=1, length=1.0,
                          track_id="write-test-recording", data_source="WriteTest")]
'''.replace("_MODE_", repr(str(mode))), encoding="utf-8")
    actual_config = beets_importer._managed_config
    actual_which = shutil.which

    def configuration(state_dir, destination):
        settings = actual_config(state_dir, destination)
        settings["plugins"] = ["downloadeusz", "writetest"]
        settings["pluginpath"].append(str(plugins))
        return settings

    with patch.object(beets_importer, "_managed_config", side_effect=configuration), patch(
        "shutil.which", side_effect=lambda name: None if name == "fpcalc" else actual_which(name)
    ):
        yield mode


def tagged_source(tmp_path):
    source = tmp_path / "incoming" / "song.wav"
    source.parent.mkdir()
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    audio = WAVE(source)
    audio.add_tags()
    for frame in (TIT2(text="Local Song!"), TPE1(text="Local Artist"), TALB(text="Local Album"),
                  TDRC(text="2001"), TRCK(text="1")):
        audio.tags.add(frame)
    audio.save()
    return source


def import_source(tmp_path, source):
    return beets_importer.import_paths(
        [str(source)], source_dir=str(source.parent), dest_dir=str(tmp_path / "music"),
        config_dir=str(tmp_path / "config"),
    )


@pytest.mark.parametrize("failure", ["fail-write", "partial-write"])
def test_copy_with_failed_tag_write_stays_tracked_but_unconfirmed(tmp_path, write_failure_catalog, failure):
    source = tagged_source(tmp_path)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    write_failure_catalog.write_text(failure, encoding="utf-8")

    result = import_source(tmp_path, source)

    assert result["errors"]
    assert result["organized"] == 1
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    assert entry["imported"] is True
    assert entry["metadata_confirmed"] is False
    assert "zapis" in entry["reason"].lower() or "write" in entry["reason"].lower()
    destination = Path(entry["destination"])
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == original
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    assert import_source(tmp_path, source)["organized"] == 0
    assert len(list((tmp_path / "music").rglob("*.wav"))) == 1


@pytest.mark.parametrize("failure", ["fail-write", "partial-write"])
def test_failed_in_place_write_restores_only_copy_and_never_confirms(tmp_path, write_failure_catalog, failure):
    source = tagged_source(tmp_path)
    assert import_source(tmp_path, source)["organized"] == 1
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    destination = Path(entry["destination"])
    original = destination.read_bytes()
    source.unlink()
    write_failure_catalog.write_text(failure, encoding="utf-8")

    result = beets_importer.enrich_pending(
        config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"),
        retry=True, item_id=entry["id"],
    )

    assert result["errors"]
    assert result["updated"] == 0
    assert destination.read_bytes() == original
    assert str(WAVE(destination).tags["TIT2"]) == "Local Song!"
    state = json.loads((tmp_path / "config" / "beets" / "library-state.json").read_text())
    assert state["tracks"][entry["id"]]["metadata_confirmed"] is False
    assert state["tracks"][entry["id"]]["destination"] == str(destination)
    assert list((tmp_path / "music").rglob("*.wav")) == [destination]


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_enrichment_never_tags_linked_file_even_when_bytes_match_saved_copy(tmp_path, link_kind):
    source = tagged_source(tmp_path)
    destination = tmp_path / "music" / "song.wav"
    destination.parent.mkdir()
    shutil.copy2(source, destination)
    config = tmp_path / "config" / "beets"
    config.mkdir(parents=True)
    state = {"version": 1, "tracks": {}}
    entry = beets_importer._save_track(state, config / "library-state.json", source, {
        "event": "imported", "destination": str(destination),
        "metadata_confirmed": False, "release_confirmed": False,
        "reason": "Niepotwierdzone metadane", "candidates": [],
    }, destination.parent)
    before = source.read_bytes()
    destination.unlink()
    if link_kind == "symlink":
        destination.symlink_to(source)
    else:
        destination.hardlink_to(source)

    with patch("shutil.which", return_value="/usr/bin/beet"), patch("subprocess.run") as cli:
        result = beets_importer.enrich_pending(
            config_dir=str(config.parent), dest_dir=str(destination.parent),
            retry=True, item_id=entry["id"],
        )

    assert result["errors"]
    assert result["updated"] == 0
    cli.assert_not_called()
    assert source.read_bytes() == before
