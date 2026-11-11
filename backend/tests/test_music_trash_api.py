"""Mark/restore are metadata filters; deleting a marked file requires confirmation."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.tests.test_music_library import write_track


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('SPOTDL_CONFIG_DIR', 'DOWNLOADS_DIR', 'LOGS_DIR', 'NORMALIZATION_QUEUE_DIR'):
        directory = tmp_path / name; directory.mkdir()
        monkeypatch.setattr(main, name, str(directory))
    return TestClient(main.app)


def indexed_track(client):
    path = write_track(Path(main.DOWNLOADS_DIR) / 'Artist' / 'Song.wav')
    assert client.post('/api/music/refresh').status_code == 200
    row, = client.get('/api/music/tracks').json()['items']
    return path, row


@pytest.mark.parametrize('payload', [{}, {'version': 1}, {'version': 'v', 'path': '/etc/passwd'}])
def test_mark_requires_version_and_forbids_paths(client, payload):
    assert client.post('/api/music/tracks/unknown/trash', json=payload).status_code == 422


@pytest.mark.parametrize('payload', [{}, {'confirmed': True}, {'version': 'v'},
    {'confirmed': 1, 'version': 'v'}, {'confirmed': 'true', 'version': 'v'},
    {'confirmed': True, 'version': 'v', 'path': '/etc/passwd'}])
def test_delete_requires_strict_confirmation_and_version(client, payload):
    assert client.post('/api/music/tracks/unknown/delete', json=payload).status_code == 422


def test_mark_restore_and_separately_confirmed_delete(client):
    path, row = indexed_track(client)
    original, inode = path.read_bytes(), path.stat().st_ino
    endpoint, version = f"/api/music/tracks/{row['id']}", {'version': row['version']}
    assert client.post(endpoint + '/trash', json=version).json()['status'] == 'trashed'
    assert path.read_bytes() == original and path.stat().st_ino == inode
    assert client.get('/api/music/tracks').json()['total'] == 0
    assert client.get('/api/music/tracks', params={'trashed': True}).json()['total'] == 1
    assert client.post(endpoint + '/restore', json=version).json()['status'] == 'restored'
    assert client.get('/api/music/tracks').json()['total'] == 1
    assert path.read_bytes() == original and path.stat().st_ino == inode
    assert client.post(endpoint + '/delete', json={**version, 'confirmed': True}).status_code == 409
    assert client.post(endpoint + '/trash', json=version).status_code == 200
    assert client.post(endpoint + '/delete', json={**version, 'confirmed': False}).status_code == 400
    assert client.post(endpoint + '/delete', json={'version': 'old', 'confirmed': True}).status_code == 409
    assert path.exists()
    assert client.post(endpoint + '/delete', json={**version, 'confirmed': True}).json()['status'] == 'deleted'
    assert not path.exists()


def test_failure_does_not_expose_paths_or_claim_backup(client, monkeypatch):
    path, row = indexed_track(client)
    class FailingTrash:
        def delete(self, *args, **kwargs):
            raise OSError('/private/config/denied')
    monkeypatch.setattr(main, 'get_music_trash', lambda: FailingTrash())
    response = client.post(f"/api/music/tracks/{row['id']}/delete", json={'confirmed': True, 'version': row['version']})
    assert response.status_code == 503 and '/private' not in response.text and 'kopia' not in response.text
    assert path.exists()


def test_pending_delete_of_same_identity_blocks_restore_even_after_version_change(client):
    path, row = indexed_track(client)
    endpoint = f"/api/music/tracks/{row['id']}"
    assert client.post(endpoint + '/trash', json={'version': row['version']}).status_code == 200
    with main.get_music_library().connect() as db:
        db.execute("INSERT INTO music_deletions VALUES(?,?,?,'pending',0)",
                   (row['id'], 'previous-version', row['relative_path']))
    response = client.post(endpoint + '/restore', json={'version': row['version']})
    assert response.status_code == 409
    assert 'usuwanie' in response.json()['detail']
    assert path.exists()
    assert client.get('/api/music/tracks', params={'trashed': True}).json()['total'] == 1
