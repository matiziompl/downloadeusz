from types import SimpleNamespace
import json
import os

import pytest

from backend.beetsplug import downloadeusz_policy as policy


def recording(**changes):
    values = {"artist": "Brent Faiyaz", "title": "Darling Wish You Well", "length": 180.0,
              "album": "Album", "year": 2024, "track_id": "recording-a"}
    values.update(changes)
    return values


def match(**changes):
    values = recording(**changes)
    return SimpleNamespace(info=SimpleNamespace(**values), distance=0.12)


def test_minor_spelling_and_punctuation_can_identify_recording():
    source = recording(title="Darling, Wish You Well!")
    selected, reason = policy.choose_recording(source, [match(title="Darling Wish You Well")])
    assert selected is not None
    assert reason
    selected, _ = policy.choose_recording(recording(title="Darling Wish You Wel"), [match()])
    assert selected is not None


def test_release_year_difference_does_not_change_recording_identity():
    selected, _ = policy.choose_recording(recording(), [match(album="Another Edition", year=2010)])
    assert selected is not None
    assert policy.release_agrees(recording(), selected.info) is False


def test_different_recording_versions_are_never_accepted():
    for marker in ("Live", "Remix", "Radio Edit", "Acoustic", "Instrumental", "Demo"):
        selected, reason = policy.choose_recording(recording(), [match(title=f"Darling Wish You Well ({marker})")])
        assert selected is None
        assert reason
        assert policy.versions_conflict(recording()["title"], f"Darling Wish You Well ({marker})")


def test_live_in_actual_song_title_is_not_a_version_marker():
    assert not policy.versions_conflict("Live Your Life", "Live Your Life")
    assert not policy.versions_conflict("Alive", "Alive")


def test_missing_artist_or_duration_cannot_relax_uncertain_match():
    for changes in ({"artist": ""}, {"artist": "Unknown Artist"}, {"length": 0}, {"length": None}):
        selected, _ = policy.choose_recording(recording(**changes), [match()])
        assert selected is None


def test_other_artist_or_duration_cannot_match():
    for changes in ({"artist": "Other Artist"}, {"length": 210.0}):
        selected, _ = policy.choose_recording(recording(), [match(**changes)])
        assert selected is None


def test_two_plausible_recordings_remain_ambiguous():
    selected, reason = policy.choose_recording(recording(), [match(), match(track_id="recording-b")])
    assert selected is None
    assert reason


def test_same_recording_across_editions_is_not_ambiguous():
    selected, _ = policy.choose_recording(recording(), [match(), match(album="Deluxe Edition", year=2025)])
    assert selected is not None


def test_exact_release_requires_existing_album_and_year():
    assert policy.release_agrees(recording(), match().info)
    assert not policy.release_agrees(recording(album=""), match().info)
    assert not policy.release_agrees(recording(year=0), match().info)
    assert not policy.release_agrees(recording(), match(year=2025).info)


def test_candidates_are_bounded_and_serializable():
    candidates = policy.candidate_summaries([match(track_id=str(index)) for index in range(5)])
    assert len(candidates) == 3
    assert candidates[0] == {"id": "0", "artist": "Brent Faiyaz", "title": "Darling Wish You Well",
                             "album": "Album", "year": 2024, "length": 180.0, "distance": 0.12}


def test_real_candidates_keep_metadata_snapshot_for_manual_selection():
    source = recording()
    source.pop("track_id")
    task = task_for(source, [recording()])
    proposal = policy.candidate_summaries(task.candidates, task.item)[0]
    assert proposal["kind"] == "recording"
    assert len(proposal["selection_key"]) == 64
    assert proposal["_track"]["track_id"] == "recording-a"
    assert json.loads(json.dumps(proposal)) == proposal


def plugin():
    from backend.beetsplug.downloadeusz import DownloadeuszPlugin
    return DownloadeuszPlugin()


def task_for(source, candidates, strong=False):
    from beets.autotag.hooks import TrackInfo
    from beets.autotag.match import TrackMatch, Recommendation
    from beets.autotag.distance import Distance
    from beets.importer import Action, SingletonImportTask
    from beets.library import Item
    item = Item(path=b"/incoming/song.mp3", **source)
    task = SingletonImportTask(None, item)
    actual = []
    for info in candidates:
        distance = Distance()
        distance.add("track_title", 0.1)
        actual.append(TrackMatch(distance, TrackInfo(**info), item))
    task.candidates = actual
    task.rec = Recommendation.strong if strong else Recommendation.medium
    task.set_choice(actual[0] if strong else Action.SKIP)
    return task


