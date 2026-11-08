"""Acquisition failures stay isolated and durable across imports and restarts."""
import json
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main, soulseek_search
from backend.job_store import JobStore
from backend.tests.test_durable_workflow import worker, enqueue_tracks, write_audio


def tick(identity):
    store = main.get_job_store()
    store.update(identity, next_attempt=0)
    main.run_pipeline_job(identity)
    return store.get(identity)


def run_due(store, identity):
    store.update(identity, next_attempt=0)
    with store.connect() as db:
        if "next_attempt" in {row["name"] for row in db.execute("PRAGMA table_info(tracks)")}:
            db.execute("UPDATE tracks SET next_attempt=0 WHERE job_id=?", (identity,))
    main.run_pipeline_job(identity)
    return store.get(identity)


def settle(store, identity, limit=40):
    for _ in range(limit):
        current = run_due(store, identity)
        if current['status'] in {'done', 'failed', 'needs_review'}:
            return current
    pytest.fail(str(current))


def test_first_unavailable_track_does_not_starve_good_tracks(worker):
    bad, good = 'https://media.invalid/bad', 'https://media.invalid/good'
    store, job = enqueue_tracks([{'url': bad, 'source_tool': 'ytdl'}, {'url': good, 'source_tool': 'ytdl'}])
    worker.failures[bad] = 100
    tick(job['id'])
    assert store.tracks(job['id'])[0]['status'] == 'retry'
    assert tick(job['id'])['status'] == 'waiting'
    assert store.tracks(job['id'])[1]['status'] == 'imported'
    assert worker.downloads == [bad, good]
    assert settle(store, job['id'])['status'] == 'needs_review'
    rows = JobStore(store.path).tracks(job['id'])
    assert rows[0]['failures'] == 8
    assert rows[1]['status'] == 'imported'
    assert worker.downloads.count(bad) == 8
    assert store.get(job['id'])['failures'] == 0


def test_track_backoff_is_durable_and_explicit_retry_resets_only_unfinished(worker, monkeypatch):
    bad = 'https://media.invalid/bad'
    worker.failures[bad] = 100
    store, job = enqueue_tracks([{'url': bad, 'source_tool': 'ytdl'}])
    tick(job['id'])
    row, = JobStore(store.path).tracks(job['id'])
    assert row['failures'] == 1
    assert row['next_attempt'] > time.time()
    tick(job['id'])
    assert worker.downloads == [bad]
    assert settle(store, job['id'])['status'] == 'needs_review'
    monkeypatch.setattr(main, 'run_pipeline_job', lambda *_: None)
    assert TestClient(main.app).post(f"/api/jobs/{job['id']}/retry").status_code == 200
    row, = store.tracks(job['id'])
    assert (row['status'], row['failures'], row['next_attempt']) == ('queued', 0, 0)


def test_completed_source_replay_uses_verified_existing_copy(worker):
    url = 'https://media.invalid/first'
    track = {'url': url, 'source_tool': 'ytdl'}
    store, first = enqueue_tracks([track], flags={'audio_only': True, 'skip_downloaded': True})
    assert settle(store, first['id'])['status'] == 'done'
    destination = Path(main.DOWNLOADS_DIR) / 'Artist/first.wav'
    original = destination.stat().st_mtime_ns
    # Container metadata may change without changing the recorded audio identity.
    with destination.open('ab') as output:
        output.write(b'metadata edit')
    store, second = enqueue_tracks([track], flags={'audio_only': True, 'skip_downloaded': True})
    assert settle(store, second['id'])['status'] == 'done'
    row, = store.tracks(second['id'])
    assert row['result']['paths'] == ['Artist/first.wav']
    assert worker.downloads == [url]
    assert len(worker.copies) == 1
    assert destination.stat().st_mtime_ns >= original


