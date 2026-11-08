"""Audio identity must ignore tag edits and reject a different recording."""

import hashlib
import shutil
import struct
import subprocess
import sys
import wave

import pytest
from mutagen.id3 import APIC, TXXX
from mutagen.wave import WAVE


def write_wave(path, *, sample=0):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(struct.pack("<h", sample) * 8000)
    return path


def test_audio_packets_ignore_replaygain_and_artwork_but_detect_changed_audio(tmp_path):
    from backend.audio_identity import audio_packet_signature

    original = write_wave(tmp_path / "song.wav")
    before_file = hashlib.sha256(original.read_bytes()).hexdigest()
    before_audio = audio_packet_signature(original)
    tags = WAVE(original)
    tags.add_tags()
    tags.tags.add(TXXX(desc="replaygain_track_gain", text="-8.12 dB"))
    tags.tags.add(APIC(mime="image/jpeg", type=3, data=b"tag-only cover image"))
    tags.save()

    assert hashlib.sha256(original.read_bytes()).hexdigest() != before_file
    assert audio_packet_signature(original) == before_audio
    replacement = write_wave(tmp_path / "different.wav", sample=1234)
    assert audio_packet_signature(replacement) != before_audio


def test_invalid_audio_has_no_usable_identity(tmp_path):
    from backend.audio_identity import audio_packet_signature

    invalid = tmp_path / "invalid.flac"
    invalid.write_bytes(b"this is not an audio stream")
    with pytest.raises(RuntimeError):
        audio_packet_signature(invalid)


def test_library_write_lock_serializes_other_processes(tmp_path):
    from backend.audio_identity import library_write_lock

    music = tmp_path / "music"
    music.mkdir()
    contender = '''
import fcntl, pathlib, sys
with pathlib.Path(sys.argv[1]).open("a") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(23)
'''
    with library_write_lock(music):
        blocked = subprocess.run([sys.executable, "-c", contender,
                                  str(music / ".downloadeusz-write.lock")])
    released = subprocess.run([sys.executable, "-c", contender,
                               str(music / ".downloadeusz-write.lock")])
    assert blocked.returncode == 23
    assert released.returncode == 0


@pytest.fixture
def tracked_library(tmp_path, monkeypatch):
    """Keep state/files real; replace only beets' external metadata lookup."""
    import json
    import fcntl
    from backend import beets_importer

    incoming = tmp_path / "incoming"
    incoming.mkdir()
    source = write_wave(incoming / "song.wav")
    music = tmp_path / "music"
    music.mkdir()
    destination = music / "song.wav"
    shutil.copy2(source, destination)
    config = tmp_path / "config"
    state_path = config / "beets" / "library-state.json"
    state_path.parent.mkdir(parents=True)
    state = {"version": 1, "tracks": {}}
    entry = beets_importer._save_track(state, state_path, source, {
        "event": "imported", "destination": str(destination),
        "metadata_confirmed": False, "release_confirmed": False,
    }, music)
    monkeypatch.setattr(beets_importer.shutil, "which", lambda name: "/usr/bin/beet" if name == "beet" else None)
    monkeypatch.setattr(beets_importer, "_preflight", lambda *args: None)
    monkeypatch.setattr(beets_importer.album_art, "artwork_pending", lambda path: False)

    def attempt(base, path, review_path, environment, dest, log, *, on_record, **kwargs):
        with (music / ".downloadeusz-write.lock").open("a") as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not kwargs.get("in_place"):
            shutil.copy2(path, destination)
        tags = WAVE(destination)
        if tags.tags is None:
            tags.add_tags()
        tags.tags.add(TXXX(desc="metadata lookup", text="updated"))
        tags.save()
        on_record({"event": "imported", "source": str(path), "destination": str(destination),
                   "metadata_confirmed": True, "release_confirmed": True})
        return {str(destination)}, "finished"

    monkeypatch.setattr(beets_importer, "_attempt_import", attempt)

    def save():
        state_path.write_text(json.dumps(state))

    def enrich(**kwargs):
        return beets_importer.enrich_pending(config_dir=str(config), dest_dir=str(music), retry=True, **kwargs)

    def current():
        return next(iter(json.loads(state_path.read_text())["tracks"].values()))

    return source, destination, entry, save, enrich, current, config


