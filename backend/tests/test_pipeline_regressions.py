from pathlib import Path
from unittest.mock import patch

from backend import main, slskd_organizer


def test_failed_peer_does_not_block_successful_alternative(tmp_path):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    (album / "Song.flac").write_bytes(b"audio")
    transfers = [
        {"username": "offline", "filename": "Artist/Album/Song.flac", "state": "Completed, Errored"},
        {"username": "online", "filename": "Artist/Album/Song.flac", "state": "Completed, Succeeded"},
    ]
    with patch.object(slskd_organizer, "fetch_slskd_transfer_files", return_value=transfers):
        assert slskd_organizer.get_completed_album_directories("http://slskd", str(tmp_path)) == [str(album)]


def test_playlist_lookup_uses_canonical_tags_of_final_file(tmp_path, monkeypatch):
    from backend.tests.test_hybrid_workflow import tagged_audio
    final = tagged_audio(tmp_path / "Artist" / "Album" / "Song.wav", "Canonical Song")
    monkeypatch.setattr(main, "DOWNLOADS_DIR", str(tmp_path))
    song = {"id": "correct", "title": "Canonical Song", "artist": "QA Artist", "duration": 1,
            "path": "Artist/Album/Song.wav", "suffix": "wav"}
    with patch.object(main.accounts, "get_navidrome_account_by_id", return_value={"server": "http://navi", "username": "u", "password": "p"}), \
         patch.object(main.navidrome_dedup, "subsonic_request", return_value={"subsonic-response": {"searchResult3": {"song": [song]}}}):
        _, lookup = main.navi_services({})
        assert lookup({"title": "Uncorrected Song", "artist": "Original Artist"}, ["Artist/Album/Song.wav"])["id"] == "correct"
