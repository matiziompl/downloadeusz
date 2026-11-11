"""Resolve a canonical MusicBrainz link into explicit, reviewable choices."""

from functools import lru_cache
from urllib.parse import urlsplit
from uuid import UUID


def parse_url(url):
    parsed = urlsplit(str(url).strip())
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme != "https" or parsed.netloc != "musicbrainz.org" or parsed.query
            or len(parts) != 2 or parts[0] not in {"release", "recording", "release-group"}):
        raise ValueError("Wklej link https://musicbrainz.org/release/, recording/ lub release-group/ z identyfikatorem")
    try:
        identity = str(UUID(parts[1]))
    except ValueError as error:
        raise ValueError("Nieprawidłowy identyfikator MusicBrainz") from error
    if identity != parts[1].lower():
        raise ValueError("Nieprawidłowy identyfikator MusicBrainz")
    return parts[0], identity


@lru_cache(maxsize=1)
def _plugin():
    from beetsplug.musicbrainz import MusicBrainzPlugin

    plugin = MusicBrainzPlugin()
    # The submitted link never controls a request host, including custom beets configuration.
    plugin.mb_api.api_host = "https://musicbrainz.org"
    return plugin


def resolve(url):
    kind, identity = parse_url(url)
    plugin = _plugin()
    try:
        if kind == "release":
            album = plugin.album_for_id(identity)
            if not album:
                raise ValueError("Nie znaleziono tego wydania w MusicBrainz")
            choices = []
            for track in album.tracks:
                choices.append({
                    "kind": "track", "title": track.title, "artist": track.artist or album.artist,
                    "album": album.album, "year": album.year or 0,
                    "track": track.medium_index or track.index or 0, "disc": track.medium or 1,
                    "label": f"{track.medium or 1}.{track.medium_index or track.index or 0} · {track.title} · {track.artist or album.artist}",
                    "_track": dict(track), "_album": {key: value for key, value in album.items() if key != "tracks"},
                    "_album_track_count": len(album.tracks),
                })
            return choices
        if kind == "recording":
            releases = plugin.mb_api.get_base_recording_with_releases(identity).get("releases", [])
        else:
            result = plugin.mb_api.get_json("https://musicbrainz.org/ws/2/release", params={"release-group": identity, "limit": 100})
            releases = result.get("releases", [])
        choices = []
        for release in releases:
            release_id = str(UUID(release["id"]))
            title = release.get("title", "Wydanie")
            choices.append({"kind": "release", "title": title, "album": title,
                            "label": " · ".join(filter(None, [title, release.get("date"), release.get("country"), release.get("disambiguation")])),
                            "url": f"https://musicbrainz.org/release/{release_id}"})
        if not choices:
            raise ValueError("Brak powiązanych wydań. Wklej link do konkretnego wydania (release)")
        return choices
    except ValueError:
        raise
    except Exception as error:
        raise RuntimeError("Nie udało się odczytać MusicBrainz. Sprawdź link i spróbuj ponownie") from error
