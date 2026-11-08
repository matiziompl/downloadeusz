import os
import json
import subprocess
import asyncio
import uuid
import urllib.parse
import time
import hashlib
import secrets
import glob
import re
import shutil
import threading
import fcntl
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from typing import Literal
from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

try:
    from backend import navidrome_dedup, slskd_organizer, accounts, spotify_meta, beets_importer, job_store, pipeline, documentation, normalizer_bridge
except ImportError:
    import navidrome_dedup
    import slskd_organizer
    import accounts
    import spotify_meta
    import beets_importer
    import job_store
    import pipeline
    import documentation
    import normalizer_bridge


@asynccontextmanager
async def lifespan(app):
    stop = asyncio.Event()
    timer = asyncio.create_task(beets_auto_loop(stop)) if BEETS_ENABLED and BEETS_AUTO_INTERVAL > 0 else None
    worker = asyncio.create_task(pipeline_worker(stop))
    try:
        yield
    finally:
        stop.set()
        try:
            from backend.acquisition import stop_active_downloads
        except ImportError:
            from acquisition import stop_active_downloads
        await asyncio.to_thread(stop_active_downloads)
        if timer:
            await timer
        await worker


APP_VERSION = "2.10"
app = FastAPI(lifespan=lifespan, version=APP_VERSION)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))
WALLPAPERS_DIR = os.getenv("WALLPAPERS_DIR", os.path.join(FRONTEND_DIR, "assets", "wallpapers"))
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "/downloads")
SPOTDL_CONFIG_DIR = os.getenv("SPOTDL_CONFIG_DIR", "/spotdl_config")
SLSKD_DOWNLOADS_DIR = os.getenv("SLSKD_DOWNLOADS_DIR", "/slskd_downloads")
BEETS_ENABLED = os.getenv("BEETS_ENABLED", "false").lower() in {"1", "true", "yes"}
BEETS_AUTO_INTERVAL = max(0, int(os.getenv("BEETS_AUTO_INTERVAL", "300")))
BEETS_INBOX_DIR = os.getenv("BEETS_INBOX_DIR", os.path.join(SPOTDL_CONFIG_DIR, "inbox"))
BEETS_NAVI_ACCOUNT_ID = os.getenv("BEETS_NAVI_ACCOUNT_ID", "")
BEETS_JOB_LOCK = threading.Lock()
BEETS_STATE = {"last_task_id": None, "last_error": None}
SLSKD_WEBHOOK_SECRET = os.getenv("SLSKD_WEBHOOK_SECRET", "")
NORMALIZATION_ENABLED = os.getenv("NORMALIZATION_ENABLED", "false").lower() in {"1", "true", "yes"}
NORMALIZATION_QUEUE_DIR = os.getenv("NORMALIZATION_QUEUE_DIR", os.path.join(SPOTDL_CONFIG_DIR, "normalization"))
PIPELINE_JOB_LOCK = threading.Lock()
LOGS_DIR = os.path.join(BASE_DIR, "logs")
PUID = int(os.getenv("PUID", "1000"))
PGID = int(os.getenv("PGID", "1000"))

