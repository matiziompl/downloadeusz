"""Durable acquisition identities, album staging and bounded downloader processes."""

import atexit
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from collections import deque
from urllib.parse import urlsplit, urlunsplit

try:
    from backend import beets_importer, pipeline, job_store
    from backend.audio_identity import audio_packet_signature, library_write_lock
except ImportError:
    import beets_importer
    import pipeline
    import job_store
    from audio_identity import audio_packet_signature, library_write_lock


_ACTIVE_PROCESSES = set()
_PROCESS_LOCK = threading.Lock()


def source_identity(track, default_tool):
    service = track.get("source_tool") or default_tool
    url = track.get("url") or track.get("song_url")
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.hostname == "open.spotify.com":
        url = urlunsplit(("https", parsed.hostname, parsed.path, "", ""))
    return service, url


def remembered_paths(store, track, tool, library_dir):
    identity = source_identity(track, tool)
    if identity is None:
        return []
    files = store.source_files(*identity)
    # Multiple outputs for one recording have no safe source-to-file identity.
    if len(files) != 1:
        return []
    try:
        with library_write_lock(library_dir):
            item = files[0]
            path = pipeline.relative_audio_path(item["path"], library_dir)
            target = Path(library_dir) / path
            if target.stat().st_nlink != 1 or not item.get("audio_signature"):
                return []
            if audio_packet_signature(target) != item["audio_signature"]:
                return []
            return [path]
    except (KeyError, TypeError, OSError, ValueError, RuntimeError):
        return []


def remember_paths(store, track, tool, paths, library_dir):
    identity = source_identity(track, tool)
    if identity is None:
        return
    with library_write_lock(library_dir):
        files = []
        for value in paths:
            try:
                path = pipeline.relative_audio_path(value, library_dir)
                target = Path(library_dir) / path
                if target.stat().st_nlink != 1:
                    return
                files.append({"path": path, "audio_signature": audio_packet_signature(target)})
            except (OSError, ValueError, RuntimeError):
                return
        if files:
            store.remember_source(*identity, files)


def verified_audio_files(directory):
    root = Path(directory).absolute()
    files = beets_importer._input_files(root, root.resolve())
    if not files:
        raise RuntimeError("Pobieranie nie zwróciło pliku audio; pominięto import")
    for path in files:
        audio_packet_signature(path)
    return [str(path) for path in files]


def album_key(track):
    if track.get("source_tool") != "spotdl":
        return None
    album = track.get("album_id") or track.get("album_name") or track.get("album")
    if isinstance(album, dict):
        album = album.get("id") or album.get("name")
    if not album:
        return None
    artist = track.get("album_artist") or track.get("album_artists") or track.get("artists") or track.get("artist") or ""
    return hashlib.sha256(json.dumps([album, artist], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def stage_album(store, identity, rows, inbox_dir):
    """Move verified children together; durable target plans make interrupted renames resumable."""
    group = Path(inbox_dir) / (identity + "-album-" + album_key(rows[0]["payload"]))
    if any(part.is_symlink() for part in (group, *group.parents)):
        raise ValueError("Dowiązanie w katalogu albumu")
    group.mkdir(parents=True, exist_ok=True)
    moved = {}
    for row in rows:
        result = dict(row["result"])
        planned, original = [], result.get("download_files") or result.get("source_files", [])
        for index, raw in enumerate(original):
            source = Path(raw)
            if not source.absolute().is_relative_to(Path(inbox_dir).absolute()) or any(part.is_symlink() for part in (source, *source.parents)):
                raise ValueError("Plik albumu poza katalogiem pobrań")
            target = moved.setdefault(str(source), group / f"{row['position']:05d}-{index:03d}-{source.name}")
            planned.append(str(target))
        result.update(source_files=planned, download_files=original, source_task=group.name)
        store.update_track(identity, row["position"], status="downloaded", result=result)
        for raw, target in zip(original, planned):
            source, target = Path(raw), Path(target)
            if source.exists() and source != target:
                # Never replace a different staged recording after an interrupted rename.
                if target.exists():
                    if audio_packet_signature(source) != audio_packet_signature(target):
                        raise ValueError("Niejednoznaczny plik w katalogu albumu")
                else:
                    source.rename(target)
            if target.stat().st_nlink != 1:
                raise ValueError("Dowiązanie w katalogu albumu")
            audio_packet_signature(target)
    return group


def terminate_process(process):
    """The downloader owns a new session, including ffmpeg child processes."""
    pid = getattr(process, "pid", None)
    try:
        if isinstance(pid, int):
            os.killpg(pid, signal.SIGTERM)
        elif hasattr(process, "terminate"):
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except (subprocess.TimeoutExpired, TypeError):
        pass
    finally:
        try:
            if isinstance(pid, int):
                os.killpg(pid, signal.SIGKILL)
            elif hasattr(process, "kill"):
                process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except TypeError:
            process.wait()


def stop_active_downloads():
    with _PROCESS_LOCK:
        processes = tuple(_ACTIVE_PROCESSES)
    for process in processes:
        terminate_process(process)


atexit.register(stop_active_downloads)


def download_error(lines, returncode):
    errors = [line.strip() for line in (lines or []) if "error" in line.lower()]
    message = " · ".join(errors[-3:] or [line.strip() for line in (lines or [])][-3:])
    message = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", message)
    # Downloader diagnostics may echo signed URLs; keep public errors free of credentials.
    message = re.sub(r"https?://[^\s]+", "[adres źródła]", message)
    return f"Pobieranie zakończone błędem (kod {returncode}): {message[:1500] or 'brak szczegółów w wyjściu programu'}"


def download_retryable(error):
    return not any(marker in error.lower() for marker in (
        "video unavailable", "private video", "video is private", "video has been removed",
        "not available in your country", "sign in to confirm", "members-only content",
    ))


def log_process(process, output, timeout=900):
    """A blocked/no-newline stdout read cannot outlive the total download deadline."""
    messages = queue.Queue(maxsize=256)
    stop = threading.Event()
    deadline = time.monotonic() + timeout

    def publish(value):
        while not stop.is_set():
            try:
                messages.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def read_output():
        try:
            for line in process.stdout:
                if stop.is_set():
                    return
                publish(("line", line))
        except Exception as error:
            publish(("error", error))
        finally:
            publish(("done", None))

    # Test doubles may be unhashable; the live-process registry only owns actual children.
    registered = isinstance(getattr(process, "pid", None), int)
    if registered:
        with _PROCESS_LOCK:
            _ACTIVE_PROCESSES.add(process)
    reader = threading.Thread(target=read_output, daemon=True, name="downloader-output")
    reader.start()
    failed = True
    tail = deque(maxlen=20)
    try:
        while True:
            job_store.check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("downloader", timeout)
            try:
                kind, value = messages.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                continue
            if kind == "done":
                break
            if kind == "error":
                raise value
            output.write(value)
            output.flush()
            tail.append(value[-2000:])
        while True:
            job_store.check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("downloader", timeout)
            try:
                process.wait(timeout=min(remaining, 0.2))
                break
            except subprocess.TimeoutExpired:
                continue
            except TypeError:
                process.wait()
                break
        failed = False
    finally:
        stop.set()
        if failed or getattr(process, "returncode", None) not in (None, 0):
            terminate_process(process)
        if hasattr(process.stdout, "close"):
            process.stdout.close()
        reader.join(timeout=2)
        if registered:
            with _PROCESS_LOCK:
                _ACTIVE_PROCESSES.discard(process)
    return list(tail)