def test_plugin_accepts_recording_without_changing_unverified_release(tmp_path, monkeypatch):
    helper = plugin()
    source = recording()
    source.pop("track_id")
    task = task_for(source, [recording(album="Another Album", year=2010)])
    helper.review_choice(None, task)
    assert task.apply
    assert helper.decisions[os.fsdecode(task.item.path)]["release_confirmed"] is False


def test_plugin_blocks_even_strong_live_version_conflict_and_reports_reason(tmp_path, monkeypatch):
    report = tmp_path / "report.jsonl"
    monkeypatch.setenv("DOWNLOADEUSZ_BEETS_REPORT", str(report))
    source = recording()
    source.pop("track_id")
    task = task_for(source, [recording(title="Darling Wish You Well (Live)")], strong=True)
    plugin().review_choice(None, task)
    assert task.skip
    record = json.loads(report.read_text().splitlines()[-1])
    assert record["event"] == "review"
    assert record["source"] == "/incoming/song.mp3"
    assert record["reason"]
    assert record["candidates"][0]["title"].endswith("(Live)")


def test_in_place_guard_accepts_actual_library_file_but_rejects_hardlink(tmp_path):
    from beets.library import Item, Library
    from beets.importer import Action, SingletonImportTask
    from beets import config
    from beets.exceptions import UserError
    library_root = tmp_path / "music"
    library_root.mkdir()
    path = library_root / "existing.wav"
    path.write_bytes(b"existing audio")
    lib = Library(str(tmp_path / "library.db"), directory=os.fsencode(library_root))
    item = Item(path=os.fsencode(path), artist="Artist", title="Song", album="Album", year=2001)
    lib.add(item)
    task = SingletonImportTask(None, item)
    task.set_choice(Action.ASIS)
    session = SimpleNamespace(lib=lib, config=config["import"])
    old_copy, old_move = config["import"]["copy"].get(), config["import"]["move"].get()
    try:
        config["import"]["copy"].set(False)
        config["import"]["move"].set(False)
        helper = plugin()
        helper.protect_destinations(session, task)
        (tmp_path / "hardlink.wav").hardlink_to(path)
        with pytest.raises(UserError):
            helper.protect_destinations(session, task)
    finally:
        config["import"]["copy"].set(old_copy)
        config["import"]["move"].set(old_move)


def test_failed_copied_tag_write_restores_database_identity_too(tmp_path):
    from beets import config
    from beets.autotag.distance import Distance
    from beets.autotag.hooks import TrackInfo
    from beets.autotag.match import TrackMatch
    from beets.exceptions import UserError
    from beets.importer import SingletonImportTask
    from beets.library import Item, Library
    from backend.tests.test_hybrid_importer import audio_file

    source = audio_file(tmp_path / "incoming" / "song.wav", tagged=False)
    root = tmp_path / "music"
    root.mkdir()
    destination = root / "song.wav"
    destination.write_bytes(b"partial metadata write")
    lib = Library(str(tmp_path / "library.db"), directory=os.fsencode(root))
    item = Item(path=os.fsencode(destination), artist="Chosen Artist", title="Chosen Song", mb_trackid="chosen-id")
    lib.add(item)
    task = SingletonImportTask(None, item)
    task.set_choice(TrackMatch(Distance(), TrackInfo(title="Chosen Song", track_id="chosen-id"), item))
    helper = plugin()
    helper.sources[item.id] = str(source)
    helper.originals[str(source)] = {"album_source": ""}
    old_copy = config["import"]["copy"].get()
    try:
        config["import"]["copy"].set(True)
        with pytest.raises(UserError):
            helper.imported_files(SimpleNamespace(lib=lib, config=config["import"]), task)
        item.load()
        assert (item.artist, item.title, item.mb_trackid) == ("", "", "")
        assert destination.read_bytes() == source.read_bytes()
    finally:
        config["import"]["copy"].set(old_copy)
