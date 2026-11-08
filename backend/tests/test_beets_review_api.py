import threading
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import main


@pytest.fixture
def review_app(tmp_path, monkeypatch):
    for key in ("LOGS_DIR", "SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "SLSKD_DOWNLOADS_DIR", "BEETS_INBOX_DIR"):
        path = tmp_path / key
        path.mkdir()
        monkeypatch.setattr(main, key, str(path))
    monkeypatch.setattr(main, "BEETS_ENABLED", True)
    monkeypatch.setattr(main, "BEETS_JOB_LOCK", threading.Lock())
    monkeypatch.setattr(main, "BEETS_STATE", {"last_task_id": None, "last_error": None})
    item = {"id": "a" * 64, "source": "/incoming/song.flac", "destination": "/music/song.flac",
            "imported": True, "metadata_confirmed": False, "release_confirmed": False,
            "status": "imported", "reason": "Niepotwierdzone metadane", "candidates": []}
    state = Path(main.SPOTDL_CONFIG_DIR) / "beets" / "library-state.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"version": 1, "tracks": {item["id"]: item}}))
    monkeypatch.setattr(main.beets_importer, "list_review", lambda config_dir: [item], raising=False)
    monkeypatch.setattr(main.accounts, "get_active_slskd_account", lambda config_dir: None)
    with patch("shutil.which", return_value="/usr/bin/beet"):
        yield TestClient(main.app), item


def test_review_lists_import_state_and_proposals(review_app):
    client, item = review_app
    response = client.get("/api/beets/review")
    assert response.status_code == 200
    assert response.json() == {"items": [item]}
    assert client.get("/api/beets/status").json()["review_count"] == 1


def test_retry_is_limited_to_known_item_and_uses_existing_task_flow(review_app):
    client, item = review_app
    with patch.object(main, "run_beets_import_task") as run:
        response = client.post(f"/api/beets/review/{item['id']}/retry", json={"navi_sync": True, "navi_account_id": "listener"})
        assert response.status_code == 200
        assert run.call_args.kwargs["review_item_id"] == item["id"]
        assert run.call_args.args[1].navi_account_id == "listener"
        assert run.call_args.args[1].navi_sync is True
    # Queued requests do not reserve the execution lock before the runner starts.
    assert not main.BEETS_JOB_LOCK.locked()
    assert client.post(f"/api/beets/review/{'b' * 64}/retry", json={}).status_code == 404
    assert client.post(f"/api/beets/review/{item['id']}/retry", json={"source_dir": "/etc"}).status_code == 422


def test_retry_refuses_parallel_import(review_app):
    client, item = review_app
    main.BEETS_JOB_LOCK.acquire()
    try:
        assert client.post(f"/api/beets/review/{item['id']}/retry", json={}).status_code == 409
    finally:
        main.BEETS_JOB_LOCK.release()


def test_enrichment_scans_navidrome_without_creating_another_copy(review_app):
    client, item = review_app
    with patch.object(main.beets_importer, "enrich_pending", return_value={"organized": 0, "updated": 1, "skipped": 0, "errors": []}, create=True) as enrich, \
         patch.object(main.beets_importer, "import_paths") as import_new, \
         patch.object(main, "fix_download_permissions"), \
         patch.object(main.accounts, "get_navidrome_account_by_id", return_value={"server": "http://navidrome", "username": "listener", "password": "secret"}), \
         patch.object(main.navidrome_dedup, "subsonic_request") as scan:
        response = client.post(f"/api/beets/review/{item['id']}/retry", json={"navi_sync": True, "navi_account_id": "listener"})
    assert response.status_code == 200
    assert enrich.call_args.kwargs["item_id"] == item["id"]
    assert enrich.call_args.kwargs["retry"] is True
    import_new.assert_not_called()
    assert scan.call_args.args[:4] == ("http://navidrome", "listener", "secret", "startScan.view")
    assert not main.BEETS_JOB_LOCK.locked()


def test_corrupt_review_state_does_not_look_like_empty_queue(review_app, monkeypatch):
    client, _ = review_app

    def unreadable(config_dir):
        raise ValueError("Nieprawidłowy plik stanu")

    monkeypatch.setattr(main.beets_importer, "list_review", unreadable)
    assert client.get("/api/beets/review").status_code == 500
    assert client.get("/api/beets/status").status_code == 200