# Ensure wallpaper and log directories exist
os.makedirs(WALLPAPERS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Set default umask to 0000 so new files and directories are completely writable across all containers
try:
    os.umask(0o000)
except Exception:
    pass

def fix_download_permissions(target_dir: str):
    """Ensure all downloaded files/folders belong to PUID:PGID with full read/write permissions for all services."""
    if not os.path.exists(target_dir):
        return
    try:
        subprocess.run(
            ["chown", "-R", f"{PUID}:{PGID}", target_dir],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        subprocess.run(
            ["chmod", "-R", "a+rwX", target_dir],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[Permissions] Warning: Failed to adjust ownership in {target_dir}: {e}")

# Ensure local directories exist (mostly for logs)
os.makedirs(LOGS_DIR, exist_ok=True)
# Only create if running locally without Docker (in Docker they are mounted)
if not os.path.exists(DOWNLOADS_DIR):
    try: os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    except: pass
fix_download_permissions(DOWNLOADS_DIR)

class DownloadRequest(BaseModel):
    tool: str # 'ytdl' or 'spotdl'
    url: str
    flags: dict = {}
    navi_account_id: str = ""
    navi_url: str = ""
    navi_user: str = ""
    navi_pass: str = ""
    navi_sync: bool = False


def get_job_store():
    return job_store.JobStore(Path(SPOTDL_CONFIG_DIR) / "jobs.sqlite3")


def public_job(job):
    value = {key: value for key, value in job.items() if key not in {"payload", "dedup_key"}}
    value["title"] = job["payload"].get("playlist_name") or {"download": "Pobieranie muzyki", "import": "Import biblioteki"}.get(job["kind"], "Zadanie")
    return value


def current_job_summary(store):
    with store.connect() as db:
        db.execute("BEGIN")
        row = db.execute("SELECT * FROM jobs WHERE status IN ('running','waiting') "
                         "ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,updated DESC LIMIT 1").fetchone()
        if row is None:
            return None
        job = store.decode(row)
        counts = {row["status"]: row["count"] for row in db.execute(
            "SELECT status,COUNT(*) count FROM tracks WHERE job_id=? GROUP BY status", (job["id"],))}
    value = {key: job[key] for key in ("id", "status", "stage", "error", "next_attempt", "updated")}
    value.update(title=public_job(job)["title"], tracks_total=sum(counts.values()), track_counts=counts)
    track = job["result"].get("current_track")
    value["current_track"] = ({key: track.get(key) for key in ("position", "title", "artist")}
                              if isinstance(track, dict) else None)
    search = job["result"].get("search")
    value["search"] = ({key: search.get(key) for key in ("query", "attempt", "total")}
                       if job["stage"] == "searching" and isinstance(search, dict) else None)
    return value


def update_job_activity(store, identity, track, position, search=None):
    job = store.get(identity)
    artist = track.get("artist") or track.get("artists") or ""
    if isinstance(artist, list):
        artist = ", ".join(str(item) for item in artist)
    progress = {**job["result"], "current_track": {
        "position": position, "title": track.get("title") or track.get("name", ""), "artist": artist}}
    if search is None:
        progress.pop("search", None)
    else:
        progress["search"] = {key: search[key] for key in ("query", "attempt", "total")}
    store.update(identity, result=progress)


@app.get("/api/version")
def get_version():
    return {"version": APP_VERSION}


@app.get("/api/documentation/{section}")
def get_documentation(section: str):
    try:
        html = documentation.render_document(section, APP_VERSION)
    except KeyError:
        raise HTTPException(404, "Nie znaleziono dokumentu") from None
    except (OSError, UnicodeError):
        raise HTTPException(503, "Dokument jest niedostępny. Sprawdź pliki dokumentacji aplikacji.") from None
    return {"version": APP_VERSION, "html": html}


class NormalizerJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["preview_albums", "repair_albums", "scan_library"]
    navi_account_id: str = ""


@app.get("/api/normalizer")
def get_normalizer():
    return normalizer_bridge.snapshot(NORMALIZATION_QUEUE_DIR, NORMALIZATION_ENABLED)


def enqueue_normalizer(operation, navi_account_id=""):
    state = normalizer_bridge.snapshot(NORMALIZATION_QUEUE_DIR)
    if not state["available"] or operation not in state["capabilities"]:
        raise HTTPException(503, "Normalizeusz jest niedostępny lub wymaga aktualizacji")
    if navi_account_id and not accounts.get_navidrome_account_by_id(navi_account_id, SPOTDL_CONFIG_DIR):
        raise HTTPException(400, "Nie znaleziono wybranego konta Navidrome")
    title = {"preview_albums": "Podgląd brakujących albumów", "repair_albums": "Uzupełnianie pustych albumów",
             "scan_library": "ReplayGain całej biblioteki"}[operation]
    store = get_job_store()
    item = store.enqueue("normalizer", {"operation": operation, "playlist_name": title,
        "navi_account_id": navi_account_id, "navi_sync": bool(navi_account_id) and operation != "preview_albums"},
        initial_status="waiting", not_before=time.time() + 30)
    warning = ""
    try:
        normalizer_bridge.publish(NORMALIZATION_QUEUE_DIR, item["id"], operation)
    except normalizer_bridge.PublicationUncertain as error:
        warning = str(error)
        store.update(item["id"], status="waiting", stage="normalizing", error=warning, next_attempt=time.time() + 5)
    except (OSError, ValueError, RuntimeError) as error:
        store.update(item["id"], status="failed", error="Nie udało się zapisać zlecenia Normalizeusza")
        raise HTTPException(503, "Nie udało się zapisać zlecenia Normalizeusza; sprawdź wspólny katalog") from error
    else:
        store.update(item["id"], status="queued", next_attempt=0)
    return item["id"], warning


@app.post("/api/normalizer/jobs", status_code=202)
def create_normalizer_job(request: NormalizerJobRequest):
    identity, warning = enqueue_normalizer(request.operation, request.navi_account_id)
    return {"id": identity, "status": "pending", **({"warning": warning} if warning else {})}


@app.get("/api/jobs")
def get_jobs():
    store = get_job_store()
    return {"items": [public_job(item) for item in store.list()], "counts": store.summary(),
            "current_job": current_job_summary(store)}


@app.post("/api/jobs/cancel")
def cancel_all_jobs():
    count = get_job_store().cancel()
    return {"status": "cancelled", "cancelled": count,
            "message": f"Zatrzymano zadania: {count}. Automatyczny import wstrzymany do dodania lub ponowienia zadania."}


@app.post("/api/jobs/{identity}/cancel")
def cancel_job(identity: str):
    store = get_job_store()
    if not store.get(identity):
        raise HTTPException(404, "Nie znaleziono zadania")
    if not store.cancel(identity):
        raise HTTPException(409, "Zadanie nie jest już aktywne")
    return {"status": "cancelled", "cancelled": 1}


@app.get("/api/jobs/{identity}")
def get_job(identity: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
    store = get_job_store()
    item = store.get(identity)
    if not item:
        raise HTTPException(404, "Nie znaleziono zadania")
    value = public_job(item)
    with store.connect() as db:
        rows = [store.decode(row) for row in db.execute("SELECT * FROM tracks WHERE job_id=? ORDER BY position LIMIT ? OFFSET ?", (identity, limit, offset))]
        value["tracks_total"] = db.execute("SELECT COUNT(*) FROM tracks WHERE job_id=?", (identity,)).fetchone()[0]
        value["track_counts"] = {row["status"]: row["count"] for row in db.execute("SELECT status,COUNT(*) count FROM tracks WHERE job_id=? GROUP BY status", (identity,))}
    value.update(tracks_offset=offset, tracks_limit=limit)
    value["tracks"] = [{"position": row["position"], "title": row["payload"].get("title", row["payload"].get("name", "")),
                         "status": row["status"], "error": row["error"]} for row in rows]
    return value


@app.post("/api/jobs/{identity}/retry")
def retry_job(identity: str, background_tasks: BackgroundTasks):
    store = get_job_store()
    with idle_pipeline(store):
        return retry_idle_job(store, identity, background_tasks)


@contextmanager
def idle_pipeline(store):
    if not PIPELINE_JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Wykonawca jeszcze pracuje. Spróbuj ponownie za chwilę.")
    try:
        import stat
        descriptor = os.open(str(store.path) + ".worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Nieprawidłowy plik blokady kolejki")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(409, "Wykonawca jeszcze pracuje. Spróbuj ponownie za chwilę.") from None
            yield
    finally:
        PIPELINE_JOB_LOCK.release()


def retry_idle_job(store, identity, background_tasks):
    item = store.get(identity)
    if not item:
        raise HTTPException(404, "Nie znaleziono zadania")
    if item["status"] == "running":
        raise HTTPException(409, "Zadanie jest w toku")
    if item["kind"] == "normalizer":
        if item["status"] not in {"done", "failed", "needs_review", "cancelled"}:
            raise HTTPException(409, "Zlecenie Normalizeusza nadal oczekuje na zakończenie")
        try:
            ack = normalizer_bridge.acknowledgement(NORMALIZATION_QUEUE_DIR, identity)
            unfinished = ack is None and (item["status"] == "cancelled" or normalizer_bridge.request_present(NORMALIZATION_QUEUE_DIR, identity))
        except (OSError, ValueError):
            raise HTTPException(409, "Nie można potwierdzić zakończenia poprzedniej operacji Normalizeusza") from None
        if unfinished:
            raise HTTPException(409, "Poprzednia operacja nadal może działać w Normalizeuszu. Poczekaj na jej zakończenie.")
        new_id, warning = enqueue_normalizer(item["payload"]["operation"], item["payload"].get("navi_account_id", ""))
        return {"task_id": new_id, "status": "queued", **({"warning": warning} if warning else {})}
    store.retry(identity)
    background_tasks.add_task(run_pipeline_job, identity)
    return {"task_id": identity, "status": "queued"}


@app.post("/api/slskd/events", status_code=202)
def slskd_event(event: dict, background_tasks: BackgroundTasks, x_webhook_key: str = Header(default="")):
    if not SLSKD_WEBHOOK_SECRET or not secrets.compare_digest(x_webhook_key, SLSKD_WEBHOOK_SECRET):
        raise HTTPException(401, "Nieprawidłowy klucz powiadomień slskd")
    if event.get("type") != "DownloadFileComplete" or not isinstance(event.get("id"), str) or not 0 < len(event["id"]) <= 128:
        raise HTTPException(400, "Nieobsługiwane powiadomienie slskd")
    payload = {"navi_account_id": BEETS_NAVI_ACCOUNT_ID, "navi_sync": bool(BEETS_NAVI_ACCOUNT_ID)}
    job = get_job_store().enqueue("import", payload, key="slskd-event:" + event["id"], automatic=True)
    if job is None:
        return {"status": "paused"}
    # Remote filenames are deliberately not a source of filesystem authorization.
    background_tasks.add_task(run_pipeline_job, job["id"])
    return {"task_id": job["id"], "status": job["status"]}

class NaviTestRequest(BaseModel):
    account_id: str = ""
    url: str = ""
    user: str = ""
    password: str = ""

class SlskdTestRequest(BaseModel):
    url: str = ""
    username: str = ""
    password: str = ""
    api_key: str = ""

class SpotifyMetaRequest(BaseModel):
    url: str

class SlskdOrganizeRequest(BaseModel):
    slskd_url: str = ""
    slskd_user: str = ""
    slskd_pass: str = ""
    slskd_api_key: str = ""
    navi_sync: bool = False
    navi_account_id: str = ""
    navi_url: str = ""
    navi_user: str = ""
    navi_pass: str = ""


class BeetsImportRequest(SlskdOrganizeRequest):
    model_config = ConfigDict(extra="forbid")
    retry: bool = False


class BeetsReviewRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    navi_sync: bool = False
    navi_account_id: str = ""


def validate_beets_job():
    if not BEETS_ENABLED or not shutil.which("beet"):
        raise HTTPException(status_code=400, detail="Import beets jest wyłączony lub beets nie jest zainstalowany")
    if BEETS_JOB_LOCK.locked():
        raise HTTPException(status_code=409, detail="Import beets już trwa. Poczekaj na zakończenie.")


def reserve_beets_job(task_id: str):
    validate_beets_job()
    if not BEETS_JOB_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Import beets już trwa. Poczekaj na zakończenie.")
    BEETS_STATE.update(last_task_id=task_id, last_error=None)


@app.get("/api/beets/status")
def get_beets_status():
    try:
        review_count = len(beets_importer.list_review(SPOTDL_CONFIG_DIR))
    except (OSError, ValueError):
        review_count = None
    return {"enabled": BEETS_ENABLED, "available": bool(shutil.which("beet")),
            "auto_interval": BEETS_AUTO_INTERVAL if BEETS_ENABLED else 0,
            "running": BEETS_JOB_LOCK.locked(), "inbox_dir": BEETS_INBOX_DIR,
            "review_count": review_count, **BEETS_STATE}


@app.get("/api/beets/review")
def get_beets_review():
    try:
        return {"items": beets_importer.list_review(SPOTDL_CONFIG_DIR)}
    except (OSError, ValueError):
        raise HTTPException(status_code=500, detail="Nie udało się odczytać kolejki. Sprawdź plik stanu biblioteki.")


@app.post("/api/beets/review/{item_id}/retry")
def retry_beets_review(item_id: str, request: BeetsReviewRetryRequest, background_tasks: BackgroundTasks):
    if not re.fullmatch(r"[0-9a-f]{64}", item_id) or not any(
        item["id"] == item_id for item in get_beets_review()["items"]
    ):
        raise HTTPException(status_code=404, detail="Nie znaleziono pozycji do ponowienia")
    task_id = str(uuid.uuid4())
    validate_beets_job()
    get_job_store().enqueue("import", {**request.model_dump(), "review_item_id": item_id}, job_id=task_id)
    background_tasks.add_task(run_pipeline_job, task_id)
    return {"status": "started", "task_id": task_id, "message": "Rozpoczęto ponowne sprawdzanie utworu"}


@app.post("/api/beets/import")
def start_beets_import(request: BeetsImportRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    validate_beets_job()
    get_job_store().enqueue("import", request.model_dump(), job_id=task_id)
    background_tasks.add_task(run_pipeline_job, task_id)
    return {"status": "started", "task_id": task_id, "message": "Rozpoczęto import nowych plików przez beets"}


def get_ready_download_paths():
    albums = set()
    if not os.path.isdir(BEETS_INBOX_DIR):
        return []
    with os.scandir(BEETS_INBOX_DIR) as batches:
        for batch in batches:
            marker = os.path.join(batch.path, ".ready")
            if not batch.is_dir(follow_symlinks=False) or not os.path.isfile(marker) or os.path.islink(marker):
                continue
            for root, dirs, files in os.walk(batch.path):
                if any(os.path.splitext(name)[1].lower() in slskd_organizer.AUDIO_EXTENSIONS for name in files):
                    directory = root
                    if re.fullmatch(r'(?:cd|disc|disk)[\s._-]*\d+', os.path.basename(root), re.IGNORECASE):
                        directory = os.path.dirname(root)
                    albums.add(directory)
    return sorted(path for path in albums
                  if not any(path.startswith(parent + os.sep) for parent in albums if path != parent))


def run_beets_import_task(task_id: str, request: BeetsImportRequest, log_callback=None, review_item_id=None, defer_finalize=False):
    job_store.check_cancelled()
    if not (BEETS_JOB_LOCK.locked() and BEETS_STATE["last_task_id"] == task_id):
        try:
            reserve_beets_job(task_id)
        except HTTPException as error:
            if log_callback:
                log_callback(f"[beets] {error.detail}. Pliki pozostają w katalogu pobrań.")
            return
    log_file = None
    try:
        if log_callback is None:
            log_file = open(os.path.join(LOGS_DIR, f"{task_id}.log"), "w", encoding="utf-8")

            def log_callback(message):
                log_file.write(message + "\n")
                log_file.flush()

        log_callback("[beets] Rozpoczynam import. Oryginały pozostają w folderach pobrań.")
        started_at = time.time()
        errors = []
        sources = []
        slskd_url, slskd_user, slskd_password = request.slskd_url, request.slskd_user, request.slskd_pass
        account = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
        if account and not slskd_url:
            slskd_url = account.get("server", "")
            slskd_user = account.get("username", "")
            slskd_password = account.get("password", "")
        elif account and not slskd_password and not request.slskd_api_key and slskd_url == account.get("server"):
            slskd_user = slskd_user or account.get("username", "")
            slskd_password = account.get("password", "")
        if slskd_url:
            try:
                paths = slskd_organizer.get_completed_album_directories(
                    slskd_url, SLSKD_DOWNLOADS_DIR, slskd_user, slskd_password,
                    request.slskd_api_key, log_callback)
                if paths:
                    sources.append((SLSKD_DOWNLOADS_DIR, paths))
            except Exception as error:
                errors.append(f"Soulseek: {error}")
        paths = get_ready_download_paths()
        if paths:
            sources.append((BEETS_INBOX_DIR, paths))
        ready_sources = set()
        for source_dir, groups in sources:
            for group in groups:
                try:
                    ready_sources.update(str(path.resolve()) for path in beets_importer._input_files(Path(group), Path(source_dir).resolve()))
                except ValueError as error:
                    errors.append(str(error))
        pending = beets_importer.enrich_pending(
            config_dir=SPOTDL_CONFIG_DIR, dest_dir=DOWNLOADS_DIR, log_callback=log_callback,
            retry=request.retry or bool(review_item_id), item_id=review_item_id, ready_sources=ready_sources)
        job_store.check_cancelled()
        errors.extend(pending["errors"])
        if review_item_id:
            sources = []
        organized, skipped = pending["organized"], pending["skipped"]
        updated = pending.get("updated", 0)
        for source_dir, paths in sources:
            job_store.check_cancelled()
            result = beets_importer.import_paths(
                paths, source_dir=source_dir, dest_dir=DOWNLOADS_DIR,
                config_dir=SPOTDL_CONFIG_DIR, log_callback=log_callback, retry=request.retry)
            organized += result["organized"]
            skipped += result["skipped"]
            errors.extend(result["errors"])
        job_store.check_cancelled()
        if organized or updated:
            fix_download_permissions(DOWNLOADS_DIR)
            if request.navi_sync and not defer_finalize and not NORMALIZATION_ENABLED:
                navi = accounts.get_navidrome_account_by_id(request.navi_account_id or request.navi_user, SPOTDL_CONFIG_DIR)
                navi_url = (navi or {}).get("server", request.navi_url)
                navi_user = (navi or {}).get("username", request.navi_user)
                navi_password = (navi or {}).get("password", request.navi_pass)
                if navi_url and navi_user and navi_password:
                    try:
                        navidrome_dedup.subsonic_request(navi_url, navi_user, navi_password, "startScan.view")
                        log_callback("[Navidrome] Zlecono skanowanie biblioteki.")
                    except Exception as error:
                        errors.append(f"Navidrome: {error}")
        for error in errors:
            log_callback(f"[beets] Błąd: {error}")
        BEETS_STATE["last_error"] = "; ".join(errors) or None
        marker = "[beets]" if log_file is None else "[Zakończono]"
        log_callback(f"{marker} Zaimportowano: {organized}, uzupełniono: {updated}, pominięto: {skipped}, błędów: {len(errors)}.")
        entries = beets_importer._load_library_state(SPOTDL_CONFIG_DIR)["tracks"].values()
        changed = [entry["destination"] for entry in entries if entry.get("imported") and entry.get("destination")
                   and entry.get("last_attempt", 0) >= started_at]
        return {"paths": changed, "errors": errors, "organized": organized, "updated": updated,
                "ready_sources": sorted(ready_sources)}
    except Exception as error:
        BEETS_STATE["last_error"] = str(error)
        if log_callback:
            log_callback(f"[Błąd]: Import beets: {error}")
        return {"paths": [], "errors": [str(error)], "organized": 0, "updated": 0}
    finally:
        if log_file:
            log_file.close()
        BEETS_JOB_LOCK.release()


async def beets_auto_loop(stop):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=BEETS_AUTO_INTERVAL)
            return
        except asyncio.TimeoutError:
            pass
        if BEETS_JOB_LOCK.locked():
            continue
        payload = {"navi_account_id": BEETS_NAVI_ACCOUNT_ID, "navi_sync": bool(BEETS_NAVI_ACCOUNT_ID)}
        get_job_store().enqueue("import", payload, key=f"poll:{int(time.time() // BEETS_AUTO_INTERVAL)}", automatic=True)


def job_log(identity, message):
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(os.path.join(LOGS_DIR, identity + ".log"), "a", encoding="utf-8") as output:
        output.write(message + "\n")


def navi_services(payload):
    account = accounts.get_navidrome_account_by_id(payload.get("navi_account_id") or payload.get("navi_user", ""), SPOTDL_CONFIG_DIR) or {}
    url = account.get("server") or payload.get("navi_url")
    user = account.get("username") or payload.get("navi_user")
    password = account.get("password") or payload.get("navi_pass")
    if not all((url, user, password)):
        return None, None

    def call(endpoint, params=None):
        job_store.check_cancelled()
        return navidrome_dedup.subsonic_request(url, user, password, endpoint, params)

    def lookup(track, paths):
        if paths:
            try:
                from mediafile import MediaFile
                final_path = pipeline.relative_audio_path(paths[0], DOWNLOADS_DIR)
                audio = MediaFile(str(Path(DOWNLOADS_DIR) / final_path))
                track = {**track, "title": audio.title or track.get("title") or track.get("name", ""),
                         "artist": audio.artist or track.get("artist"), "length": audio.length,
                         "mb_trackid": audio.mb_trackid or track.get("mb_trackid")}
            except (OSError, ValueError):
                return None
        artist = track.get("artist") or next(iter(track.get("artists") or []), "")
        song = navidrome_dedup.search_navidrome_song(url, user, password, artist,
                 track.get("title") or track.get("name", ""), length=track.get("length") or track.get("duration"),
                 recording_id=track.get("mb_trackid"), skip_mode=track.get("_skip_mode", "all"), required_paths=paths or None)
        return song
    return call, lookup


def expand_tracks(request, identity):
    tool, url = request["tool"], request["url"]
    if tool == "ytdl" and not request.get("flags", {}).get("audio_only"):
        return [{"url": url, "source_tool": tool}]
    if "open.spotify.com/" in url or url.startswith("spotify:"):
        temporary = Path(SPOTDL_CONFIG_DIR) / ("manifest-" + identity + ".spotdl")
        try:
            proc = subprocess.run(["spotdl", "save", url, "--save-file", str(temporary)], capture_output=True, text=True, timeout=60)
            if proc.returncode == 0 and temporary.is_file():
                songs = json.loads(temporary.read_text(encoding="utf-8"))
                if isinstance(songs, list) and songs:
                    return [{**song, "title": song.get("name", ""), "url": song.get("url") or song.get("song_url") or url,
                             "source_tool": "spotdl"} for song in songs if isinstance(song, dict)]
        finally:
            temporary.unlink(missing_ok=True)
        raise RuntimeError("Nie udało się zapisać listy utworów Spotify")
    entries = navidrome_dedup.extract_ytdl_metadata(url)
    if entries:
        return [{**entry, "title": entry.get("name", ""), "source_tool": "ytdl"} for entry in entries]
    if tool == "soulseek":
        raise RuntimeError("Nie udało się odczytać listy nagrań do wyszukania w Soulseeku")
    return [{"url": url, "source_tool": tool}]


def tracked_paths(source_prefix):
    root = Path(source_prefix).resolve()
    state = beets_importer._load_library_state(SPOTDL_CONFIG_DIR)
    paths = []
    for item in state["tracks"].values():
        if item.get("imported") and item.get("destination") and Path(item["source"]).resolve().is_relative_to(root):
            try:
                paths.append(pipeline.relative_audio_path(item["destination"], DOWNLOADS_DIR))
            except (OSError, ValueError):
                continue
    return list(dict.fromkeys(paths))


def preserve_import_changes(store, identity, outcome):
    if not outcome or not outcome.get("paths"):
        return
    job = store.get(identity)
    progress = dict(job["result"])
    paths = [pipeline.relative_audio_path(path, DOWNLOADS_DIR) for path in outcome["paths"]]
    progress["paths"] = list(dict.fromkeys(progress.get("paths", []) + paths))
    store.update(identity, result=progress)


def process_download_job(store, job):
    try:
        from backend import acquisition
    except ImportError:
        import acquisition
    payload, identity = job["payload"], job["id"]
    video = payload["tool"] == "ytdl" and not payload.get("flags", {}).get("audio_only")
    if not BEETS_ENABLED or video:
        if payload["tool"] == "soulseek":
            raise RuntimeError("Pobieranie Soulseek wymaga włączonego importu beets")
        store.update(identity, stage="downloading")
        result = run_download_task(identity, DownloadRequest(**payload)) or {}
        if result.get("returncode", 0):
            if result.get("retryable") is False:
                store.update(identity, status="failed", error=result.get("error") or "Źródło wymaga sprawdzenia")
                job_log(identity, f"[Kolejka] {result.get('error')}; sprawdź źródło przed ponowieniem")
                return
            raise RuntimeError(result.get("error") or "Pobieranie zakończone błędem")
        store.update(identity, status="done", stage="done")
        return
    if not store.tracks(identity):
        store.update(identity, stage="resolving")
        store.set_tracks(identity, expand_tracks(payload, identity))
    rows = store.tracks(identity)
    pending_statuses = {"queued", "transferring", "retry"}
    groups = {}
    if payload["tool"] != "soulseek":
        for row in rows:
            if key := acquisition.album_key(row["payload"]):
                groups.setdefault(key, []).append(row)
    groups = {key: group for key, group in groups.items() if len(group) > 1}
    for group in groups.values():
        downloaded = [row for row in group if row["status"] == "downloaded"]
        if downloaded and not any(row["status"] in pending_statuses for row in group):
            directory = acquisition.stage_album(store, identity, downloaded, BEETS_INBOX_DIR)
            (directory / ".ready").touch()
            store.update(identity, stage="importing")
            outcome = run_beets_import_task(directory.name, BeetsImportRequest(retry=True), defer_finalize=True)
            if outcome is None:
                raise RuntimeError("Import beets jest zajęty")
            preserve_import_changes(store, identity, outcome)
            for row in store.tracks(identity):
                if row["position"] not in {item["position"] for item in downloaded}:
                    continue
                result = dict(row["result"])
                paths = list(dict.fromkeys(path for source in result.get("source_files", []) for path in tracked_paths(source)))
                if paths:
                    result["paths"] = paths
                    store.update_track(identity, row["position"], status="imported", result=result)
                    acquisition.remember_paths(store, row["payload"], payload["tool"], paths, DOWNLOADS_DIR)
                else:
                    store.update_track(identity, row["position"], status="needs_review", result=result,
                                       error="Pobrano plik, ale import wymaga sprawdzenia")
            store.update(identity, status="waiting", next_attempt=0)
            return
    pending = [row for row in rows if row["status"] in pending_statuses]
    ready = [row for row in pending if row["next_attempt"] <= time.time()]
    active = min(ready, key=lambda row: (row["failures"], row["next_attempt"], row["position"]), default=None)
    if active is None:
        if pending:
            store.update(identity, status="waiting", next_attempt=min(row["next_attempt"] for row in pending))
        else:
            store.update(identity, stage="finalizing")
        return
    position, track = active["position"], active["payload"]
    update_job_activity(store, identity, track, position)
    store.update(identity, stage="checking")
    result = dict(active["result"])
    _, lookup = navi_services(payload)
    duplicate = next((row for row in rows if row["position"] < position and track.get("url")
                      and row["payload"].get("url") == track["url"]
                      and row["status"] in {"imported", "existing", "downloaded"}), None)
    cached = acquisition.remembered_paths(store, track, payload["tool"], DOWNLOADS_DIR) if payload.get("flags", {}).get("skip_downloaded") else []
    if duplicate:
        store.update_track(identity, position, status=duplicate["status"], result=duplicate["result"])
    elif cached:
        store.update_track(identity, position, status="imported", result={**result, "paths": cached})
    elif lookup and active["status"] == "queued" and (payload.get("flags", {}).get("check_navidrome") or payload["tool"] == "soulseek") and (song := lookup({**track, "_skip_mode": payload.get("flags", {}).get("navidrome_skip_mode", "flac_only")}, [])):
        store.update_track(identity, position, status="existing", result={"navidrome_id": song["id"]})
    else:
        if payload["tool"] == "soulseek" and not accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR):
            raise RuntimeError("Brak konta slskd w konfiguracji serwera")
        try:
            if payload["tool"] == "soulseek" and not result.get("fallback"):
                process_soulseek_track(store, job, active)
            else:
                source_task = result.get("source_task") or identity + "-" + str(position)
                result["source_task"] = source_task
                inbox = Path(BEETS_INBOX_DIR) / source_task
                grouped = acquisition.album_key(track) in groups
                if grouped and result.get("source_files") and all(Path(path).is_file() for path in result["source_files"]):
                    store.update_track(identity, position, status="downloaded", result=result)
                else:
                    if (inbox / ".ready").is_file() and not grouped:
                        store.update(identity, stage="importing")
                        outcome = run_beets_import_task(source_task, BeetsImportRequest(retry=True), defer_finalize=True)
                        preserve_import_changes(store, identity, outcome)
                        if outcome is None or outcome.get("errors"):
                            raise RuntimeError("; ".join(outcome.get("errors", [])) if outcome else "Import beets jest zajęty")
                    else:
                        store.update(identity, stage="downloading")
                        flags = {**payload.get("flags", {}), "generate_m3u": False, "check_navidrome": False,
                                 "_bypass_archive": True}
                        if track.get("source_tool") == "ytdl":
                            flags["audio_only"] = True
                        child = {**payload, "tool": track.get("source_tool", "ytdl"), "url": track.get("url") or payload["url"],
                                 "navi_sync": False, "flags": flags}
                        kwargs = {"defer_finalize": True, "activity_callback": lambda stage: store.update(identity, stage=stage),
                                  **({"defer_import": True} if grouped else {})}
                        outcome = run_download_task(source_task, DownloadRequest(**child), **kwargs) or {}
                        preserve_import_changes(store, identity, {"paths": outcome.get("import_paths", [])})
                        if outcome.get("import_deferred") or outcome.get("import_errors"):
                            raise RuntimeError("; ".join(outcome.get("import_errors", [])) or "Import beets jest zajęty")
                        if outcome.get("returncode", 0):
                            if outcome.get("retryable") is False:
                                store.update_track(identity, position, status="failed", result=result,
                                                   error=outcome.get("error") or "Źródło wymaga sprawdzenia")
                                job_log(identity, f"[Utwór {position + 1}] {outcome.get('error')}")
                                store.update(identity, status="waiting", next_attempt=0)
                                return
                            raise RuntimeError(outcome.get("error") or "Pobieranie utworu zakończone błędem")
                        if grouped:
                            result["source_files"] = outcome.get("source_files", [])
                            if not result["source_files"]:
                                raise RuntimeError("Pobieranie nie zwróciło pliku audio")
                            store.update_track(identity, position, status="downloaded", result=result)
                    if not grouped:
                        paths = tracked_paths(inbox)
                        if paths:
                            store.update_track(identity, position, status="imported", result={**result, "paths": paths})
                            acquisition.remember_paths(store, track, payload["tool"], paths, DOWNLOADS_DIR)
                        else:
                            store.update_track(identity, position, status="needs_review", result=result,
                                               error="Pobrano plik, ale import wymaga sprawdzenia")
        except Exception as error:
            store.fail_track(identity, position, error, result=store.tracks(identity)[position]["result"] or result)
            failed = store.tracks(identity)[position]
            suffix = (f"; próba {failed['failures']}/8, ponowienie za {max(0, round(failed['next_attempt'] - time.time()))} s"
                      if failed["status"] == "retry" else "; wyczerpano próby automatyczne")
            job_log(identity, f"[Utwór {position + 1}] {error}{suffix}")
    current_rows = store.tracks(identity)
    due = [row["next_attempt"] for row in current_rows if row["status"] in pending_statuses]
    # A completed download group needs an immediate import step even if other albums wait.
    has_downloaded = any(row["status"] == "downloaded" for row in current_rows)
    store.update(identity, status="waiting", next_attempt=0 if has_downloaded or not due else min(due))


def process_soulseek_track(store, job, row):
    try:
        from backend import soulseek_search
    except ImportError:
        import soulseek_search
    identity, position = job["id"], row["position"]
    payload, track, result = job["payload"], dict(row["payload"]), dict(row["result"])
    account = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
    if not account:
        raise RuntimeError("Brak konta slskd w konfiguracji serwera")
    if row["status"] == "transferring":
        store.update(identity, stage="transferring")
        selected = result.get("selected", {})
        local = slskd_organizer.find_file_in_dir(selected.get("filename", ""), SLSKD_DOWNLOADS_DIR, allow_basename=False)
        ready = slskd_organizer.get_completed_album_directories(account["server"], SLSKD_DOWNLOADS_DIR, account.get("username", ""), account.get("password", ""))
        if local and any(Path(local).resolve().is_relative_to(Path(group).resolve()) for group in ready):
            store.update(identity, stage="importing")
            outcome = run_beets_import_task(identity + "-slsk", BeetsImportRequest(), defer_finalize=True)
            preserve_import_changes(store, identity, outcome)
            paths = tracked_paths(local)
            if paths:
                store.update_track(identity, position, status="imported", result={**result, "paths": paths})
                return
        if time.time() - result.get("requested_at", time.time()) < 900:
            store.update_track(identity, position, status="transferring", result=result, next_attempt=time.time() + 15)
            return
        result["excluded_usernames"] = list(dict.fromkeys(result.get("excluded_usernames", []) + [selected.get("username", "")]))
        if len(result["excluded_usernames"]) >= 3:
            if payload.get("flags", {}).get("youtube_fallback"):
                store.update_track(identity, position, status="queued", result={**result, "fallback": True})
            else:
                store.update_track(identity, position, status="needs_review", result=result, error="Źródła Soulseek nie ukończyły pobierania")
            return
    store.update(identity, stage="searching")
    track["preferred_format"] = payload.get("flags", {}).get("preferred_format", "flac")
    track["excluded_usernames"] = result.get("excluded_usernames", [])
    response = soulseek_search.search_and_queue(
        track, url=account["server"], username=account.get("username", ""), password=account.get("password", ""),
        download_dir=SLSKD_DOWNLOADS_DIR,
        progress_callback=lambda search: update_job_activity(store, identity, track, position, search))
    result.update(response)
    if response["state"] == "queued":
        store.update(identity, stage="transferring")
        store.update_track(identity, position, status="transferring", result={**result, "requested_at": time.time()}, next_attempt=time.time() + 15)
    elif response.get("retryable"):
        selected = response.get("selected") or {}
        excluded = list(result.get("excluded_usernames", []))
        if selected.get("username") and selected["username"] not in excluded:
            excluded.append(selected["username"])
        result["excluded_usernames"] = excluded
        if len(excluded) >= 3:
            if payload.get("flags", {}).get("youtube_fallback"):
                store.update_track(identity, position, status="queued", result={**result, "fallback": True})
            else:
                store.update_track(identity, position, status="needs_review", result=result, error=response.get("message", "Brak pewnego dopasowania"))
        else:
            store.fail_track(identity, position, response.get("message", "Wyszukiwanie Soulseek niedostępne"), result=result)
    elif response["state"] == "not_found" and payload.get("flags", {}).get("youtube_fallback"):
        store.update_track(identity, position, status="queued", result={**result, "fallback": True})
    else:
        store.update_track(identity, position, status="needs_review", result=result, error=response.get("message", "Brak pewnego dopasowania"))


def process_normalizer_job(store, job):
    try:
        ack = normalizer_bridge.acknowledgement(NORMALIZATION_QUEUE_DIR, job["id"])
    except (OSError, ValueError):
        store.update(job["id"], status="failed", stage="normalizing", error="Nieprawidłowe potwierdzenie Normalizeusza")
        return
    if ack is None:
        if normalizer_bridge.request_present(NORMALIZATION_QUEUE_DIR, job["id"]):
            store.update(job["id"], status="waiting", stage="normalizing", next_attempt=time.time() + 5)
            return
        # A consumer may have durably completed between the ACK and claim reads.
        ack = normalizer_bridge.acknowledgement(NORMALIZATION_QUEUE_DIR, job["id"])
        if ack is None:
            store.update(job["id"], status="needs_review", stage="normalizing",
                         error="Brak zlecenia i potwierdzenia Normalizeusza. Sprawdź wynik przed jawnym ponowieniem; niczego nie wysłano automatycznie ponownie.")
            return
    if ack.get("operation") != job["payload"]["operation"] or (ack["status"] == "done" and not isinstance(ack.get("result"), dict)):
        store.update(job["id"], status="failed", stage="normalizing", error="Nieprawidłowe potwierdzenie operacji Normalizeusza")
        return
    report = normalizer_bridge.clean_report(ack.get("result")) or {}
    progress = {**job["result"], "normalizer_result": report, "normalized": True}
    failed = ack["status"] == "failed" or bool(report.get("errors"))
    error = str(ack.get("error") or "Normalizeusz zgłosił błędy; sprawdź raport poszczególnych plików")[:2000] if failed else ""
    if failed and (not report.get("changed") or not job["payload"].get("navi_sync")):
        store.update(job["id"], status="failed", stage="normalizing", result=progress, error=error)
        return
    payload = {**job["payload"], "navi_sync": bool(job["payload"].get("navi_sync")) and
               (job["payload"]["operation"] == "scan_library" or bool(report.get("changed")))}
    store.update(job["id"], result=progress)
    current = store.get(job["id"])
    current["payload"] = payload
    call, lookup = navi_services(payload)
    pipeline.finalize(store, current, library_dir=DOWNLOADS_DIR, navi_call=call, lookup=lookup)
    if failed and store.get(job["id"])["status"] == "done":
        store.update(job["id"], status="failed", error=error)


def run_pipeline_job(identity=None):
    if not PIPELINE_JOB_LOCK.acquire(blocking=False):
        return
    try:
        store = get_job_store()
        import stat
        descriptor = os.open(str(store.path) + ".worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, "a") as lock, job_store.cancellation_scope(store, identity) as cancellation:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Nieprawidłowy plik blokady kolejki")
            os.fchmod(lock.fileno(), 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            store.recover()
            job = store.claim(identity)
            if not job:
                return
            cancellation["identity"] = job["id"]
            try:
                if job["kind"] == "normalizer":
                    process_normalizer_job(store, job)
                    return
                progress = dict(job["result"])
                if "import_started" not in progress:
                    progress["import_started"] = time.time()
                    store.update(job["id"], result=progress)
                    job["result"] = progress
                if job["stage"] not in {"finalizing", "normalizing", "scanning", "playlist"}:
                    if job["kind"] == "download":
                        process_download_job(store, job)
                    elif job["kind"] == "organize":
                        store.update(job["id"], stage="organizing")
                        result = run_slskd_organize_task(job["id"], SlskdOrganizeRequest(**job["payload"])) or {}
                        if result.get("errors"):
                            raise RuntimeError("; ".join(str(error) for error in result["errors"]))
                        store.update(job["id"], status="done", stage="done", result=result)
                    else:
                        store.update(job["id"], stage="importing")
                        payload = dict(job["payload"])
                        review_id = payload.pop("review_item_id", None)
                        result = run_beets_import_task(job["id"], BeetsImportRequest(**payload),
                                                       review_item_id=review_id, defer_finalize=True)
                        if result is None:
                            raise RuntimeError("Import beets jest zajęty")
                        progress = dict(job["result"])
                        progress["paths"] = list(dict.fromkeys(progress.get("paths", []) + result["paths"]))
                        progress["library_changed"] = bool(progress.get("library_changed") or result["organized"] or result["updated"])
                        store.update(job["id"], result=progress)
                        if result["errors"]:
                            raise RuntimeError("; ".join(result["errors"]))
                        if review_id:
                            entry = beets_importer._load_library_state(SPOTDL_CONFIG_DIR)["tracks"].get(review_id, {})
                            if not entry.get("imported"):
                                if entry.get("source") in result.get("ready_sources", []):
                                    store.update(job["id"], status="needs_review", stage="importing",
                                                 error=entry.get("reason") or "Brak pewnego rozpoznania nagrania")
                                else:
                                    store.update(job["id"], status="waiting", stage="importing", next_attempt=time.time() + 30,
                                                 error="Plik oczekuje na zakończenie pobierania")
                                return
                        store.update(job["id"], stage="finalizing")
                current = store.get(job["id"])
                job_store.check_cancelled()
                if current["status"] in {"done", "cancelled", "failed"}:
                    return
                if current["stage"] == "finalizing" or job["stage"] in {"normalizing", "scanning", "playlist"}:
                    if current["stage"] == "finalizing":
                        # Reconcile beets' committed state after a crash before SQLite
                        # received the importer result. The watermark precedes all writes.
                        entries = beets_importer._load_library_state(SPOTDL_CONFIG_DIR)["tracks"].values()
                        recovered = [entry["destination"] for entry in entries
                                     if entry.get("imported") and entry.get("destination")
                                     and entry.get("last_attempt", 0) >= progress["import_started"]]
                        preserve_import_changes(store, job["id"], {"paths": recovered})
                        current = store.get(job["id"])
                        if current["result"].get("paths"):
                            store.update(job["id"], result={**current["result"], "library_changed": True})
                            current = store.get(job["id"])
                    if current["kind"] == "import" and not current["result"].get("library_changed"):
                        store.update(job["id"], status="done", stage="done")
                    else:
                        call, lookup = navi_services(current["payload"])
                        pipeline.finalize(store, current, library_dir=DOWNLOADS_DIR,
                                          normalization_dir=NORMALIZATION_QUEUE_DIR if NORMALIZATION_ENABLED else None,
                                          navi_call=call, lookup=lookup)
                elif current["status"] == "running":
                    store.update(job["id"], status="waiting", next_attempt=time.time() + 1)
                current = store.get(job["id"])
                store.update(job["id"], failures=0, error="" if current["status"] != "needs_review" else current["error"])
                job_log(job["id"], f"[Kolejka] {current['stage']}: {current['status']}")
            except job_store.JobCancelled:
                job_log(job["id"], "[Kolejka] Zatrzymano na żądanie użytkownika.")
            except Exception as error:
                failures = job["failures"] + 1
                delay = min(3600, 30 * 2 ** min(failures - 1, 7))
                status = "failed" if failures >= 8 else "waiting"
                store.update(job["id"], status=status, failures=failures, error=str(error), next_attempt=time.time() + delay)
                job_log(job["id"], f"[Kolejka] {error}; ponowienie za {delay} s")
    except job_store.JobCancelled:
        if job:
            job_log(job["id"], "[Kolejka] Zatrzymano na żądanie użytkownika.")
    finally:
        PIPELINE_JOB_LOCK.release()


async def pipeline_worker(stop):
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_pipeline_job)
        except Exception as error:
            BEETS_STATE["last_error"] = str(error)
            print(f"[Kolejka] Przejściowy błąd wykonawcy: {error}", flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass

@app.get("/api/accounts/navidrome")
async def get_navidrome_accounts_endpoint():
    accs = accounts.get_navidrome_accounts(SPOTDL_CONFIG_DIR)
    safe_accs = [
        {"id": a["id"], "server": a["server"], "username": a["username"]}
        for a in accs
    ]
    return {"accounts": safe_accs}

@app.get("/api/accounts/slskd")
async def get_slskd_account_endpoint():
    acc = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
    if acc:
        return {"configured": True, "server": acc["server"], "username": acc["username"]}
    return {"configured": False}

@app.post("/api/spotify/metadata")
async def get_spotify_metadata_endpoint(req: SpotifyMetaRequest):
    res = spotify_meta.fetch_spotify_metadata(
        url=req.url,
        downloads_dir=DOWNLOADS_DIR,
        config_dir=SPOTDL_CONFIG_DIR
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("message", "Błąd pobierania metadanych"))
    return res

@app.post("/api/test/navidrome")
async def test_navidrome(req: NaviTestRequest):
    url = req.url
    user = req.user
    password = req.password

    # If account_id provided or password empty, lookup from navidrome_accounts.txt
    if (req.account_id or not password):
        lookup_id = req.account_id or req.user
        if lookup_id:
            acc = accounts.get_navidrome_account_by_id(lookup_id, SPOTDL_CONFIG_DIR)
            if acc:
                url = acc.get("server") or url
                user = acc.get("username") or user
                password = acc.get("password") or password

    if not url:
        return {"ok": False, "message": "Brak wybranego konta lub adresu URL Navidrome"}
    try:
        data = navidrome_dedup.subsonic_request(
            base_url=url,
            user=user,
            password=password,
            endpoint="ping.view",
            timeout=5
        )
        sub_resp = data.get("subsonic-response", {})
        if sub_resp.get("status") == "ok":
            server_version = sub_resp.get("serverVersion", sub_resp.get("version", ""))
            return {
                "ok": True,
                "message": f"Połączono pomyślnie z Navidrome ({user})!",
                "server_version": server_version
            }
        else:
            error = sub_resp.get("error", {})
            return {"ok": False, "message": f"Błąd Navidrome: {error.get('message', 'Nieznany błąd')}"}
    except Exception as e:
        return {"ok": False, "message": f"Nie udało się połączyć z Navidrome: {str(e)}"}

@app.post("/api/test/slskd")
async def test_slskd(req: SlskdTestRequest):
    url = req.url
    username = req.username
    password = req.password
    api_key = req.api_key

    # If credentials not provided, auto-load active account from slskd_accounts.txt
    if not password and not api_key:
        acc = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
        if acc:
            url = url or acc.get("server", "")
            username = username or acc.get("username", "")
            password = acc.get("password", "")

    if not url:
        return {"ok": False, "message": "Brak skonfigurowanego konta Soulseek (slskd) w slskd_accounts.txt"}
    try:
        import urllib.request
        import urllib.error
        slskd_url = url
        if not slskd_url.startswith(('http://', 'https://')):
            slskd_url = f"http://{slskd_url}"

        headers = slskd_organizer.get_slskd_auth_header(
            slskd_url,
            username=username,
            password=password,
            api_key=api_key,
            timeout=5
        )

        api_url = f"{slskd_url.rstrip('/')}/api/v0/transfers/downloads"
        request_obj = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(request_obj, timeout=5) as response:
            status_code = getattr(response, "status", None)
            if status_code is None and hasattr(response, "getcode"):
                status_code = response.getcode()
            if status_code is None or status_code == 200:
                return {"ok": True, "message": f"Połączono i zalogowano pomyślnie do Soulseek ({username or 'slskd'})!"}
            return {"ok": False, "message": f"Odpowiedź serwera: {status_code}"}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"ok": False, "message": "Błąd autoryzacji Soulseek (slskd): Nieprawidłowy login lub hasło"}
        return {"ok": False, "message": f"Błąd Soulseek (slskd): HTTP {e.code}"}
    except RuntimeError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"Nie udało się połączyć z Soulseek (slskd): {str(e)}"}


def run_slskd_organize_task(task_id: str, request: SlskdOrganizeRequest):
    job_store.check_cancelled()
    if BEETS_ENABLED:
        run_beets_import_task(task_id, BeetsImportRequest(**request.model_dump()))
        return
    log_file_path = os.path.join(LOGS_DIR, f"{task_id}.log")
    with open(log_file_path, "w", encoding="utf-8") as f:
        def log_cb(msg: str):
            job_store.check_cancelled()
            f.write(f"{msg}\n")
            f.flush()

        log_cb("=== Rozpoczynanie porządkowania plików Soulseek (slskd) ===")
        try:
            slskd_url = request.slskd_url
            slskd_user = request.slskd_user
            slskd_pass = request.slskd_pass
            slskd_api_key = request.slskd_api_key
            if not slskd_url or (not slskd_pass and not slskd_api_key):
                acc = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
                if acc:
                    slskd_url = slskd_url or acc.get("server", "")
                    slskd_user = slskd_user or acc.get("username", "")
                    slskd_pass = acc.get("password", "")

            navi_url = request.navi_url
            navi_user = request.navi_user
            navi_pass = request.navi_pass
            if request.navi_sync and (request.navi_account_id or not navi_pass):
                n_acc = accounts.get_navidrome_account_by_id(request.navi_account_id or request.navi_user, SPOTDL_CONFIG_DIR)
                if n_acc:
                    navi_url = n_acc.get("server", navi_url)
                    navi_user = n_acc.get("username", navi_user)
                    navi_pass = n_acc.get("password", navi_pass)

            res = slskd_organizer.organize_slskd_downloads(
                slskd_url=slskd_url,
                slskd_api_key=slskd_api_key,
                slskd_username=slskd_user,
                slskd_password=slskd_pass,
                source_dir=SLSKD_DOWNLOADS_DIR,
                dest_dir=DOWNLOADS_DIR,
                log_callback=log_cb
            )
            log_cb(f"\n[Uprawnienia] Ustawianie praw dostępu (PUID:PGID {PUID}:{PGID})...")
            fix_download_permissions(DOWNLOADS_DIR)

            if request.navi_sync and navi_url and navi_user and navi_pass:
                log_cb("\n[Navidrome] Uruchamianie skanowania biblioteki po uporządkowaniu...")
                try:
                    navidrome_dedup.subsonic_request(
                        base_url=navi_url,
                        user=navi_user,
                        password=navi_pass,
                        endpoint="startScan.view",
                        timeout=10
                    )
                    log_cb("[Navidrome] Skanowanie zostało pomyślnie zlecone!")
                except Exception as e:
                    log_cb(f"[Navidrome] Nie udało się wywołać skanowania: {e}")

            log_cb(f"\n[Zakończono] Uporządkowano: {res['organized']} utworów.")
            return {"organized": res["organized"], "errors": []}
        except Exception as e:
            log_cb(f"\n[Błąd]: {str(e)}")
            return {"organized": 0, "errors": [str(e)]}




def run_download_task(task_id: str, request: DownloadRequest, defer_finalize=False, defer_import=False,
                      download_timeout=900, activity_callback=None):
    job_store.check_cancelled()
    try:
        from backend import acquisition
    except ImportError:
        import acquisition
    log_file_path = os.path.join(LOGS_DIR, f"{task_id}.log")
    temp_spotdl_file = os.path.join(SPOTDL_CONFIG_DIR, f"temp_{task_id}.spotdl")
    
    with open(log_file_path, "w", encoding="utf-8") as f:
        cmd = []
        flags = request.flags

        # Resolve navidrome credentials
        navi_url = request.navi_url
        navi_user = request.navi_user
        navi_pass = request.navi_pass
        if request.navi_account_id or not navi_pass:
            n_acc = accounts.get_navidrome_account_by_id(request.navi_account_id or request.navi_user, SPOTDL_CONFIG_DIR)
            if n_acc:
                navi_url = n_acc.get("server", navi_url)
                navi_user = n_acc.get("username", navi_user)
                navi_pass = n_acc.get("password", navi_pass)

        if request.tool == "spotdl":
            # Wyczyść stary plik błędów przed nowym pobieraniem
            error_file = os.path.join(SPOTDL_CONFIG_DIR, "nadal_brakujace.txt")
            if os.path.exists(error_file):
                try:
                    os.remove(error_file)
                except:
                    pass

            target_source = request.url

            # Navidrome deduplication pre-check
            if flags.get("check_navidrome") and navi_url and navi_user and navi_pass:
                f.write("[Deduplikacja] Pobieranie metadanych utworów do weryfikacji w bazie Navidrome...\n")
                f.flush()
                try:
                    subprocess.run(
                        ["spotdl", "save", request.url, "--save-file", temp_spotdl_file],
                        capture_output=True,
                        text=True,
                        cwd=DOWNLOADS_DIR
                    )
                    if os.path.exists(temp_spotdl_file):
                        with open(temp_spotdl_file, "r", encoding="utf-8") as sf:
                            song_list = json.load(sf)
                        
                        skip_mode = flags.get("navidrome_skip_mode", "flac_only")
                        def d_log(msg):
                            f.write(f"{msg}\n")
                            f.flush()

                        kept, skipped = navidrome_dedup.filter_existing_songs(
                            songs=song_list,
                            navi_url=navi_url,
                            navi_user=navi_user,
                            navi_pass=navi_pass,
                            skip_mode=skip_mode,
                            log_callback=d_log
                        )

                        if not kept:
                            f.write(f"\n[Deduplikacja] Wszystkie utwory ({len(skipped)}) znajdują się już w Twojej bibliotece Navidrome! Pomijam pobieranie.\n")
                            if flags.get("generate_m3u") and request.navi_sync and navi_url and navi_user and navi_pass:
                                f.write("[Navidrome] Tworzenie natywnej playlisty ze zindeksowanych utworów...\n")
                                navi_song_ids = [s.get("_navidrome_id") for s in skipped if s.get("_navidrome_id")]
                                if navi_song_ids:
                                    derived_name = flags.get("m3u_name", "").strip()
                                    if not derived_name or derived_name == "{list}":
                                        derived_name = (song_list[0].get("list_name") if song_list else "") or "Nowa Playlista"
                                    try:
                                        navidrome_dedup.subsonic_request(
                                            base_url=navi_url,
                                            user=navi_user,
                                            password=navi_pass,
                                            endpoint="createPlaylist.view",
                                            extra_params={"name": derived_name, "songId": navi_song_ids}
                                        )
                                        f.write(f"[Navidrome] Natywna playlista '{derived_name}' została utworzona pomyślnie z {len(navi_song_ids)} utworów!\n")
                                    except Exception as pe:
                                        f.write(f"[Navidrome] Ostrzeżenie przy tworzeniu playlisty: {pe}\n")
                            f.write("\n[Zakończono z kodem 0]\n")
                            try:
                                os.remove(temp_spotdl_file)
                            except:
                                pass
                            return

                        if skipped:
                            f.write(f"[Deduplikacja] Pominięto {len(skipped)} istniejących utworów. Pobieranie {len(kept)} brakujących pozycji...\n")
                            with open(temp_spotdl_file, "w", encoding="utf-8") as sf:
                                json.dump(kept, sf)
                            target_source = temp_spotdl_file
                except Exception as e:
                    f.write(f"[Deduplikacja] Ostrzeżenie: Błąd podczas sprawdzania duplikatów: {e}. Kontynuuję standardowe pobieranie.\n")
                    f.flush()

            if target_source == temp_spotdl_file:
                cmd = ["spotdl", "download", temp_spotdl_file]
            else:
                cmd = ["spotdl", target_source]

            # Hardcoded hierarchy
            cmd.extend(["--output", "{artist}/{year} - {album}/{track-number} {title}.{output-ext}"])
            
            if flags.get("skip_downloaded"):
                cmd.extend(["--overwrite", "skip"])
                
            if flags.get("use_cookies"):
                cmd.extend(["--cookie-file", os.path.join(SPOTDL_CONFIG_DIR, "youtube_cookies.txt")])
                
            if flags.get("dont_filter"):
                cmd.append("--dont-filter-results")
                
            if flags.get("audio_sources"):
                cmd.extend(["--audio", "youtube-music", "youtube", "soundcloud"])
                
            if flags.get("threads"):
                cmd.extend(["--threads", str(flags.get("threads"))])
                
            if flags.get("save_errors"):
                cmd.extend(["--save-errors", os.path.join(SPOTDL_CONFIG_DIR, "nadal_brakujace.txt")])
                
            if flags.get("yt_dlp_args"):
                cmd.extend(["--yt-dlp-args", "--sleep-interval 2 --max-sleep-interval 4"])
                
            if flags.get("generate_m3u"):
                m3u_name = flags.get("m3u_name", "{list}")
                if not m3u_name.strip() or m3u_name == "{list}":
                    if target_source == temp_spotdl_file and 'song_list' in locals() and song_list:
                        m3u_name = song_list[0].get("list_name") or song_list[0].get("album_name") or "{list}"
                    else:
                        m3u_name = "{list}"
                cmd.extend(["--m3u", f"temp_dl_{m3u_name}.m3u"])
                
        elif request.tool == "ytdl":
            batch_file_path = None
            if flags.get("check_navidrome") and navi_url and navi_user and navi_pass:
                f.write("[Deduplikacja] Pobieranie metadanych z YouTube / SoundCloud do weryfikacji w Navidrome...\n")
                f.flush()
                try:
                    meta_entries = navidrome_dedup.extract_ytdl_metadata(request.url)
                    if meta_entries:
                        skip_mode = flags.get("navidrome_skip_mode", "flac_only")
                        def d_log(msg):
                            f.write(f"{msg}\n")
                            f.flush()

                        kept, skipped = navidrome_dedup.filter_existing_songs(
                            songs=meta_entries,
                            navi_url=navi_url,
                            navi_user=navi_user,
                            navi_pass=navi_pass,
                            skip_mode=skip_mode,
                            log_callback=d_log
                        )


                        if not kept:
                            mode_desc = "lepszej jakości (FLAC/Lossless)" if skip_mode == "flac_only" else "bibliotece"
                            f.write(f"\n[Deduplikacja] Wszystkie utwory ({len(skipped)}) znajdują się już w Twojej bibliotece Navidrome w {mode_desc}! Pomijam pobieranie.\n")
                            return

                        if skipped:
                            batch_file_path = os.path.join(SPOTDL_CONFIG_DIR, f"temp_ytdl_{task_id}.txt")
                            with open(batch_file_path, "w", encoding="utf-8") as bf:
                                for item in kept:
                                    if item.get("url"):
                                        bf.write(f"{item['url']}\n")
                            f.write(f"[Deduplikacja] Pobieranie pozostałych {len(kept)} z {len(meta_entries)} utworów...\n")
                            f.flush()
                except Exception as e:
                    f.write(f"[Deduplikacja] Ostrzeżenie przy sprawdzaniu duplikatów: {e}. Kontynuuję normalne pobieranie...\n")
                    f.flush()

            if 'batch_file_path' in locals() and batch_file_path and os.path.exists(batch_file_path):
                cmd = ["yt-dlp", "-a", batch_file_path]
            else:
                cmd = ["yt-dlp", request.url]

            if request.flags.get("audio_only"):
                cmd.extend(["-x", "--audio-format", "mp3", "--embed-thumbnail", "--embed-metadata"])
            if request.flags.get("skip_downloaded"):
                cmd.append("--no-overwrites")
        else:
            return

        use_beets = (BEETS_ENABLED and (request.tool == "spotdl" or flags.get("audio_only")))
        if use_beets and flags.get("skip_downloaded") and not flags.get("_bypass_archive"):
            archive_flag = "--archive" if request.tool == "spotdl" else "--download-archive"
            archive_name = "spotdl-archive.txt" if request.tool == "spotdl" else "yt-dlp-archive.txt"
            cmd.extend([archive_flag, os.path.join(SPOTDL_CONFIG_DIR, archive_name)])
        f.write(f"Wykonywanie komendy: {' '.join(cmd)}\n")
        f.flush()
        
        try:
            # Setting PYTHONUNBUFFERED can help, but subprocess stdout parsing is what we do here
            download_dir = DOWNLOADS_DIR
            import_result = {}
            if use_beets:
                download_dir = os.path.join(BEETS_INBOX_DIR, task_id)
                os.makedirs(download_dir, exist_ok=True)
            elif BEETS_ENABLED and flags.get("generate_m3u"):
                f.write("[beets] Pobranie z playlistą zachowuje dotychczasowe ścieżki w bibliotece.\n")
            job_store.check_cancelled()
            process = subprocess.Popen(
                cmd,
                cwd=download_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True
            )
            output_tail = acquisition.log_process(process, f, timeout=download_timeout)
            job_store.check_cancelled()
            if process.returncode:
                error = acquisition.download_error(output_tail, process.returncode)
                f.write(f"\n{error}\n")
                return {"returncode": process.returncode, "download_dir": download_dir, "error": error,
                        "retryable": acquisition.download_retryable(error)}

            if use_beets:
                if process.returncode != 0:
                    f.write(f"\n[Zakończono z kodem {process.returncode}] Pliki pozostały w katalogu pobrań; import pominięto.\n")
                    return {"returncode": process.returncode, "download_dir": download_dir}
                source_files = acquisition.verified_audio_files(download_dir)
                if defer_import:
                    return {"returncode": 0, "download_dir": download_dir, "source_files": source_files}
                with open(os.path.join(download_dir, ".ready"), "w", encoding="utf-8"):
                    pass
                import_request = BeetsImportRequest(
                    navi_sync=request.navi_sync, navi_account_id=request.navi_account_id,
                    navi_url=request.navi_url, navi_user=request.navi_user, navi_pass=request.navi_pass)
                if activity_callback:
                    activity_callback("importing")
                import_result = run_beets_import_task(task_id, import_request,
                                      log_callback=lambda message: (f.write(message + "\n"), f.flush()),
                                      defer_finalize=defer_finalize)
            
            # Ensure downloaded files have correct PUID:PGID and permissions
            if not use_beets:
                f.write(f"\n[Uprawnienia] Ustawianie praw dostepu (PUID:PGID {PUID}:{PGID})...\n")
                f.flush()
                fix_download_permissions(DOWNLOADS_DIR)
            
            # Navidrome Integration
            if not use_beets and request.navi_sync and navi_url and navi_user and navi_pass:
                f.write("\n[Navidrome] Rozpoczynanie integracji tworzenia natywnej playlisty...\n")
                f.flush()
                try:
                    def call_navi(endpoint, extra_params=None):
                        job_store.check_cancelled()
                        return navidrome_dedup.subsonic_request(
                            base_url=navi_url,
                            user=navi_user,
                            password=navi_pass,
                            endpoint=endpoint,
                            extra_params=extra_params,
                            timeout=10
                        )

                    # 1. Start Scan
                    call_navi("startScan.view")
                    f.write("[Navidrome] Skanowanie rozpoczete. Oczekuje 15 sekund...\n")

                    f.flush()
                    time.sleep(15)
                    
                    if request.flags.get("generate_m3u"):
                        f.write("[Navidrome] Szukam tymczasowej playlisty z pliku .m3u...\n")
                        f.flush()
                        
                        # 2. Get Playlists
                        res_playlists = call_navi("getPlaylists.view")
                        playlists = res_playlists.get("subsonic-response", {}).get("playlists", {}).get("playlist", [])
                        
                        target_playlist_id = None
                        actual_playlist_name = ""
                        for p in playlists:
                            name = p.get("name", "")
                            if name.startswith("temp_dl_"):
                                target_playlist_id = p.get("id")
                                actual_playlist_name = name[8:] # strip 'temp_dl_'
                                break
                                
                        if target_playlist_id:
                            f.write(f"[Navidrome] Znaleziono playliste (ID: {target_playlist_id}). Pobieranie utworow...\n")
                            # 3. Get Playlist tracks
                            res_tracks = call_navi("getPlaylist.view", {"id": target_playlist_id})
                            tracks = res_tracks.get("subsonic-response", {}).get("playlist", {}).get("entry", [])
                            song_ids = [t.get("id") for t in tracks]
                            
                            if song_ids:
                                f.write(f"[Navidrome] Znaleziono {len(song_ids)} utworow. Tworzenie natywnej playlisty '{actual_playlist_name}'...\n")
                                # 4. Create Native Playlist
                                res_create = call_navi("createPlaylist.view", {
                                    "name": actual_playlist_name,
                                    "songId": song_ids
                                })
                                new_playlist = res_create.get("subsonic-response", {}).get("playlist", {})
                                new_id = new_playlist.get("id")
                                    
                                f.write("[Navidrome] Natywna playlista zostala utworzona pomyslnie!\n")
                                
                                if request.flags.get("public_playlist") and new_id:
                                    f.write(f"[Navidrome] Ustawianie playlisty (ID: {new_id}) jako publicznej...\n")
                                    call_navi("updatePlaylist.view", {"playlistId": new_id, "public": "true"})
                                        
                                time.sleep(2)
                                
                                # Explicitly delete the temporary imported playlist from Navidrome DB
                                f.write(f"[Navidrome] Usuwanie tymczasowej playlisty z bazy (ID: {target_playlist_id})...\n")
                                call_navi("deletePlaylist.view", {"id": target_playlist_id})
                            else:
                                f.write("[Navidrome] Tymczasowa playlista jest pusta, pomijam tworzenie natywnej.\n")
                                # Still try to delete it from DB if empty
                                call_navi("deletePlaylist.view", {"id": target_playlist_id})
                        else:
                            f.write("[Navidrome] Nie znaleziono tymczasowej playlisty w bazie Navidrome.\n")
                            
                        # 5. Delete m3u files
                        f.write("[Navidrome] Usuwanie plikow tymczasowych z dysku...\n")
                        expected = f"temp_dl_{flags.get('m3u_name') or '{list}'}.m3u"
                        m3u_files = [os.path.join(DOWNLOADS_DIR, expected)] if os.path.basename(expected) == expected else []
                        for m3u in m3u_files:
                            try:
                                os.remove(m3u)
                                f.write(f" -> Usunieto: {os.path.basename(m3u)}\n")
                            except:
                                pass
                            
                        # 6. Final Scan to prune old playlist references
                        f.write("[Navidrome] Koncowe skanowanie sprzatajace...\n")
                        call_navi("startScan.view")
                    else:
                        f.write("[Navidrome] Zakonczono pobieranie. (Brak playlist do konwersji)\n")
                        
                except Exception as e:
                    f.write(f"[Navidrome] Blad integracji: {str(e)}\n")
            
            # Clean up temporary files if present
            if os.path.exists(temp_spotdl_file):
                try:
                    os.remove(temp_spotdl_file)
                except:
                    pass
            if 'batch_file_path' in locals() and batch_file_path and os.path.exists(batch_file_path):
                try:
                    os.remove(batch_file_path)
                except:
                    pass

            f.write(f"\n[Zakończono z kodem {process.returncode}]\n")
            return {"returncode": process.returncode, "download_dir": download_dir,
                    "import_paths": (import_result or {}).get("paths", []),
                    "import_errors": (import_result or {}).get("errors", []),
                    "import_deferred": import_result is None}
            
        except Exception as e:
            if os.path.exists(temp_spotdl_file):
                try:
                    os.remove(temp_spotdl_file)
                except:
                    pass
            if 'batch_file_path' in locals() and batch_file_path and os.path.exists(batch_file_path):
                try:
                    os.remove(batch_file_path)
                except:
                    pass
            f.write(f"\n[Błąd]: {str(e)}\n")
            return {"returncode": 1, "error": str(e)}


@app.post("/api/download")
async def start_download(request: DownloadRequest, background_tasks: BackgroundTasks):
    if request.tool not in ["ytdl", "spotdl", "soulseek"]:
        raise HTTPException(status_code=400, detail="Invalid tool selected")
    if not request.url:
        raise HTTPException(status_code=400, detail="URL is required")
        
    if request.tool == "soulseek" and not BEETS_ENABLED:
        raise HTTPException(400, "Automatyczne pobieranie Soulseek wymaga włączonego beets")
    task_id = get_job_store().enqueue("download", request.model_dump())["id"]
    job_log(task_id, "[Kolejka] Zapisano zadanie. Postęp zostanie zachowany po restarcie.")
    background_tasks.add_task(run_pipeline_job, task_id)
    
    return {"status": "started", "task_id": task_id, "message": f"Rozpoczęto pobieranie w tle"}
 
@app.post("/api/slskd/organize")
async def start_slskd_organize(request: SlskdOrganizeRequest, background_tasks: BackgroundTasks):
    effective_url = request.slskd_url
    if not effective_url:
        acc = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
        if acc:
            effective_url = acc.get("server", "")

    if not effective_url:
        raise HTTPException(status_code=400, detail="Brak skonfigurowanego serwera Soulseek (slskd) w slskd_accounts.txt ani w żądaniu")
        
    task_id = str(uuid.uuid4())
    if BEETS_ENABLED:
        validate_beets_job()
    get_job_store().enqueue("import" if BEETS_ENABLED else "organize", request.model_dump(), job_id=task_id)
    background_tasks.add_task(run_pipeline_job, task_id)
    
    return {"status": "started", "task_id": task_id, "message": "Rozpoczęto porządkowanie plików z Soulseeka"}


@app.get("/api/logs/{task_id}")
async def stream_logs(task_id: str):
    log_file_path = os.path.join(LOGS_DIR, f"{task_id}.log")
    
    async def event_generator():
        # wait a bit for file to be created
        for _ in range(20):
            if os.path.exists(log_file_path):
                break
            await asyncio.sleep(0.1)
            
        if not os.path.exists(log_file_path):
            yield f"data: Brak pliku logów\n\n"
            return
            
        with open(log_file_path, "r", encoding="utf-8") as f:
            while True:
                line = f.readline()
                if line:
                    # yield it in SSE format
                    yield f"data: {line}\n\n"
                    if "[Zakończono" in line or "[Błąd]" in line:
                        job = get_job_store().get(task_id)
                        if not job or job["status"] in {"done", "failed", "needs_review"}:
                            break
                else:
                    job = get_job_store().get(task_id)
                    if job and job["status"] in {"done", "failed", "needs_review"}:
                        yield f"data: [Kolejka] {job['status']}\n\n"
                        break
                    await asyncio.sleep(0.5)
                    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/errors")
async def get_errors():
    errors_file = os.path.join(SPOTDL_CONFIG_DIR, "nadal_brakujace.txt")
    if not os.path.exists(errors_file):
        return {"errors": []}
    try:
        with open(errors_file, "r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                return {"errors": []}
            
            # Try parsing as JSON (spotdl v4 uses JSON for --save-errors)
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    return {"errors": [{"name": item.get("name", "Nieznana nazwa"), "url": item.get("url", "")} for item in data]}
            except json.JSONDecodeError:
                # Fallback to text lines if not JSON
                lines = [line.strip() for line in content.split('\n') if line.strip()]
                return {"errors": [{"name": line, "url": ""} for line in lines]}
    except Exception as e:
        return {"errors": []}
        
@app.get("/api/wallpapers")
async def get_wallpapers():
    try:
        files = os.listdir(WALLPAPERS_DIR)
        images = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))]
        return {"wallpapers": images}
    except Exception as e:
        return {"wallpapers": [], "error": str(e)}

# Serve wallpapers separately so frontend can load them
app.mount("/wallpapers", StaticFiles(directory=WALLPAPERS_DIR), name="wallpapers")
# Serve static files (Frontend) - MUST be at the bottom!
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
