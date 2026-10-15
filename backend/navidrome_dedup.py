import re
import unicodedata
import urllib.request
import urllib.parse
import hashlib
import secrets
import json
import subprocess
from typing import Dict, List, Any, Optional, Tuple, Callable

POLISH_TRANS = str.maketrans({
    'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
    'Ą': 'a', 'Ć': 'c', 'Ę': 'e', 'Ł': 'l', 'Ń': 'n', 'Ó': 'o', 'Ś': 's', 'Ź': 'z', 'Ż': 'z',
})

def strip_noise(title: str) -> str:
    """Strip common noise phrases from titles like (Remastered 2011), (Official Video), [Live], track numbers, etc."""
    noise_patterns = [
        r'\((?:official|remaster(?:ed)?|live|audio|video|feat\.?|ft\.?|mono|stereo|deluxe|version|anniversary|expanded)[^)]*\)',
        r'\[(?:official|remaster(?:ed)?|live|audio|video|feat\.?|ft\.?|mono|stereo|deluxe|version|anniversary|expanded)[^\]]*\]',
        r'-\s*(?:official|remaster(?:ed)?|live|audio|video|feat\.?|ft\.?|mono|stereo|deluxe|version|anniversary|expanded).*$',
        r'-\s*.*?(?:version|remaster(?:ed)?|mix|edit|deluxe|anniversary|session|live|mono|stereo).*$',
    ]
    res = title
    for p in noise_patterns:
        res = re.sub(p, '', res, flags=re.IGNORECASE)
    # Strip leading track numbers like '01 - ' or '01. '
    res = re.sub(r'^\d+[\s\.\-]+', '', res)
    return res.strip()

def clean_string(s: str) -> str:
    """Normalize and clean string for resilient fuzzy comparison across all unicode languages."""
    if not s:
        return ""
    # Transliterate polish letters first
    s_clean = s.translate(POLISH_TRANS)
    # Strip noise phrases first
    s_clean = strip_noise(s_clean)
    # Normalize unicode to NFKD (decomposes characters with accents)
    s_norm = unicodedata.normalize('NFKD', s_clean)
    # Remove combining characters (accents)
    s_no_accents = ''.join(c for c in s_norm if not unicodedata.combining(c))
    # Replace non-alphanumeric (keeping unicode letters/digits across languages) with spaces
    cleaned = re.sub(r'[^\w\s]', ' ', s_no_accents, flags=re.UNICODE).lower()
    # Collapse multiple spaces
    return ' '.join(cleaned.split())

def is_lossless(suffix: str, content_type: str = "", path: str = "") -> bool:
    """Check if file format is lossless (FLAC, WAV, ALAC, AIFF, APE)."""
    s = (suffix or "").lower().strip('.')
    ct = (content_type or "").lower()
    p = (path or "").lower()
    lossless_formats = {"flac", "wav", "alac", "aiff", "ape"}
    if s in lossless_formats:
        return True
    if any(p.endswith(f".{ext}") for ext in lossless_formats):
        return True
    if "flac" in ct or "audio/x-wav" in ct or "audio/wav" in ct or "audio/x-flac" in ct:
        return True
    return False

def subsonic_request(
    base_url: str,
    user: str,
    password: str,
    endpoint: str,
    extra_params: Optional[Dict[str, Any]] = None,
    timeout: int = 10
) -> Dict[str, Any]:
    """Reusable Subsonic API client with authentication and URL scheme normalization."""
    if not base_url:
        raise ValueError("Adres URL Navidrome jest wymagany")
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"

    salt = secrets.token_hex(6)
    token = hashlib.md5((password + salt).encode('utf-8')).hexdigest()
    params = {
        "u": user,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "downloadeusz",
        "f": "json"
    }
    if extra_params:
        params.update(extra_params)

    query = urllib.parse.urlencode(params, doseq=True)
    api_url = f"{base_url.rstrip('/')}/rest/{endpoint}?{query}"
    req = urllib.request.Request(api_url)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))

