import re
import unicodedata
import urllib.request
import urllib.parse
import hashlib
import secrets
import json
from typing import Dict, List, Any, Optional, Tuple, Callable

POLISH_TRANS = str.maketrans({
    'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
    'Ą': 'a', 'Ć': 'c', 'Ę': 'e', 'Ł': 'l', 'Ń': 'n', 'Ó': 'o', 'Ś': 's', 'Ź': 'z', 'Ż': 'z',
})

def clean_string(s: str) -> str:
    """Normalize and clean string for resilient fuzzy comparison."""
    if not s:
        return ""
    # Transliterate polish letters first
    s_polish = s.translate(POLISH_TRANS)
    # Normalize unicode to NFKD (decomposes characters with accents)
    s_norm = unicodedata.normalize('NFKD', s_polish)
    # Remove combining characters (accents)
    s_no_accents = ''.join(c for c in s_norm if not unicodedata.combining(c))
    # Replace non-alphanumeric with spaces and lowercase
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', s_no_accents).lower()
    # Collapse multiple spaces
    return ' '.join(cleaned.split())

def is_lossless(suffix: str, content_type: str = "") -> bool:
    """Check if file format is lossless (FLAC, WAV, ALAC, AIFF)."""
    s = (suffix or "").lower().strip('.')
    ct = (content_type or "").lower()
    if s in ["flac", "wav", "alac", "aiff"]:
        return True
    if "flac" in ct or "audio/x-wav" in ct or "audio/wav" in ct:
        return True
    return False

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

    salt = secrets.token_hex(6)
    token = hashlib.md5((navi_pass + salt).encode('utf-8')).hexdigest()
    
    clean_search_query = f"{artist} {title}".strip()
    params = {
        "u": navi_user,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "downloadeusz",
        "f": "json",
        "query": clean_search_query,
        "songCount": 10,
        "albumCount": 0,
        "artistCount": 0
    }
    query_str = urllib.parse.urlencode(params)
    api_url = f"{navi_url.rstrip('/')}/rest/search3.view?{query_str}"

    try:
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            songs = data.get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
            if not songs:
                # Fallback to search2 if searchResult3 empty
                songs = data.get("subsonic-response", {}).get("searchResult2", {}).get("song", [])

            c_target_title = clean_string(title)
            c_target_artist = clean_string(artist)

            for song in songs:
                c_song_title = clean_string(song.get("title", ""))
                c_song_artist = clean_string(song.get("artist", ""))

                # Check if titles match closely
                title_match = (
                    c_target_title == c_song_title
                    or c_target_title in c_song_title
                    or c_song_title in c_target_title
                )
                
                # Check if artists match closely (or if one is contained in the other)
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
            match_lossless = is_lossless(suffix, content_type)

            should_skip = False
            if skip_mode == "all":
                should_skip = True
            elif skip_mode == "flac_only" and match_lossless:
                should_skip = True

            if should_skip:
                fmt_name = suffix.upper() if suffix else "ISTNIEJĄCY"
                log(f"[Deduplikacja] Znaleziono w Navidrome ({fmt_name}): {artist} - {title} -> POMIJAM")
                skipped.append(song)
                continue

        kept.append(song)

    return kept, skipped
