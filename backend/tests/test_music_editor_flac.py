"""Real FLAC queue regressions: Vorbis comments do not implement dict.pop(key, default)."""

import json
import subprocess

import pytest
from mediafile import MediaFile
from mutagen.flac import FLAC

from backend import main, music_editor
from backend.audio_identity import audio_packet_signature
from backend.music_library import MusicLibrary
from backend.tests.test_music_library import write_track


@pytest.mark.parametrize('action', ['title', 'musicbrainz'])
@pytest.mark.parametrize('existing_marker', [False, True])
def test_flac_metadata_job_finishes_and_preserves_audio(tmp_path, monkeypatch, action, existing_marker):
    config, library_dir, logs = tmp_path / 'config', tmp_path / 'music', tmp_path / 'logs'
    for directory in (config, library_dir, logs):
        directory.mkdir()
    source = write_track(tmp_path / 'source.wav', album='Old Album')
    original = library_dir / 'song.flac'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-c:a', 'flac', str(original)],
                   check=True, capture_output=True, timeout=20)
    if existing_marker:
        audio = FLAC(original)
        audio['downloadeusz_album_fallback'] = [json.dumps({'version': 1, 'album': 'Old Album'})]
        audio.save()
    signature = audio_packet_signature(original)
    original_source = source.read_bytes()
    library = MusicLibrary(config, library_dir)
    library.refresh()
    track, = library.list_tracks()['items']
    editor = music_editor.MusicEditor(library)
    if action == 'musicbrainz':
        choice = {'kind': 'track', 'title': 'Selected', 'artist': 'Band', 'album': 'Record',
                  '_track': {'title': 'Selected', 'artist': 'Band', 'index': 2, 'medium_index': 2, 'medium': 1,
                             'track_id': '22222222-2222-4222-8222-222222222222'},
                  '_album': {'album': 'Record', 'artist': 'Band', 'year': 2020,
                             'album_id': '11111111-1111-4111-8111-111111111111'},
                  '_album_track_count': 5}
        monkeypatch.setattr(music_editor.musicbrainz_metadata, 'resolve', lambda _: [choice])
        monkeypatch.setattr(music_editor.album_art, 'enrich_artwork', lambda *args, **kwargs: False)
        choices = editor.preview(track['id'], url='https://musicbrainz.org/release/11111111-1111-4111-8111-111111111111')
        preview = editor.preview(track['id'], candidate_key=choices['choices'][0]['key'])
    else:
        preview = editor.preview(track['id'], action='title')
    for name, value in [('SPOTDL_CONFIG_DIR', config), ('DOWNLOADS_DIR', library_dir), ('LOGS_DIR', logs)]:
        monkeypatch.setattr(main, name, str(value))
    monkeypatch.setattr(main, 'NORMALIZATION_ENABLED', False)
    monkeypatch.setattr(main, 'get_music_editor', lambda: editor)
    store = main.get_job_store()
    job = store.enqueue('music_edit', {'track_id': track['id'], 'preview_token': preview['preview_token']})
    main.run_pipeline_job(job['id'])
    completed = store.get(job['id'])
    assert completed['status'] == 'done', completed['error']
    assert completed['failures'] == 0
    assert completed['result']['paths'] == [preview['destination']]
    output = library.path(track['id'])
    assert output.suffix == '.flac'
    assert audio_packet_signature(output) == signature
    assert source.read_bytes() == original_source
    assert not original.exists()
    tags = FLAC(output)
    if action == 'title':
        marker = json.loads(tags['downloadeusz_album_fallback'][0])
        assert marker['accepted'] is True
        assert MediaFile(output).album == 'Song'
        assert library.get_track(track['id'])['album_accepted'] is True
    else:
        assert 'downloadeusz_album_fallback' not in tags
        assert MediaFile(output).album == 'Record'
        assert MediaFile(output).title == 'Selected'
        assert MediaFile(output).mb_albumid == '11111111-1111-4111-8111-111111111111'