def search_navidrome_song(
    navi_url: str,
    navi_user: str,
    navi_pass: str,
    artist: str,
    title: str
) -> Optional[Dict[str, Any]]:
    """Search Navidrome Subsonic API for a song matching artist and title."""
    if not navi_url or not navi_user or not navi_pass:
        return None

    c_target_title = clean_string(title)
    c_target_artist = clean_string(artist)

    # If title is empty after cleaning, never match
    if not c_target_title:
        return None

    def sanitize_search_query(text: str) -> str:
        s = re.sub(r'[-/\\()\[\]"\'*+~^:?!]', ' ', text)
        return ' '.join(s.split())

    clean_target_title = strip_noise(title)
    primary_query = sanitize_search_query(f"{artist} {clean_target_title}")
    fallback_query = sanitize_search_query(clean_target_title)

    queries_to_try = [q for q in [primary_query, fallback_query] if q]
    seen_queries = set()

    for query_text in queries_to_try:
        if query_text in seen_queries:
            continue
        seen_queries.add(query_text)

        try:
            data = subsonic_request(
                base_url=navi_url,
                user=navi_user,
                password=navi_pass,
                endpoint="search3.view",
                extra_params={
                    "query": query_text,
                    "songCount": 15,
                    "albumCount": 0,
                    "artistCount": 0
                },
                timeout=5
            )
            songs = data.get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
            if not songs:
                songs = data.get("subsonic-response", {}).get("searchResult2", {}).get("song", [])

            for song in songs:
                c_song_title = clean_string(song.get("title", ""))
                c_song_artist = clean_string(song.get("artist", ""))

                if not c_song_title:
                    continue

                # Title matching:
                # For short titles (<= 4 chars, e.g. "One", "War", "Run"), require exact match
                if len(c_target_title) <= 4 or len(c_song_title) <= 4:
                    title_match = (c_target_title == c_song_title)
                else:
                    title_match = (
                        c_target_title == c_song_title
                        or c_target_title.startswith(c_song_title + " ")
                        or c_song_title.startswith(c_target_title + " ")
                    )

                # Artist matching:
                artist_match = (
                    not c_target_artist
                    or not c_song_artist
                    or c_target_artist == c_song_artist
                    or c_target_artist in c_song_artist
                    or c_song_artist in c_target_artist
                )

                if title_match and artist_match:
                    return song

        except Exception:
            pass

    return None

def filter_existing_songs(
    songs: List[Dict[str, Any]],
    navi_url: str,
    navi_user: str,
    navi_pass: str,
    skip_mode: str = "flac_only",
    log_callback: Optional[Callable[[str], None]] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Check songs against Navidrome library and filter out already existing ones.
    skip_mode can be "flac_only" (skip only if lossless) or "all" (skip if any format exists).
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)

    kept: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for song in songs:
        title = song.get("name") or song.get("title", "")
        # Artists can be a list or a string
        artists_val = song.get("artists") or song.get("artist") or []
        if isinstance(artists_val, list):
            artist = artists_val[0] if artists_val else ""
        else:
            artist = str(artists_val)

        match = search_navidrome_song(
            navi_url=navi_url,
            navi_user=navi_user,
            navi_pass=navi_pass,
            artist=artist,
            title=title
        )

        if match:
            suffix = match.get("suffix", "").lower()
            content_type = match.get("contentType", "")
            path = match.get("path", "")
            match_lossless = is_lossless(suffix, content_type, path)

            should_skip = False
            if skip_mode == "all":
                should_skip = True
            elif skip_mode == "flac_only" and match_lossless:
                should_skip = True

            if should_skip:
                fmt_name = suffix.upper() if suffix else ("FLAC" if match_lossless else "ISTNIEJĄCY")
                log(f"[Deduplikacja] Znaleziono w Navidrome ({fmt_name}): {artist} - {title} -> POMIJAM")
                # Store matched song ID in metadata in case needed for playlist reconstruction
                song["_navidrome_id"] = match.get("id")
                skipped.append(song)
                continue

        kept.append(song)

    return kept, skipped

def extract_ytdl_metadata(url: str, timeout: int = 30) -> List[Dict[str, Any]]:
    """
    Extract metadata from a YouTube or SoundCloud URL using yt-dlp --dump-single-json --flat-playlist.
    Returns list of dicts with 'name', 'artists', 'url', and '_raw'.
    """
    try:
        proc = subprocess.run(
            ["yt-dlp", "--dump-single-json", "--flat-playlist", url],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        data = json.loads(proc.stdout)
        raw_entries = data.get("entries") if data.get("_type") == "playlist" else [data]
        entries = []
        for e in raw_entries:
            if not e:
                continue
            title = e.get("track") or e.get("title") or ""
            artist = e.get("artist") or e.get("creator") or e.get("uploader") or ""
            # Often YouTube or SoundCloud titles are "Artist - Title"
            if " - " in title:
                parts = title.split(" - ", 1)
                if parts[0].strip() and parts[1].strip():
                    artist = parts[0].strip()
                    title = parts[1].strip()

            entry_url = e.get("url") or e.get("webpage_url") or url
            if entry_url and not entry_url.startswith(('http://', 'https://')):
                if "soundcloud" in url:
                    entry_url = f"https://soundcloud.com/{entry_url.lstrip('/')}"
                else:
                    entry_url = f"https://www.youtube.com/watch?v={entry_url}"
            entries.append({
                "name": title,
                "artists": [artist] if artist else [],
                "url": entry_url,
                "_raw": e
            })
        return entries
    except Exception:
        return []
