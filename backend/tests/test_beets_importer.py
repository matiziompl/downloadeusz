import json
import hashlib
import shutil
import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from backend import beets_importer


@pytest.fixture
def library(tmp_path):
    source = tmp_path / "incoming"
    album = source / "An album"
    album.mkdir(parents=True)
    track = album / "01 Song.flac"
    track.write_bytes(b"original audio")
    return source, album, track, tmp_path / "music", tmp_path / "config"


def run_import(library, **kwargs):
    source, album, _, dest, config = library
    return beets_importer.import_paths(
        [str(album)], source_dir=str(source), dest_dir=str(dest),
        config_dir=str(config), **kwargs,
    )


def test_import_counts_real_copied_files_and_keeps_source(library):
    source, album, track, dest, config = library
    commands = []

    def run(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        commands.append(command)
        settings = json.loads((config / "beets" / "managed.yaml").read_text())
        assert settings["import"]["copy"] is True
        assert settings["import"]["move"] is False
        assert settings["import"]["delete"] is False
        assert settings["import"]["quiet_fallback"] == "skip"
        assert settings["import"]["duplicate_action"] == "skip"
        assert settings["plugins"] == ["downloadeusz", "fromfilename", "musicbrainz", "fetchart", "embedart"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs.get("shell", False) is False
        assert kwargs["env"]["BEETSDIR"] == str(config / "beets")
        if "import" in command:
            assert "-c" in command and "-M" in command and "-q" in command
            copied = dest / "Artist" / "2001 - Album" / "01 Song.flac"
            copied.parent.mkdir(parents=True)
            copied.write_bytes(track.read_bytes())
            Path(kwargs["env"]["DOWNLOADEUSZ_BEETS_REPORT"]).write_text(json.dumps({
                "event": "imported", "source": str(track), "destination": str(copied),
                "metadata_confirmed": True, "release_confirmed": False,
            }) + "\n")
            return subprocess.CompletedProcess(command, 0, "Import complete\n", "")
        assert "list" in command
        return subprocess.CompletedProcess(command, 0, str(copied_file(dest)) + "\n", "")

    with patch("shutil.which", return_value="/usr/bin/beet"), patch("subprocess.run", side_effect=run):
        result = run_import(library)
        second = run_import(library)
    assert result == {"organized": 1, "skipped": 0, "errors": []}
    assert second == {"organized": 0, "skipped": 1, "errors": []}
    assert len(commands) == 2
    assert track.read_bytes() == b"original audio"
    assert copied_file(dest).read_bytes() == b"original audio"


def copied_file(dest):
    return dest / "Artist" / "2001 - Album" / "01 Song.flac"


def test_quiet_skip_is_not_reported_as_import_and_retry_is_explicit(library):
    with patch("shutil.which", return_value="/usr/bin/beet"), patch(
        "subprocess.run", return_value=subprocess.CompletedProcess([], 0, "plugins: downloadeusz\n", "")
    ) as run:
        assert run_import(library) == {"organized": 0, "skipped": 1, "errors": []}
        assert run_import(library) == {"organized": 0, "skipped": 1, "errors": []}
        assert run.call_count == 3
        run_import(library, retry=True)
        assert run.call_count == 6


def test_changed_album_is_considered_again(library):
    with patch("shutil.which", return_value="/usr/bin/beet"), patch(
        "subprocess.run", return_value=subprocess.CompletedProcess([], 0, "plugins: downloadeusz\n", "")
    ) as run:
        run_import(library)
        library[2].write_bytes(b"changed audio data")
        run_import(library)
    assert run.call_count == 6


@pytest.mark.parametrize("kind", ["outside", "symlink", "nested_symlink", "overlap", "hardlink"])
def test_unsafe_paths_never_start_beets(library, tmp_path, kind):
    source, album, track, dest, config = library
    paths = [str(album)]
    if kind == "outside":
        paths = [str(tmp_path)]
    elif kind == "symlink":
        link = source / "link"
        link.symlink_to(album, target_is_directory=True)
        paths = [str(link)]
    elif kind == "nested_symlink":
        (album / "bad.flac").symlink_to(track)
    elif kind == "hardlink":
        (tmp_path / "linked-track.flac").hardlink_to(track)
    else:
        dest = source / "library"
    with patch("subprocess.run") as run:
        result = beets_importer.import_paths(
            paths, source_dir=str(source), dest_dir=str(dest), config_dir=str(config)
        )
    assert result["organized"] == 0
    assert result["errors"]
    run.assert_not_called()
    assert track.read_bytes() == b"original audio"


def test_timeout_keeps_inputs_and_does_not_suppress_next_attempt(library):
    def timeout_after_preflight(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        raise subprocess.TimeoutExpired("beet", 900)

    with patch("shutil.which", return_value="/usr/bin/beet"), patch(
        "subprocess.run", side_effect=timeout_after_preflight
    ) as run:
        first = run_import(library)
        second = run_import(library)
    assert first["errors"] and second["errors"]
    assert run.call_count == 4
    assert library[2].read_bytes() == b"original audio"


def test_missing_beets_is_an_actionable_error(library):
    with patch("shutil.which", return_value=None):
        result = run_import(library)
    assert result["organized"] == 0
    assert "beet" in result["errors"][0].lower()


def test_held_lock_does_not_start_second_import(library):
    import fcntl

    state_dir = library[4] / "beets"
    state_dir.mkdir(parents=True)
    with (state_dir / "import.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch("subprocess.run") as run:
            result = run_import(library)
        run.assert_not_called()
    assert result["organized"] == 0
    assert result["errors"]


def test_unmatched_album_falls_back_to_tracks_then_audio_fingerprint(library):
    source, album, first, dest, config = library
    second = album / "02 Second.flac"
    second.write_bytes(b"second original")
    attempts = []
    batch_files = {}

    def run(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        if "list" in command:
            batch = command[-1].split(":", 1)[1]
            return subprocess.CompletedProcess(command, 0, "\n".join(batch_files[batch]), "")
        settings_path = Path(command[command.index("-c") + 1])
        settings = json.loads(settings_path.read_text())
        fingerprint = settings_path.name == "managed-fingerprint.yaml"
        assert ("chroma" in settings["plugins"]) == fingerprint
        if fingerprint:
            assert settings["chroma"]["auto"] is True
        item = Path(command[-1])
        attempts.append((item, fingerprint, "-s" in command))
        batch = command[command.index("--set") + 1].split("=", 1)[1]
        batch_files[batch] = []
        if item == first or (item == second and fingerprint):
            target = dest / item.name
            target.write_bytes(item.read_bytes())
            batch_files[batch] = [str(target)]
            Path(kwargs["env"]["DOWNLOADEUSZ_BEETS_REPORT"]).write_text(json.dumps({
                "event": "imported", "source": str(item), "destination": str(target),
                "metadata_confirmed": True, "release_confirmed": False,
            }) + "\n")
        else:
            review = config / "beets" / "review.log"
            with review.open("a") as log:
                log.write(f"skip {item}\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("shutil.which", side_effect=lambda name: "/usr/bin/" + name), patch("subprocess.run", side_effect=run):
        result = run_import(library)
    assert result == {"organized": 2, "skipped": 0, "errors": []}
    assert attempts == [(album, False, False), (first, False, True), (second, False, True), (second, True, True)]
    assert first.read_bytes() == b"original audio"
    assert second.read_bytes() == b"second original"


@pytest.mark.parametrize("duplicate_entry", ["duplicate-skip", "duplicate-keep"])
def test_album_duplicate_does_not_turn_into_singleton_duplicates(library, duplicate_entry):
    source, album, first, dest, config = library
    (album / "02 Second.flac").write_bytes(b"second original")
    attempts = []

    def run(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        if "import" in command:
            attempts.append(command)
            with (config / "beets" / "review.log").open("a") as log:
                log.write(f"{duplicate_entry} {album}\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("shutil.which", side_effect=lambda name: "/usr/bin/" + name), patch("subprocess.run", side_effect=run):
        result = run_import(library)
    assert result == {"organized": 0, "skipped": 2, "errors": []}
    assert len(attempts) == 1
    entry = json.loads((config / "beets" / "import-state.json").read_text())[str(album)]
    assert entry["status"] == "duplicate"


@pytest.mark.parametrize("disc_total", [1, 2])
@pytest.mark.parametrize("destination_kind", ["collision", "existing_art", "symlink", "singleton", "partial_album", "unmatched", "legacy_album_addition"])
def test_real_beets_copies_tags_and_art_without_modifying_source(tmp_path, disc_total, destination_kind):
    """Keep beets' real importer; replace only the external metadata provider."""
    if not shutil.which("beet"):
        pytest.skip("beet CLI not installed")
    from mutagen.id3 import TALB, TIT2, TPE1, TRCK, TDRC, TPOS
    from mutagen.wave import WAVE
    from PIL import Image

    source = tmp_path / "incoming"
    album = source / "album"
    album.mkdir(parents=True)
    track = album / "01 Song.wav"
    with wave.open(str(track), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    tags = WAVE(track)
    tags.add_tags()
    for frame in [TIT2(text="Song!"), TPE1(text="Artist"), TALB(text="Album"), TRCK(text="1"), TDRC(text="2001"), TPOS(text=f"{disc_total}/{disc_total}")]:
        tags.tags.add(frame)
    tags.save()
    second_track = album / "02 Song Two.wav"
    shutil.copy2(track, second_track)
    second_tags = WAVE(second_track)
    second_tags.tags.add(TIT2(text="Song Two"))
    second_tags.tags.add(TRCK(text="2"))
    second_tags.save()
    if destination_kind == "singleton":
        second_track.unlink()
    third_track = None
    if destination_kind == "partial_album":
        third_track = album / "03 Song Three.wav"
        shutil.copy2(track, third_track)
        third_tags = WAVE(third_track)
        third_tags.tags.add(TIT2(text="Song Three"))
        third_tags.tags.add(TRCK(text="3"))
        third_tags.save()
    cover = album / "cover.jpg"
    Image.new("RGB", (20, 20), "red").save(cover)
    original_track = hashlib.sha256(track.read_bytes()).hexdigest()
    original_second_track = hashlib.sha256(second_track.read_bytes()).hexdigest() if second_track.exists() else None
    original_third_track = hashlib.sha256(third_track.read_bytes()).hexdigest() if third_track else None
    original_cover = cover.read_bytes()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "offline.py").write_text('''
from beets.metadata_plugins import MetadataSourcePlugin
from beets.autotag.hooks import AlbumInfo, TrackInfo

class OfflinePlugin(MetadataSourcePlugin):
    def album_for_id(self, album_id):
        return None
    def track_for_id(self, track_id):
        return None
    def item_candidates(self, item, artist, title):
        if _UNMATCHED_:
            return []
        return [TrackInfo(title=title.rstrip("!"), artist="Artist", artists=["Artist"],
                          album="Album", year=2001, track_id=f"offline-track-{item.track}", index=item.track,
                          medium=_DISC_, length=1.0, data_source="Offline")]
    def candidates(self, items, artist, album, va_likely):
        if _UNMATCHED_:
            return []
        track = TrackInfo(title="Song", artist="Artist", artists=["Artist"],
                          track_id="offline-track", index=1, medium=_DISC_,
                          medium_index=1, medium_total=1, length=1.0,
                          data_source="Offline")
        track_two = TrackInfo(title="Song Two", artist="Artist", artists=["Artist"],
                              track_id="offline-track-two", index=2, medium=_DISC_,
                              medium_index=2, medium_total=2, length=1.0,
                              data_source="Offline")
        if _SINGLE_:
            tracks = [track] + [TrackInfo(title=f"Other {i}", track_id=f"other-{i}",
                       index=i, artist="Artist", length=1.0) for i in range(2, 15)]
        elif _PARTIAL_:
            third = TrackInfo(title="Song Three", track_id="offline-track-three", index=3,
                              artist="Artist", length=1.0, medium=_DISC_, medium_index=3)
            tracks = [track, track_two, third] + [TrackInfo(title=f"Other {i}", track_id=f"other-{i}",
                       index=i, artist="Artist", length=1.0) for i in range(4, 15)]
        else:
            tracks = [track, track_two]
        return [AlbumInfo(album="Album", artist="Artist", artists=["Artist"],
                          album_id="offline-album", tracks=tracks,
                          year=2001, mediums=_DISC_, data_source="Offline")]
'''.replace("_DISC_", str(disc_total)).replace("_SINGLE_", str(destination_kind == "singleton"))
        .replace("_PARTIAL_", str(destination_kind == "partial_album"))
        .replace("_UNMATCHED_", str(destination_kind == "unmatched")), encoding="utf-8")
    actual_config = beets_importer._managed_config

    def offline_config(state_dir, dest):
        settings = actual_config(state_dir, dest)
        settings["plugins"] = ["downloadeusz", "offline", "fetchart", "embedart"]
        settings["pluginpath"].append(str(plugins))
        settings["fetchart"]["sources"] = ["filesystem"]
        return settings

    dest = tmp_path / "music"
    filename = "02-01 Song.wav" if disc_total == 2 else "01 Song.wav"
    existing = dest / "Artist" / "2001 - Album" / filename
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing library audio")
    existing_cover = existing.parent / "cover.jpg"
    if destination_kind == "existing_art":
        Image.new("RGB", (20, 20), "blue").save(existing_cover)
        old_cover = existing_cover.read_bytes()
    elif destination_kind == "symlink":
        shutil.rmtree(dest / "Artist")
        outside = tmp_path / "outside"
        outside.mkdir()
        (dest / "Artist").symlink_to(outside, target_is_directory=True)
    actual_which = shutil.which
    with patch.object(beets_importer, "_managed_config", side_effect=offline_config), patch(
        "shutil.which", side_effect=lambda name: None if name == "fpcalc" else actual_which(name)
    ):
        result = beets_importer.import_paths(
            [str(album)], source_dir=str(source), dest_dir=str(dest),
            config_dir=str(tmp_path / "config"),
        )
    if destination_kind == "symlink":
        assert result["errors"]
        assert list(outside.iterdir()) == []
        assert hashlib.sha256(track.read_bytes()).hexdigest() == original_track
        (dest / "Artist").unlink()
        (dest / "Artist").mkdir()
        with patch.object(beets_importer, "_managed_config", side_effect=offline_config):
            retried = beets_importer.import_paths(
                [str(album)], source_dir=str(source), dest_dir=str(dest),
                config_dir=str(tmp_path / "config"), retry=True,
            )
        assert retried == {"organized": 2, "skipped": 0, "errors": []}
        assert len(list(dest.rglob("*.wav"))) == 2
        return
    if destination_kind == "unmatched":
        assert result == {"organized": 2, "skipped": 0, "errors": []}
        assert len(list(dest.rglob("*.wav"))) == 3
        assert existing.read_bytes() == b"existing library audio"
        assert hashlib.sha256(track.read_bytes()).hexdigest() == original_track
        assert hashlib.sha256(second_track.read_bytes()).hexdigest() == original_second_track
        queued = beets_importer.list_review(str(tmp_path / "config"))
        assert len(queued) == 2
        assert all(entry["imported"] and not entry["metadata_confirmed"] for entry in queued)
        return
    assert result == {"organized": 1 if destination_kind == "singleton" else 3 if third_track else 2, "skipped": 0, "errors": []}
    imported = existing.with_name(existing.stem + ".1.wav")
    assert imported.is_file()
    assert existing.read_bytes() == b"existing library audio"
    assert str(WAVE(imported).tags["TDRC"]) == "2001"
    assert str(WAVE(imported).tags["TIT2"]) == "Song"
    if destination_kind not in ("singleton", "partial_album"):
        assert WAVE(imported).tags.getall("APIC")
        assert (imported.parent / "cover.jpg").is_file()
    assert hashlib.sha256(track.read_bytes()).hexdigest() == original_track
    if second_track.exists():
        assert hashlib.sha256(second_track.read_bytes()).hexdigest() == original_second_track
    if third_track:
        assert hashlib.sha256(third_track.read_bytes()).hexdigest() == original_third_track
    assert cover.read_bytes() == original_cover
    if destination_kind == "existing_art":
        assert existing_cover.read_bytes() == old_cover
    if destination_kind == "collision":
        duplicate = source / "another-copy"
        shutil.copytree(album, duplicate)
        before = set(dest.rglob("*.wav"))
        with patch.object(beets_importer, "_managed_config", side_effect=offline_config):
            duplicate_result = beets_importer.import_paths(
                [str(duplicate)], source_dir=str(source), dest_dir=str(dest),
                config_dir=str(tmp_path / "config"),
            )
        assert duplicate_result == {"organized": 0, "skipped": 2, "errors": []}
        assert set(dest.rglob("*.wav")) == before
        duplicate_entry = json.loads((tmp_path / "config" / "beets" / "import-state.json").read_text())[str(duplicate)]
        assert duplicate_entry["status"] == "duplicate"
    if destination_kind == "legacy_album_addition":
        legacy_path = tmp_path / "config" / "beets" / "import-state.json"
        legacy = json.loads(legacy_path.read_text())
        legacy[str(album)]["recognition_version"] = 2
        legacy_path.write_text(json.dumps(legacy))
        (tmp_path / "config" / "beets" / "library-state.json").unlink()
        previous_copies = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in dest.rglob("*.wav")}
        bonus = album / "03 Bonus.wav"
        shutil.copy2(track, bonus)
        bonus_tags = WAVE(bonus)
        bonus_tags.tags.add(TIT2(text="Bonus"))
        bonus_tags.tags.add(TRCK(text="3"))
        bonus_tags.save()
        with patch.object(beets_importer, "_managed_config", side_effect=offline_config):
            upgraded = beets_importer.import_paths(
                [str(album)], source_dir=str(source), dest_dir=str(dest),
                config_dir=str(tmp_path / "config"),
            )
        assert upgraded == {"organized": 1, "skipped": 2, "errors": []}
        assert len(list(dest.rglob("*.wav"))) == len(previous_copies) + 1
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in previous_copies.items())


def test_real_chroma_fallback_recognizes_untagged_audio_with_fpcalc(tmp_path):
    if not shutil.which("beet") or not shutil.which("fpcalc"):
        pytest.skip("beet CLI and fpcalc required")
    from mutagen.wave import WAVE

    source = tmp_path / "incoming"
    source.mkdir()
    track = source / "Recovered Song.wav"
    with wave.open(str(track), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(11025)
        audio.writeframes(b"\x00\x00" * 11025 * 20)
    original = hashlib.sha256(track.read_bytes()).hexdigest()
    markers = tmp_path / "lookups.txt"
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "offlinechroma.py").write_text('''
from pathlib import Path
import acoustid
from beets.plugins import BeetsPlugin
from beets.autotag.hooks import TrackInfo
from beetsplug.musicbrainz import MusicBrainzPlugin

MARKERS = Path(_MARKERS_)

def marker(value):
    with MARKERS.open("a") as output:
        output.write(value + "\\n")

def text_candidates(self, item, artist, title):
    marker("text")
    return []

def recording(self, recording_id):
    assert recording_id == "00000000-0000-0000-0000-000000000001"
    marker("recording")
    return TrackInfo(title="Recovered Song", artist="Recovered Artist",
                     artists=["Recovered Artist"], album="Recovered Album", year=2001,
                     track_id=recording_id, length=20.0, index=1, data_source="MusicBrainz")

def lookup(api_key, fingerprint, duration, **kwargs):
    assert fingerprint and duration >= 19
    marker("acoustid")
    return {"status": "ok", "results": [{"score": 1.0, "id": "offline-acoustid",
            "recordings": [{"id": "00000000-0000-0000-0000-000000000001"}]}]}

class OfflineChromaPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()
        MusicBrainzPlugin.item_candidates = text_candidates
        MusicBrainzPlugin.track_for_id = recording
        acoustid.lookup = lookup
        actual_fingerprint = acoustid.fingerprint_file
        acoustid.fingerprint_file = lambda path: actual_fingerprint(path, force_fpcalc=True)
'''.replace("_MARKERS_", repr(str(markers))), encoding="utf-8")
    actual_config = beets_importer._managed_config

    def offline_config(state_dir, dest):
        settings = actual_config(state_dir, dest)
        settings["plugins"].append("offlinechroma")
        settings["pluginpath"].append(str(plugin_dir))
        return settings

    dest = tmp_path / "music"
    messages = []
    with patch.object(beets_importer, "_managed_config", side_effect=offline_config):
        result = beets_importer.import_paths(
            [str(track)], source_dir=str(source), dest_dir=str(dest),
            config_dir=str(tmp_path / "config"), log_callback=messages.append,
        )
    assert result == {"organized": 1, "skipped": 0, "errors": []}, messages
    copied = list(dest.rglob("*.wav"))
    assert len(copied) == 1
    assert str(WAVE(copied[0]).tags["TIT2"]) == "Recovered Song"
    assert str(WAVE(copied[0]).tags["TPE1"]) == "Recovered Artist"
    assert hashlib.sha256(track.read_bytes()).hexdigest() == original
    calls = markers.read_text().splitlines()
    assert calls[0] == "text"
    assert calls.count("acoustid") == 1
    assert calls.count("recording") == 1
