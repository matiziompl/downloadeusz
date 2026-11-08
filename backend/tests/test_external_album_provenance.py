import json
from types import SimpleNamespace

import pytest
from mutagen.id3 import TXXX
from mutagen.wave import WAVE
from beets.library import Item

from backend import music_metadata
from backend.beetsplug.downloadeusz import DownloadeuszPlugin
from backend.tests.test_hybrid_importer import audio_file


@pytest.mark.parametrize("album,marked_album,expected", [
    ("Piosenka Łódź", "Piosenka Łódź", True), ("Prawdziwy album", "Piosenka Łódź", False),
])
def test_normalizer_portable_marker_is_bound_to_its_synthetic_album(tmp_path, album, marked_album, expected):
    path = audio_file(tmp_path / "song.wav", title="Piosenka Łódź", album=album)
    audio = WAVE(path)
    audio.tags.add(TXXX(encoding=3, desc="DOWNLOADEUSZ_ALBUM_FALLBACK",
                        text=[json.dumps({"version": 1, "album": marked_album}, ensure_ascii=False)]))
    audio.save()
    assert music_metadata.album_fallback_from_tags(path) is expected


def test_importer_recognizes_external_marker_but_preserves_confirmed_provenance(tmp_path, monkeypatch):
    path = audio_file(tmp_path / "song.wav", title="Song", album="Song")
    audio = WAVE(path)
    audio.tags.add(TXXX(encoding=3, desc="DOWNLOADEUSZ_ALBUM_FALLBACK",
                        text=[json.dumps({"version": 1, "album": "Song"})]))
    audio.save()
    item = Item.from_path(str(path).encode())
    monkeypatch.delenv("DOWNLOADEUSZ_BEETS_ALBUM_SOURCE", raising=False)
    plugin = object.__new__(DownloadeuszPlugin)
    plugin.originals = {}
    plugin.snapshot_sources(None, SimpleNamespace(items=[item]))
    assert plugin.originals[str(path)]["album_source"] == "fallback"
    monkeypatch.setenv("DOWNLOADEUSZ_BEETS_ALBUM_SOURCE", "confirmed")
    plugin.originals = {}
    plugin.snapshot_sources(None, SimpleNamespace(items=[item]))
    assert plugin.originals[str(path)]["album_source"] == "confirmed"