def add_gain(path):
    tags = WAVE(path)
    if tags.tags is None:
        tags.add_tags()
    tags.tags.add(TXXX(desc="replaygain_track_gain", text="-9.23 dB"))
    tags.save()


def test_tracked_copy_can_be_enriched_after_tag_only_edits_without_original(tracked_library):
    source, destination, entry, save, enrich, current, _ = tracked_library
    source.unlink()
    add_gain(destination)

    result = enrich()

    assert result["updated"] == 1, result
    assert result["errors"] == []
    assert "replaygain_track_gain" in str(WAVE(destination).tags)
    assert current()["destination_audio_signature"]


@pytest.mark.parametrize("trusted_reference", ["unchanged_destination", "unchanged_source"])
def test_legacy_identity_initializes_only_from_trusted_reference(tracked_library, trusted_reference):
    source, destination, entry, save, enrich, current, _ = tracked_library
    entry.pop("destination_audio_signature", None)
    save()
    if trusted_reference == "unchanged_destination":
        source.unlink()
    else:
        add_gain(destination)

    result = enrich()

    assert result["updated"] == 1, result
    assert result["errors"] == []
    assert current()["destination_audio_signature"]


@pytest.mark.parametrize("untrusted_reference", ["missing_source", "changed_source", "missing_baselines"])
def test_changed_legacy_copy_without_trusted_reference_is_never_accepted(tracked_library, untrusted_reference):
    source, destination, entry, save, enrich, current, _ = tracked_library
    entry.pop("destination_audio_signature", None)
    add_gain(destination)
    if untrusted_reference == "missing_source":
        source.unlink()
    elif untrusted_reference == "changed_source":
        write_wave(source, sample=1234)
    else:
        entry.pop("source_fingerprint", None)
        entry.pop("destination_fingerprint", None)
    save()
    before = destination.read_bytes()

    result = enrich()

    assert result["errors"]
    assert result["updated"] == 0
    assert destination.read_bytes() == before
    assert not current().get("destination_audio_signature")


def test_different_audio_is_rejected_despite_saved_packet_identity(tracked_library):
    _, destination, _, _, enrich, _, _ = tracked_library
    write_wave(destination, sample=1234)
    before = destination.read_bytes()

    result = enrich()

    assert result["errors"]
    assert result["updated"] == 0
    assert destination.read_bytes() == before


@pytest.mark.parametrize("readiness", [None, "empty", "other_file", "eligible"])
def test_unimported_source_retries_require_explicit_readiness(tracked_library, readiness):
    source, destination, entry, save, enrich, current, _ = tracked_library
    entry.update(imported=False, destination=None)
    save()
    destination.unlink()
    kwargs = {}
    if readiness is not None:
        kwargs["ready_sources"] = {source} if readiness == "eligible" else {source.parent / "other.wav"} if readiness == "other_file" else set()

    result = enrich(**kwargs)

    assert result["errors"] == [], result
    assert result["organized"] == (1 if readiness == "eligible" else 0)
    assert destination.exists() == (readiness == "eligible")
    assert current()["imported"] == (readiness == "eligible")


def test_rejected_legacy_copy_does_not_create_its_own_trusted_source_baseline(tracked_library):
    _, destination, entry, save, enrich, current, _ = tracked_library
    entry.pop("destination_audio_signature", None)
    entry.pop("source_fingerprint", None)
    entry.pop("destination_fingerprint", None)
    add_gain(destination)
    save()
    before = destination.read_bytes()

    first = enrich()
    second = enrich()

    assert first["errors"]
    assert second["errors"]
    assert destination.read_bytes() == before
    assert not current().get("source_fingerprint")
    assert not current().get("destination_audio_signature")
