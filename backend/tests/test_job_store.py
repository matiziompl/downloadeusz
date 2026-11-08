import concurrent.futures
import time

from backend.job_store import JobStore


def test_restart_retains_track_order_and_finished_progress(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("download", {"url": "example"}, key="request-1")
    store.set_tracks(job["id"], [{"title": "First"}, {"title": "Second"}, {"title": "First"}])
    store.update_track(job["id"], 0, status="done", result={"path": "first.mp3"})
    reopened = JobStore(tmp_path / "jobs.db")
    assert reopened.enqueue("download", {}, key="request-1")["id"] == job["id"]
    tracks = reopened.tracks(job["id"])
    assert [item["payload"]["title"] for item in tracks] == ["First", "Second", "First"]
    assert tracks[0]["status"] == "done"
    assert tracks[0]["result"] == {"path": "first.mp3"}


def test_atomic_claim_and_retry_delay(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("import", {})
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: store.claim(), range(2)))
    assert sum(item is not None for item in results) == 1
    store.update(job["id"], status="waiting", next_attempt=time.time() + 60)
    assert store.claim() is None
    store.update(job["id"], next_attempt=0)
    assert store.claim()["id"] == job["id"]


def test_only_interrupted_jobs_are_recovered(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    first = store.enqueue("download", {})
    second = store.enqueue("download", {})
    store.claim(first["id"])
    store.update(second["id"], status="done")
    store.recover()
    assert store.get(first["id"])["status"] == "queued"
    assert store.get(second["id"])["status"] == "done"


def test_bulk_queue_handles_ten_thousand_tracks(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    job = store.enqueue("download", {})
    store.set_tracks(job["id"], [{"title": str(index)} for index in range(10_000)])
    assert len(store.tracks(job["id"])) == 10_000
    assert store.summary()["queued"] == 1
