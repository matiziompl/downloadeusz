import io
import wave
from unittest.mock import patch

import pytest
from PIL import Image

from backend import album_art


@pytest.fixture
def track(tmp_path):
    from mediafile import MediaFile
    path = tmp_path / "song.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    media = MediaFile(path)
    media.mb_albumid = "00000000-0000-0000-0000-000000000001"
    media.save()
    return path


def cover():
    stream = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(stream, format="JPEG")
    return stream.getvalue()


def test_missing_cover_of_known_release_is_embedded_without_replacing_audio(track):
    from mediafile import MediaFile
    assert album_art.artwork_pending(track)
    with patch("urllib.request.urlopen", return_value=io.BytesIO(cover())) as request:
        assert album_art.enrich_artwork(track)
    assert MediaFile(track).images
    assert not album_art.artwork_pending(track)
    assert request.call_args.args[0].full_url.endswith("00000000-0000-0000-0000-000000000001/front-500")
    assert MediaFile(track).length == pytest.approx(1.0)
    before = track.read_bytes()
    with patch("urllib.request.urlopen") as request:
        assert not album_art.enrich_artwork(track)
    request.assert_not_called()
    assert track.read_bytes() == before


def test_unknown_release_or_existing_local_cover_needs_no_lookup(track):
    from mediafile import MediaFile
    (track.parent / "cover.jpg").write_bytes(cover())
    assert not album_art.artwork_pending(track)
    (track.parent / "cover.jpg").unlink()
    media = MediaFile(track)
    media.mb_albumid = "../../arbitrary"
    media.save()
    before = track.read_bytes()
    with patch("urllib.request.urlopen") as request:
        assert not album_art.enrich_artwork(track)
    request.assert_not_called()
    assert track.read_bytes() == before


def test_invalid_image_or_link_never_changes_file(track):
    before = track.read_bytes()
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b"not an image")):
        assert not album_art.enrich_artwork(track)
    assert track.read_bytes() == before
    link = track.parent / "link.wav"
    link.symlink_to(track)
    with patch("urllib.request.urlopen") as request:
        assert not album_art.enrich_artwork(link)
    request.assert_not_called()
    assert track.read_bytes() == before


def test_write_failure_is_not_silently_reported_as_success(track):
    from mediafile import MediaFile
    with patch("urllib.request.urlopen", return_value=io.BytesIO(cover())), \
         patch.object(MediaFile, "save", side_effect=RuntimeError("storage failure")):
        with pytest.raises(OSError, match="storage failure"):
            album_art.enrich_artwork(track)


def test_explicit_replacement_preserves_existing_cover_on_network_failure(track):
    from mediafile import MediaFile, Image as EmbeddedImage
    old = cover()
    media = MediaFile(track); media.images = [EmbeddedImage(old)]; media.save()
    before = track.read_bytes()
    with patch("urllib.request.urlopen", side_effect=OSError("offline")):
        assert not album_art.enrich_artwork(track, replace=True)
    assert track.read_bytes() == before
    new = io.BytesIO(); Image.new("RGB", (20, 20), "red").save(new, format="JPEG")
    with patch("urllib.request.urlopen", return_value=io.BytesIO(new.getvalue())):
        assert album_art.enrich_artwork(track, replace=True)
    assert MediaFile(track).images[0].data == new.getvalue()
