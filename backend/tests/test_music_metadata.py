import importlib

import pytest


def metadata():
    return importlib.import_module("backend.music_metadata")


@pytest.mark.parametrize(("title", "artist", "expected"), [
    ("Playboi Carti COCAINE NOSE Official Audio", "Playboi Carti", "COCAINE NOSE"),
    ("Playboi Carti - COCAINE NOSE (Official Video)", "Playboi Carti", "COCAINE NOSE"),
    ("Song [Lyrics]", "Artist", "Song"),
    ("Song (Live) (Official Audio)", "Artist", "Song (Live)"),
    ("Song (Remix) [Official Video]", "Artist", "Song (Remix)"),
    ("Song (Acoustic / Instrumental Demo Edit)", "Artist", "Song (Acoustic / Instrumental Demo Edit)"),
    ("Talk Talk", "Talk Talk", "Talk Talk"),
    ("Talk Talk Talk", "Talk Talk", "Talk Talk Talk"),
    ("Official Secrets", "Artist", "Official Secrets"),
    ("Live and Let Die", "Artist", "Live and Let Die"),
    ("Artist ... (Official Audio)", "Artist", "Artist ..."),
])
def test_only_upload_decoration_is_removed(title, artist, expected):
    assert metadata().clean_title(title, artist) == expected


def test_channel_suffixes_clean_identity_without_mutating_source():
    track = {"title": "Playboi Carti COCAINE NOSE (Official Audio)",
             "artist": "Playboi Carti - Topic", "length": 160, "source_url": "https://example.org/video"}
    clean = metadata().clean_track_metadata(track)
    assert clean["title"] == "COCAINE NOSE"
    assert clean["artist"] == "Playboi Carti"
    assert track["title"].endswith("(Official Audio)")
    assert metadata().clean_track_metadata({"title": "Song", "artist": "ArtistVEVO"})["artist"] == "Artist"


@pytest.mark.parametrize("album", [None, "", "Unknown Album", "unknown", "N/A", "Nieznany album", "None", "_Single"])
def test_placeholder_album_is_not_release_evidence(album):
    assert metadata().usable_album(album) is False


def test_real_album_is_release_evidence():
    assert metadata().usable_album("MUSIC") is True
    assert metadata().usable_album("Single") is True


@pytest.mark.parametrize(("title", "expected"), [
    ("Song (Remastered 2011)", "Song"),
    ("Song [2011 Remaster]", "Song"),
    ("Song - Remastered 2011", "Song"),
    ("Song - 2011 Remaster", "Song"),
    ("Song Remastered 2011", "Song"),
    ("Song 2011 Remaster", "Song"),
    ("Song (Deluxe Edition)", "Song"),
    ("Song [25th Anniversary Edition]", "Song"),
    ("Song - Anniversary Edition", "Song"),
    ("Song (Live) (Remastered 2011)", "Song (Live)"),
    ("Song (Live - Remastered 2011)", "Song (Live - Remastered 2011)"),
    ("Song (Demo Remaster)", "Song (Demo Remaster)"),
    ("Remaster", "Remaster"),
    ("The Remaster", "The Remaster"),
    ("Deluxe", "Deluxe"),
    ("Anniversary", "Anniversary"),
])
def test_edition_decoration_is_separate_from_recording_identity(title, expected):
    assert metadata().clean_title(title) == expected


def test_edition_cleanup_does_not_turn_real_title_artist_into_upload_prefix():
    assert metadata().clean_title("Alice Cooper (Remastered 2011)", "Alice") == "Alice Cooper"
