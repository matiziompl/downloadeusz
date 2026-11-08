"""Conservative beets imports of completed downloads, keeping source files intact."""

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import mutagen

try:
    from backend import album_art, job_store
    from backend.audio_identity import audio_packet_signature, library_write_lock
    from backend.music_metadata import usable_album
except ImportError:
    import album_art
    import job_store
    from audio_identity import audio_packet_signature, library_write_lock
    from music_metadata import usable_album


AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".alac", ".ape", ".wv"}
PLUGIN_DIR = Path(__file__).resolve().parent / "beetsplug"
RECOGNITION_VERSION = 3
ENRICH_INTERVAL = 86400


def _contains(parent: Path, path: Path) -> bool:
    return path == parent or parent in path.parents


def _input_files(path: Path, source: Path) -> list[Path]:
    if not path.exists() or not _contains(source, path.resolve()):
        raise ValueError(f"Ścieżka poza katalogiem pobrań lub nie istnieje: {path}")
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError(f"Dowiązanie symboliczne w katalogu pobrań: {path}")
    files = []
    entries = path.rglob("*") if path.is_dir() else [path]
    for entry in entries:
        if entry.is_symlink():
            raise ValueError(f"Dowiązanie symboliczne w katalogu pobrań: {entry}")
        if entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS:
            if entry.stat().st_nlink > 1:
                raise ValueError(f"Plik audio ma dowiązania twarde; skopiuj go do osobnego pliku przed importem: {entry}")
            files.append(entry)
    return sorted(files)


