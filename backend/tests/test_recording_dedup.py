import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend import navidrome_dedup as dedup
from backend import slskd_organizer as organizer


def search(songs, title="Song", artist="Artist", **kwargs):
    response = {"subsonic-response": {"searchResult3": {"song": songs}}}
    with patch.object(dedup, "subsonic_request", return_value=response):
        return dedup.search_navidrome_song("http://navi", "user", "pass", artist, title, **kwargs)


@pytest.mark.parametrize("title,candidate", [
    ("Song (Live)", "Song"), ("Song", "Song (Live)"),
    ("Song (Acoustic)", "Song"), ("Song", "Song (Remix)"),
])
def test_different_recording_versions_never_deduplicate(title, candidate):
    assert search([{"title": candidate, "artist": "Artist", "suffix": "flac"}], title) is None


def test_quality_search_checks_later_candidates_and_fallback_query():
    mp3 = {"id": "mp3", "title": "Song", "artist": "Artist", "suffix": "mp3"}
    flac = {"id": "flac", "title": "Song", "artist": "Artist", "suffix": "flac"}
    responses = [{"subsonic-response": {"searchResult3": {"song": [mp3]}}},
                 {"subsonic-response": {"searchResult3": {"song": [mp3, flac]}}}]
    with patch.object(dedup, "subsonic_request", side_effect=responses):
        match = dedup.search_navidrome_song("http://navi", "user", "pass", "Artist", "Song", skip_mode="flac_only")
    assert match["id"] == "flac"


@pytest.mark.parametrize("artist", ["Art", "Other Artist", "", "Unknown Artist"])
def test_artist_substrings_and_unknown_artist_are_not_recording_identity(artist):
    assert search([{"title": "Song", "artist": artist, "suffix": "flac"}]) is None


def test_duration_and_real_recording_id_do_not_override_version_conflicts():
    recording_id = "2b827344-d11e-4490-b323-defa898264ca"
    candidate = {"title": "Song", "artist": "Artist", "suffix": "flac", "duration": 180,
                 "musicBrainzId": recording_id}
    assert search([candidate], "Song (Live)", length=180, recording_id=recording_id) is None
    assert search([candidate], length=210, recording_id=recording_id) is None
    assert search([candidate], length=180, recording_id=recording_id)["duration"] == 180


def test_ambiguous_duration_without_source_duration_does_not_skip():
    songs = [{"title": "Song", "artist": "Artist", "duration": duration, "suffix": "flac"}
             for duration in (180, 250)]
    assert search(songs) is None
    assert search(songs, length=180)["duration"] == 180


def test_unverified_source_id_cannot_hide_ambiguous_recordings():
    songs = [{"title": "Song", "artist": "Artist", "duration": duration, "suffix": "flac"}
             for duration in (180, 250)]
    assert search(songs, recording_id="2b827344-d11e-4490-b323-defa898264ca") is None


def test_distinct_recording_ids_with_same_length_are_ambiguous():
    songs = [{"title": "Song", "artist": "Artist", "duration": 180, "suffix": "flac", "musicBrainzId": recording_id}
             for recording_id in ("2b827344-d11e-4490-b323-defa898264ca", "4ad4b3af-30ca-4e73-a448-ea1148698d8c")]
    assert search(songs, length=180) is None


def test_filter_forwards_source_recording_metadata():
    song = {"name": "Song", "artists": ["Artist"], "duration": 180,
            "musicbrainz_recording_id": "2b827344-d11e-4490-b323-defa898264ca"}
    with patch.object(dedup, "search_navidrome_song", return_value=None) as lookup:
        assert dedup.filter_existing_songs([song], "url", "user", "pass")[0] == [song]
    assert lookup.call_args.kwargs["length"] == 180
    assert lookup.call_args.kwargs["recording_id"] == song["musicbrainz_recording_id"]
    assert lookup.call_args.kwargs["skip_mode"] == "flac_only"


def test_ytdlp_metadata_keeps_duration_and_recording_id():
    metadata = {"title": "Artist - Song", "duration": 180, "id": "youtube-id",
                "musicbrainz_recording_id": "2b827344-d11e-4490-b323-defa898264ca"}
    with patch.object(dedup.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(metadata))):
        entry = dedup.extract_ytdl_metadata("https://youtube.com/watch?v=video")[0]
    assert entry["length"] == 180
    assert entry["recording_id"] == metadata["musicbrainz_recording_id"]
    assert entry["source_id"] == "youtube-id"


def test_legacy_organizer_does_not_move_active_same_basename(tmp_path, monkeypatch):
    source, dest = tmp_path / "source", tmp_path / "music"
    active = source / "new-album" / "01.flac"
    active.parent.mkdir(parents=True)
    active.write_bytes(b"incomplete")
    monkeypatch.setattr(organizer, "fetch_slskd_transfer_files", lambda *args, **kwargs: [
        {"filename": "old-album/01.flac", "state": "Completed, Succeeded"},
        {"filename": "new-album/01.flac", "state": "InProgress, Downloading"},
    ])
    result = organizer.organize_slskd_downloads("http://slskd", source_dir=str(source), dest_dir=str(dest))
    assert result["organized"] == 0
    assert active.read_bytes() == b"incomplete"


def test_missing_completed_download_mount_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(organizer, "fetch_slskd_transfer_files", lambda **kwargs: [
        {"filename": "Album/01.flac", "state": "Completed, Succeeded"}])
    logs = []
    assert organizer.get_completed_album_directories("http://slskd", str(tmp_path), log_callback=logs.append) == []
    assert any(str(tmp_path) in line and "SLSKD_DOWNLOADS_DIR" in line for line in logs)


def test_missing_mount_logs_without_callback(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(organizer, "fetch_slskd_transfer_files", lambda **kwargs: [
        {"filename": "Album/01.flac", "state": "Completed, Succeeded"}])
    organizer.get_completed_album_directories("http://slskd", str(tmp_path))
    assert "SLSKD_DOWNLOADS_DIR" in caplog.text
