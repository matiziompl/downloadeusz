import hashlib
import io
import json
import shutil
import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from backend import beets_importer


def audio_file(path, *, artist="Local Artist", title="Local Song!", album="Local Album", tagged=True, sample=0):
    from mutagen.id3 import TALB, TIT2, TPE1, TRCK, TDRC
    from mutagen.wave import WAVE
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * 8000)
    if tagged:
        audio = WAVE(path)
        audio.add_tags()
        for frame in [TIT2(text=title), TPE1(text=artist), TRCK(text="1")]:
            audio.tags.add(frame)
        if album:
            audio.tags.add(TALB(text=album))
            audio.tags.add(TDRC(text="2001"))
        audio.save()
    return path


@pytest.fixture
def offline_beets(tmp_path):
    if not shutil.which("beet"):
        pytest.skip("beet CLI required")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    mode = tmp_path / "mode"
    mode.write_text("unknown")
    (plugin_dir / "hybridtest.py").write_text('''
from pathlib import Path
from beets.metadata_plugins import MetadataSourcePlugin
from beets.autotag.hooks import TrackInfo

class HybridTestPlugin(MetadataSourcePlugin):
    def album_for_id(self, value): return None
    def track_for_id(self, value): return None
    def candidates(self, *args): return []
    def item_candidates(self, item, artist, title):
        if Path(_MODE_).read_text() == "unknown": return []
        return [TrackInfo(title="Local Song", artist="Local Artist", artists=["Local Artist"],
                          album="Local Album", year=2001, index=1, length=1.0,
                          track_id="offline-recording", data_source="HybridTest")]
'''.replace("_MODE_", repr(str(mode))), encoding="utf-8")
    actual_config = beets_importer._managed_config
    actual_which = shutil.which

    def settings(state_dir, dest):
        config = actual_config(state_dir, dest)
        config["plugins"] = ["downloadeusz", "hybridtest", "fetchart", "embedart"]
        config["pluginpath"].append(str(plugin_dir))
        config["fetchart"]["sources"] = ["filesystem"]
        return config

    with patch.object(beets_importer, "_managed_config", side_effect=settings), patch(
        "shutil.which", side_effect=lambda name: None if name == "fpcalc" else actual_which(name)
    ):
        yield mode


def import_one(tmp_path, source):
    return beets_importer.import_paths(
        [str(source)], source_dir=str(tmp_path / "incoming"),
        dest_dir=str(tmp_path / "music"), config_dir=str(tmp_path / "config"),
    )


@pytest.mark.parametrize("album", ["Local Album", ""])
def test_real_unknown_recording_with_usable_tags_imports_and_tracks_copy(tmp_path, offline_beets, album):
    source = audio_file(tmp_path / "incoming" / "song.wav", album=album)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    assert import_one(tmp_path, source) == {"organized": 1, "skipped": 0, "errors": []}
    copies = list((tmp_path / "music").rglob("*.wav"))
    assert len(copies) == 1
    if not album:
        assert copies[0].relative_to(tmp_path / "music").parts == ("Local Artist", "Local Song!", "Local Song!.wav")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    queue = beets_importer.list_review(str(tmp_path / "config"))
    assert len(queue) == 1
    assert queue[0]["source"] == str(source)
    assert queue[0]["destination"] == str(copies[0])
    assert queue[0]["imported"] is True
    assert queue[0]["metadata_confirmed"] is False
    assert queue[0]["status"] == "imported"
    assert queue[0]["reason"]
    assert import_one(tmp_path, source)["organized"] == 0
    assert len(list((tmp_path / "music").rglob("*.wav"))) == 1


def test_real_untagged_unrecognized_audio_stays_in_review_queue(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "arbitrary.wav", tagged=False)
    original = source.read_bytes()
    result = import_one(tmp_path, source)
    assert result == {"organized": 0, "skipped": 1, "errors": []}
    assert not list((tmp_path / "music").rglob("*.wav"))
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    assert entry["status"] == "needs_review"
    assert entry["imported"] is False
    assert "tag" in entry["reason"].lower() or "metadan" in entry["reason"].lower()
    assert source.read_bytes() == original


def test_real_enrichment_updates_same_copy_after_source_disappears(tmp_path, offline_beets):
    from mutagen.wave import WAVE
    source = audio_file(tmp_path / "incoming" / "song.wav")
    assert import_one(tmp_path, source)["organized"] == 1
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    destination = Path(entry["destination"])
    source.unlink()
    offline_beets.write_text("recognized")
    result = beets_importer.enrich_pending(
        config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"),
        retry=True, item_id=entry["id"],
    )
    assert result["updated"] == 1, result
    assert result["organized"] == 0
    assert result["errors"] == []
    assert list((tmp_path / "music").rglob("*.wav")) == [destination]
    assert str(WAVE(destination).tags["TIT2"]) == "Local Song"
    state = json.loads((tmp_path / "config" / "beets" / "library-state.json").read_text())
    assert state["tracks"][entry["id"]]["metadata_confirmed"] is True
    assert state["tracks"][entry["id"]]["destination"] == str(destination)


