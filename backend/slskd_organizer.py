import os
import re
import json
import logging
import shutil
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, List, Any, Optional, Callable
import mutagen

AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".alac", ".aiff", ".ape", ".wv"}

def sanitize_filename(name: str) -> str:
    """Sanitize string to be safe for filenames across Linux, macOS, and Windows."""
    if not name:
        return "Unknown"
    # Replace characters forbidden in Unix/Windows file systems
    sanitized = re.sub(r'[/\\:*?"<>|]', '_', name)
    # Strip leading/trailing spaces and dots
    sanitized = sanitized.strip().strip('.')
    return sanitized or "Unknown"

def parse_audio_tags(file_path: str) -> Dict[str, str]:
    """Extract artist, album, year, tracknumber, and title from an audio file."""
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    result = {
        "artist": "Unknown Artist",
        "album": "Unknown Album",
        "year": "Brak roku",
        "tracknumber": "01",
        "title": base_name
    }

    try:
        audio = mutagen.File(file_path, easy=True)
        if audio is not None:
            # Artist
            artist = None
            for key in ["artist", "albumartist", "composer"]:
                val = audio.get(key)
                if val and isinstance(val, (list, tuple)) and val[0]:
                    artist = str(val[0]).strip()
                    break
            if artist:
                result["artist"] = artist

            # Album
            val = audio.get("album")
            if val and isinstance(val, (list, tuple)) and val[0]:
                result["album"] = str(val[0]).strip()

            # Year / Date
            date_str = ""
            for key in ["date", "year", "originalyear", "originaldate"]:
                val = audio.get(key)
                if val and isinstance(val, (list, tuple)) and val[0]:
                    date_str = str(val[0]).strip()
                    break
            if date_str:
                match = re.search(r'\b(19\d\d|20\d\d)\b', date_str)
                if match:
                    result["year"] = match.group(1)

            # Track number
            val = audio.get("tracknumber")
            if val and isinstance(val, (list, tuple)) and val[0]:
                raw_track = str(val[0]).strip()
                # Handle formats like "1/12"
                num_part = raw_track.split('/')[0].strip()
                match = re.search(r'\d+', num_part)
                if match:
                    result["tracknumber"] = f"{int(match.group(0)):02d}"

            # Title
            val = audio.get("title")
            if val and isinstance(val, (list, tuple)) and val[0]:
                result["title"] = str(val[0]).strip()
    except Exception:
        pass

    return result

def build_target_path(tags: Dict[str, str], ext: str, base_dir: str) -> str:
    """Format target path matching Downloadeusz hierarchy: {artist}/{year} - {album}/{tracknumber} {title}.{ext}"""
    artist = sanitize_filename(tags.get("artist", "Unknown Artist"))
    album = sanitize_filename(tags.get("album", "Unknown Album"))
    year = sanitize_filename(tags.get("year", "Brak roku"))
    track = sanitize_filename(tags.get("tracknumber", "01"))
    title = sanitize_filename(tags.get("title", "Unknown Title"))
    clean_ext = ext.lstrip('.')

    folder = f"{year} - {album}"
    filename = f"{track} {title}.{clean_ext}"
    return os.path.join(base_dir, artist, folder, filename)

def find_file_in_dir(search_path: str, base_dir: str, allow_basename: bool = True) -> Optional[str]:
    """Resolve a remote path without following symlinks or guessing ambiguous filenames."""
    base_abs = os.path.realpath(base_dir)
    parts = [p for p in search_path.replace('\\', '/').split('/') if p and p != '.']
    if not parts or '..' in parts:
        return None

    def safe_file(path):
        return (path.startswith(base_abs + os.sep) and os.path.realpath(path) == path
                and os.path.isfile(path))

    for offset in range(max(1, len(parts) - 1)):
        candidate = os.path.join(base_abs, *parts[offset:])
        if safe_file(candidate):
            return candidate

    if not allow_basename:
        return None

    matches = []
    for root, dirs, files in os.walk(base_abs):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        if parts[-1] in files:
            candidate = os.path.join(root, parts[-1])
            if safe_file(candidate):
                matches.append(candidate)
                if len(matches) > 1:
                    return None
    return matches[0] if matches else None

def clean_empty_directories(directory: str):
    """Remove empty folders left behind in directory tree."""
    for root, dirs, files in os.walk(directory, topdown=False):
        if root == directory:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except Exception:
            pass

