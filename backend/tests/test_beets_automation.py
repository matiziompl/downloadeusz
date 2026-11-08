"""Recognition retries must never turn skipped or failed imports into data loss."""

import json
import shutil
import subprocess
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend import beets_importer


@pytest.fixture
def incoming(tmp_path):
    source = tmp_path / "incoming"
    album = source / "LP1"
    album.mkdir(parents=True)
    tracks = []
    for number in range(1, 4):
        track = album / f"{number:02d} Song.flac"
        track.write_bytes(f"original audio {number}".encode())
        tracks.append(track)
    return SimpleNamespace(
        source=source, album=album, tracks=tracks,
        dest=tmp_path / "music", config=tmp_path / "config",
    )


def import_album(incoming, **kwargs):
    return beets_importer.import_paths(
        [str(incoming.album)], source_dir=str(incoming.source),
        dest_dir=str(incoming.dest), config_dir=str(incoming.config), **kwargs,
    )


class BeetsCLI:
    """Replace only the external CLI; use real importer state and copied files."""

    def __init__(self, incoming, actions):
        self.incoming = incoming
        self.actions = deque(actions)
        self.imports = []
        self.batch_outputs = {}

    def __call__(self, command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        if "import" in command:
            arguments = command[command.index("import") + 1:]
            self.imports.append(command)
            action = self.actions.popleft()
            batch = next(value.split("=", 1)[1] for value in arguments if value.startswith("downloadeusz_batch="))
            outputs = []
            if action in {"skip", "duplicate-skip", "fatal"}:
                log = Path(arguments[arguments.index("-l") + 1])
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(f"{'skip' if action == 'fatal' else action} {arguments[-1]}\n")
            elif action.startswith("import:"):
                filename = action.split(":", 1)[1]
                output = self.incoming.dest / "Artist" / "LP1" / filename
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.incoming.album / filename, output)
                outputs.append(str(output))
                Path(kwargs["env"]["DOWNLOADEUSZ_BEETS_REPORT"]).write_text(json.dumps({
                    "event": "imported", "source": str(self.incoming.album / filename),
                    "destination": str(output), "metadata_confirmed": True, "release_confirmed": False,
                }) + "\n", encoding="utf-8")
            elif action != "no-match-without-log":
                raise AssertionError(f"Unexpected fake CLI outcome: {action}")
            self.batch_outputs[batch] = outputs
            return subprocess.CompletedProcess(command, 2 if action == "fatal" else 0, "", "CLI failed" if action == "fatal" else "")
        if "list" in command:
            batch = command[-1].split(":", 1)[1]
            return subprocess.CompletedProcess(command, 0, "\n".join(self.batch_outputs[batch]), "")
        raise AssertionError(f"Unexpected beets operation: {command}")


def executable(name):
    return "/usr/bin/beet" if name == "beet" else "/usr/bin/fpcalc" if name == "fpcalc" else None


def test_new_recognition_revisits_legacy_skipped_input(incoming):
    cli = BeetsCLI(incoming, ["no-match-without-log", "import:01 Song.flac"])
    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=cli):
        first = import_album(incoming)
        state_path = incoming.config / "beets" / "import-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state[str(incoming.album)].pop("recognition_version", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        second = import_album(incoming)

    assert first["organized"] == 0
    assert second["organized"] == 1
    assert (incoming.dest / "Artist" / "LP1" / "01 Song.flac").read_bytes() == b"original audio 1"


def test_new_recognition_never_reimports_legacy_success(incoming):
    for track in incoming.tracks[1:]:
        track.unlink()
    cli = BeetsCLI(incoming, ["import:01 Song.flac"])
    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=cli):
        assert import_album(incoming)["organized"] == 1
        state_path = incoming.config / "beets" / "import-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state[str(incoming.album)].pop("recognition_version", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        second = import_album(incoming, retry=True)

    assert second == {"organized": 0, "skipped": 1, "errors": []}
    assert len(cli.imports) == 1
    assert incoming.tracks[0].read_bytes() == b"original audio 1"


def test_duplicate_album_never_falls_back_to_individual_tracks(incoming):
    cli = BeetsCLI(incoming, ["duplicate-skip"])
    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=cli):
        result = import_album(incoming)

    assert result == {"organized": 0, "skipped": 3, "errors": []}
    assert len(cli.imports) == 1
    assert not list(incoming.dest.rglob("*.flac"))
    assert [track.read_bytes() for track in incoming.tracks] == [b"original audio 1", b"original audio 2", b"original audio 3"]


