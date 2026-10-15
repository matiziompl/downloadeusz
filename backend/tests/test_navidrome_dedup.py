import os
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

def test_extract_ytdl_metadata_single_and_playlist():
    from backend.navidrome_dedup import extract_ytdl_metadata

    # 1. Single track (e.g. SoundCloud / YouTube)
    single_mock = MagicMock()
    single_mock.returncode = 0
    single_mock.stdout = json.dumps({
        "_type": "video",
        "title": "Justice - Genesis (Official Audio)",
        "uploader": "Ed Banger Records",
        "url": "https://soundcloud.com/edbangerrecords/genesis"
    })

    with patch("subprocess.run", return_value=single_mock):
        entries = extract_ytdl_metadata("https://soundcloud.com/edbangerrecords/genesis")
        assert len(entries) == 1
        assert entries[0]["name"] == "Genesis (Official Audio)"
        assert entries[0]["artists"] == ["Justice"]
        assert entries[0]["url"] == "https://soundcloud.com/edbangerrecords/genesis"

    # 2. Playlist
    playlist_mock = MagicMock()
    playlist_mock.returncode = 0
    playlist_mock.stdout = json.dumps({
        "_type": "playlist",
        "entries": [
            {
                "title": "Daft Punk - One More Time",
                "uploader": "Daft Punk",
                "url": "https://youtube.com/watch?v=1"
            },
            {
                "title": "Harder Better Faster Stronger",
                "uploader": "Daft Punk",
                "artist": "Daft Punk",
                "url": "https://youtube.com/watch?v=2"
            }
        ]
    })

    with patch("subprocess.run", return_value=playlist_mock):
        entries = extract_ytdl_metadata("https://youtube.com/playlist?list=123")
        assert len(entries) == 2
        assert entries[0]["name"] == "One More Time"
        assert entries[0]["artists"] == ["Daft Punk"]
        assert entries[1]["name"] == "Harder Better Faster Stronger"
        assert entries[1]["artists"] == ["Daft Punk"]

def test_run_download_task_ytdl_dedup_skips_flac(tmp_path):
    from backend.main import run_download_task, DownloadRequest

    req = DownloadRequest(
        tool="ytdl",
        url="https://soundcloud.com/artist/track",
        flags={
            "check_navidrome": True,
            "navidrome_skip_mode": "flac_only",
            "audio_only": True
        },
        navi_url="http://navidrome:4533",
        navi_user="admin",
        navi_pass="secret"
    )

    mock_entries = [{
        "name": "One More Time",
        "artists": ["Daft Punk"],
        "url": "https://soundcloud.com/artist/track"
    }]

    # Navidrome returns FLAC
    mock_search_resp = {
        "title": "One More Time",
        "artist": "Daft Punk",
        "suffix": "flac"
    }

    try:
        with patch("backend.navidrome_dedup.extract_ytdl_metadata", return_value=mock_entries), \
             patch("backend.navidrome_dedup.search_navidrome_song", return_value=mock_search_resp), \
             patch("subprocess.Popen") as mock_popen:

            run_download_task("test-task-1", req)

            # Since it's already in FLAC, yt-dlp download must be skipped (Popen not called)
            assert mock_popen.call_count == 0
    finally:
        from backend.main import LOGS_DIR
        lf = os.path.join(LOGS_DIR, "test-task-1.log")
        if os.path.exists(lf):
            os.remove(lf)


def test_run_download_task_spotdl_dedup_uses_json_without_unbound_error(tmp_path):
    from backend.main import run_download_task, DownloadRequest, LOGS_DIR

    req = DownloadRequest(
        tool="spotdl",
        url="https://open.spotify.com/album/2UJcKiJxNryhL050F5Z1Fk",
        flags={
            "check_navidrome": True,
            "navidrome_skip_mode": "flac_only",
        },
        navi_url="http://navidrome:4533",
        navi_user="admin",
        navi_pass="secret"
    )

    fake_songs = [
        {"name": "Smells Like Teen Spirit", "artists": ["Nirvana"], "url": "https://spotify/track1"},
        {"name": "In Bloom", "artists": ["Nirvana"], "url": "https://spotify/track2"}
    ]

    def mock_subp_run(cmd, *args, **kwargs):
        # When spotdl save is executed, write JSON to --save-file
        if "save" in cmd:
            save_file_idx = cmd.index("--save-file") + 1
            save_file_path = cmd[save_file_idx]
            with open(save_file_path, "w", encoding="utf-8") as sf:
                json.dump(fake_songs, sf)
        mock_res = MagicMock()
        mock_res.returncode = 0
        return mock_res

    # Both tracks skipped because existing in Navidrome
    mock_filtered = ([], [{"name": "Smells Like Teen Spirit", "_navidrome_id": "n1"}, {"name": "In Bloom", "_navidrome_id": "n2"}])

    task_id = "test-spotdl-dedup-task"
    try:
        with patch("backend.main.SPOTDL_CONFIG_DIR", str(tmp_path)), \
             patch("subprocess.run", side_effect=mock_subp_run), \
             patch("backend.navidrome_dedup.filter_existing_songs", return_value=mock_filtered), \
             patch("subprocess.Popen") as mock_popen:

            run_download_task(task_id, req)

            # Read log file to verify no UnboundLocalError occurred
            log_file = os.path.join(LOGS_DIR, f"{task_id}.log")
            assert os.path.exists(log_file)
            with open(log_file, "r", encoding="utf-8") as lf:
                log_content = lf.read()

            assert "cannot access local variable 'json'" not in log_content
            assert "Wszystkie utwory (2) znajdują się już w Twojej bibliotece Navidrome" in log_content
            # SpotDL download process should not have been spawned since all kept=0
            assert mock_popen.call_count == 0
    finally:
        log_file = os.path.join(LOGS_DIR, f"{task_id}.log")
        if os.path.exists(log_file):
            os.remove(log_file)



