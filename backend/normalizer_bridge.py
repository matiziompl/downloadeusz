"""Bounded, allowlisted access to the separate Normalizeusz file queue."""

import heapq
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path


OPERATIONS = {"preview_albums", "repair_albums", "scan_library"}
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MAX_JSON_BYTES = 512 * 1024


class PublicationUncertain(RuntimeError):
    """A visible request must remain tracked even when its directory flush fails."""


def safe_queue(queue):
    root = Path(queue).absolute()
    if any(part.is_symlink() for part in (root, *root.parents)) or not root.is_dir():
        raise ValueError("Brak bezpiecznego wspólnego katalogu Normalizeusza")
    for name in ("pending", "processing", "done", "progress"):
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Katalog kolejki Normalizeusza jest niekompletny lub zawiera dowiązanie")
    return root


def read_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_JSON_BYTES:
            raise ValueError("Nieprawidłowy plik protokołu Normalizeusza")
        value = json.loads(handle.read(MAX_JSON_BYTES + 1))
    if not isinstance(value, dict):
        raise ValueError("Nieprawidłowy dokument protokołu Normalizeusza")
    return value


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "\\" in value or Path(value).is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        return None
    return value


def number(value, default=0):
    return value if type(value) in (int, float) and 0 <= value < 10**15 else default


def clean_report(value, *, progress=False):
    if not isinstance(value, dict):
        return None
    report = {key: int(number(value.get(key))) for key in ("processed", "changed", "skipped", "errors")}
    if progress:
        report.update(total=number(value.get("total"), None), current_path=relative_path(value.get("current_path")))
        return report
    for name in ("samples", "error_samples"):
        report[name] = []
        rows = value.get(name, [])
        for item in rows[:100] if isinstance(rows, list) else []:
            if not isinstance(item, dict) or not (path := relative_path(item.get("path"))):
                continue
            fields = ("error",) if name == "error_samples" else ("title", "album")
            report[name].append({"path": path, **{key: str(item.get(key, ""))[:2000] for key in fields}})
    return report


def acknowledgement(queue, identity):
    if not SAFE_ID.fullmatch(identity):
        raise ValueError("Nieprawidłowy identyfikator Normalizeusza")
    root = safe_queue(queue)
    try:
        result = read_json(root / "done" / f"{identity}.json")
    except FileNotFoundError:
        return None
    if result.get("id") != identity or result.get("status") not in {"done", "failed"}:
        raise ValueError("Nieprawidłowe potwierdzenie Normalizeusza")
    return result


def request_present(queue, identity):
    if not SAFE_ID.fullmatch(identity):
        return False
    root = safe_queue(queue)
    for directory in ("pending", "processing"):
        try:
            if read_json(root / directory / f"{identity}.json").get("id") == identity:
                return True
        except (OSError, ValueError):
            continue
    return False


def snapshot(queue, enabled=False):
    value = {"enabled": bool(enabled), "available": False, "version": None, "capabilities": [],
             "message": "Normalizeusz jest niedostępny", "counts": {"pending": 0, "processing": 0}, "requests": []}
    try:
        root = safe_queue(queue)
        heartbeat = read_json(root / "heartbeat.json")
        value["version"] = str(heartbeat.get("version", ""))[:64] or None
        capabilities = heartbeat.get("capabilities", [])
        value["capabilities"] = [item for item in capabilities if item in OPERATIONS or item == "normalize"] if isinstance(capabilities, list) else []
        age = time.time() - number(heartbeat.get("updated_at"))
        value["available"] = (heartbeat.get("protocol_version") == 2 and -5 <= age <= 30
                              and all(os.access(root / name, os.W_OK) for name in ("pending", "processing", "done", "progress")))
        value["message"] = "Normalizeusz połączony" if value["available"] else "Brak aktualnego sygnału Normalizeusza lub praw zapisu kolejki"
    except (OSError, ValueError, TypeError):
        return value
    candidates = []
    for directory in ("pending", "processing", "done"):
        def entries():
            for path in (root / directory).glob("*.json"):
                try:
                    if SAFE_ID.fullmatch(path.stem) and path.is_file() and not path.is_symlink():
                        yield path.stat().st_mtime_ns, path
                except OSError:
                    continue
        recent = heapq.nlargest(20, entries(), key=lambda entry: entry[0])
        candidates.extend(path.stem for _, path in recent)
        if directory != "done":
            value["counts"][directory] = sum(1 for path in (root / directory).glob("*.json")
                                             if SAFE_ID.fullmatch(path.stem) and path.is_file() and not path.is_symlink())
    for identity in dict.fromkeys(candidates):
        for directory in ("done", "processing", "pending"):
            path = root / directory / f"{identity}.json"
            try:
                item = read_json(path)
                if item.get("id") != identity:
                    raise ValueError("Niezgodny identyfikator raportu")
                operation = item.get("operation", "normalize")
                if operation not in OPERATIONS | {"normalize"}:
                    raise ValueError("Nieznana operacja")
                status = item.get("status") if directory == "done" else directory
                if status not in {"pending", "processing", "done", "failed"}:
                    raise ValueError("Nieznany wynik operacji")
                row = {"id": identity, "operation": operation, "status": status,
                       "created_at": number(item.get("created_at"), path.stat().st_mtime),
                       "updated_at": number(item.get("completed_at"), path.stat().st_mtime),
                       "progress": None, "result": clean_report(item.get("result")),
                       "error": str(item.get("error", ""))[:2000]}
                try:
                    report = read_json(root / "progress" / f"{identity}.json")
                    if report.get("id") == identity:
                        row["progress"] = clean_report(report, progress=True)
                except (OSError, ValueError, TypeError):
                    pass
                value["requests"].append(row)
                break
            except (OSError, ValueError, TypeError):
                continue
    value["requests"] = sorted(value["requests"], key=lambda row: row["created_at"], reverse=True)[:20]
    return value


def publish(queue, identity, operation):
    if operation not in OPERATIONS or not SAFE_ID.fullmatch(identity):
        raise ValueError("Nieprawidłowe zlecenie Normalizeusza")
    state = snapshot(queue)
    if not state["available"] or operation not in state["capabilities"]:
        raise RuntimeError("Normalizeusz jest niedostępny lub nie obsługuje tej operacji")
    root = safe_queue(queue)
    if any((root / name / f"{identity}.json").exists() for name in ("pending", "processing", "done")):
        raise ValueError("Zlecenie Normalizeusza już istnieje")
    temporary = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root / "pending", prefix=".request-", delete=False) as output:
            temporary = Path(output.name)
            json.dump({"id": identity, "operation": operation, "created_at": time.time()}, output)
            os.fchmod(output.fileno(), 0o644)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(root / "pending" / f"{identity}.json")
        published = True
        descriptor = os.open(root / "pending", os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        if published:
            raise PublicationUncertain("Zlecenie jest widoczne, ale nie potwierdzono trwałości zapisu. "
                                       "Śledzimy jego wynik; nie wysyłaj go ponownie.") from error
        raise
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
