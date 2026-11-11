from backend.job_store import JobStore


def test_move_remaps_durable_paths_without_touching_other_files(tmp_path):
    root = tmp_path / 'music'
    store = JobStore(tmp_path / 'jobs.db')
    old, new = 'Old/song.flac', 'Artist/Album/song.flac'
    store.remember_source('soulseek', 'source', [{'path': old, 'audio_signature': 'audio'}])
    job = store.enqueue('download', {})
    store.set_tracks(job['id'], [{'title': 'Song'}])
    store.update(job['id'], result={'paths': [old, 'other.flac'], 'nested': {'destination': str(root / old)}})
    store.update_track(job['id'], 0, status='done', result={'paths': [old]})
    assert store.library_path_in_use(old, root)
    assert not store.library_path_in_use(old, root, exclude_job_id=job['id'])
    store.remap_library_path(old, new, root)
    assert store.source_files('soulseek', 'source') == [{'path': new, 'audio_signature': 'audio'}]
    assert store.get(job['id'])['result'] == {'paths': [new, 'other.flac'], 'nested': {'destination': str(root / new)}}
    assert store.tracks(job['id'])[0]['result']['paths'] == [new]
    assert not store.library_path_in_use(old, root)
    assert store.library_path_in_use(new, root)


def test_manually_accepted_album_survives_index_recreation(tmp_path):
    import json
    from mutagen.id3 import TXXX
    from mutagen.wave import WAVE
    from backend.music_library import MusicLibrary
    from backend.tests.test_music_library import write_track

    root = tmp_path / 'music'
    path = write_track(root / 'song.wav', title='Song', album='Song')
    audio = WAVE(path)
    audio.tags.add(TXXX(encoding=3, desc='DOWNLOADEUSZ_ALBUM_FALLBACK',
                        text=[json.dumps({'version': 1, 'album': 'Song', 'accepted': True})]))
    audio.save()
    library = MusicLibrary(tmp_path / 'fresh-config', root)
    library.refresh()
    track, = library.list_tracks()['items']
    assert track['album_accepted'] is True
    assert track['album_source'] == 'fallback'
    assert library.list_tracks(incomplete=True)['total'] == 0


def test_reused_old_path_does_not_replace_moved_track(tmp_path):
    from backend.music_library import MusicLibrary
    from backend.tests.test_music_library import write_track
    root = tmp_path / 'music'
    original = write_track(root / 'original.wav', title='First')
    library = MusicLibrary(tmp_path / 'config', root)
    library.refresh()
    first, = library.list_tracks()['items']
    original.rename(root / 'moved.wav')
    library.update_path(first['id'], 'moved.wav')
    write_track(root / 'original.wav', title='Second')
    library.refresh()
    tracks = library.list_tracks()['items']
    assert {track['title'] for track in tracks} == {'First', 'Second'}
    assert len({track['id'] for track in tracks}) == 2
    assert library.get_track(first['id'])['relative_path'] == 'moved.wav'


def test_index_does_not_reconcile_files_before_interrupted_edit_recovery(tmp_path):
    import pytest
    from backend.music_library import MusicLibrary
    from backend.tests.test_music_library import write_track
    root = tmp_path / 'music'
    original = write_track(root / 'song.wav')
    library = MusicLibrary(tmp_path / 'config', root)
    library.refresh()
    track, = library.list_tracks()['items']
    journal = library.config_dir / 'music-edits' / 'pending' / 'journal.json'
    journal.parent.mkdir(parents=True)
    journal.write_text('{}')
    (root / 'new.wav').write_bytes(original.read_bytes())
    with pytest.raises(ValueError, match='odzyskiwan'):
        library.refresh()
    assert library.get_track(track['id'])['relative_path'] == 'song.wav'
    assert library.list_tracks()['total'] == 1


def test_symlink_library_root_is_rejected_before_creating_lock(tmp_path):
    import pytest
    from backend.music_library import MusicLibrary
    outside = tmp_path / 'outside'
    outside.mkdir()
    linked = tmp_path / 'linked'
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        MusicLibrary(tmp_path / 'config', linked).refresh()
    assert list(outside.iterdir()) == []
