import json
from unittest.mock import patch, MagicMock
import pytest
from backend.navidrome_dedup import (
    clean_string,
    is_lossless,
    search_navidrome_song,
    filter_existing_songs
)

def test_clean_string():
    assert clean_string("Daft Punk - One More Time (Official Audio)!") == "daft punk one more time"
    assert clean_string("Żółty") == "zolty"
    assert clean_string("Кино - Группа крови") == "кино группа крови"
    assert clean_string("宇多田ヒカル - First Love") == "宇多田ヒカル first love"

def test_short_title_does_not_false_match():
    # Searching for "One" should NOT match "One More Time"
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "subsonic-response": {
            "status": "ok",
            "searchResult3": {
                "song": [
                    {
                        "id": "s1",
                        "title": "One More Time",
                        "artist": "U2",
                        "suffix": "flac"
                    }
                ]
            }
        }
    }).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        match = search_navidrome_song(
            navi_url="http://navidrome:4533",
            navi_user="admin",
            navi_pass="pass",
            artist="U2",
            title="One"
        )
        assert match is None

def test_is_lossless():
    assert is_lossless("flac") is True
    assert is_lossless("wav") is True
    assert is_lossless("alac") is True
    assert is_lossless("mp3") is False
    assert is_lossless("m4a", "audio/mp4") is False
    assert is_lossless("m4a", "audio/flac") is True

def test_search_navidrome_song():
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "subsonic-response": {
            "status": "ok",
            "searchResult3": {
                "song": [
                    {
                        "id": "s1",
                        "title": "One More Time",
                        "artist": "Daft Punk",
                        "album": "Discovery",
                        "suffix": "flac",
                        "contentType": "audio/flac"
                    }
                ]
            }
        }
    }).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        match = search_navidrome_song(
            navi_url="http://navidrome:4533",
            navi_user="admin",
            navi_pass="pass",
            artist="Daft Punk",
            title="One More Time"
        )
        assert match is not None
        assert match["title"] == "One More Time"
        assert match["suffix"] == "flac"

def test_filter_existing_songs_flac_only():
    songs = [
        {"name": "One More Time", "artists": ["Daft Punk"], "url": "http://sp/1"},
        {"name": "Aerodynamic", "artists": ["Daft Punk"], "url": "http://sp/2"},
        {"name": "Digital Love", "artists": ["Daft Punk"], "url": "http://sp/3"},
    ]

    # Mock search responses:
    # 1: One More Time -> FLAC (should skip)
    # 2: Aerodynamic -> MP3 (should NOT skip under flac_only)
    # 3: Digital Love -> Not found (should NOT skip)
    def fake_search(navi_url, navi_user, navi_pass, artist, title):
        if "One More Time" in title:
            return {"title": "One More Time", "artist": "Daft Punk", "suffix": "flac"}
        elif "Aerodynamic" in title:
            return {"title": "Aerodynamic", "artist": "Daft Punk", "suffix": "mp3"}
        return None

    logs = []
    with patch("backend.navidrome_dedup.search_navidrome_song", side_effect=fake_search):
        kept, skipped = filter_existing_songs(
            songs=songs,
            navi_url="http://navidrome:4533",
            navi_user="admin",
            navi_pass="pass",
            skip_mode="flac_only",
            log_callback=lambda m: logs.append(m)
        )
        assert len(skipped) == 1
        assert skipped[0]["name"] == "One More Time"
        assert len(kept) == 2
        assert kept[0]["name"] == "Aerodynamic"
        assert kept[1]["name"] == "Digital Love"

def test_filter_existing_songs_all():
    songs = [
        {"name": "One More Time", "artists": ["Daft Punk"], "url": "http://sp/1"},
        {"name": "Aerodynamic", "artists": ["Daft Punk"], "url": "http://sp/2"},
    ]

    def fake_search(navi_url, navi_user, navi_pass, artist, title):
        if "One More Time" in title:
            return {"title": "One More Time", "artist": "Daft Punk", "suffix": "flac"}
        elif "Aerodynamic" in title:
            return {"title": "Aerodynamic", "artist": "Daft Punk", "suffix": "mp3"}
        return None

    with patch("backend.navidrome_dedup.search_navidrome_song", side_effect=fake_search):
        kept, skipped = filter_existing_songs(
            songs=songs,
            navi_url="http://navidrome:4533",
            navi_user="admin",
            navi_pass="pass",
            skip_mode="all"
        )
        assert len(skipped) == 2
        assert len(kept) == 0
