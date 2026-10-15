import os
import tempfile
import shutil
import json
from unittest.mock import patch, MagicMock
import pytest
from backend.slskd_organizer import (
    sanitize_filename,
    build_target_path,
    parse_audio_tags,
    organize_slskd_downloads
)

def test_sanitize_filename():
    assert sanitize_filename('AC/DC: "Back in Black"?') == "AC_DC_ _Back in Black__" or "_" in sanitize_filename('AC/DC')
    # Must not contain invalid characters
    cleaned = sanitize_filename('Song: Title/Name? *test* <tag> | "quoted"')
    for bad_char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        assert bad_char not in cleaned

def test_build_target_path():
    tags = {
        "artist": "Daft Punk",
        "album": "Discovery",
        "year": "2001",
        "tracknumber": "01",
        "title": "One More Time"
    }
    target = build_target_path(tags, "flac", "/downloads")
    expected = os.path.join("/downloads", "Daft Punk", "2001 - Discovery", "01 One More Time.flac")
    assert target == expected

def test_parse_audio_tags_fallback(tmp_path):
    # Dummy file without tags
    dummy_file = tmp_path / "05 - Unknown Song.mp3"
    dummy_file.write_text("dummy audio data")
    
    with patch("mutagen.File", return_value=None):
        tags = parse_audio_tags(str(dummy_file))
        assert tags["title"] == "05 - Unknown Song"
        assert tags["artist"] == "Unknown Artist"

def test_organize_slskd_downloads(tmp_path):
    source_dir = tmp_path / "slskd_complete"
    source_dir.mkdir()
    dest_dir = tmp_path / "music_library"
    dest_dir.mkdir()

    # Create dummy downloaded file in source
    album_dir = source_dir / "user1" / "Daft Punk - Discovery"
    album_dir.mkdir(parents=True)
    track_file = album_dir / "01 - One More Time.flac"
    track_file.write_text("flac content")

    mock_slskd_response = MagicMock()
    mock_slskd_response.status = 200
    mock_slskd_response.getcode.return_value = 200
    # slskd API transfers response structure
    mock_slskd_response.read.return_value = json.dumps([
        {
            "username": "user1",
            "directories": [
                {
                    "directory": "Daft Punk - Discovery",
                    "files": [
                        {
                            "id": "123",
                            "filename": "user1\\Daft Punk - Discovery\\01 - One More Time.flac",
                            "state": "Completed, Succeeded",
                            "size": 1024
                        }
                    ]
                }
            ]
        }
    ]).encode("utf-8")
    mock_slskd_response.__enter__.return_value = mock_slskd_response

    mock_tags = {
        "artist": ["Daft Punk"],
        "album": ["Discovery"],
        "date": ["2001"],
        "tracknumber": ["1/14"],
        "title": ["One More Time"]
    }

    logs = []
    def log_cb(msg):
        logs.append(msg)

    with patch("urllib.request.urlopen", return_value=mock_slskd_response), \
         patch("mutagen.File", return_value=mock_tags):
        
        result = organize_slskd_downloads(
            slskd_url="http://slskd:5030",
            slskd_api_key="test-key",
            source_dir=str(source_dir),
            dest_dir=str(dest_dir),
            log_callback=log_cb
        )

        assert result["organized"] == 1
        expected_file = dest_dir / "Daft Punk" / "2001 - Discovery" / "01 One More Time.flac"
        assert expected_file.exists()
        assert not track_file.exists()

def test_start_slskd_organize_endpoint():
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    # Missing url -> 400
    res_bad = client.post("/api/slskd/organize", json={"slskd_url": ""})
    assert res_bad.status_code == 400

    # Valid url -> 200 started
    with patch("backend.main.run_slskd_organize_task"):
        res = client.post("/api/slskd/organize", json={
            "slskd_url": "http://slskd:5030",
            "slskd_api_key": "abc"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "started"
        assert "task_id" in data

