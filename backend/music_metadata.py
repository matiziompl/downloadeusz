"""Conservative upload decoration cleanup shared by search and tag fallback."""

import re
import unicodedata
import json
from pathlib import Path


def _key(value):
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(re.sub(r"[\W_]+", " ", "".join(
        char for char in value if not unicodedata.combining(char))).split())


def _artist(value):
    value = str(value or "").strip()
    value = re.sub(r"\s+[-–—]\s*Topic$", "", value, flags=re.I)
    return re.sub(r"(?:\s*[-–—]?\s*)VEVO$", "", value).strip()


def clean_title(title, artist=""):
    """Remove upload labels, preserving recording versions and actual title words."""
    title = str(title or "").strip()
    decoration = r"(?:official\s+(?:audio|(?:music\s+)?video|lyrics?)|lyrics?(?:\s+video)?)"
    cleaned = re.sub(rf"[\[(]\s*{decoration}\s*[\])]", "", title, flags=re.I).strip()
    cleaned = re.sub(rf"(?:\s+[-–—|]?\s*){decoration}\s*[.!]*$", "", cleaned, flags=re.I).strip()
    artist = _artist(artist)
    if artist:
        prefix = re.match(rf"^{re.escape(artist)}\s+[-–—]\s+(.+)$", cleaned, flags=re.I)
        if prefix:
            cleaned = prefix.group(1)
        elif cleaned != title:
            prefix = re.match(rf"^{re.escape(artist)}\s+(.+)$", cleaned, flags=re.I)
            # Repeated title words (e.g. Talk Talk) do not identify an upload prefix.
            remainder = _key(prefix.group(1)).split() if prefix else []
            if remainder and remainder[0] not in _key(artist).split():
                cleaned = prefix.group(1)
    year = r"(?:19|20)\d{2}"
    remaster = rf"(?:{year}\s+)?remaster(?:ed)?(?:\s+{year})?"
    edition = rf"(?:{remaster}|deluxe|(?:\d+(?:st|nd|rd|th)\s+)?anniversary)(?:\s+(?:edition|version))?"
    # Remove only entire edition labels; mixed groups retain real recording markers.
    cleaned = re.sub(rf"[\[(]\s*{edition}\s*[\])]", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(rf"\s+[-–—]\s*{edition}\s*$", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(rf"\s+(?:remaster(?:ed)?\s+{year}|{year}\s+remaster(?:ed)?)\s*$",
                     "", cleaned, flags=re.I).strip()
    return " ".join(cleaned.split()).strip(" -–—|")


def clean_track_metadata(track):
    """Return a search identity copy; source metadata stays intact for review."""
    artist = track.get("artist") or track.get("artists") or ""
    if isinstance(artist, list):
        artist = artist[0] if artist else ""
    artist = _artist(artist)
    title = clean_title(track.get("title") or track.get("name", ""), artist)
    return {**track, "artist": artist, "title": title}


def usable_album(value):
    return str(value or "").strip() != "_Single" and _key(value) not in {
        "", "unknown", "unknown album", "nieznany album", "n a", "none", "null"}


def album_fallback_from_tags(path):
    """Recognize Normalizeusz's portable marker only for its original album."""
    import mutagen

    def first(value):
        value = getattr(value, "text", value)
        value = value[0] if isinstance(value, (list, tuple)) and value else value
        return value.decode("utf-8") if isinstance(value, bytes) else value

    try:
        source = Path(path).absolute()
        if any(part.is_symlink() for part in (source, *source.parents)) or source.stat().st_nlink != 1:
            return False
        audio = mutagen.File(source)
        tags = audio.tags if audio else None
        if not tags:
            return False
        marker = first(tags.get("downloadeusz_album_fallback") or tags.get("TXXX:DOWNLOADEUSZ_ALBUM_FALLBACK")
                       or tags.get("----:com.apple.iTunes:DOWNLOADEUSZ_ALBUM_FALLBACK"))
        album = first(tags.get("album") or tags.get("TALB") or tags.get("\xa9alb"))
        if not isinstance(marker, str) or len(marker) > 65536:
            return False
        marker = json.loads(marker)
        return bool(isinstance(marker, dict) and marker.get("version") == 1 and album
                    and marker.get("album") == album)
    except (OSError, ValueError, mutagen.MutagenError):
        return False
