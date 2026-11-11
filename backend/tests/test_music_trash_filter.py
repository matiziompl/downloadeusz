"""Soft trash remains a persistent filter while audio stays in place."""

import json
import sqlite3
from pathlib import Path

import pytest

from backend import main
from backend.music_library import MusicLibrary, file_version
from backend.tests.test_hybrid_importer import audio_file, offline_beets
from backend.tests.test_music_library import write_track
from backend.tests.test_music_library_api import client


def test_trash_search_and_pagination_stay_separate_after_refresh_and_restart(client):
    music = Path(main.DOWNLOADS_DIR)
    write_track(music / 'active.wav', title='Łódź A', album='')
    write_track(music / 'other.wav', title='Other')
    write_track(music / 'trash-b.wav', title='Łódź B', album='')
    write_track(music / 'trash-c.wav', title='Łódź C')
    write_track(music / 'trash-d.wav', title='Łódź D')
    assert client.post('/api/music/refresh').status_code == 200
    initial = client.get('/api/music/tracks').json()['items']
    trashed_ids = {row['id'] for row in initial if row['relative_path'].startswith('trash-')}
    for row in initial:
        if row['id'] in trashed_ids:
            assert client.post(f"/api/music/tracks/{row['id']}/trash", json={'version': row['version']}).status_code == 200

    assert client.post('/api/music/refresh').status_code == 200
    restarted = MusicLibrary(main.SPOTDL_CONFIG_DIR, main.DOWNLOADS_DIR)
    assert restarted.list_tracks()['total'] == 2
    assert restarted.list_tracks(trashed=True)['total'] == 3
    visible = client.get('/api/music/tracks', params={'q': 'LODZ', 'incomplete': True}).json()
    assert visible['total'] == 1
    assert visible['items'][0]['relative_path'] == 'active.wav'
    assert visible['items'][0]['trashed'] is False

    pages = [client.get('/api/music/tracks', params={'trashed': True, 'q': 'LODZ', 'offset': offset, 'limit': 1}).json()
             for offset in range(3)]
    assert all(page['total'] == 3 and len(page['items']) == 1 for page in pages)
    assert {page['items'][0]['id'] for page in pages} == trashed_ids
    assert all(page['items'][0]['trashed'] is True for page in pages)
    incomplete = client.get('/api/music/tracks', params={'trashed': True, 'incomplete': True}).json()
    assert incomplete['total'] == 1
    assert incomplete['items'][0]['relative_path'] == 'trash-b.wav'
    assert client.get('/api/music/tracks', params={'trashed': True, 'offset': 3}).json()['items'] == []


def test_same_track_path_update_preserves_soft_trash_and_restore_survives_refresh(client):
    path = write_track(Path(main.DOWNLOADS_DIR) / 'before.wav')
    original, inode = path.read_bytes(), path.stat().st_ino
    assert client.post('/api/music/refresh').status_code == 200
    row, = client.get('/api/music/tracks').json()['items']
    base = f"/api/music/tracks/{row['id']}"
    assert client.post(base + '/trash', json={'version': row['version']}).status_code == 200
    renamed = path.with_name('after.wav')
    path.rename(renamed)
    index = MusicLibrary(main.SPOTDL_CONFIG_DIR, main.DOWNLOADS_DIR)
    updated = index.update_path(row['id'], 'after.wav')
    assert updated['trashed'] is True
    assert index.refresh()['skipped'] == 0
    hidden, = index.list_tracks(trashed=True)['items']
    assert hidden['id'] == row['id'] and hidden['relative_path'] == 'after.wav'
    assert index.list_tracks()['total'] == 0
    response = client.post(base + '/restore', json={'version': hidden['version']})
    assert response.status_code == 200
    assert response.json()['status'] == 'restored'
    assert renamed.stat().st_ino == inode and renamed.read_bytes() == original
    assert client.post('/api/music/refresh').status_code == 200
    restarted = MusicLibrary(main.SPOTDL_CONFIG_DIR, main.DOWNLOADS_DIR)
    visible, = restarted.list_tracks()['items']
    assert visible['id'] == row['id'] and visible['trashed'] is False
    assert restarted.list_tracks(trashed=True)['total'] == 0


