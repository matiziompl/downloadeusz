"""Fill missing artwork only for an already identified MusicBrainz release."""

import io
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID

from mediafile import Image as EmbeddedImage, ImageType, MediaFile
from mediafile.exceptions import MediaFileError
from PIL import Image


MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _pending(path):
    path = Path(path).absolute()
    if (not path.is_file() or any(part.is_symlink() for part in [path, *path.parents])
            or path.stat().st_nlink > 1):
        return None
    media = MediaFile(path)
    if media.images:
        return None
    for extension in (".jpg", ".jpeg", ".png", ".webp"):
        local = path.parent / ("cover" + extension)
        if local.is_file() and not local.is_symlink():
            return None
    release = str(media.mb_albumid or "")
    if str(UUID(release)) != release.casefold():
        return None
    return media, release


def artwork_pending(path) -> bool:
    try:
        return _pending(path) is not None
    except (OSError, ValueError, MediaFileError):
        return False


def enrich_artwork(path, log_callback=None) -> bool:
    """Embed available art; the caller backs up the tracked copy before writes."""
    try:
        pending = _pending(path)
    except (OSError, ValueError, MediaFileError):
        return False
    if pending is None:
        return False
    media, release = pending
    request = urllib.request.Request(
        f"https://coverartarchive.org/release/{release}/front-500",
        headers={"User-Agent": "Downloadeusz/beets", "Accept": "image/jpeg,image/png,image/webp"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Okładka przekracza dopuszczalny rozmiar")
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format not in {"JPEG", "PNG", "WEBP"} or picture.width * picture.height > 40_000_000:
                raise ValueError("Nieobsługiwany format lub rozmiar okładki")
            picture.verify()
    except (OSError, ValueError, urllib.error.URLError, Image.DecompressionBombError) as error:
        if log_callback:
            log_callback(f"Nie udało się uzupełnić okładki: {error}")
        return False
    media.images = [EmbeddedImage(data, desc="Cover (front)", type=ImageType.front)]
    try:
        media.save()
    except Exception as error:
        raise OSError(f"Nie udało się zapisać okładki: {error}") from error
    if log_callback:
        log_callback("Uzupełniono brakującą okładkę istniejącej kopii")
    return True
