"""A trash mark never moves audio; permanent deletion is a separate operation."""

import fcntl
import json
import os
from pathlib import Path

import pytest

from backend.job_store import JobStore
from backend.music_library import MusicLibrary
from backend.tests.test_music_library import write_track


@pytest.fixture
def trash(tmp_path):
    from backend.music_trash import MusicTrash
    config, music = tmp_path / 'config', tmp_path / 'music'
    config.mkdir(); music.mkdir()
    source = write_track(music / 'Artist' / 'Album' / 'Song.wav')
    index = MusicLibrary(config, music)
    index.refresh()
    row, = index.list_tracks()['items']
    return MusicTrash(index, normalization_dir=config / 'normalization'), index, row, source, JobStore(config / 'jobs.sqlite3')


def test_mark_and_restore_change_only_metadata(trash):
    service, index, row, source, store = trash
    original, info = source.read_bytes(), source.stat()
    assert service.move(row['id'], row['version'], store=store)['status'] == 'trashed'
    assert index.list_tracks()['total'] == 0
    marked, = index.list_tracks(trashed=True)['items']
    assert marked['trashed'] is True and marked['relative_path'] == row['relative_path']
    assert source.read_bytes() == original
    assert source.stat().st_ino == info.st_ino and source.stat().st_mtime_ns == info.st_mtime_ns
    assert service.restore(row['id'], row['version'], store=store)['status'] == 'restored'
    assert index.list_tracks()['items'][0]['trashed'] is False
    assert source.read_bytes() == original and source.stat().st_ino == info.st_ino


def test_mark_does_not_touch_importer_or_block_queued_edit(trash):
    service, index, row, source, store = trash
    state_path = index.config_dir / 'beets' / 'library-state.json'
    state_path.parent.mkdir()
    state_path.write_text(json.dumps({'version': 1, 'tracks': {'source': {'id': 'source',
        'source': '/original/download.wav', 'destination': str(source), 'imported': True}}}))
    original = state_path.read_bytes()
    store.enqueue('music_edit', {'track_id': row['id'], 'preview_token': 'pending'})
    service.move(row['id'], row['version'], store=store)
    service.restore(row['id'], row['version'], store=store)
    assert state_path.read_bytes() == original


def test_delete_requires_mark_and_version_and_preserves_separate_source(trash):
    service, index, row, source, store = trash
    with pytest.raises(ValueError, match='kosz'):
        service.delete(row['id'], row['version'], store=store)
    service.move(row['id'], row['version'], store=store)
    with pytest.raises(ValueError, match='zmieni'):
        service.delete(row['id'], 'old-version', store=store)
    download = write_track(index.config_dir / 'original.wav')
    result = service.delete(row['id'], row['version'], store=store)
    assert result['status'] == 'deleted' and not source.exists() and download.exists()
    assert index.list_tracks(trashed=True)['total'] == 0
    assert service.delete(row['id'], row['version'], store=store) == result


def test_pending_edit_normalizer_and_import_lock_prevent_delete(trash):
    service, index, row, source, store = trash
    service.move(row['id'], row['version'], store=store)
    task = store.enqueue('music_edit', {'track_id': row['id'], 'preview_token': 'pending'})
    with pytest.raises(RuntimeError, match='zadani'):
        service.delete(row['id'], row['version'], store=store)
    store.cancel(task['id'])
    pending = index.config_dir / 'normalization' / 'pending'; pending.mkdir(parents=True)
    request = pending / 'active.json'; request.write_text(json.dumps({'operation': 'scan_library'}))
    with pytest.raises(RuntimeError, match='Normalizeusz'):
        service.delete(row['id'], row['version'], store=store)
    request.unlink()
    lock_path = index.config_dir / 'beets' / 'import.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match='import'):
            service.delete(row['id'], row['version'], store=store)
    assert source.exists()


@pytest.mark.parametrize('link', ['symlink', 'hardlink'])
def test_links_cannot_be_deleted(trash, tmp_path, link):
    service, _, row, source, store = trash
    service.move(row['id'], row['version'], store=store)
    outside = tmp_path / 'outside.wav'
    if link == 'symlink':
        write_track(outside); source.unlink(); source.symlink_to(outside)
    else:
        os.link(source, outside)
    with pytest.raises(ValueError):
        service.delete(row['id'], row['version'], store=store)
    assert outside.exists() and source.exists()


def test_failed_unlink_retains_flag_and_restores_importer(trash, monkeypatch):
    service, index, row, source, store = trash
    service.move(row['id'], row['version'], store=store)
    state_path = index.config_dir / 'beets' / 'library-state.json'; state_path.parent.mkdir(exist_ok=True)
    state = {'version': 1, 'tracks': {'source': {'id': 'source', 'source': '/original/download.wav',
        'destination': str(source), 'imported': True}}}
    state_path.write_text(json.dumps(state))
    original_unlink = Path.unlink
    def fail(path, *args, **kwargs):
        if path == source:
            raise OSError('readonly')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail)
    with pytest.raises(OSError):
        service.delete(row['id'], row['version'], store=store)
    assert source.exists() and index.list_tracks(trashed=True)['total'] == 1
    assert json.loads(state_path.read_text()) == state


def test_interrupted_delete_retry_never_deletes_new_replacement(trash, monkeypatch):
    service, index, row, source, store = trash
    service.move(row['id'], row['version'], store=store)
    finish = service._finish_delete
    def fail(*args):
        raise OSError('index interrupted')
    monkeypatch.setattr(service, '_finish_delete', fail)
    with pytest.raises(OSError):
        service.delete(row['id'], row['version'], store=store)
    assert not source.exists()
    monkeypatch.setattr(service, '_finish_delete', finish)
    assert service.delete(row['id'], row['version'], store=store)['status'] == 'deleted'
    replacement = write_track(source, title='New song')
    assert service.delete(row['id'], row['version'], store=store)['status'] == 'deleted'
    assert replacement.exists()


def test_restore_conflicts_after_delete_intent_and_importer_tombstone(trash, monkeypatch):
    service, index, row, source, store = trash
    service.move(row['id'], row['version'], store=store)
    state_path = index.config_dir / 'beets' / 'library-state.json'
    state_path.parent.mkdir(exist_ok=True)
    state_path.write_text(json.dumps({'version': 1, 'tracks': {'source': {'id': 'source',
        'source': '/original/download.wav', 'destination': str(source), 'imported': True}}}))
    original, info = source.read_bytes(), source.stat()
    original_unlink = Path.unlink
    def interrupt_before_unlink(path, *args, **kwargs):
        if path == source:
            raise SystemExit('process interrupted after importer tombstone')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', interrupt_before_unlink)
    with pytest.raises(SystemExit):
        service.delete(row['id'], row['version'], store=store)
    assert json.loads(state_path.read_text())['tracks']['source']['trashed']['deleted'] is True
    with pytest.raises(ValueError, match='usuwanie'):
        service.restore(row['id'], row['version'], store=store)
    assert index.get_track(row['id'])['trashed'] is True
    assert source.read_bytes() == original and source.stat().st_ino == info.st_ino
    with index.connect() as db:
        assert db.execute('SELECT state FROM music_deletions WHERE track_id=?', (row['id'],)).fetchone()['state'] == 'pending'
    monkeypatch.setattr(Path, 'unlink', original_unlink)
    assert service.delete(row['id'], row['version'], store=store)['status'] == 'deleted'
    assert not source.exists()
