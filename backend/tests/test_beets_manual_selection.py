"""Explicit beets choices must apply only the proposal the user reviewed."""

import json
import os
import sqlite3
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import beets_importer, main
from backend.main import run_pipeline_job
from backend.beetsplug import downloadeusz_policy as policy
from backend.tests.test_beets_policy import plugin, recording, task_for
from backend.tests.test_hybrid_importer import audio_file, import_one, offline_beets


def proposal(**changes):
    task = task_for({"artist": "Source", "title": "Source", "length": 180.0}, [recording(**changes)])
    return policy.candidate_summaries(task.candidates)[0]


@pytest.fixture
def manual_app(tmp_path, monkeypatch):
    for key in ("LOGS_DIR", "SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "SLSKD_DOWNLOADS_DIR", "BEETS_INBOX_DIR", "ACCOUNTS_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        monkeypatch.setattr(main, key, str(directory))
    monkeypatch.setattr(main, "BEETS_ENABLED", True)
    monkeypatch.setattr(main, "BEETS_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main, "BEETS_STATE", {"last_task_id": None, "last_error": None})
    item = {"id": "a" * 64, "source": "/incoming/song.flac", "destination": "/music/song.flac",
            "imported": True, "metadata_confirmed": False, "release_confirmed": False,
            "status": "imported", "reason": "Niepotwierdzone metadane", "candidates": []}
    state_path = Path(main.SPOTDL_CONFIG_DIR) / "beets" / "library-state.json"
    state_path.parent.mkdir()

    def save():
        state_path.write_text(json.dumps({"version": 1, "tracks": {item["id"]: item}}))

    save()
    with patch("shutil.which", return_value="/usr/bin/beet"), patch.object(main, "run_pipeline_job") as runner:
        yield TestClient(main.app), item, save, runner


def test_select_endpoint_rejects_unknown_proposal(manual_app):
    client, item, _, _ = manual_app
    response = client.post(f"/api/beets/review/{item['id']}/select", json={"candidate_key": "b" * 64})
    assert response.status_code == 409


def test_candidates_preserve_full_metadata_and_stable_choice_key():
    candidate = proposal()
    assert candidate.get("kind") == "recording"
    assert len(candidate.get("selection_key", "")) == 64
    assert candidate["_track"]["track_id"] == "recording-a"
    assert candidate["selection_key"] == proposal()["selection_key"]
    assert candidate["selection_key"] != proposal(title="Different recording")["selection_key"]


def test_review_exposes_details_without_internal_snapshot(manual_app):
    client, item, save, _ = manual_app
    candidate = proposal()
    item["candidates"] = [candidate]
    save()
    public = client.get("/api/beets/review").json()["items"][0]["candidates"][0]
    assert "_track" not in public and "_album" not in public
    assert public["selection_key"] == candidate["selection_key"]
    assert public["title"] == "Darling Wish You Well"
    assert "_track" in beets_importer._load_library_state(main.SPOTDL_CONFIG_DIR)["tracks"][item["id"]]["candidates"][0]


def test_select_enqueues_only_server_choice_and_existing_navidrome_options(manual_app):
    client, item, save, runner = manual_app
    item["candidates"] = [proposal()]
    save()
    response = client.post(f"/api/beets/review/{item['id']}/select", json={
        "candidate_key": item["candidates"][0]["selection_key"], "navi_sync": True, "navi_account_id": "listener",
    })
    assert response.status_code == 200
    job = main.get_job_store().get(response.json()["task_id"])
    assert job["kind"] == "import"
    assert job["payload"] == {"review_item_id": item["id"], "candidate_key": item["candidates"][0]["selection_key"],
                              "navi_sync": True, "navi_account_id": "listener"}
    runner.assert_called_once_with(response.json()["task_id"])
    assert not main.BEETS_JOB_LOCK.locked()


def test_queued_selection_reaches_importer_through_real_job_runner(manual_app):
    client, item, save, _ = manual_app
    item["candidates"] = [proposal()]
    save()
    selected_key = item["candidates"][0]["selection_key"]
    response = client.post(f"/api/beets/review/{item['id']}/select", json={"candidate_key": selected_key})
    assert response.status_code == 200
    identity = response.json()["task_id"]
    with patch.object(main, "run_beets_import_task", return_value={
        "paths": [], "errors": [], "organized": 0, "updated": 0, "ready_sources": [],
    }) as importer:
        run_pipeline_job(identity)
    assert main.get_job_store().get(identity)["status"] == "done"
    assert importer.call_args.kwargs["review_item_id"] == item["id"]
    assert importer.call_args.kwargs["candidate_key"] == selected_key
    assert importer.call_args.kwargs["defer_finalize"] is True


@pytest.mark.parametrize("body", [
    {}, {"candidate_key": "bad"}, {"candidate_key": "b" * 64, "source_dir": "/etc"},
    {"candidate_key": "b" * 64, "_track": {"title": "Client supplied metadata"}},
])
def test_select_rejects_invalid_key_and_client_metadata(manual_app, body):
    client, item, _, runner = manual_app
    response = client.post(f"/api/beets/review/{item['id']}/select", json=body)
    assert response.status_code == 422
    runner.assert_not_called()


def test_select_unknown_item_is_not_accepted(manual_app):
    client, _, _, runner = manual_app
    assert client.post(f"/api/beets/review/{'f' * 64}/select", json={"candidate_key": "b" * 64}).status_code == 404
    runner.assert_not_called()


@pytest.mark.parametrize("blocked", ["disabled", "locked"])
def test_select_obeys_import_availability_and_lock(manual_app, monkeypatch, blocked):
    client, item, save, runner = manual_app
    item["candidates"] = [proposal()]
    save()
    if blocked == "disabled":
        monkeypatch.setattr(main, "BEETS_ENABLED", False)
    else:
        main.BEETS_JOB_LOCK.acquire()
    try:
        response = client.post(f"/api/beets/review/{item['id']}/select", json={"candidate_key": item["candidates"][0]["selection_key"]})
        assert response.status_code == (400 if blocked == "disabled" else 409)
        runner.assert_not_called()
    finally:
        if main.BEETS_JOB_LOCK.locked():
            main.BEETS_JOB_LOCK.release()


@pytest.mark.parametrize("strong", [False, True])
def test_plugin_applies_explicit_snapshot_instead_of_another_match(monkeypatch, strong):
    chosen = proposal(title="Selected version", track_id="chosen-recording")
    task = task_for({"artist": "Source", "title": "Source", "album": "Keep this album", "length": 180.0},
                    [recording(title="Other version", track_id="another-recording")], strong=strong)
    monkeypatch.setenv("DOWNLOADEUSZ_BEETS_SELECTION", json.dumps(chosen))
    helper = plugin()
    helper.review_choice(None, task)
    assert task.apply
    assert task.match.info.track_id == "chosen-recording"
    decision = helper.decisions[os.fsdecode(task.item.path)]
    assert decision["metadata_confirmed"] is True
    assert decision["release_confirmed"] is False


def test_selected_snapshot_is_checked_again_before_processing(manual_app):
    _, item, save, _ = manual_app
    selected = proposal()
    item["candidates"] = [selected]
    save()
    item["candidates"] = [proposal(title="New proposal")]
    save()
    with patch.object(beets_importer, "_attempt_import") as attempt:
        result = beets_importer.enrich_pending(config_dir=main.SPOTDL_CONFIG_DIR, dest_dir=main.DOWNLOADS_DIR,
                                               retry=True, item_id=item["id"], candidate_key=selected["selection_key"])
    assert result["errors"]
    assert result["updated"] == 0
    attempt.assert_not_called()


def album_match():
    from beets.autotag.distance import Distance
    from beets.autotag.hooks import AlbumInfo, TrackInfo
    from beets.autotag.match import AlbumMatch
    from beets.library import Item

    first = Item(path=b"/incoming/first.wav", title="First local title")
    second = Item(path=b"/incoming/second.wav", title="Second local title")
    tracks = [TrackInfo(title="First song", artist="Artist", track_id="first-id", index=1, medium=1, length=1.0),
              TrackInfo(title="Second song", artist="Artist", track_id="second-id", index=2, medium=1, length=1.0)]
    info = AlbumInfo(tracks=tracks, album="Chosen release", artist="Artist", artists=["Artist"], album_id="release-id",
                     year=1994, country="PL", media="CD", label="Label", catalognum="CAT-1")
    return AlbumMatch(Distance(), info, {first: tracks[0], second: tracks[1]}), first, second


def test_album_proposals_keep_the_track_mapped_to_this_file():
    match, first, second = album_match()
    candidate = policy.candidate_summaries([match], item=second)[0]
    assert candidate["kind"] == "release"
    assert candidate["id"] == "release-id"
    assert candidate["title"] == "Second song"
    assert candidate["_track"]["track_id"] == "second-id"
    assert candidate["_album"]["album_id"] == "release-id"
    assert "tracks" not in candidate["_album"]
    assert candidate["selection_key"] != policy.candidate_summaries([match], item=first)[0]["selection_key"]


def save_choice(tmp_path, candidate):
    config = tmp_path / "config"
    state = beets_importer._load_library_state(str(config))
    entry, = state["tracks"].values()
    entry["candidates"] = [candidate]
    beets_importer._write_json(config / "beets" / "library-state.json", state)
    return entry


@pytest.mark.parametrize("release", [False, True])
def test_real_manual_selection_retags_existing_copy_without_recopying_original(tmp_path, offline_beets, release):
    from mutagen.wave import WAVE
    from backend.audio_identity import audio_packet_signature

    source = audio_file(tmp_path / "incoming" / "song.wav")
    original = source.read_bytes()
    assert import_one(tmp_path, source)["organized"] == 1
    if release:
        match, first, _ = album_match()
        candidate = policy.candidate_summaries([match], item=first)[0]
    else:
        candidate = proposal(artist="Local Artist", title="Selected title", track_id="chosen-recording")
    entry = save_choice(tmp_path, candidate)
    destination = Path(entry["destination"])
    before_audio = audio_packet_signature(destination)
    result = beets_importer.enrich_pending(
        config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"), retry=True,
        item_id=entry["id"], candidate_key=candidate["selection_key"],
    )
    assert result["errors"] == [], result
    assert result["updated"] == 1 and result["organized"] == 0
    assert list((tmp_path / "music").rglob("*.wav")) == [destination]
    assert source.read_bytes() == original
    assert audio_packet_signature(destination) == before_audio
    tags = WAVE(destination).tags
    assert str(tags["TIT2"]) == ("First song" if release else "Selected title")
    assert str(tags["TALB"]) == ("Chosen release" if release else "Local Album")
    if release:
        assert str(tags["TRCK"]) == "1/2"
    current = beets_importer._load_library_state(str(tmp_path / "config"))["tracks"][entry["id"]]
    assert current["metadata_confirmed"] is True
    assert current["release_confirmed"] is release


def test_manual_selection_failure_restores_copy_and_does_not_fallback(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav")
    original = source.read_bytes()
    assert import_one(tmp_path, source)["organized"] == 1
    candidate = proposal()
    entry = save_choice(tmp_path, candidate)
    destination = Path(entry["destination"])
    before = destination.read_bytes()
    commands = []

    def failing_cli(command, **kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "plugins: downloadeusz\n", "")
        assert "import" in command
        commands.append(command)
        destination.write_bytes(b"partial write")
        return subprocess.CompletedProcess(command, 2, "", "write failed")

    with patch("subprocess.run", side_effect=failing_cli):
        result = beets_importer.enrich_pending(
            config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"), retry=True,
            item_id=entry["id"], candidate_key=candidate["selection_key"],
        )
    assert result["errors"] and result["updated"] == 0
    assert len(commands) == 1
    assert destination.read_bytes() == before
    assert source.read_bytes() == original
    current = beets_importer._load_library_state(str(tmp_path / "config"))["tracks"][entry["id"]]
    assert current["metadata_confirmed"] is False
    assert not list(destination.parent.glob(".downloadeusz-enrich-*"))


def test_unimported_selection_waits_for_ready_source_then_copies_once(tmp_path, offline_beets):
    from mutagen.wave import WAVE

    source = audio_file(tmp_path / "incoming" / "song.wav", tagged=False)
    original = source.read_bytes()
    assert import_one(tmp_path, source)["organized"] == 0
    candidate = proposal(artist="Selected Artist", title="Selected title")
    entry = save_choice(tmp_path, candidate)
    arguments = {"config_dir": str(tmp_path / "config"), "dest_dir": str(tmp_path / "music"),
                 "retry": True, "item_id": entry["id"], "candidate_key": candidate["selection_key"]}
    with patch.object(beets_importer, "_attempt_import") as attempt:
        waiting = beets_importer.enrich_pending(**arguments, ready_sources=[])
    assert waiting["organized"] == waiting["updated"] == 0
    attempt.assert_not_called()
    copied = beets_importer.enrich_pending(**arguments, ready_sources=[str(source)])
    assert copied["errors"] == []
    assert copied["organized"] == 1 and copied["updated"] == 0
    current = beets_importer._load_library_state(str(tmp_path / "config"))["tracks"][entry["id"]]
    destination = Path(current["destination"])
    assert current["metadata_confirmed"] is True
    assert str(WAVE(destination).tags["TIT2"]) == "Selected title"
    assert source.read_bytes() == original
    assert list((tmp_path / "music").rglob("*.wav")) == [destination]


def test_unimported_selection_rejects_source_replaced_after_lookup(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav", tagged=False)
    assert import_one(tmp_path, source)["organized"] == 0
    candidate = proposal()
    entry = save_choice(tmp_path, candidate)
    audio_file(source, tagged=False, sample=100)
    changed_source = source.read_bytes()
    result = beets_importer.enrich_pending(
        config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"), retry=True,
        item_id=entry["id"], candidate_key=candidate["selection_key"], ready_sources=[str(source)],
    )
    assert result["errors"] and result["organized"] == 0
    assert source.read_bytes() == changed_source
    assert not list((tmp_path / "music").rglob("*.wav"))


def test_release_artwork_failure_restores_audio_tags_and_confirmation_state(tmp_path, offline_beets):
    source = audio_file(tmp_path / "incoming" / "song.wav")
    assert import_one(tmp_path, source)["organized"] == 1
    match, first, _ = album_match()
    candidate = policy.candidate_summaries([match], item=first)[0]
    entry = save_choice(tmp_path, candidate)
    destination = Path(entry["destination"])
    before = destination.read_bytes()
    with sqlite3.connect(tmp_path / "config" / "beets" / "library.db") as database:
        original_database_identity = database.execute("SELECT artist, title, album, mb_trackid FROM items").fetchall()
    with patch.object(beets_importer.album_art, "artwork_pending", return_value=True), \
         patch.object(beets_importer.album_art, "enrich_artwork", side_effect=RuntimeError("artwork write failed")):
        result = beets_importer.enrich_pending(
            config_dir=str(tmp_path / "config"), dest_dir=str(tmp_path / "music"), retry=True,
            item_id=entry["id"], candidate_key=candidate["selection_key"],
        )
    assert result["errors"] and result["updated"] == 0
    assert destination.read_bytes() == before
    current = beets_importer._load_library_state(str(tmp_path / "config"))["tracks"][entry["id"]]
    assert current["metadata_confirmed"] is False and current["release_confirmed"] is False
    assert current["destination_fingerprint"] == entry["destination_fingerprint"]
    with sqlite3.connect(tmp_path / "config" / "beets" / "library.db") as database:
        assert database.execute("SELECT artist, title, album, mb_trackid FROM items").fetchall() == original_database_identity