def get_slskd_auth_header(
    slskd_url: str,
    username: str = "",
    password: str = "",
    api_key: str = "",
    timeout: int = 10
) -> Dict[str, str]:
    """
    Generate authentication headers for slskd.
    Authenticates via POST /api/v0/session if username or password are provided.
    Supports static API key via X-API-Key if api_key is provided.
    Returns empty dict if no auth credentials are provided.
    """
    if not slskd_url.startswith(('http://', 'https://')):
        slskd_url = f"http://{slskd_url}"

    if username or password:
        session_url = f"{slskd_url.rstrip('/')}/api/v0/session"
        payload = json.dumps({"username": username, "password": password}).encode('utf-8')
        req = urllib.request.Request(
            session_url,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                token = data.get("token")
                if not token:
                    raise RuntimeError("Brak tokenu autoryzacyjnego w odpowiedzi serwera Soulseek (slskd)")
                return {"Authorization": f"Bearer {token}"}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("Błąd logowania do Soulseek (slskd): nieprawidłowy login lub hasło")
            raise RuntimeError(f"Błąd logowania do Soulseek (slskd): HTTP {e.code} - {e.reason}")
        except Exception as e:
            raise RuntimeError(f"Nie udało się połączyć z serwerem logowania Soulseek (slskd): {e}")

    if api_key:
        return {"X-API-Key": api_key}

    return {}


def fetch_slskd_transfer_files(slskd_url: str, slskd_username: str = "",
                              slskd_password: str = "", slskd_api_key: str = "") -> List[dict]:
    if not slskd_url.startswith(('http://', 'https://')):
        slskd_url = f"http://{slskd_url}"
    headers = get_slskd_auth_header(slskd_url, username=slskd_username,
                                  password=slskd_password, api_key=slskd_api_key, timeout=15)
    request = urllib.request.Request(f"{slskd_url.rstrip('/')}/api/v0/transfers/downloads", headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode('utf-8'))
    files = []

    def extract(node, username=""):
        if isinstance(node, list):
            for value in node:
                extract(value, username)
        elif isinstance(node, dict):
            username = node.get("username") or username
            if isinstance(node.get("filename"), str) and "state" in node:
                files.append({**node, **({"username": username} if username else {})})
            for value in node.values():
                if isinstance(value, (list, dict)):
                    extract(value, username)

    extract(data)
    return files


def transfer_succeeded(transfer: dict) -> bool:
    state = str(transfer.get("state", "")).lower().strip()
    return state == "completed" or {part.strip() for part in state.split(',')} == {"completed", "succeeded"}


def get_completed_album_directories(slskd_url: str, source_dir: str,
                                    slskd_username: str = "", slskd_password: str = "",
                                    slskd_api_key: str = "", log_callback=None) -> List[str]:
    """Only offer complete local albums to beets, including all discs together."""
    transfers = fetch_slskd_transfer_files(slskd_url=slskd_url, slskd_username=slskd_username,
                                         slskd_password=slskd_password, slskd_api_key=slskd_api_key)
    completed = set()
    blocked_remote_dirs = set()
    remote_dirs = {}
    missing = []
    for transfer in transfers:
        remote = transfer["filename"].replace('\\', '/')
        remote_dir = remote.rpartition('/')[0]
        owner = transfer.get("username", "")
        if not transfer_succeeded(transfer):
            blocked_remote_dirs.add((owner, remote_dir))
            continue
        local = find_file_in_dir(remote, source_dir, allow_basename=False)
        if local and os.path.splitext(local)[1].lower() in AUDIO_EXTENSIONS:
            completed.add(local)
            remote_dirs[local] = (owner, remote_dir)
        elif os.path.splitext(remote)[1].lower() in AUDIO_EXTENSIONS:
            missing.append(remote)

    if missing:
        message = (f"[Soulseek] Brak {len(missing)} ukończonych plików w {source_dir}. "
                   "Sprawdź SLSKD_DOWNLOADS_DIR i wspólny wolumen downloads slskd. "
                   f"Przykład: {missing[0]}")
        if log_callback:
            log_callback(message)
        else:
            logging.getLogger(__name__).warning(message)

    albums = set()
    for path in completed:
        directory = os.path.dirname(path)
        if re.fullmatch(r'(?:cd|disc|disk)[\s._-]*\d+', os.path.basename(directory), re.IGNORECASE):
            parent = os.path.dirname(directory)
            if parent != os.path.realpath(source_dir):
                directory = parent
        albums.add(directory)

    ready = []
    for album in sorted(albums):
        audio = set()
        unsafe = False
        for root, dirs, files in os.walk(album):
            if any(os.path.islink(os.path.join(root, name)) for name in dirs + files):
                unsafe = True
                break
            audio.update(os.path.join(root, name) for name in files
                         if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS)
        remote_album_dirs = set()
        for path in audio & completed:
            owner, remote_dir = remote_dirs[path]
            if os.path.dirname(path) != album:
                depth = len(os.path.relpath(os.path.dirname(path), album).split(os.sep))
                for _ in range(depth):
                    remote_dir = remote_dir.rpartition('/')[0]
            remote_album_dirs.add((owner, remote_dir))
        blocked = any(blocked_owner == owner and (blocked_dir == remote_dir or blocked_dir.startswith(remote_dir + '/'))
                      for blocked_owner, blocked_dir in blocked_remote_dirs for owner, remote_dir in remote_album_dirs)
        if not unsafe and audio and audio <= completed and not blocked:
            ready.append(album)
        elif log_callback:
            log_callback(f"[beets] Album czeka na zakończenie wszystkich transferów: {os.path.basename(album)}")
    return ready

def organize_slskd_downloads(
    slskd_url: str,
    slskd_api_key: str = "",
    source_dir: str = "/slskd_downloads",
    dest_dir: str = "/music",
    log_callback: Optional[Callable[[str], None]] = None,
    slskd_username: str = "",
    slskd_password: str = ""
) -> Dict[str, Any]:
    """Fetch completed downloads from slskd API, read audio tags, and organize them into dest_dir."""
    def log(msg: str):
        if log_callback:
            log_callback(msg)

    stats = {"organized": 0, "skipped": 0, "errors": []}
    
    if not os.path.exists(source_dir):
        log(f"[Soulseek] Błąd: Folder źródłowy nie istnieje: {source_dir}")
        return stats

    try:
        files = fetch_slskd_transfer_files(slskd_url, slskd_username, slskd_password, slskd_api_key)
    except Exception as e:
        err_msg = f"Nie udało się pobrać transferów slskd: {e}"
        log(f"[Soulseek] {err_msg}")
        stats["errors"].append(err_msg)
        return stats

    completed_files = [file["filename"] for file in files if transfer_succeeded(file)]
    log(f"[Soulseek] Znaleziono {len(completed_files)} ukończonych plików w slskd.")

    if not completed_files:
        log("[Soulseek] Brak nowych ukończonych plików do uporządkowania.")
        return stats

    for rel_path in completed_files:
        src_file = find_file_in_dir(rel_path, source_dir, allow_basename=False)
        if not src_file:
            log(f"[Soulseek] Pominięto (brak pliku na dysku): {rel_path}")
            stats["skipped"] += 1
            continue

        ext = os.path.splitext(src_file)[1].lower()
        if ext not in AUDIO_EXTENSIONS:
            log(f"[Soulseek] Pominięto (nieobsługiwany format): {os.path.basename(src_file)}")
            stats["skipped"] += 1
            continue

        try:
            tags = parse_audio_tags(src_file)
            target_path = build_target_path(tags, ext, dest_dir)
            
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            
            if os.path.abspath(src_file) == os.path.abspath(target_path):
                log(f"[Soulseek] Plik jest już w docelowej lokalizacji: {os.path.basename(target_path)}")
                stats["skipped"] += 1
                continue

            if os.path.exists(target_path):
                log(f"[Soulseek] Pominięto (plik docelowy już istnieje w bibliotece): {os.path.relpath(target_path, dest_dir)}")
                stats["skipped"] += 1
                continue

            shutil.move(src_file, target_path)
            stats["organized"] += 1
            rel_target = os.path.relpath(target_path, dest_dir)
            log(f"[Soulseek] Uporządkowano ({tags.get('artist')} - {tags.get('title')}): -> {rel_target}")
        except Exception as e:
            err = f"Błąd przy przenoszeniu {src_file}: {e}"
            log(f"[Soulseek] {err}")
            stats["errors"].append(err)

    # 3. Clean empty source folders
    clean_empty_directories(source_dir)
    log(f"[Soulseek] Zakończono porządkowanie. Przeniesiono: {stats['organized']}, Pominięto: {stats['skipped']}.")
    return stats
