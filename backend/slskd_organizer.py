import os
import re
import json
import shutil
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, List, Any, Optional, Callable
import mutagen

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

def find_file_in_dir(search_path: str, base_dir: str) -> Optional[str]:
    """Find file in base_dir by normalized path or basename, strictly preventing directory traversal."""
    base_abs = os.path.abspath(base_dir)
    # Normalize slashes and prevent directory traversal
    norm_rel = os.path.normpath(search_path.replace('\\', '/')).lstrip('/\\')
    
    # Check direct candidate inside base_dir
    candidate = os.path.abspath(os.path.join(base_abs, norm_rel))
    if candidate.startswith(base_abs + os.sep) and os.path.isfile(candidate):
        return candidate

    # Try removing the top directory (e.g. username directory)
    parts = [p for p in norm_rel.split(os.sep) if p and p != '..']
    if len(parts) > 1:
        sub_candidate = os.path.abspath(os.path.join(base_abs, *parts[1:]))
        if sub_candidate.startswith(base_abs + os.sep) and os.path.isfile(sub_candidate):
            return sub_candidate

    # Fallback: search for filename recursively strictly within base_dir
    target_basename = os.path.basename(norm_rel)
    if target_basename and target_basename != '..':
        for root, _, files in os.walk(base_abs):
            if target_basename in files:
                found = os.path.join(root, target_basename)
                if found.startswith(base_abs + os.sep):
                    return found

    return None

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

    if not slskd_url.startswith(('http://', 'https://')):
        slskd_url = f"http://{slskd_url}"

    # 1. Authenticate and prepare headers
    try:
        headers = get_slskd_auth_header(
            slskd_url,
            username=slskd_username,
            password=slskd_password,
            api_key=slskd_api_key,
            timeout=15
        )
    except Exception as e:
        err_msg = f"Błąd autoryzacji Soulseek (slskd): {e}"
        log(f"[Soulseek] {err_msg}")
        stats["errors"].append(err_msg)
        return stats

    # 2. Fetch completed transfers from slskd
    api_url = f"{slskd_url.rstrip('/')}/api/v0/transfers/downloads"
    log(f"[Soulseek] Pobieranie listy transferów z {api_url}...")
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        err_msg = f"Nie udało się połączyć z API slskd: {e}"
        log(f"[Soulseek] {err_msg}")
        stats["errors"].append(err_msg)
        return stats

    # Extract all completed files from response
    completed_files = []

    def extract_files(node):
        if isinstance(node, list):
            for item in node:
                extract_files(item)
        elif isinstance(node, dict):
            # Check if this node is a file object
            if "filename" in node and "state" in node:
                state_str = str(node.get("state", ""))
                if "Completed, Succeeded" in state_str or state_str.lower() == "completed":
                    completed_files.append(node["filename"])
            # Recurse into directories or lists
            for key, val in node.items():
                if isinstance(val, (list, dict)):
                    extract_files(val)

    extract_files(data)
    log(f"[Soulseek] Znaleziono {len(completed_files)} ukończonych plików w slskd.")

    if not completed_files:
        log("[Soulseek] Brak nowych ukończonych plików do uporządkowania.")
        return stats

    for rel_path in completed_files:
        src_file = find_file_in_dir(rel_path, source_dir)
        if not src_file:
            log(f"[Soulseek] Pominięto (brak pliku na dysku): {rel_path}")
            stats["skipped"] += 1
            continue

        ext = os.path.splitext(src_file)[1].lower()
        if ext not in [".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".alac", ".aiff"]:
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
