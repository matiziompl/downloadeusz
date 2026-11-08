from types import SimpleNamespace
import pytest

from backend import main
from backend import acquisition, job_store
from backend.tests.test_durable_workflow import worker, enqueue_tracks, tick


def test_permanent_youtube_error_is_visible_and_not_retried(worker, monkeypatch):
    message = "ERROR: [youtube] 7I5xCGOJiG0: Video unavailable. This video is private"
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(
        stdout=iter([message + "\n"]), returncode=1, wait=lambda: 1))
    store, job = enqueue_tracks([{"url": "https://www.youtube.com/watch?v=7I5xCGOJiG0", "source_tool": "ytdl"}])
    tick(job["id"])
    row, = store.tracks(job["id"])
    assert row["status"] == "failed"
    assert "Video unavailable" in row["error"]
    assert "Video unavailable" in (main.Path(main.LOGS_DIR) / (job["id"] + ".log")).read_text()
    assert tick(job["id"])["status"] == "needs_review"


def test_temporary_download_error_is_visible_with_backoff(worker, monkeypatch):
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(
        stdout=iter(["ERROR: HTTP Error 503: Service Unavailable\n"]), returncode=1, wait=lambda: 1))
    store, job = enqueue_tracks([{"url": "https://media.invalid/song", "source_tool": "ytdl"}])
    tick(job["id"])
    row, = store.tracks(job["id"])
    assert row["status"] == "retry"
    assert "503" in row["error"]
    assert row["next_attempt"] > main.time.time()


@pytest.mark.parametrize("beets, audio", [(False, True), (True, False)])
def test_permanent_error_also_stops_legacy_and_video_jobs(worker, monkeypatch, beets, audio):
    monkeypatch.setattr(main, "BEETS_ENABLED", beets)
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(
        stdout=iter(["ERROR: Video unavailable\n"]), returncode=1, wait=lambda: 1))
    store = main.get_job_store()
    job = store.enqueue("download", {"tool": "ytdl", "url": "https://media.invalid/video", "flags": {"audio_only": audio}})
    main.run_pipeline_job(job["id"])
    assert store.get(job["id"])["status"] == "failed"
    assert "Video unavailable" in store.get(job["id"])["error"]
    assert store.claim(job["id"]) is None


def test_cancel_downloader_after_stdout_has_closed(worker, tmp_path):
    import concurrent.futures
    import io
    import subprocess
    import sys
    import threading

    store = main.get_job_store()
    job = store.enqueue("download", {})
    stdout_closed = threading.Event()
    process = subprocess.Popen([sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(60)"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    def monitor():
        with job_store.cancellation_scope(store, job["id"]):
            with pytest.raises(job_store.JobCancelled):
                acquisition.log_process(process, io.StringIO(), timeout=30)
    # Wrap only stdout so the real process and its wait/termination stay under test.
    original_stdout = process.stdout
    class WrappedOutput:
        def __iter__(self):
            yield from original_stdout
            stdout_closed.set()
        def close(self):
            original_stdout.close()
    process.stdout = WrappedOutput()
    with concurrent.futures.ThreadPoolExecutor(1) as executor:
        future = executor.submit(monitor)
        try:
            assert stdout_closed.wait(5)
            store.cancel(job["id"])
            future.result(timeout=3)
        finally:
            acquisition.terminate_process(process)
