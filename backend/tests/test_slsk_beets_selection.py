from backend import slskd_organizer


def select(monkeypatch, root, transfers):
    monkeypatch.setattr(slskd_organizer, "fetch_slskd_transfer_files", lambda **kwargs: transfers, raising=False)
    return slskd_organizer.get_completed_album_directories("http://slskd", str(root))


def test_only_fully_completed_album_is_selected(tmp_path, monkeypatch):
    album = tmp_path / "Artist - Album"
    album.mkdir()
    (album / "01.flac").write_bytes(b"audio")
    transfers = [{"filename": "Artist - Album\\01.flac", "state": "Completed, Succeeded"}]
    assert select(monkeypatch, tmp_path, transfers) == [str(album)]
    transfers.append({"filename": "Artist - Album\\02.flac", "state": "InProgress, Downloading"})
    assert select(monkeypatch, tmp_path, transfers) == []


def test_unlisted_audio_keeps_album_waiting(tmp_path, monkeypatch):
    album = tmp_path / "Album"
    album.mkdir()
    (album / "01.flac").write_bytes(b"audio")
    (album / "02.flac").write_bytes(b"unfinished")
    assert select(monkeypatch, tmp_path, [{"filename": "Album/01.flac", "state": "Completed, Succeeded"}]) == []


def test_ambiguous_filename_is_not_selected(tmp_path):
    for album in ("A", "B"):
        (tmp_path / album).mkdir()
        (tmp_path / album / "01.flac").write_bytes(b"audio")
    assert slskd_organizer.find_file_in_dir("other/01.flac", str(tmp_path)) is None


def test_symlink_is_not_selected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "01.flac").write_bytes(b"audio")
    source = tmp_path / "source"
    source.mkdir()
    (source / "Album").symlink_to(outside, target_is_directory=True)
    assert slskd_organizer.find_file_in_dir("Album/01.flac", str(source)) is None


def test_old_history_cannot_select_current_incomplete_file(tmp_path, monkeypatch):
    album = tmp_path / "new-album"
    album.mkdir()
    (album / "01.flac").write_bytes(b"downloading")
    transfers = [
        {"filename": "old-album/01.flac", "state": "Completed, Succeeded"},
        {"filename": "new-album/01.flac", "state": "InProgress, Downloading"},
    ]
    assert select(monkeypatch, tmp_path, transfers) == []