def _fingerprint(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        stat = path.stat()
        digest.update(os.fsencode(str(path)))
        digest.update(f"\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode())
    return digest.hexdigest()


def _content_fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as audio:
        for chunk in iter(lambda: audio.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_sources(files, previous):
    if previous.get("legacy_sources"):
        return previous["legacy_sources"]
    # Bound upgrade recovery to common additions; never guess old source membership or destination paths.
    if len(files) > 64:
        return []
    candidates = [[track] for track in files]
    candidates.extend(files[:end] for end in range(2, len(files)))
    candidates.extend(files[:index] + files[index + 1:] for index in range(len(files)))
    for old_files in candidates:
        if old_files and _fingerprint(old_files) == previous.get("fingerprint"):
            return [str(track.resolve()) for track in old_files]
    return []


def _write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _load_library_state(config_dir):
    path = Path(config_dir) / "beets" / "library-state.json"
    if not path.exists():
        return {"version": 1, "tracks": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("version") != 1 or not isinstance(state.get("tracks"), dict):
            raise ValueError("nieobsługiwany format")
        for identity, entry in state["tracks"].items():
            if not isinstance(entry, dict) or entry.get("id") != identity or not isinstance(entry.get("source"), str):
                raise ValueError("nieprawidłowy wpis utworu")
            if entry.get("next_attempt") is not None and not isinstance(entry["next_attempt"], (int, float)):
                raise ValueError("nieprawidłowy termin ponowienia")
        return state
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Nieprawidłowy stan biblioteki beets ({path}): {exc}") from exc


def list_review(config_dir) -> list[dict]:
    """Read exceptions without changing files or contacting metadata services."""
    return [entry for entry in _load_library_state(config_dir)["tracks"].values()
            if entry.get("status") == "needs_review" or not entry.get("metadata_confirmed")
            or not entry.get("release_confirmed") or entry.get("artwork_pending") or entry.get("source_changed")]


def _track_id(source):
    return hashlib.sha256(os.fsencode(str(Path(source).resolve()))).hexdigest()


def _library_file(path, dest):
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError("Brak zapisanej ścieżki kopii w bibliotece muzyki")
    path = Path(path).absolute()
    if not _contains(dest.resolve(), path.resolve()) or not path.is_file():
        raise ValueError(f"Plik nie istnieje w bibliotece muzyki: {path}")
    if any(part.is_symlink() for part in [path, *path.parents]) or path.stat().st_nlink > 1:
        raise ValueError(f"Dowiązanie zamiast osobnej kopii w bibliotece: {path}")
    return path


def _save_track(state, state_path, source, record, dest, *, audio_signature=None):
    source = Path(source).resolve()
    identity = _track_id(source)
    previous = state["tracks"].get(identity, {})
    now = time.time()
    entry = {
        "id": identity, "source": str(source), "destination": previous.get("destination"),
        "imported": previous.get("imported", False),
        "metadata_confirmed": previous.get("metadata_confirmed", False),
        "release_confirmed": previous.get("release_confirmed", False),
        "album_source": previous.get("album_source", ""),
        "artwork_pending": previous.get("artwork_pending", False),
        "source_changed": previous.get("source_changed", False),
        "source_fingerprint": previous.get("source_fingerprint", ""),
        "destination_fingerprint": previous.get("destination_fingerprint", ""),
        "destination_audio_signature": previous.get("destination_audio_signature", ""),
        "reason": record.get("reason") or previous.get("reason", "Brak pewnego rozpoznania"),
        "candidates": (record.get("candidates") or previous.get("candidates", []))[:3],
        "last_attempt": now, "next_attempt": now + ENRICH_INTERVAL,
    }
    if source.is_file() and not entry["source_fingerprint"] and not previous.get("imported"):
        entry["source_fingerprint"] = _fingerprint([source])
    if record.get("event") == "imported":
        destination = _library_file(record["destination"], dest)
        if audio_signature is None:
            try:
                audio_signature = audio_packet_signature(destination)
            except RuntimeError:
                # Preserve the copied-file record even when audio cannot be read.
                # Enrichment may migrate it only from a trusted unchanged baseline.
                audio_signature = ""
        entry.update(destination=str(destination), imported=True,
                     album_source=record.get("album_source", entry["album_source"]),
                     metadata_confirmed=bool(record.get("metadata_confirmed")),
                     release_confirmed=bool(record.get("release_confirmed")),
                     destination_fingerprint=_content_fingerprint(destination),
                     destination_audio_signature=audio_signature,
                     artwork_pending=album_art.artwork_pending(destination))
        if (not entry["metadata_confirmed"] and previous.get("reason")
                and record.get("reason") in {"Zachowano istniejące tagi", "Zaimportowano według istniejących tagów"}):
            entry["reason"] = previous["reason"]
    entry["status"] = "confirmed" if entry["metadata_confirmed"] else "imported" if entry["imported"] else "needs_review"
    if entry["source_changed"]:
        warning = "Źródło zostało zmienione; istniejąca kopia została zachowana. Nowe nagranie umieść pod inną ścieżką"
        if entry["reason"].startswith("Źródło zostało zmienione"):
            entry["reason"] = warning
        elif warning not in entry["reason"]:
            entry["reason"] += " " + warning
    if entry["metadata_confirmed"] and entry["release_confirmed"] and not entry["artwork_pending"] and not entry["source_changed"]:
        entry["next_attempt"] = None
    state["tracks"][identity] = entry
    _write_json(state_path, state)
    return entry


def _usable_tags(path):
    """Validate actual tags, never the fallback names used by the old organizer."""
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return False
        values = []
        for key, id3_key in (("artist", "TPE1"), ("title", "TIT2")):
            value = audio.get(key) or audio.get(id3_key)
            value = getattr(value, "text", value)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            values.append(str(value or "").strip())
        placeholders = {"unknown", "unknown artist", "unknown title", "nieznany wykonawca", "nieznany tytuł"}
        return all(value and value.casefold() not in placeholders for value in values)
    except (OSError, ValueError, mutagen.MutagenError):
        return False


def _needs_album_fallback(path):
    if not _usable_tags(path):
        return False
    audio = mutagen.File(path, easy=True)
    value = audio.get("album") or audio.get("TALB")
    value = getattr(value, "text", value)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return not usable_album(value)


def _managed_config(state_dir: Path, dest: Path) -> dict:
    return {
        "directory": str(dest),
        "library": str(state_dir / "library.db"),
        "statefile": str(state_dir / "beets-state.pickle"),
        "plugins": ["downloadeusz", "fromfilename", "musicbrainz", "fetchart", "embedart"],
        "pluginpath": [str(PLUGIN_DIR)],
        "disabled_plugins": [],
        "include": [],
        "threaded": False,
        "asciify_paths": False,
        "art_filename": "cover",
        "per_disc_numbering": True,
        "paths": {
            "downloadeusz_album_source:fallback": "$artist/$album/$title",
            "disctotal:2..": "%if{$albumartist,$albumartist,$artist}/%if{$album,%if{$year,$year - }$album%aunique{},_Single}/$disc-$track $title",
            "default": "%if{$albumartist,$albumartist,$artist}/%if{$album,%if{$year,$year - }$album%aunique{},_Single}/%if{$track,$track }$title",
            "singleton": "%if{$albumartist,$albumartist,$artist}/%if{$album,%if{$year,$year - }$album,_Single}/%if{$album,%if{$track,$track }}$title",
        },
        "import": {
            "copy": True, "move": False, "delete": False,
            "link": False, "hardlink": False, "reflink": False,
            "write": True, "autotag": True, "quiet": True,
            "quiet_fallback": "skip", "none_rec_action": "skip",
            "duplicate_action": "skip", "resume": False,
            "incremental": False, "timid": False,
        },
        "fetchart": {"auto": True, "sources": ["filesystem", "coverart"], "maxwidth": 1200},
        "embedart": {"auto": True, "remove_art_file": False, "clearart_on_import": False},
        "ui": {"color": False},
    }


def _preflight(base, environment):
    for name in ("downloadeusz.py", "downloadeusz_policy.py"):
        if not (PLUGIN_DIR / name).is_file():
            raise RuntimeError(f"Brak wymaganej wtyczki bezpieczeństwa beets: {name}")
    process = subprocess.run(base + ["version"], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, errors="replace", env=environment, timeout=30)
    loaded = set()
    for line in process.stdout.splitlines():
        if line.startswith("plugins:"):
            loaded = {name.strip() for name in line.partition(":")[2].split(",")}
    if process.returncode or "error loading plugin" in process.stderr.lower() or "downloadeusz" not in loaded:
        raise RuntimeError("Nie załadowano wymaganej wtyczki bezpieczeństwa beets; przebuduj obraz Dockera. "
                           + process.stderr.strip())


def _attempt_import(base, path, review_path, environment, dest, log, *, on_record=None, autotag=True, in_place=False):
    """Return verified copied paths and only the decisions from this CLI attempt."""
    offset = review_path.stat().st_size if review_path.exists() else 0
    batch = uuid.uuid4().hex
    command = base + [
        "import", "-q", "-C" if in_place else "-c", "-M", "-w", "-a" if autotag else "-A", "-P", "-I",
        "--quiet-fallback", "skip", "--set", f"downloadeusz_batch={batch}",
        "-l", str(review_path), str(path),
    ]
    if path.is_file():
        command.insert(len(base) + 1, "-s")
    records = []
    with tempfile.NamedTemporaryFile(prefix="import-report-", suffix=".jsonl", dir=review_path.parent) as report:
        attempt_env = {**environment, "DOWNLOADEUSZ_BEETS_REPORT": report.name}
        if in_place:
            attempt_env["DOWNLOADEUSZ_BEETS_ENRICH"] = "1"
        try:
            proc = subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, errors="replace", env=attempt_env, timeout=900,
            )
        finally:
            for line in Path(report.name).read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                records.append(record)
                if on_record:
                    on_record(record)
    for output in (proc.stdout, proc.stderr):
        if output and output.strip():
            log(output.strip())
    if proc.returncode:
        raise RuntimeError(f"Import {path.name} zakończony błędem (kod {proc.returncode})")
    listing = subprocess.run(
        base + ["list", "-f", "$path", f"downloadeusz_batch:{batch}"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        errors="replace", env=environment, timeout=30,
    )
    if listing.returncode:
        raise RuntimeError(f"Nie udało się odczytać wyniku importu {path.name}: {listing.stderr.strip()}")
    imported = {
        line for line in listing.stdout.splitlines()
        if Path(line).is_file() and _contains(dest, Path(line).resolve())
    }
    imported.update(str(_library_file(record["destination"], dest)) for record in records if record.get("event") == "imported")
    if imported and not any(record.get("event") == "imported" for record in records):
        raise RuntimeError("Beets nie zwrócił raportu importu; nie można bezpiecznie potwierdzić zapisanych metadanych")
    decisions = set()
    if review_path.exists():
        with review_path.open("rb") as review:
            review.seek(offset)
            decisions = {line.split(" ", 1)[0] for line in review.read().decode("utf-8", errors="replace").splitlines()}
    decision = "duplicate" if any(value.startswith("duplicate-") for value in decisions) else "unmatched" if "skip" in decisions else "finished"
    return imported, decision


def import_paths(
    paths: list[str], *, source_dir: str, dest_dir: str, config_dir: str,
    log_callback=None, retry: bool = False,
) -> dict:
    """Import completed inputs once per fingerprint; uncertain matches stay at source."""
    result = {"organized": 0, "skipped": 0, "errors": []}

    def log(message):
        if log_callback:
            log_callback(f"[beets] {message}")

    def error(message):
        result["errors"].append(message)
        log(message)

    if not paths:
        return result
    try:
        source = Path(source_dir).absolute()
        dest = Path(dest_dir).resolve()
        state_dir = (Path(config_dir) / "beets").resolve()
        if _contains(source.resolve(), dest) or _contains(dest, source.resolve()):
            raise ValueError("Katalog źródłowy i biblioteka muzyki nie mogą się nakładać")
        if _contains(source.resolve(), state_dir) or _contains(dest, state_dir):
            raise ValueError("Konfiguracja beets musi znajdować się poza muzyką i pobraniami")
        inputs = []
        for raw in dict.fromkeys(paths):
            path = Path(raw).absolute()
            files = _input_files(path, source.resolve())
            if files:
                inputs.append((path, files, _fingerprint(files)))
        if not inputs:
            return result
        state_dir.mkdir(parents=True, exist_ok=True)
        with (state_dir / "import.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                error("Inny import beets już trwa; spróbuj ponownie po jego zakończeniu")
                return result
            executable = shutil.which("beet")
            if not executable:
                error("Brak programu beet. Zainstaluj zależności beets lub przebuduj obraz Dockera")
                return result
            if not (PLUGIN_DIR / "downloadeusz.py").is_file():
                error("Brak wtyczki bezpieczeństwa beets; przywróć backend/beetsplug/downloadeusz.py")
                return result
            state_path = state_dir / "import-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            library_state = _load_library_state(config_dir)
            library_path = state_dir / "library-state.json"
            config_path = state_dir / "managed.yaml"
            settings = _managed_config(state_dir, dest)
            _write_json(config_path, settings)
            dest.mkdir(parents=True, exist_ok=True)
            with library_write_lock(dest):
                environment = {**os.environ, "BEETSDIR": str(state_dir)}
                base = [executable, "-c", str(config_path), "-l", str(state_dir / "library.db"), "-d", str(dest)]
                review_path = state_dir / "review.log"
                checked_plugins = False
                for path, files, fingerprint in inputs:
                    job_store.check_cancelled()
                    previous = state.get(str(path), {})
                    for track in files:
                        known = library_state["tracks"].get(_track_id(track))
                        if known and known.get("imported") and known.get("source_fingerprint") and known["source_fingerprint"] != _fingerprint([track]):
                            known["source_changed"] = True
                            _save_track(library_state, library_path, track,
                                        {"event": "review", "reason": "Źródło zostało zmienione; istniejąca kopia została zachowana"}, dest)
                    if previous.get("status") == "imported" and previous.get("recognition_version") != RECOGNITION_VERSION and not any(
                        _track_id(track) in library_state["tracks"] for track in files
                    ):
                        old_sources = _legacy_sources(files, previous) if previous.get("fingerprint") != fingerprint else []
                        if not old_sources:
                            result["skipped"] += len(files)
                            log(f"Starszy udany import {path.name}: nie można bezpiecznie odtworzyć mapowania plików; zachowano bibliotekę bez ponownego kopiowania")
                            continue
                        previous["legacy_sources"] = old_sources
                        state[str(path)] = previous
                        _write_json(state_path, state)
                    if previous.get("fingerprint") == fingerprint and (
                        previous.get("status") == "imported"
                        or (not retry and previous.get("recognition_version") == RECOGNITION_VERSION)
                    ):
                        result["skipped"] += len(files)
                        log(f"Bez zmian: {path.name} ({len(files)} plików), poprzedni wynik: {previous.get('status', 'skipped')}")
                        continue
                    legacy_sources = set(previous.get("legacy_sources", []))
                    new_files = [track for track in files if str(track.resolve()) not in legacy_sources
                                 and not library_state["tracks"].get(_track_id(track), {}).get("imported")]
                    result["skipped"] += len(files) - len(new_files)
                    if not new_files:
                        log(f"Pliki {path.name} mają już kopie w bibliotece; dalsze wzbogacanie odbywa się na tych kopiach")
                        continue
                    complete_group = len(new_files) == len(files)
                    files = new_files
                    effective_path = files[0] if len(files) == 1 else path
                    log(f"Rozpoznawanie po tagach i nazwach: {path.name} ({len(files)} plików)")
                    imported = set()
                    duplicate_sources = set()

                    def record_track(record):
                        record_source = str(Path(record.get("source", "")).resolve())
                        if record_source in {str(track.resolve()) for track in files}:
                            saved = _save_track(library_state, library_path, record_source, record, dest)
                            if record.get("event") == "imported":
                                imported.add(saved["destination"])

                    try:
                        if not checked_plugins:
                            _preflight(base, environment)
                            checked_plugins = True
                        if complete_group or len(files) == 1:
                            imported, decision = _attempt_import(base, effective_path, review_path, environment, dest, log, on_record=record_track)
                        else:
                            decision = "unmatched"
                        unmatched = []
                        if not imported and decision == "unmatched":
                            if len(files) > 1:
                                log(f"Brak pewnego dopasowania całego albumu {path.name}; sprawdzanie utworów pojedynczo")
                                for track in files:
                                    copied, track_decision = _attempt_import(base, track, review_path, environment, dest, log, on_record=record_track)
                                    imported.update(copied)
                                    if not copied and track_decision == "unmatched":
                                        unmatched.append(track)
                                    elif track_decision == "duplicate":
                                        duplicate_sources.add(track)
                                        log(f"Duplikat utworu: {track.name}; bez ponownego kopiowania")
                            else:
                                unmatched = files
                        elif decision == "duplicate":
                            log(f"Duplikat w bibliotece: {path.name}; bez rozbijania albumu na utwory")
                        if unmatched:
                            if shutil.which("fpcalc"):
                                fingerprint_config = state_dir / "managed-fingerprint.yaml"
                                fingerprint_settings = {
                                    **settings,
                                    "plugins": settings["plugins"] + ["chroma"],
                                    "chroma": {"auto": True},
                                    # Chroma resolves MusicBrainz IDs; adding that provider must not penalize the same source.
                                    "musicbrainz": {**settings.get("musicbrainz", {}), "data_source_mismatch_penalty": 0},
                                }
                                _write_json(fingerprint_config, fingerprint_settings)
                                fingerprint_base = [executable, "-c", str(fingerprint_config), "-l", str(state_dir / "library.db"), "-d", str(dest)]
                                for track in unmatched:
                                    log(f"Rozpoznawanie po dźwięku (Chromaprint/AcoustID): {track.name}")
                                    copied, track_decision = _attempt_import(fingerprint_base, track, review_path, environment, dest, log, on_record=record_track)
                                    imported.update(copied)
                                    if track_decision == "duplicate":
                                        duplicate_sources.add(track)
                                    if not copied:
                                        reason = "duplikat" if track_decision == "duplicate" else "brak pewnego dopasowania"
                                        log(f"Pominięto {track.name}: {reason}")
                            else:
                                log("Brak fpcalc do rozpoznawania po dźwięku. Przebuduj obraz Dockera i ponów import; oryginały pozostały na miejscu")
                        if decision != "duplicate":
                            fallback_path = state_dir / "managed-tags.yaml"
                            fallback_settings = {**settings, "import": {**settings["import"], "autotag": False},
                                                 "plugins": [name for name in settings["plugins"] if name not in ("fromfilename", "chroma")]}
                            for track in files:
                                if track in duplicate_sources or library_state["tracks"].get(_track_id(track), {}).get("imported"):
                                    continue
                                if _usable_tags(track):
                                    _write_json(fallback_path, fallback_settings)
                                    fallback_base = [executable, "-c", str(fallback_path), "-l", str(state_dir / "library.db"), "-d", str(dest)]
                                    log(f"Import według istniejących tagów: {track.name}; rozpoznanie pozostaje do sprawdzenia")
                                    copied, _ = _attempt_import(fallback_base, track, review_path, environment, dest, log, on_record=record_track, autotag=False)
                                    imported.update(copied)
                                else:
                                    record_track({"event": "review", "source": str(track),
                                                  "reason": "Brak użytecznych tagów wykonawcy i tytułu; plik wymaga sprawdzenia"})
                        state[str(path)] = {
                            "fingerprint": fingerprint,
                            "status": "imported" if len(imported) == len(files) else "duplicate" if decision == "duplicate" else "skipped",
                            "recognition_version": RECOGNITION_VERSION,
                            "legacy_sources": list(legacy_sources),
                        }
                        _write_json(state_path, state)
                        if not imported and decision != "duplicate":
                            log(f"Nie rozpoznano pewnie {path.name}; oryginały czekają na sprawdzenie")
                    except subprocess.TimeoutExpired:
                        error(f"Przekroczono limit czasu importu {path.name}; pliki źródłowe pozostały na miejscu")
                    except (OSError, RuntimeError) as exc:
                        error(f"Błąd importu {path.name}: {exc}")
                    finally:
                        result["organized"] += len(imported)
                        result["skipped"] += max(0, len(files) - len(imported))
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        error(str(exc))
    return result


def enrich_pending(*, config_dir: str, dest_dir: str, log_callback=None, retry: bool = False,
                   item_id=None, ready_sources=None) -> dict:
    """Enrich tracked library copies in place; missing originals do not block updates."""
    result = {"organized": 0, "skipped": 0, "errors": [], "updated": 0}

    def log(message):
        if log_callback:
            log_callback(f"[beets] {message}")

    try:
        state = _load_library_state(config_dir)
        if item_id is not None and item_id not in state["tracks"]:
            raise ValueError("Nie znaleziono zapisanego zadania do wzbogacenia")
        now = time.time()
        pending = [entry for entry in state["tracks"].values()
                   if (item_id is None or entry["id"] == item_id)
                   and (not entry.get("metadata_confirmed") or not entry.get("release_confirmed") or entry.get("artwork_pending") or entry.get("source_changed"))
                   and (retry or not entry.get("next_attempt") or entry["next_attempt"] <= now)]
        if not pending:
            return result
        eligible_sources = {Path(path).resolve() for path in (ready_sources or ())}
        # Source retries use import_paths' own lock; never call it while holding the enrichment lock.
        for entry in pending:
            job_store.check_cancelled()
            source = Path(entry["source"])
            if not entry.get("imported") and source.is_file() and source.resolve() in eligible_sources:
                imported = import_paths([str(source)], source_dir=str(source.parent), dest_dir=dest_dir,
                                        config_dir=config_dir, log_callback=log_callback, retry=True)
                for key in ("organized", "skipped"):
                    result[key] += imported[key]
                result["errors"].extend(imported["errors"])
        state_dir = (Path(config_dir) / "beets").resolve()
        with (state_dir / "import.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Inny import beets już trwa; ponów wzbogacanie po jego zakończeniu")
            state = _load_library_state(config_dir)
            state_path = state_dir / "library-state.json"
            dest = Path(dest_dir).resolve()
            with library_write_lock(dest):
                executable = shutil.which("beet")
                if not executable:
                    raise RuntimeError("Brak programu beet; przebuduj obraz Dockera")
                if not (PLUGIN_DIR / "downloadeusz.py").is_file():
                    raise RuntimeError("Brak wtyczki bezpieczeństwa beets; przywróć backend/beetsplug/downloadeusz.py")
                settings = _managed_config(state_dir, dest)
                settings["import"] = {**settings["import"], "copy": False, "move": False}
                config_path = state_dir / "managed-enrich.yaml"
                _write_json(config_path, settings)
                environment = {**os.environ, "BEETSDIR": str(state_dir)}
                base = [executable, "-c", str(config_path), "-l", str(state_dir / "library.db"), "-d", str(dest)]
                review_path = state_dir / "review.log"
                checked_plugins = False
                for old_entry in pending:
                    job_store.check_cancelled()
                    if not old_entry.get("imported"):
                        if not Path(old_entry["source"]).is_file():
                            _save_track(state, state_path, old_entry["source"],
                                        {"event": "review", "reason": "Brak pliku źródłowego; przywróć pobranie i ponów import"}, dest)
                            result["skipped"] += 1
                        continue
                    entry = state["tracks"][old_entry["id"]]
                    updated = False
                    backup = None
                    records = []
                    try:
                        destination = _library_file(entry["destination"], dest)
                        current_signature = audio_packet_signature(destination)
                        trusted_signature = entry.get("destination_audio_signature")
                        if not trusted_signature:
                            old_hash = entry.get("destination_fingerprint")
                            if old_hash and old_hash == _content_fingerprint(destination):
                                trusted_signature = current_signature
                            else:
                                source = Path(entry["source"])
                                if (entry.get("source_fingerprint") and source.is_file()
                                        and not any(part.is_symlink() for part in [source, *source.parents])
                                        and source.stat().st_nlink == 1
                                        and entry["source_fingerprint"] == _fingerprint([source])):
                                    trusted_signature = audio_packet_signature(source)
                        if not trusted_signature or trusted_signature != current_signature:
                            raise ValueError("Audio kopii w bibliotece zostało zmienione lub nie ma zaufanej tożsamości; sprawdź je przed wzbogacaniem")
                        entry["destination_audio_signature"] = trusted_signature
                        descriptor, backup_name = tempfile.mkstemp(prefix=".downloadeusz-enrich-", suffix=".bak", dir=destination.parent)
                        os.close(descriptor)
                        backup_candidate = Path(backup_name)
                        try:
                            shutil.copy2(destination, backup_candidate)
                        except OSError:
                            backup_candidate.unlink(missing_ok=True)
                            raise
                        backup = backup_candidate

                        def update_record(record):
                            if str(Path(record.get("source", "")).resolve()) != str(destination.resolve()):
                                return
                            records.append(record)

                        def commit_records():
                            nonlocal updated
                            for record in records:
                                saved = _save_track(state, state_path, entry["source"], record, dest)
                                if record.get("event") == "imported" and (saved.get("metadata_confirmed") or record.get("album_fallback_written")):
                                    updated = True
                            records.clear()

                        log(f"Wzbogacanie istniejącej kopii: {destination.name}")
                        entry_environment = {**environment,
                                             "DOWNLOADEUSZ_BEETS_ALBUM_SOURCE": entry.get("album_source", ""),
                                             "DOWNLOADEUSZ_BEETS_ALLOW_FALLBACK": "1" if retry else "0"}
                        decision = "finished"
                        if not entry.get("metadata_confirmed") or not entry.get("release_confirmed"):
                            if not checked_plugins:
                                _preflight(base, environment)
                                checked_plugins = True
                            _, decision = _attempt_import(base, destination, review_path, entry_environment, dest, log,
                                                          on_record=update_record, in_place=True)
                            commit_records()
                        if not updated and decision == "unmatched" and shutil.which("fpcalc"):
                            fingerprint_config = state_dir / "managed-enrich-fingerprint.yaml"
                            fingerprint_settings = {
                                **settings, "plugins": settings["plugins"] + ["chroma"], "chroma": {"auto": True},
                                "musicbrainz": {**settings.get("musicbrainz", {}), "data_source_mismatch_penalty": 0},
                            }
                            _write_json(fingerprint_config, fingerprint_settings)
                            fingerprint_base = [executable, "-c", str(fingerprint_config), "-l", str(state_dir / "library.db"), "-d", str(dest)]
                            _, decision = _attempt_import(fingerprint_base, destination, review_path, entry_environment, dest, log,
                                                          on_record=update_record, in_place=True)
                            commit_records()
                        if retry and not updated and decision != "duplicate" and _needs_album_fallback(destination):
                            _attempt_import(base, destination, review_path, entry_environment, dest, log,
                                            on_record=update_record, in_place=True, autotag=False)
                            commit_records()
                        if album_art.artwork_pending(destination) and album_art.enrich_artwork(destination, log_callback=log):
                            current = state["tracks"][entry["id"]]
                            _save_track(state, state_path, entry["source"],
                                        {"event": "imported", "destination": str(destination),
                                         "metadata_confirmed": current["metadata_confirmed"],
                                         "release_confirmed": current["release_confirmed"],
                                         "reason": "Uzupełniono brakującą okładkę istniejącej kopii"}, dest)
                            updated = True
                        if not updated:
                            _save_track(state, state_path, entry["source"],
                                        {"event": "review", "reason": state["tracks"][entry["id"]].get("reason") or "Brak nowego pewnego rozpoznania"}, dest)
                            result["skipped"] += 1
                    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                        reason = next((record.get("reason") for record in reversed(records) if record.get("reason")), str(exc))
                        if backup is not None:
                            try:
                                backup.replace(destination)
                                backup = None
                            except OSError as restore_error:
                                reason += f"; nie udało się przywrócić kopii ({backup}): {restore_error}"
                                backup = None
                        updated = False
                        state["tracks"][entry["id"]] = entry
                        result["errors"].append(reason)
                        _save_track(state, state_path, entry["source"],
                                    {"event": "review", "reason": reason}, dest)
                    finally:
                        if updated:
                            result["updated"] += 1
                        if backup is not None:
                            backup.unlink(missing_ok=True)
    except (OSError, ValueError, RuntimeError) as exc:
        result["errors"].append(str(exc))
        log(str(exc))
    return result
