"""Small durable work queue; beets remains the owner of music metadata."""

import json
import sqlite3
import time
import uuid
import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path


class JobCancelled(BaseException):
    """Control flow: cancellation must bypass operation retry/error handlers."""


_CURRENT_JOB = threading.local()


@contextmanager
def cancellation_scope(store, identity):
    previous = getattr(_CURRENT_JOB, "value", None)
    context = {"store": store, "identity": identity}
    _CURRENT_JOB.value = context
    try:
        yield context
    finally:
        _CURRENT_JOB.value = previous


def check_cancelled():
    context = getattr(_CURRENT_JOB, "value", None)
    if context and context["identity"]:
        job = context["store"].get(context["identity"])
        if job and job["status"] == "cancelled":
            raise JobCancelled()


class JobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Nieprawidłowy plik bazy kolejki")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, dedup_key TEXT UNIQUE,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    stage TEXT NOT NULL DEFAULT 'queued', result TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,next_attempt,created);
                CREATE TABLE IF NOT EXISTS tracks (
                    job_id TEXT NOT NULL REFERENCES jobs(id), position INTEGER NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                    failures INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(job_id,position)
                );
                CREATE TABLE IF NOT EXISTS source_files (
                    service TEXT NOT NULL, source_key TEXT NOT NULL, files TEXT NOT NULL,
                    PRIMARY KEY(service,source_key)
                );
                CREATE TABLE IF NOT EXISTS queue_control (
                    id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO queue_control(id,paused) VALUES(1,0);
            """)
            if "failures" not in {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN failures INTEGER NOT NULL DEFAULT 0")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tracks)")}
            for column in ("failures", "next_attempt"):
                if column not in columns:
                    db.execute(f"ALTER TABLE tracks ADD COLUMN {column} {'INTEGER' if column == 'failures' else 'REAL'} NOT NULL DEFAULT 0")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        item = dict(row)
        for key in ("payload", "result"):
            if key in item:
                item[key] = json.loads(item[key])
        return item

    def enqueue(self, kind, payload, *, key=None, job_id=None, initial_status="queued", not_before=0, automatic=False):
        if initial_status not in {"queued", "waiting"}:
            raise ValueError("Unsupported initial job status")
        identity, now = job_id or str(uuid.uuid4()), time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if automatic and db.execute("SELECT paused FROM queue_control WHERE id=1").fetchone()[0]:
                return None
            if not automatic:
                db.execute("UPDATE queue_control SET paused=0 WHERE id=1")
            db.execute("INSERT OR IGNORE INTO jobs(id,kind,dedup_key,payload,status,next_attempt,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                       (identity, kind, key, json.dumps(payload, ensure_ascii=False), initial_status, not_before, now, now))
            row = db.execute("SELECT * FROM jobs WHERE dedup_key=?" if key else "SELECT * FROM jobs WHERE id=?",
                             (key or identity,)).fetchone()
        return self.decode(row)

    def get(self, identity):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone())

    def list(self, limit=50):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,))]

    def summary(self):
        with self.connect() as db:
            return {row["status"]: row["count"] for row in db.execute("SELECT status,COUNT(*) count FROM jobs GROUP BY status")}

    def claim(self, identity=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM jobs WHERE status IN ('queued','waiting') AND next_attempt<=?"
            args = [time.time()]
            if identity:
                query += " AND id=?"
                args.append(identity)
            row = db.execute(query + " ORDER BY updated,created LIMIT 1", args).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated=? WHERE id=?", (time.time(), row["id"]))
            return self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def update(self, identity, **values):
        check_cancelled()
        allowed = {"status", "stage", "result", "error", "next_attempt", "payload", "failures"}
        if not values or set(values) - allowed:
            raise ValueError("Unsupported job update")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT status FROM jobs WHERE id=?", (identity,)).fetchone()
            if previous and previous["status"] == "cancelled":
                return
            if "stage" in values or values.get("status") in {"done", "failed", "needs_review"}:
                row = db.execute("SELECT result FROM jobs WHERE id=?", (identity,)).fetchone()
                progress = dict(values.get("result", json.loads(row["result"]) if row else {}))
                before = dict(progress)
                if values.get("stage") != "searching":
                    progress.pop("search", None)
                if (values.get("stage") in {"resolving", "finalizing", "normalizing", "scanning", "playlist", "done"}
                        or values.get("status") in {"done", "failed", "needs_review"}):
                    progress.pop("current_track", None)
                if progress != before:
                    values["result"] = progress
            fields = {**values, "updated": time.time()}
            for key in ("result", "payload"):
                if key in fields:
                    fields[key] = json.dumps(fields[key], ensure_ascii=False)
            db.execute("UPDATE jobs SET " + ",".join(key + "=?" for key in fields) + " WHERE id=?", (*fields.values(), identity))

    def recover(self):
        # Call only while holding the worker flock: no other executor may be active.
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='queued',updated=? WHERE status='running'", (time.time(),))

    def set_tracks(self, identity, tracks):
        check_cancelled()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM jobs WHERE id=?", (identity,)).fetchone()[0] == "cancelled":
                return
            db.executemany("INSERT OR IGNORE INTO tracks(job_id,position,payload) VALUES(?,?,?)",
                           ((identity, index, json.dumps(track, ensure_ascii=False)) for index, track in enumerate(tracks)))

    def tracks(self, identity):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM tracks WHERE job_id=? ORDER BY position", (identity,))]

    def update_track(self, identity, position, *, status, result=None, error="", failures=None, next_attempt=None):
        check_cancelled()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM jobs WHERE id=?", (identity,)).fetchone()[0] == "cancelled":
                return
            previous = db.execute("SELECT status,failures,next_attempt FROM tracks WHERE job_id=? AND position=?",
                                  (identity, position)).fetchone()
            if previous is None:
                return
            explicit_retry = previous["status"] in {"failed", "needs_review", "cancelled"} and status in {"queued", "transferring"}
            failures = (0 if explicit_retry else previous["failures"]) if failures is None else failures
            next_attempt = (0 if explicit_retry or status not in {"retry", "transferring"} else previous["next_attempt"]) if next_attempt is None else next_attempt
            db.execute("UPDATE tracks SET status=?,result=?,error=?,failures=?,next_attempt=? WHERE job_id=? AND position=?",
                       (status, json.dumps(result or {}, ensure_ascii=False), error, failures, next_attempt, identity, position))

    def fail_track(self, identity, position, error, result=None):
        check_cancelled()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM jobs WHERE id=?", (identity,)).fetchone()[0] == "cancelled":
                return
            row = db.execute("SELECT * FROM tracks WHERE job_id=? AND position=?", (identity, position)).fetchone()
            failures = row["failures"] + 1
            next_attempt = time.time() + min(3600, 30 * 2 ** min(failures - 1, 7))
            db.execute("UPDATE tracks SET status=?,failures=?,next_attempt=?,error=?,result=? WHERE job_id=? AND position=?",
                       ("failed" if failures >= 8 else "retry", failures, next_attempt, str(error),
                        json.dumps(result if result is not None else json.loads(row["result"]), ensure_ascii=False), identity, position))

    def cancel(self, identity=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if identity is None:
                db.execute("UPDATE queue_control SET paused=1 WHERE id=1")
            query = "SELECT id FROM jobs WHERE status IN ('queued','running','waiting')"
            rows = db.execute(query + (" AND id=?" if identity else ""), (identity,) if identity else ()).fetchall()
            for row in rows:
                db.execute("UPDATE jobs SET status='cancelled',error='',next_attempt=0,updated=? WHERE id=?", (time.time(), row["id"]))
                db.execute("UPDATE tracks SET status='cancelled',error='',next_attempt=0 WHERE job_id=? "
                           "AND status IN ('queued','running','waiting','retry','transferring','downloaded')", (row["id"],))
            return len(rows)

    def retry(self, identity):
        """The caller owns the worker lock; serialize against cancellation too."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone())
            acquire_again = False
            for row in db.execute("SELECT * FROM tracks WHERE job_id=?", (identity,)).fetchall():
                track = self.decode(row)
                if track["status"] not in {"failed", "needs_review", "cancelled"}:
                    continue
                acquire_again = True
                result = dict(track["result"])
                uncertain = result.get("selected") and result.get("state") == "needs_review" and not result.get("retryable")
                if uncertain:
                    result.setdefault("requested_at", time.time())
                db.execute("UPDATE tracks SET status=?,result=?,error='',failures=0,next_attempt=0 WHERE job_id=? AND position=?",
                           ("transferring" if uncertain else "queued", json.dumps(result, ensure_ascii=False), identity, track["position"]))
            progress = dict(item["result"])
            if acquire_again:
                for key in ("normalized", "normalization_id", "scan_started", "search", "current_track"):
                    progress.pop(key, None)
            db.execute("UPDATE jobs SET status='queued',stage=?,result=?,next_attempt=0,failures=0,error='',updated=? WHERE id=?",
                       ("queued" if acquire_again else item["stage"], json.dumps(progress, ensure_ascii=False), time.time(), identity))
            db.execute("UPDATE queue_control SET paused=0 WHERE id=1")

    def remember_source(self, service, source_key, files):
        with self.connect() as db:
            db.execute("INSERT INTO source_files(service,source_key,files) VALUES(?,?,?) "
                       "ON CONFLICT(service,source_key) DO UPDATE SET files=excluded.files",
                       (service, source_key, json.dumps(files, ensure_ascii=False)))

    def source_files(self, service, source_key):
        with self.connect() as db:
            row = db.execute("SELECT files FROM source_files WHERE service=? AND source_key=?", (service, source_key)).fetchone()
            return json.loads(row["files"]) if row else []