def test_existing_index_without_trash_column_migrates_without_losing_track_decisions(tmp_path):
    config, music = tmp_path / 'config', tmp_path / 'music'
    config.mkdir()
    path = write_track(music / 'existing.wav')
    record = {'id': 'existing-track', 'relative_path': 'existing.wav', 'version': file_version(path),
              'title': 'Song', 'artist': 'Artist', 'album': 'Album', 'needs_album': False,
              'album_source': 'fallback', 'album_accepted': True}
    with sqlite3.connect(config / 'music-library.sqlite3') as database:
        database.execute('''CREATE TABLE music_tracks (
            id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,version TEXT NOT NULL,data TEXT NOT NULL,
            search TEXT NOT NULL,incomplete INTEGER NOT NULL,decision_album TEXT NOT NULL DEFAULT '',
            decision_source TEXT NOT NULL DEFAULT '',accepted INTEGER NOT NULL DEFAULT 0)''')
        database.execute('INSERT INTO music_tracks VALUES(?,?,?,?,?,?,?,?,?)',
                         (record['id'], record['relative_path'], record['version'], json.dumps(record),
                          'song artist album', 0, 'Album', 'fallback', 1))
    migrated = MusicLibrary(config, music)
    visible, = migrated.list_tracks()['items']
    assert visible['id'] == 'existing-track' and visible['trashed'] is False
    assert visible['album_accepted'] is True
    assert migrated.refresh()['skipped'] == 0
    preserved = migrated.get_track('existing-track')
    assert preserved['album_accepted'] is True
    assert preserved['album_source'] == 'fallback'
    assert preserved['needs_album'] is False
    assert migrated.list_tracks(trashed=True)['total'] == 0


def test_permanently_deleted_imported_original_is_not_resurrected_by_real_beets(tmp_path, offline_beets):
    from backend import beets_importer
    from backend.job_store import JobStore
    from backend.music_trash import MusicTrash

    source = audio_file(tmp_path / 'incoming' / 'song.wav')
    config, music = tmp_path / 'config', tmp_path / 'music'
    options = {'source_dir': str(source.parent), 'dest_dir': str(music), 'config_dir': str(config)}
    assert beets_importer.import_paths([str(source)], **options) == {'organized': 1, 'skipped': 0, 'errors': []}
    original = source.read_bytes()
    index = MusicLibrary(config, music)
    index.refresh()
    row, = index.list_tracks()['items']
    store = JobStore(config / 'jobs.sqlite3')
    service = MusicTrash(index)
    assert service.move(row['id'], row['version'], store=store)['status'] == 'trashed'
    assert service.delete(row['id'], row['version'], store=store)['status'] == 'deleted'
    assert source.read_bytes() == original
    with source.open('ab') as audio:
        audio.write(b'changed-original')
    assert beets_importer.import_paths([str(source)], retry=True, **options) == {'organized': 0, 'skipped': 1, 'errors': []}
    assert beets_importer.list_review(config) == []
    assert beets_importer.enrich_pending(config_dir=str(config), dest_dir=str(music), retry=True)['errors'] == []
    assert not list(music.rglob('*.wav'))
    assert index.refresh()['total'] == 0


def test_delete_retry_syncs_the_parent_after_an_interrupted_unlink(client, monkeypatch):
    from backend import music_trash

    source = write_track(Path(main.DOWNLOADS_DIR) / 'sync.wav')
    assert client.post('/api/music/refresh').status_code == 200
    row, = client.get('/api/music/tracks').json()['items']
    base, payload = f"/api/music/tracks/{row['id']}", {'version': row['version']}
    assert client.post(base + '/trash', json=payload).status_code == 200
    service = main.get_music_trash()
    store = main.get_job_store()

    def interrupt_sync(path):
        raise OSError('directory sync interrupted')

    monkeypatch.setattr(music_trash, '_sync_directory', interrupt_sync)
    with pytest.raises(OSError, match='directory sync interrupted'):
        service.delete(row['id'], row['version'], store=store)
    assert not source.exists()
    synced = []
    monkeypatch.setattr(music_trash, '_sync_directory', synced.append)
    assert service.delete(row['id'], row['version'], store=store)['status'] == 'deleted'
    assert source.parent in synced
