"""Exact audio-packet identity and the library's cross-process write lock."""

import fcntl
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from subprocess import DEVNULL, TimeoutExpired, run


def audio_packet_signature(path) -> str:
    """Hash demuxed audio packets without decoding or including container tags."""
    try:
        result = run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", os.fspath(path),
             "-map", "0:a", "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"],
            stdin=DEVNULL, capture_output=True, text=True, errors="replace", timeout=120,
        )
    except (OSError, TimeoutExpired) as exc:
        raise RuntimeError(f"Nie można obliczyć tożsamości audio {Path(path).name}: {exc}") from exc
    match = re.fullmatch(r"SHA256=([0-9a-fA-F]{64})\s*", result.stdout)
    if result.returncode or not match:
        raise RuntimeError(f"Nie można obliczyć tożsamości audio {Path(path).name}: "
                           + (result.stderr.strip() or "brak poprawnego strumienia audio"))
    return match[1].lower()


@contextmanager
def library_write_lock(dest):
    """Serialize beets and the ReplayGain sidecar using the same flock file."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(dest / ".downloadeusz-write.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o666)
    with os.fdopen(descriptor, "a") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Nieprawidłowy plik blokady biblioteki")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
