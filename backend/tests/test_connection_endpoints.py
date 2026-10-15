import json
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_test_navidrome_success():
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "subsonic-response": {
            "status": "ok",
            "version": "1.16.1",
            "type": "navidrome",
            "serverVersion": "0.52.5"
        }
    }).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        response = client.post("/api/test/navidrome", json={
            "url": "http://navidrome:4533",
            "user": "admin",
            "password": "secretpassword"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "Połączono" in data["message"]
        assert data.get("server_version") == "0.52.5"

def test_test_navidrome_failure():
    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        response = client.post("/api/test/navidrome", json={
            "url": "http://invalid-navidrome:4533",
            "user": "admin",
            "password": "secretpassword"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "Connection refused" in data["message"]

def test_test_slskd_with_credentials_success():
    # 1st call: POST /api/v0/session -> {"token": "jwt-abc-123"}
    mock_session_resp = MagicMock()
    mock_session_resp.read.return_value = json.dumps({"token": "jwt-abc-123"}).encode("utf-8")
    mock_session_resp.__enter__.return_value = mock_session_resp

    # 2nd call: GET /api/v0/transfers/downloads -> []
    mock_downloads_resp = MagicMock()
    mock_downloads_resp.status = 200
    mock_downloads_resp.getcode.return_value = 200
    mock_downloads_resp.read.return_value = json.dumps([]).encode("utf-8")
    mock_downloads_resp.__enter__.return_value = mock_downloads_resp

    with patch("urllib.request.urlopen", side_effect=[mock_session_resp, mock_downloads_resp]) as mock_urlopen:
        response = client.post("/api/test/slskd", json={
            "url": "http://slskd:5030",
            "username": "slskd",
            "password": "mypassword"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "zalogowano pomyślnie" in data["message"].lower()

        # Check that 2 requests were made
        assert mock_urlopen.call_count == 2
        
        # Check first request (login)
        login_req = mock_urlopen.call_args_list[0][0][0]
        assert "/api/v0/session" in login_req.full_url
        assert login_req.get_method() == "POST"
        assert json.loads(login_req.data.decode("utf-8")) == {"username": "slskd", "password": "mypassword"}

        # Check second request (transfers)
        transfers_req = mock_urlopen.call_args_list[1][0][0]
        assert "/api/v0/transfers/downloads" in transfers_req.full_url
        assert transfers_req.get_header("Authorization") == "Bearer jwt-abc-123"

def test_test_slskd_with_credentials_invalid():
    import urllib.error
    # Mock HTTP 401 on /api/v0/session
    err = urllib.error.HTTPError("http://slskd:5030/api/v0/session", 401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        response = client.post("/api/test/slskd", json={
            "url": "http://slskd:5030",
            "username": "slskd",
            "password": "wrongpassword"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "nieprawidłowy login lub hasło" in data["message"].lower()

def test_test_slskd_api_key_success():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.getcode.return_value = 200
    mock_response.read.return_value = json.dumps([]).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        response = client.post("/api/test/slskd", json={
            "url": "http://slskd:5030",
            "api_key": "my-secret-key"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "Połączono" in data["message"]
        
        # Verify X-API-Key header was sent
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("X-api-key") == "my-secret-key"

def test_test_slskd_failure():
    with patch("urllib.request.urlopen", side_effect=Exception("Connection timed out")):
        response = client.post("/api/test/slskd", json={
            "url": "http://slskd:5030",
            "api_key": "wrong-key"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "Connection timed out" in data["message"]

