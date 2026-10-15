import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_get_accounts_navidrome_and_slskd(tmp_path):
    navi_file = tmp_path / "navidrome_accounts.txt"
    navi_file.write_text("""
Server: "http://test.pl"
user: "test_user"
password: "test"
user: "test_user2"
password: "test2"
""", encoding="utf-8")

    slskd_file = tmp_path / "slskd_accounts.txt"
    slskd_file.write_text("""
Server: "http://test.pl"
user: "test_user"
password: "test"
""", encoding="utf-8")

    with patch("backend.accounts.SPOTDL_CONFIG_DIR", str(tmp_path)), \
         patch("backend.main.SPOTDL_CONFIG_DIR", str(tmp_path)):
        # Navidrome accounts
        resp_navi = client.get("/api/accounts/navidrome")
        assert resp_navi.status_code == 200
        data_navi = resp_navi.json()
        assert len(data_navi["accounts"]) == 2
        assert data_navi["accounts"][0]["username"] == "test_user"
        assert data_navi["accounts"][0]["server"] == "http://test.pl"
        assert "password" not in data_navi["accounts"][0]  # passwords must be withheld from frontend
        assert data_navi["accounts"][1]["username"] == "test_user2"

        # Slskd account
        resp_slskd = client.get("/api/accounts/slskd")
        assert resp_slskd.status_code == 200
        data_slskd = resp_slskd.json()
        assert data_slskd["configured"] is True
        assert data_slskd["server"] == "http://test.pl"
        assert data_slskd["username"] == "test_user"
        assert "password" not in data_slskd


def test_test_navidrome_by_account_id(tmp_path):
    navi_file = tmp_path / "navidrome_accounts.txt"
    navi_file.write_text("""
Server: "http://test.pl"
user: "test_user"
password: "test"
""", encoding="utf-8")

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "subsonic-response": {
            "status": "ok",
            "version": "1.16.1",
            "serverVersion": "0.52.5"
        }
    }).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("backend.accounts.SPOTDL_CONFIG_DIR", str(tmp_path)), \
         patch("backend.main.SPOTDL_CONFIG_DIR", str(tmp_path)), \
         patch("urllib.request.urlopen", return_value=mock_response):
        resp = client.post("/api/test/navidrome", json={
            "account_id": "http://test.pl::test_user"
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert "Połączono" in resp.json()["message"]


def test_test_slskd_from_file_account(tmp_path):
    slskd_file = tmp_path / "slskd_accounts.txt"
    slskd_file.write_text("""
Server: "http://test.pl"
user: "test_user"
password: "test"
""", encoding="utf-8")

    # 1st call: session token, 2nd call: downloads
    mock_session_resp = MagicMock()
    mock_session_resp.read.return_value = json.dumps({"token": "jwt-token"}).encode("utf-8")
    mock_session_resp.__enter__.return_value = mock_session_resp

    mock_downloads_resp = MagicMock()
    mock_downloads_resp.status = 200
    mock_downloads_resp.getcode.return_value = 200
    mock_downloads_resp.read.return_value = json.dumps([]).encode("utf-8")
    mock_downloads_resp.__enter__.return_value = mock_downloads_resp

    with patch("backend.accounts.SPOTDL_CONFIG_DIR", str(tmp_path)), \
         patch("backend.main.SPOTDL_CONFIG_DIR", str(tmp_path)), \
         patch("urllib.request.urlopen", side_effect=[mock_session_resp, mock_downloads_resp]):
        resp = client.post("/api/test/slskd", json={})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


def test_post_spotify_metadata_endpoint():
    mock_meta = {
        "ok": True,
        "title": "Smells Like Teen Spirit",
        "artist": "Nirvana",
        "album": "Nevermind",
        "year": "1991",
        "track_number": "1",
        "cover_url": "https://img.url",
        "command": 'ffmpeg -i "PLIK_PIOSENKI.mp3" -c copy -metadata title="Smells Like Teen Spirit" "PLIK_PIOSENKI_otagowany.mp3"'
    }

    with patch("backend.spotify_meta.fetch_spotify_metadata", return_value=mock_meta):
        resp = client.post("/api/spotify/metadata", json={"url": "https://open.spotify.com/track/123"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["title"] == "Smells Like Teen Spirit"
        assert "PLIK_PIOSENKI.mp3" in data["command"]
