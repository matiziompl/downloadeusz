"""Independent hybrid workflow regressions around durable copies and retries."""

import hashlib
import json
import shutil
import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.id3 import TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK
from mutagen.wave import WAVE

from backend import beets_importer


def tagged_audio(path, title, number=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    audio = WAVE(path)
    audio.add_tags()
    for frame in (TIT2(text=title), TPE1(text="QA Artist"), TALB(text="QA Album"),
                  TDRC(text="2020"), TRCK(text=str(number))):
        audio.tags.add(frame)
    audio.save()
    return path


@pytest.fixture
def offline_catalog(tmp_path):
    if not shutil.which("beet"):
        pytest.skip("beet CLI required")
    plugins = tmp_path / "offline-plugins"
    plugins.mkdir()
    (plugins / "qacatalog.py").write_text('''
from beets.metadata_plugins import MetadataSourcePlugin
class QACatalogPlugin(MetadataSourcePlugin):
    def album_for_id(self, value): return None
    def track_for_id(self, value): return None
    def candidates(self, *args): return []
    def item_candidates(self, *args): return []
''', encoding="utf-8")
    configuration = beets_importer._managed_config
    which = shutil.which

    def settings(state_dir, dest):
        value = configuration(state_dir, dest)
        value["plugins"] = ["downloadeusz", "qacatalog"]
        value["pluginpath"].append(str(plugins))
        return value

    with patch.object(beets_importer, "_managed_config", side_effect=settings), \
         patch("shutil.which", side_effect=lambda name: None if name == "fpcalc" else which(name)):
        yield


def import_folder(tmp_path, album):
    return beets_importer.import_paths(
        [str(album)], source_dir=str(tmp_path / "incoming"),
        dest_dir=str(tmp_path / "music"), config_dir=str(tmp_path / "config"),
    )


def test_changed_folder_adds_only_new_song_after_process_restart(tmp_path, offline_catalog):
    album = tmp_path / "incoming" / "album"
    first = tagged_audio(album / "01 First.wav", "First")
    original = hashlib.sha256(first.read_bytes()).hexdigest()
    assert import_folder(tmp_path, album)["organized"] == 1
    first_entry, = beets_importer.list_review(str(tmp_path / "config"))
    first_destination = Path(first_entry["destination"])
    second = tagged_audio(album / "02 Second.wav", "Second", 2)

    # Every import uses a new beets subprocess; only durable state can survive it.
    result = import_folder(tmp_path, album)
    repeated = import_folder(tmp_path, album)

    assert result == {"organized": 1, "skipped": 1, "errors": []}
    assert repeated["organized"] == 0
    assert len(list((tmp_path / "music").rglob("*.wav"))) == 2
    entries = beets_importer.list_review(str(tmp_path / "config"))
    assert len(entries) == 2
    unchanged = next(entry for entry in entries if entry["source"] == str(first))
    assert unchanged["id"] == first_entry["id"]
    assert unchanged["destination"] == str(first_destination)
    assert all(entry["imported"] and not entry["metadata_confirmed"] for entry in entries)
    assert hashlib.sha256(first.read_bytes()).hexdigest() == original
    assert second.exists()


def test_legacy_success_does_not_hide_new_song_added_after_upgrade(tmp_path, offline_catalog):
    album = tmp_path / "incoming" / "album"
    tagged_audio(album / "01 First.wav", "First")
    assert import_folder(tmp_path, album)["organized"] == 1
    existing, = (tmp_path / "music").rglob("*.wav")
    original_copy = existing.read_bytes()
    state_path = tmp_path / "config" / "beets" / "import-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state[str(album)]["recognition_version"] = 2
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (state_path.parent / "library-state.json").unlink()
    tagged_audio(album / "02 Second.wav", "Second", 2)

    result = import_folder(tmp_path, album)

    assert result == {"organized": 1, "skipped": 1, "errors": []}
    assert len(list((tmp_path / "music").rglob("*.wav"))) == 2
    assert existing.read_bytes() == original_copy


def test_existing_album_without_year_does_not_gain_a_fabricated_year(tmp_path, offline_catalog):
    source = tagged_audio(tmp_path / "incoming" / "album" / "01 First.wav", "First")
    audio = WAVE(source)
    audio.tags.delall("TDRC")
    audio.save()

    result = import_folder(tmp_path, source.parent)

    assert result["organized"] == 1
    destination, = (tmp_path / "music").rglob("*.wav")
    assert destination.relative_to(tmp_path / "music").parent.parts == ("QA Artist", "QA Album")
    assert "TDRC" not in WAVE(destination).tags


@pytest.mark.parametrize("disc_total", [1, 2])
def test_tag_fallback_keeps_compilation_under_album_artist(tmp_path, offline_catalog, disc_total):
    album = tmp_path / "incoming" / "compilation"
    first = tagged_audio(album / "01 First.wav", "First")
    second = tagged_audio(album / "02 Second.wav", "Second", 1 if disc_total == 2 else 2)
    for index, source in enumerate((first, second), 1):
        audio = WAVE(source)
        audio.tags.add(TPE1(text=f"Guest {index}"))
        audio.tags.add(TPE2(text="Various Artists"))
        audio.tags.add(TPOS(text=f"{index if disc_total == 2 else 1}/{disc_total}"))
        audio.save()

    result = import_folder(tmp_path, album)

    assert result["organized"] == 2
    destinations = list((tmp_path / "music").rglob("*.wav"))
    assert len(destinations) == 2
    assert {path.relative_to(tmp_path / "music").parts[0] for path in destinations} == {"Various Artists"}
    if disc_total == 2:
        assert {path.name[:5] for path in destinations} == {"01-01", "02-01"}


@pytest.mark.parametrize("version", ["Live", "Remix"])
def test_conflicting_version_proposal_stays_unconfirmed_after_tag_fallback(tmp_path, offline_catalog, version):
    source = tagged_audio(tmp_path / "incoming" / "album" / "01 First.wav", f"First ({version})")
    (tmp_path / "offline-plugins" / "qacatalog.py").write_text('''
from beets.metadata_plugins import MetadataSourcePlugin
from beets.autotag.hooks import TrackInfo
class QACatalogPlugin(MetadataSourcePlugin):
    def album_for_id(self, value): return None
    def track_for_id(self, value): return None
    def candidates(self, *args): return []
    def item_candidates(self, *args):
        return [TrackInfo(title="First", artist="QA Artist", artists=["QA Artist"],
                          album="QA Album", year=2020, index=1, length=1.0,
                          track_id="studio-first", data_source="QACatalog")]
''', encoding="utf-8")

    result = import_folder(tmp_path, source.parent)

    assert result == {"organized": 1, "skipped": 0, "errors": []}
    destination, = (tmp_path / "music").rglob("*.wav")
    assert str(WAVE(destination).tags["TIT2"]) == f"First ({version})"
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    assert entry["imported"] is True
    assert entry["metadata_confirmed"] is False
    assert entry["release_confirmed"] is False
    assert entry["candidates"][0]["id"] == "studio-first"
    assert "wers" in entry["reason"].lower() or "version" in entry["reason"].lower()


def test_partial_cli_failure_preserves_reported_copy_and_avoids_recopied_audio(tmp_path):
    source = tagged_audio(tmp_path / "incoming" / "album" / "01 First.wav", "First")
    destination = tmp_path / "music" / "QA Artist" / "2020 - QA Album" / "01 First.wav"
    attempts = []

    def interrupted_cli(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        attempts.append(command)
        assert "import" in command
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        report = Path(kwargs["env"]["DOWNLOADEUSZ_BEETS_REPORT"])
        report.write_text(json.dumps({
            "event": "imported", "source": str(source), "destination": str(destination),
            "metadata_confirmed": False, "release_confirmed": False,
            "reason": "Zaimportowano według istniejących tagów", "candidates": [],
        }) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 2, "", "later import stage failed")

    with patch("shutil.which", return_value="/usr/bin/beet"), patch("subprocess.run", side_effect=interrupted_cli):
        failed = import_folder(tmp_path, source.parent)
        entries = beets_importer.list_review(str(tmp_path / "config"))
        repeated = import_folder(tmp_path, source.parent)

    assert failed["errors"]
    assert failed["organized"] == 1
    assert len(entries) == 1
    assert entries[0]["destination"] == str(destination)
    assert entries[0]["imported"] is True
    assert entries[0]["metadata_confirmed"] is False
    assert repeated["organized"] == 0
    assert len(attempts) == 1
    assert destination.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("explicit_retry", [False, True])
def test_daily_enrichment_waits_until_due_but_item_retry_bypasses_delay(tmp_path, explicit_retry):
    config = tmp_path / "config"
    dest = tmp_path / "music"
    destination = tagged_audio(dest / "actual-existing-location.wav", "First")
    source = tmp_path / "incoming" / "original-is-gone.wav"
    item_id = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    now = 2_000_000_000.0
    entry = {
        "id": item_id, "source": str(source), "destination": str(destination),
        "imported": True, "metadata_confirmed": False, "release_confirmed": False,
        "status": "imported", "reason": "Do uzupełnienia", "candidates": [],
        "source_fingerprint": "original-fingerprint", "last_attempt": now,
        "destination_fingerprint": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "next_attempt": now + 86400,
    }
    state_path = config / "beets" / "library-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"version": 1, "tracks": {item_id: entry}}), encoding="utf-8")
    attempts = []

    def recognize_existing(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        if "import" in command:
            attempts.append(command)
            assert command[-1] == str(destination)
            assert "-C" in command[command.index("import") + 1:]
            assert kwargs["env"]["DOWNLOADEUSZ_BEETS_ENRICH"] == "1"
            audio = WAVE(destination)
            audio.tags.add(TIT2(text="First Confirmed"))
            audio.save()
            Path(kwargs["env"]["DOWNLOADEUSZ_BEETS_REPORT"]).write_text(json.dumps({
                "event": "imported", "source": str(destination), "destination": str(destination),
                "metadata_confirmed": True, "release_confirmed": False,
                "reason": "Potwierdzone nagranie, wydanie do sprawdzenia", "candidates": [],
            }) + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        assert "list" in command
        return subprocess.CompletedProcess(command, 0, str(destination) + "\n", "")

    enrich = getattr(beets_importer, "enrich_pending", None)
    assert callable(enrich), "Hybrid imports need an enrichment entry point"
    with patch("shutil.which", side_effect=lambda name: "/usr/bin/beet" if name == "beet" else None), \
         patch("time.time", return_value=now), patch("subprocess.run", side_effect=recognize_existing):
        waiting = enrich(config_dir=str(config), dest_dir=str(dest))
        assert waiting["updated"] == 0
        assert attempts == []
        with patch("time.time", return_value=now if explicit_retry else now + 86401):
            retried = enrich(config_dir=str(config), dest_dir=str(dest), retry=explicit_retry,
                             item_id=item_id if explicit_retry else None)

    assert retried["updated"] == 1
    assert retried["organized"] == 0
    assert retried["errors"] == []
    assert len(attempts) == 1
    assert list(dest.rglob("*.wav")) == [destination]
    assert str(WAVE(destination).tags["TIT2"]) == "First Confirmed"
    updated, = beets_importer.list_review(str(config))
    assert updated["id"] == item_id
    assert updated["source"] == str(source)
    assert updated["destination"] == str(destination)
    assert updated["metadata_confirmed"] is True
    assert updated["release_confirmed"] is False
