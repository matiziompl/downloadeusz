"""Recording identity checks independent of a particular album edition."""

import math
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher

try:
    from backend.music_metadata import usable_album
except ImportError:
    from music_metadata import usable_album


def field(value, name, default=""):
    result = value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)
    return default if result is None else result


def normalized(text):
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def version_markers(title):
    text = str(title or "").casefold()
    markers = {name for name in ("remix", "acoustic", "instrumental", "demo")
               if re.search(rf"\b{name}\b", text)}
    if re.search(r"\bradio[\s-]+edit\b", text):
        markers.add("radio edit")
    if re.search(r"[\[(]\s*live\b|[-–—]\s*live\b|\blive\s+(?:at|from|in|version)\b", text):
        markers.add("live")
    return markers


def versions_conflict(source_title, candidate_title):
    return version_markers(source_title) != version_markers(candidate_title)


def _title(text):
    text = re.sub(r"[\[(][^\])]*(?:remaster|deluxe|anniversary)[^\])]*[\])]", "", str(text or ""), flags=re.I)
    return normalized(text)


def _usable(text):
    return normalized(text) not in {"", "unknown", "unknown artist", "unknown title", "unknown album",
                                    "nieznany wykonawca", "nieznany tytul"}


def _duration(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0
    except (ValueError, TypeError):
        return 0


def recording_agrees(source, candidate):
    artist, title = field(source, "artist"), field(source, "title")
    if not _usable(artist) or not _usable(title):
        return False
    if normalized(artist) != normalized(field(candidate, "artist")):
        return False
    if versions_conflict(title, field(candidate, "title")):
        return False
    left, right = _title(title), _title(field(candidate, "title"))
    if left != right and (min(len(left), len(right)) < 8 or SequenceMatcher(None, left, right).ratio() < 0.94):
        return False
    source_length, candidate_length = _duration(field(source, "length", 0)), _duration(field(candidate, "length", 0))
    tolerance = max(2.0, min(5.0, source_length * 0.02))
    return bool(source_length and candidate_length and abs(source_length - candidate_length) <= tolerance)


def choose_recording(source, candidates):
    matching = [candidate for candidate in candidates if recording_agrees(source, candidate.info)]
    if not matching:
        return None, "Brak zgodności wykonawcy, tytułu, wersji nagrania lub długości"
    identities = {str(field(candidate.info, "track_id")) for candidate in matching}
    if len(identities) > 1 or "" in identities:
        return None, "Kilka różnych nagrań pasuje podobnie; potrzebne sprawdzenie"
    return matching[0], "Nagranie rozpoznane po wykonawcy, tytule i długości; wydanie oceniane osobno"


def release_agrees(source, candidate):
    if field(source, "album_source") == "fallback" or field(source, "downloadeusz_album_source") == "fallback":
        return False
    album = field(source, "album")
    source_year, candidate_year = field(source, "year", 0), field(candidate, "year", 0)
    return bool(usable_album(album) and normalized(album) == normalized(field(candidate, "album"))
                and source_year and candidate_year and str(source_year) == str(candidate_year))


def candidate_summaries(candidates, item=None):
    summaries = []
    for candidate in list(candidates or [])[:3]:
        album = candidate.info if hasattr(candidate, "mapping") else None
        info = candidate.mapping.get(item) if album is not None else candidate.info
        if info is None:
            continue
        summary = {"id": str(field(album, "album_id") if album is not None else field(info, "track_id")),
                          "artist": str(field(info, "artist")),
                          "title": str(field(info, "title") or field(info, "album")),
                          "album": str(field(album if album is not None else info, "album")),
                          "year": field(album if album is not None else info, "year", 0),
                          "length": field(info, "length", 0),
                          "distance": round(float(candidate.distance), 6)}
        # Persist provider metadata, not just its label: a later lookup can return different proposals.
        if isinstance(info, Mapping) and field(info, "track_id"):
            summary.update(kind="release" if album is not None else "recording", _track=dict(info),
                           track=field(info, "medium_index") or field(info, "index", 0),
                           disc=field(info, "medium", 0))
            details = album if album is not None else info
            for name in ("country", "media", "label", "catalognum"):
                summary[name] = field(details, name)
            summary["disambiguation"] = field(details, "albumdisambig") or field(details, "disambiguation")
            if album is not None:
                summary["_album"] = {key: value for key, value in album.items() if key != "tracks"}
                summary["_album_track_count"] = len(album.tracks)
            snapshot = json.dumps(summary, ensure_ascii=False, sort_keys=True)
            summary["selection_key"] = hashlib.sha256(snapshot.encode()).hexdigest()
        summaries.append(summary)
    return summaries
