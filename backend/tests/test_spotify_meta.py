import os
import json
import pytest
from unittest.mock import patch, MagicMock
from backend.spotify_meta import (
    build_ffmpeg_tag_command,
    parse_spotdl_song_metadata,
    fetch_spotify_metadata
)

def test_build_ffmpeg_tag_command_basic():
    tags = {
        "title": "Smells Like Teen Spirit",
        "artist": "Nirvana",
        "album": "Nevermind",
        "year": "1991",
        "track_number": "1"
    }
    cmd = build_ffmpeg_tag_command(tags, placeholder="PLIK_PIOSENKI.mp3")
    assert cmd.startswith('ffmpeg -i "PLIK_PIOSENKI.mp3" -c copy')
    assert "-metadata 'title=Smells Like Teen Spirit'" in cmd
    assert "-metadata artist=Nirvana" in cmd or "-metadata 'artist=Nirvana'" in cmd
    assert "-metadata album=Nevermind" in cmd or "-metadata 'album=Nevermind'" in cmd
    assert "-metadata date=1991" in cmd or "-metadata 'date=1991'" in cmd
    assert "-metadata track=1" in cmd or "-metadata 'track=1'" in cmd
    assert cmd.endswith('"PLIK_PIOSENKI_otagowany.mp3"')


def test_build_ffmpeg_tag_command_escaping():
    tags = {
        "title": 'Song "With Quotes"',
        "artist": 'A$AP Rocky & AC/DC "Rock"',
        "album": "Highway to Hell",
        "year": "1979",
        "track_number": "2"
    }
    cmd = build_ffmpeg_tag_command(tags, placeholder="PLIK_PIOSENKI.mp3")
    # Must use single quotes around values containing $ or double quotes to prevent shell evaluation
    assert "-metadata 'title=Song \"With Quotes\"'" in cmd
    assert "-metadata 'artist=A$AP Rocky & AC/DC \"Rock\"'" in cmd


def test_fetch_spotify_metadata_invalid_url():
    res = fetch_spotify_metadata("https://youtube.com/watch?v=123", "/tmp", "/tmp")
    assert res["ok"] is False
    assert "Podaj poprawny link" in res["message"]

    res_empty = fetch_spotify_metadata("", "/tmp", "/tmp")
    assert res_empty["ok"] is False



def test_parse_spotdl_song_metadata():
    raw_song = {
        "name": "Come As You Are",
        "artists": ["Nirvana"],
        "album_name": "Nevermind",
        "release_date": "1991-09-24",
        "year": 1991,
        "track_number": 3,
        "cover_url": "https://i.scdn.co/image/ab67616d0000b273"
    }
    parsed = parse_spotdl_song_metadata(raw_song)
    assert parsed["title"] == "Come As You Are"
    assert parsed["artist"] == "Nirvana"
    assert parsed["album"] == "Nevermind"
    assert parsed["year"] == "1991"
    assert parsed["track_number"] == "3"
    assert parsed["cover_url"] == "https://i.scdn.co/image/ab67616d0000b273"
    assert "PLIK_PIOSENKI.mp3" in parsed["command"]


def test_fetch_spotify_metadata_success(tmp_path):
    mock_song = [{
        "name": "In Bloom",
        "artists": ["Nirvana"],
        "album_name": "Nevermind",
        "release_date": "1991-09-24",
        "track_number": 2,
        "cover_url": "https://image.url"
    }]

    def fake_subprocess_run(cmd, *args, **kwargs):
        save_file = cmd[cmd.index("--save-file") + 1]
        with open(save_file, "w", encoding="utf-8") as f:
            json.dump(mock_song, f)
        res = MagicMock()
        res.returncode = 0
        return res

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        meta = fetch_spotify_metadata("https://open.spotify.com/track/xyz", str(tmp_path), str(tmp_path))
        assert meta["ok"] is True
        assert meta["title"] == "In Bloom"
        assert meta["artist"] == "Nirvana"
        assert meta["album"] == "Nevermind"
        assert meta["year"] == "1991"
        assert meta["track_number"] == "2"
        assert "PLIK_PIOSENKI.mp3" in meta["command"]
        assert "-id3v2_version" in meta["command"]
        assert "https://image.url" in meta["command"]


def test_build_ffmpeg_tag_command_with_cover_art():
    tags = {
        "title": "Lithium",
        "artist": "Nirvana",
        "album": "Nevermind",
        "year": "1991",
        "track_number": "5",
        "cover_url": "https://i.scdn.co/image/ab67616d0000b273"
    }
    cmd = build_ffmpeg_tag_command(tags, placeholder="PLIK_PIOSENKI.mp3")
    assert '-i "PLIK_PIOSENKI.mp3"' in cmd
    assert '-i "https://i.scdn.co/image/ab67616d0000b273"' in cmd
    assert "-map 0:a -map 1:v" in cmd
    assert "-c:a copy -c:v copy" in cmd
    assert "-id3v2_version 3" in cmd
    assert '-metadata:s:v title="Album cover"' in cmd
    assert '-metadata:s:v comment="Cover (front)"' in cmd
    assert "-metadata title=Lithium" in cmd or "-metadata 'title=Lithium'" in cmd
    assert cmd.endswith('"PLIK_PIOSENKI_otagowany.mp3"')


