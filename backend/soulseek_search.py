"""Bounded slskd search and enqueue; remote files never become local commands.

API verified against slskd/slskd commit 2b827344d11e7490b323defa898264cadbdb6089:
Search/API/Controllers/SearchesController.cs and Transfers/API/Controllers/TransfersController.cs.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID

try:
    from backend.navidrome_dedup import clean_string, is_lossless
    from backend.beetsplug.downloadeusz_policy import _duration, _usable, versions_conflict, normalized
    from backend.music_metadata import clean_track_metadata, clean_title
    from backend.slskd_organizer import get_slskd_auth_header, find_file_in_dir, AUDIO_EXTENSIONS
except ImportError:
    from navidrome_dedup import clean_string, is_lossless
    from beetsplug.downloadeusz_policy import _duration, _usable, versions_conflict, normalized
    from music_metadata import clean_track_metadata, clean_title
    from slskd_organizer import get_slskd_auth_header, find_file_in_dir, AUDIO_EXTENSIONS


def _result(track, state, message, *, selected=None, transfer_ids=None, retryable=False,
            expected_local_path=None):
    return {"state": state, "transfer_ids": transfer_ids or [], "selected": selected,
            "source": {"service": "soulseek", **selected} if selected else None,
            "track": dict(track), "message": message, "retryable": retryable,
            "expected_local_path": expected_local_path}


def _candidate(track, response, file):
    """Require whole artist and title fields inferred from ordinary Soulseek filenames."""
    filename = file.get("filename", "")
    if not isinstance(filename, str) or "://" in filename or any(ord(char) < 32 for char in filename):
        return None
    parts = [part for part in filename.replace('\\', '/').split('/') if part]
    if not parts or any(part in {".", ".."} for part in parts) or file.get("isLocked"):
        return None
    stem, extension = os.path.splitext(parts[-1])
    if extension.lower() not in AUDIO_EXTENSIONS or not _duration(file.get("size")):
        return None
    stem = re.sub(r'^\d+[\s._-]+', '', stem).strip()
    artist = str(track["artist"])
    candidate_title = stem
    artist_matches = False
    for segment in parts[:-1]:
        if normalized(segment) == normalized(artist):
            artist_matches = True
        if " - " in segment and normalized(segment.split(" - ", 1)[0]) == normalized(artist):
            artist_matches = True
    if " - " in stem:
        possible_artist, possible_title = stem.split(" - ", 1)
        if normalized(possible_artist) == normalized(artist):
            artist_matches = True
            candidate_title = possible_title
    candidate_title = clean_title(candidate_title)
    if not artist_matches or normalized(candidate_title) != normalized(track["title"]):
        return None
    if versions_conflict(track["title"], candidate_title):
        return None
    source_length = _duration(track.get("length") or track.get("duration"))
    length = _duration(file.get("length"))
    tolerance = max(2.0, min(5.0, source_length * 0.02))
    if source_length and length and abs(source_length - length) > tolerance:
        return None
    username = response.get("username")
    if not isinstance(username, str) or not username or any(ord(char) < 32 for char in username):
        return None
    return {"username": username, "filename": filename, "size": int(file["size"]),
            "length": length or None, "format": extension.lower().lstrip('.'),
            "title": candidate_title, "artist": artist, "album": parts[-2] if len(parts) > 1 else "",
            "has_free_upload_slot": bool(response.get("hasFreeUploadSlot")),
            "upload_speed": _duration(response.get("uploadSpeed")),
            "queue_length": _duration(response.get("queueLength")),
            "bit_depth": _duration(file.get("bitDepth")),
            "sample_rate": _duration(file.get("sampleRate")),
            "bit_rate": _duration(file.get("bitRate"))}


def _queries(identity):
    artist, title = identity["artist"], identity["title"]
    choices = [f"{artist} {title}", f"{artist.split()[-1]} {title}", title]
    words = re.findall(r"\w+", title)
    if len(words) > 5:
        stopwords = {"the", "a", "an", "and", "or", "of", "to", "in", "at", "from", "for", "with", "about"}
        distinctive = sorted((word for word in words if word.casefold() not in stopwords),
                             key=lambda word: -len(word))[:3]
        markers = [word for word in words if word.casefold() in {
            "live", "remix", "acoustic", "instrumental", "demo", "edit"}]
        compact = list(dict.fromkeys(distinctive + markers))
        choices.append(f"{artist} {' '.join(compact)}")
    queries, seen = [], set()
    for value in choices:
        query = " ".join(re.sub(r"[^\w\s]", " ", value).split()).lower()
        if query.casefold() not in seen:
            seen.add(query.casefold())
            queries.append(query)
    return queries


def search_and_queue(track: dict, *, url: str, username: str = '', password: str = '',
                     api_key: str = '', download_dir: str = '', progress_callback=None) -> dict:
    """Select one unambiguous recording and enqueue it through slskd's REST API.

    Results always contain state, transfer_ids, selected/source, track, message,
    retryable and expected_local_path. A missing/incomplete identity needs review;
    exhausted valid results are not_found. Retrying can exclude earlier peers via
    track['excluded_usernames']. No music is downloaded by this process itself.
    """
    identity = clean_track_metadata(track)
    artist, title = identity["artist"], identity["title"]
    if not _usable(title) or not _usable(artist):
        return _result(track, "needs_review", "Do wyszukiwania potrzebny jest wykonawca i tytuł nagrania")
    base = str(url or "").strip()
    if not base.startswith(('http://', 'https://')):
        base = "http://" + base
    parsed = urllib.parse.urlsplit(base)
    if not parsed.hostname or parsed.scheme not in {"http", "https"} or parsed.query or parsed.fragment:
        return _result(track, "needs_review", "Nieprawidłowy adres serwera slskd")
    base = base.rstrip('/') + "/api/v0"
    deadline = time.monotonic() + 60
    selected = None
    enqueue_attempted = False

    try:
        headers = get_slskd_auth_header(url, username=username, password=password, api_key=api_key, timeout=10)

        def request(path, payload=None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Przekroczono limit czasu wyszukiwania Soulseek")
            req = urllib.request.Request(base + path,
                data=json.dumps(payload).encode('utf-8') if payload is not None else None,
                headers={**headers, "Accept": "application/json", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=min(10, remaining)) as response:
                return json.loads(response.read().decode('utf-8'))

        excluded = {str(value).casefold() for value in track.get("excluded_usernames", [])}
        source_length = _duration(identity.get("length") or identity.get("duration"))
        found = {}
        candidates = []
        response_count = file_count = 0
        queries = _queries(identity)
        for index, query in enumerate(queries, 1):
            if time.monotonic() >= deadline:
                raise TimeoutError("Przekroczono limit czasu wyszukiwania Soulseek")
            if progress_callback:
                progress_callback({"query": query, "attempt": index, "total": len(queries)})
            # slskd passes the idle timeout directly to Soulseek.NET in milliseconds.
            search = request("/searches", {"searchText": query, "searchTimeout": 5000,
                                         "responseLimit": 100, "fileLimit": 1000})
            search_id = str(UUID(str(search.get("id", ""))))
            for attempt in range(60):
                search = request(f"/searches/{search_id}?includeResponses=true")
                complete = search.get("isComplete") or "completed" in str(search.get("state", "")).lower()
                # Completed is persisted before EndedAt and the final response list.
                if complete and search.get("endedAt"):
                    break
                if attempt < 59:
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
            else:
                return _result(track, "needs_review", "Wyszukiwanie Soulseek nie zakończyło się w limicie czasu", retryable=True)
            if "errored" in str(search.get("state", "")).lower():
                return _result(track, "needs_review", "Serwer slskd zakończył wyszukiwanie błędem", retryable=True)
            if "cancel" in str(search.get("state", "")).lower():
                return _result(track, "needs_review", "Wyszukiwanie slskd zostało anulowane; spróbuj ponownie", retryable=True)
            if time.monotonic() >= deadline:
                raise TimeoutError("Przekroczono limit czasu wyszukiwania Soulseek")

            for response in search.get("responses") or []:
                if not isinstance(response, dict):
                    continue
                response_count += 1
                file_count += len(response.get("files") or [])
                if str(response.get("username", "")).casefold() in excluded:
                    continue
                for file in response.get("files") or []:
                    candidate = _candidate(identity, response, file) if isinstance(file, dict) else None
                    if candidate:
                        key = (candidate["username"], candidate["filename"])
                        candidate["search_id"] = search_id
                        if key not in found or (not found[key]["length"] and candidate["length"]):
                            found[key] = candidate
            if source_length:
                candidates = [candidate for candidate in found.values() if candidate["length"]]
            else:
                lengths = [candidate["length"] for candidate in found.values() if candidate["length"]]
                if lengths and len(lengths) == len(found) and max(lengths) - min(lengths) <= max(2.0, min(5.0, min(lengths) * 0.02)):
                    candidates = list(found.values())
            if candidates:
                break
        if not found:
            return _result(track, "not_found", "Brak plików zgodnych z wykonawcą, tytułem, wersją i długością. "
                           f"Łącznie odebrano ze slskd — odpowiedzi: {response_count}, plików: {file_count}")
        if not candidates:
            if source_length:
                return _result(track, "needs_review", "Wyniki nie zawierają długości potrzebnej do potwierdzenia nagrania")
            return _result(track, "needs_review", "Kilka nagrań lub nieznana długość; potrzebne sprawdzenie")

        preferred = str(track.get("preferred_format") or "flac").lower().lstrip('.')
        album = clean_string(track.get("album", ""))

        def ranking(candidate):
            return (abs(source_length - candidate["length"]) if source_length else 0,
                    bool(album and clean_string(candidate["album"]) != album),
                    candidate["format"] != preferred, not is_lossless(candidate["format"]),
                    not candidate["has_free_upload_slot"], candidate["queue_length"],
                    -candidate["upload_speed"], -candidate["bit_depth"],
                    -candidate["sample_rate"], -candidate["bit_rate"],
                    candidate["username"], candidate["filename"])

        selected = min(candidates, key=ranking)
        enqueue_attempted = True
        queued = request("/transfers/downloads/" + urllib.parse.quote(selected["username"], safe=''),
                         [{"filename": selected["filename"], "size": selected["size"]}])
        enqueued = queued.get("enqueued", []) if isinstance(queued, dict) else queued
        ids = [str(transfer["id"]) for transfer in enqueued or []
               if isinstance(transfer, dict) and transfer.get("id")]
        if not ids:
            return _result(track, "needs_review", "slskd nie potwierdził dodania transferu; spróbuj innego źródła",
                           selected=selected, retryable=True)
        local = find_file_in_dir(selected["filename"], download_dir, allow_basename=False) if download_dir else None
        return _result(track, "queued", f"Dodano transfer Soulseek od {selected['username']}",
                       selected=selected, transfer_ids=ids, expected_local_path=local)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as error:
        message = ("Nieznany wynik dodawania transferu; sprawdź transfery slskd przed ponowieniem"
                   if enqueue_attempted else f"Błąd połączenia z Soulseek: {error}")
        return _result(track, "needs_review", message, selected=selected, retryable=not enqueue_attempted)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return _result(track, "needs_review", f"Nieprawidłowa odpowiedź Soulseek: {error}", selected=selected)