@pytest.mark.parametrize('changed', [False, True])
def test_unrecoverable_archive_mapping_downloads_without_global_archive(worker, monkeypatch, changed):
    url = 'https://media.invalid/first'
    track = {'url': url, 'source_tool': 'ytdl'}
    if changed:
        store, previous = enqueue_tracks([track], flags={'audio_only': True, 'skip_downloaded': True})
        assert settle(store, previous['id'])['status'] == 'done'
        destination = Path(main.DOWNLOADS_DIR) / 'Artist/first.wav'
        with wave.open(str(destination), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
            audio.writeframes(b'\1\0' * 800)
    original = subprocess.Popen
    commands = []
    def popen(command, **kwargs):
        commands.append(command)
        # The global archive would skip to an empty inbox.
        if '--download-archive' in command:
            from types import SimpleNamespace
            return SimpleNamespace(stdout=iter([]), returncode=0, wait=lambda: 0)
        return original(command, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    store, job = enqueue_tracks([track], flags={'audio_only': True, 'skip_downloaded': True})
    assert settle(store, job['id'])['status'] == 'done'
    assert commands and '--download-archive' not in commands[0]
    assert store.tracks(job['id'])[0]['result']['paths'] == ['Artist/first.wav']


def test_skip_downloaded_false_reacquires_even_a_known_source(worker):
    track = {'url': 'https://media.invalid/first', 'source_tool': 'ytdl'}
    store, first = enqueue_tracks([track], flags={'audio_only': True, 'skip_downloaded': True})
    assert settle(store, first['id'])['status'] == 'done'
    store, second = enqueue_tracks([track], flags={'audio_only': True, 'skip_downloaded': False})
    assert settle(store, second['id'])['status'] == 'done'
    assert len(worker.downloads) == 2


def test_spotify_album_is_imported_as_one_group_and_sources_replay(worker, monkeypatch):
    original = subprocess.run
    album_inputs = []
    def run(command, **kwargs):
        if Path(command[0]).name != 'beet' or 'import' not in command:
            return original(command, **kwargs)
        source = Path(command[-1])
        if source.is_file():
            pytest.fail('Album track was imported as a singleton')
        album_inputs.append(source)
        assert '-s' not in command
        files = sorted(source.rglob('*.wav'))
        assert len(files) == 2
        records = []
        for file in files:
            destination = Path(main.DOWNLOADS_DIR) / 'Artist' / file.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, destination)
            worker.copies.append(str(destination))
            records.append({'event': 'imported', 'source': str(file), 'destination': str(destination),
                            'metadata_confirmed': True, 'release_confirmed': True, 'reason': 'Album', 'candidates': []})
        Path(kwargs['env']['DOWNLOADEUSZ_BEETS_REPORT']).write_text('\n'.join(json.dumps(row) for row in records))
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', run)
    tracks = [{'url': f'https://open.spotify.com/track/{name}', 'source_tool': 'spotdl',
               'album_name': 'Whole Album', 'artists': ['Artist'], 'title': name} for name in ('first', 'second')]
    store, job = enqueue_tracks(tracks, tool='spotdl', flags={'skip_downloaded': True})
    tick(job['id'])
    assert worker.copies == []
    assert not list(Path(main.BEETS_INBOX_DIR).rglob('.ready'))
    assert settle(store, job['id'])['status'] == 'done'
    assert len(album_inputs) == 1
    rows = store.tracks(job['id'])
    assert all(row['status'] == 'imported' and len(row['result']['paths']) == 1 for row in rows)
    assert rows[0]['result']['paths'] != rows[1]['result']['paths']
    store, replay = enqueue_tracks(tracks, tool='spotdl', flags={'skip_downloaded': True})
    assert settle(store, replay['id'])['status'] == 'done'
    assert len(worker.downloads) == len(worker.copies) == 2


def test_rejected_soulseek_peer_is_durably_excluded(worker, monkeypatch):
    monkeypatch.setattr(main.accounts, 'get_active_slskd_account', lambda *_: {'server': 'http://slskd'})
    searches = []
    def search(track, **kwargs):
        searches.append(list(track['excluded_usernames']))
        if 'rejected-peer' not in track['excluded_usernames']:
            return {'state': 'needs_review', 'selected': {'username': 'rejected-peer', 'filename': 'Artist/Album/Song.flac'},
                    'retryable': True, 'message': 'enqueue rejected'}
        return {'state': 'queued', 'selected': {'username': 'other-peer', 'filename': 'Artist/Album/Song.flac'},
                'transfer_ids': ['other-transfer'], 'retryable': False}
    monkeypatch.setattr(soulseek_search, 'search_and_queue', search)
    store, job = enqueue_tracks([{'title': 'Song', 'artist': 'Artist'}], tool='soulseek')
    tick(job['id'])
    row, = JobStore(store.path).tracks(job['id'])
    assert row['result']['excluded_usernames'] == ['rejected-peer']
    run_due(store, job['id'])
    row, = store.tracks(job['id'])
    assert row['status'] == 'transferring'
    assert row['result']['selected']['username'] == 'other-peer'
    assert searches == [[], ['rejected-peer']]


def test_empty_downloader_success_never_marks_inbox_ready(worker, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs:
                        SimpleNamespace(stdout=iter([]), returncode=0, wait=lambda: 0))
    result = main.run_download_task('empty', main.DownloadRequest(tool='ytdl', url='https://media.invalid/empty', flags={'audio_only': True}), defer_finalize=True)
    assert result['returncode'] != 0
    assert not (Path(main.BEETS_INBOX_DIR) / 'empty/.ready').exists()


def test_downloader_timeout_terminates_process_group_and_leaves_no_ready_marker(worker, monkeypatch):
    real_popen = subprocess.Popen
    # worker fixture wraps Popen; use a real Python process at the external downloader boundary.
    processes = []
    def spawn(command, **kwargs):
        process = real_popen([sys.executable, '-u', '-c', 'import time; print("started", flush=True); time.sleep(60)'], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    started = time.monotonic()
    result = main.run_download_task('timeout', main.DownloadRequest(tool='ytdl', url='https://media.invalid/timeout', flags={'audio_only': True}),
                                    defer_finalize=True, download_timeout=0.15)
    assert result['returncode'] != 0
    assert time.monotonic() - started < 5
    assert processes[0].poll() is not None
    assert not (Path(main.BEETS_INBOX_DIR) / 'timeout/.ready').exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_worker_lock_rejects_special_files_without_mutation(worker, kind):
    store = main.get_job_store()
    job = store.enqueue('download', {'tool': 'ytdl', 'url': 'https://media.invalid/video', 'flags': {}})
    lock = Path(str(store.path) + '.worker.lock')
    target = Path(main.SPOTDL_CONFIG_DIR) / 'precious'
    target.write_bytes(b'unchanged')
    if kind == 'symlink':
        lock.symlink_to(target)
    elif kind == 'hardlink':
        os.link(target, lock)
    else:
        os.mkfifo(lock)
    with pytest.raises((OSError, ValueError)):
        main.run_pipeline_job(job['id'])
    assert target.read_bytes() == b'unchanged'
    assert store.get(job['id'])['attempts'] == 0
    assert worker.downloads == []


def test_partial_album_imports_good_song_then_retry_preserves_its_copy(worker):
    tracks = [{'url': f'https://open.spotify.com/track/{name}', 'source_tool': 'spotdl',
               'album_name': 'Partial Album', 'artists': ['Artist']} for name in ('bad', 'good')]
    worker.failures[tracks[0]['url']] = 100
    store, job = enqueue_tracks(tracks, tool='spotdl')
    assert settle(store, job['id'])['status'] == 'needs_review'
    rows = store.tracks(job['id'])
    assert [row['status'] for row in rows] == ['failed', 'imported']
    destination = Path(main.DOWNLOADS_DIR) / rows[1]['result']['paths'][0]
    original = destination.read_bytes(), destination.stat().st_mtime_ns
    assert worker.downloads.count(tracks[1]['url']) == 1
    worker.failures.clear()
    assert TestClient(main.app).post(f"/api/jobs/{job['id']}/retry").status_code == 200
    assert settle(store, job['id'])['status'] == 'done'
    assert (destination.read_bytes(), destination.stat().st_mtime_ns) == original
    assert len(worker.copies) == 2
    assert worker.downloads.count(tracks[1]['url']) == 1


def test_source_reuse_waits_for_library_write_lock(worker):
    import concurrent.futures
    import fcntl
    track = {'url': 'https://media.invalid/first', 'source_tool': 'ytdl'}
    store, first = enqueue_tracks([track], flags={'audio_only': True, 'skip_downloaded': True})
    assert settle(store, first['id'])['status'] == 'done'
    store, replay = enqueue_tracks([track], flags={'audio_only': True, 'skip_downloaded': True})
    with (Path(main.DOWNLOADS_DIR) / '.downloadeusz-write.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with concurrent.futures.ThreadPoolExecutor(1) as executor:
            future = executor.submit(tick, replay['id'])
            try:
                time.sleep(0.1)
                assert not future.done(), 'Cached audio was inspected while the library was being written'
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
            future.result(timeout=5)
    assert settle(store, replay['id'])['status'] == 'done'
    assert len(worker.downloads) == 1


def test_shutdown_terminates_active_downloader_and_its_descendant(worker, monkeypatch):
    import concurrent.futures
    from backend import acquisition
    real_popen = subprocess.Popen
    processes = []
    pidfile = Path(main.SPOTDL_CONFIG_DIR) / 'child.pid'
    script = ("import subprocess,sys,time; "
              "child=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
              f"open({str(pidfile)!r},'w').write(str(child.pid)); "
              "print('started',flush=True); time.sleep(60)")
    def spawn(command, **kwargs):
        process = real_popen([sys.executable, '-u', '-c', script], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with concurrent.futures.ThreadPoolExecutor(1) as executor:
        future = executor.submit(main.run_download_task, 'shutdown', main.DownloadRequest(
            tool='ytdl', url='https://media.invalid/shutdown', flags={'audio_only': True}), True)
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert pidfile.exists()
            acquisition.stop_active_downloads()
            result = future.result(timeout=5)
            assert result['returncode'] != 0
        finally:
            if processes and processes[0].poll() is None:
                acquisition.terminate_process(processes[0])
    assert processes[0].poll() is not None
    child_state = Path('/proc') / pidfile.read_text() / 'stat'
    assert not child_state.exists() or child_state.read_text().split()[2] == 'Z'
    assert not (Path(main.BEETS_INBOX_DIR) / 'shutdown/.ready').exists()


def test_album_rename_recovery_does_not_redownload_staged_children(worker, monkeypatch):
    from backend import acquisition
    tracks = [{'url': f'https://open.spotify.com/track/{name}', 'source_tool': 'spotdl',
               'album_name': 'Interrupted Album', 'artists': ['Artist']} for name in ('first', 'second')]
    store, job = enqueue_tracks(tracks, tool='spotdl')
    run_due(store, job['id'])
    run_due(store, job['id'])
    real_rename = Path.rename
    interrupted = []
    def rename(source, target):
        result = real_rename(source, target)
        if not interrupted:
            interrupted.append(str(result))
            raise OSError('worker interrupted after first atomic move')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'rename', rename)
        run_due(store, job['id'])
    # Prove the staged child survived the interruption before any import attempt.
    assert interrupted and Path(interrupted[0]).is_file()
    assert all(row['status'] == 'downloaded' for row in JobStore(store.path).tracks(job['id']))
    # The standard worker's external beet double accepts files only; inspect group
    # with the importer rather than replacing its persistent state transitions.
    original_run = subprocess.run
    def run(command, **kwargs):
        if Path(command[0]).name != 'beet' or 'import' not in command or Path(command[-1]).is_file():
            return original_run(command, **kwargs)
        source = Path(command[-1])
        records = []
        for audio in source.rglob('*.wav'):
            destination = Path(main.DOWNLOADS_DIR) / 'Artist' / audio.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(audio, destination)
            worker.copies.append(str(destination))
            records.append({'event': 'imported', 'source': str(audio), 'destination': str(destination),
                            'metadata_confirmed': True, 'release_confirmed': True, 'reason': 'Album', 'candidates': []})
        Path(kwargs['env']['DOWNLOADEUSZ_BEETS_REPORT']).write_text('\n'.join(json.dumps(row) for row in records))
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', run)
    assert settle(store, job['id'])['status'] == 'done'
    assert len(worker.downloads) == len(worker.copies) == 2


def test_manual_review_ready_source_gets_one_attempt_then_needs_review(worker):
    import hashlib
    worker.review = True
    source = write_audio(Path(main.BEETS_INBOX_DIR) / 'review-batch/unknown.wav')
    (source.parent / '.ready').touch()
    item_id = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    state_path = Path(main.SPOTDL_CONFIG_DIR) / 'beets/library-state.json'
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'version': 1, 'tracks': {item_id: {
        'id': item_id, 'source': str(source), 'status': 'needs_review', 'imported': False,
        'metadata_confirmed': False, 'release_confirmed': False, 'next_attempt': time.time() + 86400,
        'reason': 'Recording is still ambiguous',
    }}}))
    store = main.get_job_store()
    job = store.enqueue('import', {'review_item_id': item_id, 'retry': True})
    tick(job['id'])
    current = store.get(job['id'])
    assert current['status'] == 'needs_review'
    assert 'ambiguous' in current['error'] or 'tag' in current['error'].lower()
    attempts = current['attempts']
    tick(job['id'])
    assert store.get(job['id'])['attempts'] == attempts
