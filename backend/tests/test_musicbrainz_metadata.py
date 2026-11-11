from types import SimpleNamespace

import pytest
from beets.autotag.hooks import AlbumInfo, TrackInfo

from backend import musicbrainz_metadata as metadata


RID = "11111111-1111-4111-8111-111111111111"
TID = "22222222-2222-4222-8222-222222222222"


@pytest.mark.parametrize("url", [
    f"http://musicbrainz.org/release/{RID}", f"https://evil.test/release/{RID}",
    f"https://musicbrainz.org.evil.test/release/{RID}", f"https://musicbrainz.org@evil.test/release/{RID}",
    f"https://musicbrainz.org:443/release/{RID}", "https://musicbrainz.org/release/not-an-id",
    f"https://musicbrainz.org/artist/{RID}", f"https://musicbrainz.org/release/{RID}/../x",
])
def test_only_canonical_musicbrainz_entities_are_accepted(url):
    with pytest.raises(ValueError):
        metadata.parse_url(url)


def test_release_exposes_explicit_disc_track_snapshots(monkeypatch):
    album = AlbumInfo(album="Album", album_id=RID, artist="Artist", year=2020, tracks=[
        TrackInfo(title="First", artist="Artist", track_id=TID, index=1, medium=1, medium_index=1),
        TrackInfo(title="Second", artist="Artist", track_id=TID, index=2, medium=2, medium_index=1),
    ])
    monkeypatch.setattr(metadata, "_plugin", lambda: SimpleNamespace(album_for_id=lambda _: album))
    choices = metadata.resolve(f"https://musicbrainz.org/release/{RID}")
    assert [item["disc"] for item in choices] == [1, 2]
    assert [item["track"] for item in choices] == [1, 1]
    assert all(item["kind"] == "track" and item["_album"]["album_id"] == RID for item in choices)
    assert choices[1]["_track"]["title"] == "Second"


def test_recording_requires_a_release_selection(monkeypatch):
    api = SimpleNamespace(get_base_recording_with_releases=lambda _: {"releases": [
        {"id": RID, "title": "Specific edition", "date": "2020", "country": "PL"},
    ]})
    monkeypatch.setattr(metadata, "_plugin", lambda: SimpleNamespace(mb_api=api))
    choices = metadata.resolve(f"https://musicbrainz.org/recording/{TID}")
    assert choices[0]["kind"] == "release"
    assert choices[0]["url"] == f"https://musicbrainz.org/release/{RID}"
    assert "_track" not in choices[0]