def test_missing_fingerprint_tool_explains_rebuild_and_keeps_originals(incoming):
    for track in incoming.tracks[1:]:
        track.unlink()
    cli = BeetsCLI(incoming, ["skip"])
    messages = []
    with patch("shutil.which", side_effect=lambda name: executable(name) if name != "fpcalc" else None), \
         patch("subprocess.run", side_effect=cli):
        result = import_album(incoming, log_callback=messages.append)

    assert result["organized"] == 0
    assert incoming.tracks[0].read_bytes() == b"original audio 1"
    report = "\n".join(messages).lower()
    assert "fpcalc" in report
    assert "przebud" in report or "rebuild" in report
    assert len(cli.imports) == 1


def test_partial_fallback_counts_each_imported_track_once(incoming):
    cli = BeetsCLI(incoming, [
        "skip", "import:01 Song.flac", "duplicate-skip", "skip", "import:03 Song.flac",
    ])
    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=cli):
        first = import_album(incoming)
        second = import_album(incoming)

    assert first == {"organized": 2, "skipped": 1, "errors": []}
    assert second == {"organized": 0, "skipped": 3, "errors": []}
    assert len(list(incoming.dest.rglob("*.flac"))) == 2
    assert len(cli.imports) == 5
    assert [track.read_bytes() for track in incoming.tracks] == [b"original audio 1", b"original audio 2", b"original audio 3"]


def test_fingerprint_runs_only_after_metadata_miss_and_uses_lookup_import(incoming):
    for track in incoming.tracks[1:]:
        track.unlink()
    cli = BeetsCLI(incoming, [])

    def provider(command, **kwargs):
        if "import" in command:
            settings = json.loads(Path(command[command.index("-c") + 1]).read_text(encoding="utf-8"))
            fingerprinting = "chroma" in settings["plugins"] and settings["chroma"]["auto"]
            cli.actions.append("import:01 Song.flac" if fingerprinting else "skip")
        return cli(command, **kwargs)

    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=provider):
        result = import_album(incoming)

    assert result == {"organized": 1, "skipped": 0, "errors": []}
    assert len(cli.imports) == 2
    assert incoming.tracks[0].read_bytes() == b"original audio 1"


def test_fatal_cli_error_never_triggers_recognition_fallback(incoming):
    cli = BeetsCLI(incoming, ["fatal"])
    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=cli):
        result = import_album(incoming)

    assert result["organized"] == 0
    assert result["errors"]
    assert len(cli.imports) == 1
    state_path = incoming.config / "beets" / "import-state.json"
    assert not state_path.exists() or str(incoming.album) not in json.loads(state_path.read_text())
    assert incoming.tracks[0].read_bytes() == b"original audio 1"


def test_error_after_partial_import_counts_copies_without_further_attempts(incoming):
    cli = BeetsCLI(incoming, ["skip", "import:01 Song.flac", "fatal"])
    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=cli):
        result = import_album(incoming)

    assert result["organized"] == 1
    assert result["errors"]
    assert len(cli.imports) == 3
    assert len(list(incoming.dest.rglob("*.flac"))) == 1
    state_path = incoming.config / "beets" / "import-state.json"
    assert not state_path.exists() or str(incoming.album) not in json.loads(state_path.read_text())
    assert [track.read_bytes() for track in incoming.tracks] == [b"original audio 1", b"original audio 2", b"original audio 3"]


def test_old_no_match_log_does_not_trigger_new_fallback(incoming):
    log = incoming.config / "beets" / "review.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"skip {incoming.album}\n", encoding="utf-8")
    cli = BeetsCLI(incoming, ["no-match-without-log"])
    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=cli):
        result = import_album(incoming)

    assert result == {"organized": 0, "skipped": 3, "errors": []}
    assert len(cli.imports) == 1


def test_old_duplicate_log_does_not_block_new_no_match_fallback(incoming):
    log = incoming.config / "beets" / "review.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"duplicate-skip {incoming.album}\n", encoding="utf-8")
    cli = BeetsCLI(incoming, ["skip", "import:01 Song.flac", "duplicate-skip", "duplicate-skip"])
    with patch("shutil.which", side_effect=executable), patch("subprocess.run", side_effect=cli):
        result = import_album(incoming)

    assert result == {"organized": 1, "skipped": 2, "errors": []}
    assert len(cli.imports) == 4