def test_review_list_is_read_only_and_rejects_corrupt_state(tmp_path):
    config = tmp_path / "config"
    assert beets_importer.list_review(str(config)) == []
    assert not config.exists()
    path = config / "beets" / "library-state.json"
    path.parent.mkdir(parents=True)
    path.write_text("broken")
    with pytest.raises(ValueError):
        beets_importer.list_review(str(config))


def test_changed_library_copy_is_not_retagged_automatically(tmp_path, offline_beets):
    from mutagen.wave import WAVE
    source = audio_file(tmp_path / "incoming" / "song.wav")
    assert import_one(tmp_path, source)["organized"] == 1
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    destination = Path(entry["destination"])
    replacement = audio_file(tmp_path / "replacement.wav", title="Different Recording", sample=1234)
    shutil.copyfile(replacement, destination)
    before = destination.read_bytes()
    offline_beets.write_text("recognized")
    result = beets_importer.enrich_pending(
        config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"),
        retry=True, item_id=entry["id"],
    )
    assert result["updated"] == 0
    assert result["errors"]
    assert destination.read_bytes() == before
    assert str(WAVE(destination).tags["TIT2"]) == "Different Recording"


def test_partial_enrichment_write_failure_restores_only_library_copy(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav")
    assert import_one(tmp_path, source)["organized"] == 1
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    destination = Path(entry["destination"])
    before = destination.read_bytes()
    source.unlink()

    def broken_cli(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        assert "import" in command
        assert command[-1] == str(destination)
        destination.write_bytes(b"partially written audio")
        return subprocess.CompletedProcess(command, 2, "", "write failed")

    with patch("subprocess.run", side_effect=broken_cli):
        result = beets_importer.enrich_pending(
            config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"),
            retry=True, item_id=entry["id"],
        )
    assert result["updated"] == 0
    assert result["errors"]
    assert destination.read_bytes() == before
    current, = beets_importer.list_review(str(tmp_path / "config"))
    assert current["metadata_confirmed"] is False
    assert not list(destination.parent.glob(".downloadeusz-enrich-*"))


def test_reused_source_path_enters_review_without_recopying_old_recording(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav")
    assert import_one(tmp_path, source)["organized"] == 1
    entry, = beets_importer.list_review(str(tmp_path / "config"))
    destination = Path(entry["destination"])
    before = destination.read_bytes()
    audio_file(source, title="Completely Different Recording")
    repeated = import_one(tmp_path, source)
    assert repeated["organized"] == 0
    assert destination.read_bytes() == before
    assert len(list((tmp_path / "music").rglob("*.wav"))) == 1
    changed, = beets_importer.list_review(str(tmp_path / "config"))
    assert changed["source_changed"] is True
    assert "źród" in changed["reason"].lower()


def test_confirmed_known_release_gets_missing_art_in_same_copy_without_cli(tmp_path, offline_beets):
    from mediafile import MediaFile
    from PIL import Image
    source = audio_file(tmp_path / "incoming" / "song.wav")
    media = MediaFile(source)
    media.mb_albumid = "00000000-0000-0000-0000-000000000001"
    media.save()
    assert import_one(tmp_path, source)["organized"] == 1
    state_path = tmp_path / "config" / "beets" / "library-state.json"
    state = json.loads(state_path.read_text())
    entry, = state["tracks"].values()
    assert entry["artwork_pending"] is True
    entry.update(metadata_confirmed=True, release_confirmed=True, status="confirmed")
    state_path.write_text(json.dumps(state))
    source.unlink()
    destination = Path(entry["destination"])
    image_data = io.BytesIO()
    Image.new("RGB", (20, 20), "green").save(image_data, format="JPEG")
    with patch("urllib.request.urlopen", return_value=io.BytesIO(image_data.getvalue())), patch("subprocess.run") as cli:
        result = beets_importer.enrich_pending(
            config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"),
            retry=True, item_id=entry["id"],
        )
    cli.assert_not_called()
    assert result["updated"] == 1
    assert result["errors"] == []
    assert MediaFile(destination).images
    assert list((tmp_path / "music").rglob("*.wav")) == [destination]
    assert beets_importer.list_review(str(tmp_path / "config")) == []


def test_real_broken_safety_policy_stops_before_audio_copy(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav")
    original = source.read_bytes()
    broken_plugins = tmp_path / "broken-plugins"
    broken_plugins.mkdir()
    shutil.copyfile(beets_importer.PLUGIN_DIR / "downloadeusz.py", broken_plugins / "downloadeusz.py")
    (broken_plugins / "downloadeusz_policy.py").write_text("def invalid_syntax(\n")
    with patch.object(beets_importer, "PLUGIN_DIR", broken_plugins):
        result = import_one(tmp_path, source)
    assert result["organized"] == 0
    assert result["errors"]
    assert not list((tmp_path / "music").rglob("*.wav"))
    assert source.read_bytes() == original


def test_missing_import_report_never_confirms_metadata(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav")
    destination = tmp_path / "music" / "copied.wav"

    def missing_report(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        if "import" in command:
            shutil.copyfile(source, destination)
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, str(destination) + "\n", "")

    with patch("subprocess.run", side_effect=missing_report):
        result = import_one(tmp_path, source)
    assert result["errors"]
    assert not any(entry["metadata_confirmed"] for entry in beets_importer.list_review(str(tmp_path / "config")))
