import os
import json
import uuid
import subprocess
import shlex

def build_ffmpeg_tag_command(tags: dict, placeholder: str = "PLIK_PIOSENKI.mp3", output_suffix: str = "_otagowany") -> str:
    """
    Generates an ffmpeg tagging command for audio files using placeholder input.
    Properly escapes metadata using shlex.quote to prevent shell expansion ($) and command injection.
    """
    title = str(tags.get("title") or "")
    artist = str(tags.get("artist") or "")
    album = str(tags.get("album") or "")
    year = str(tags.get("year") or "")
    track = str(tags.get("track_number") or "")

    meta_args = []
    if title:
        meta_args.append(f"-metadata {shlex.quote(f'title={title}')}")
    if artist:
        meta_args.append(f"-metadata {shlex.quote(f'artist={artist}')}")
    if album:
        meta_args.append(f"-metadata {shlex.quote(f'album={album}')}")
    if year:
        meta_args.append(f"-metadata {shlex.quote(f'date={year}')}")
    if track:
        meta_args.append(f"-metadata {shlex.quote(f'track={track}')}")

    # Output file name: replace extension with _otagowany.ext
    base, ext = os.path.splitext(placeholder)
    if not ext:
        ext = ".mp3"
    out_file = f"{base}{output_suffix}{ext}"

    args_str = f" {' '.join(meta_args)}" if meta_args else ""
    return f'ffmpeg -i "{placeholder}" -c copy{args_str} "{out_file}"'


def parse_spotdl_song_metadata(song: dict, placeholder: str = "PLIK_PIOSENKI.mp3") -> dict:
    """Extracts standardized tag fields from spotdl song object."""
    title = song.get("name") or "Nieznany tytuł"
    artists = song.get("artists")
    if isinstance(artists, list):
        artist = ", ".join([str(a) for a in artists if a])
    else:
        artist = str(song.get("artist") or "Nieznany wykonawca")

    album = str(song.get("album_name") or "")
    raw_year = song.get("year") or song.get("release_date") or ""
    year = str(raw_year)[:4] if raw_year else ""
    track = str(song.get("track_number") or "1")
    cover_url = str(song.get("cover_url") or "")

    tags = {
        "title": title,
        "artist": artist,
        "album": album,
        "year": year,
        "track_number": track,
        "cover_url": cover_url
    }
    tags["command"] = build_ffmpeg_tag_command(tags, placeholder=placeholder)
    return tags


def fetch_spotify_metadata(url: str, downloads_dir: str, config_dir: str) -> dict:
    """Fetches Spotify metadata using spotdl save and returns tags + ffmpeg command."""
    url_clean = (url or "").strip()
    if not url_clean:
        return {"ok": False, "message": "Brak linku Spotify"}

    if not (url_clean.startswith("https://open.spotify.com/") or url_clean.startswith("spotify:")):
        return {"ok": False, "message": "Podaj poprawny link do utworu lub albumu ze Spotify"}

    temp_id = uuid.uuid4().hex[:8]
    temp_file = os.path.join(config_dir, f"temp_meta_{temp_id}.spotdl")

    try:
        proc = subprocess.run(
            ["spotdl", "save", url_clean, "--save-file", temp_file],
            capture_output=True,
            text=True,
            cwd=downloads_dir,
            timeout=45
        )

        if not os.path.exists(temp_file):
            err_msg = proc.stderr.strip() if proc.stderr else proc.stdout.strip()
            return {
                "ok": False,
                "message": f"Nie udało się pobrać metadanych ze Spotify: {err_msg or 'Brak odpowiedzi'}"
            }

        with open(temp_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list) or len(data) == 0:
            return {"ok": False, "message": "Nie znaleziono utworów pod podanym adresem URL"}

        primary_song = data[0]
        meta = parse_spotdl_song_metadata(primary_song)
        meta["ok"] = True
        meta["total_tracks"] = len(data)
        return meta

    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Przekroczono limit czasu podczas pobierania metadanych"}
    except Exception as e:
        return {"ok": False, "message": f"Błąd pobierania metadanych: {str(e)}"}
    finally:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
