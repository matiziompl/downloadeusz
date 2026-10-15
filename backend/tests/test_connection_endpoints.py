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

def test_test_slskd_success():
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
    with patch("urllib.request.urlopen", side_effect=Exception("Unauthorized")):
        response = client.post("/api/test/slskd", json={
            "url": "http://slskd:5030",
            "api_key": "wrong-key"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "Unauthorized" in data["message"]
