import json
import os
import wave
import time
from pathlib import Path

import pytest
from mediafile import MediaFile

from backend import music_editor
from backend.audio_identity import audio_packet_signature


class Library:
    def __init__(self, directory):
        self.config_dir = directory / "config"
        self.library_dir = directory / "library"
        self.config_dir.mkdir(); self.library_dir.mkdir()
        self.relative_path = "old.wav"
        self.accepted = False

    def path(self, identity):
        assert identity == "song"
        return self.library_dir / self.relative_path

    def get_track(self, identity):
        path = self.path(identity)
        media = MediaFile(path)
        return {"id": identity, "title": media.title, "artist": media.artist,
                "album": media.album or "", "relative_path": self.relative_path,
                "version": str(path.stat().st_mtime_ns), "album_accepted": self.accepted}

    def update_path(self, identity, relative_path, *, album_accepted=False, album_source=""):
        self.relative_path = relative_path; self.accepted = album_accepted
        return self.get_track(identity)

    def cover(self, identity, *, thumbnail=True):
        raise KeyError("No cover")


class Store:
    def __init__(self):
        self.moves = []; self.busy = False
    def library_path_in_use(self, *args, **kwargs):
        return self.busy
    def remap_library_path(self, old, new, root):
        self.moves.append((old, new))


@pytest.fixture
def editor(tmp_path):
    lib = Library(tmp_path)
    with wave.open(str(lib.path("song")), "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 800)
    media = MediaFile(lib.path("song")); media.title = "Song"; media.artist = "Artist"; media.save()
    return music_editor.MusicEditor(lib), lib, Store()


def test_manual_title_moves_with_beets_layout_preserves_audio_and_replays_once(editor):
    service, lib, store = editor
    original = lib.path("song").read_bytes()
    signature = audio_packet_signature(lib.path("song"))
    preview = service.preview("song", action="title")
    assert preview["destination"] == "Artist/Song/Song.wav"
    result = service.apply("song", preview["preview_token"], store=store, job_id="edit")
    assert result["paths"] == [preview["destination"]]
    assert audio_packet_signature(lib.path("song")) == signature
    assert MediaFile(lib.path("song")).album == "Song"
    assert lib.accepted is True
    assert not (lib.library_dir / "old.wav").exists()
    assert service.apply("song", preview["preview_token"], store=store, job_id="edit") == result
    assert store.moves == [("old.wav", preview["destination"])]
    assert lib.path("song").read_bytes() != original


def test_stale_preview_and_collision_leave_original_unchanged(editor):
    service, lib, store = editor
    preview = service.preview("song", action="title")
    target = lib.library_dir / preview["destination"]; target.parent.mkdir(parents=True); target.write_bytes(b"other")
    original = lib.path("song").read_bytes()
    with pytest.raises(ValueError, match="istnieje"):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")
    assert lib.path("song").read_bytes() == original and target.read_bytes() == b"other"
    target.unlink()
    media = MediaFile(lib.path("song")); media.title = "Changed"; media.save()
    with pytest.raises(ValueError, match="zmieni"):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")


def test_pending_external_normalization_and_other_jobs_block_move(editor):
    service, lib, store = editor
    preview = service.preview("song", action="title")
    store.busy = True
    with pytest.raises(RuntimeError, match="zadani"):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")
    store.busy = False
    service.normalization_dir = lib.config_dir / "normalization"
    pending = service.normalization_dir / "pending"; pending.mkdir(parents=True)
    (pending / "request.json").write_text(json.dumps({"paths": ["old.wav"]}))
    with pytest.raises(RuntimeError, match="Normalizeusz"):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")


def test_linked_file_is_never_modified(editor):
    service, lib, store = editor
    preview = service.preview("song", action="title")
    os.link(lib.path("song"), lib.library_dir / "other.wav")
    with pytest.raises(ValueError, match="owiąz"):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")


def test_failed_state_update_rolls_back_files_and_reference_moves(editor, monkeypatch):
    service, lib, store = editor
    preview = service.preview("song", action="title")
    original = lib.path("song").read_bytes()
    real_update = lib.update_path
    def fail_once(*args, **kwargs):
        monkeypatch.setattr(lib, "update_path", real_update)
        raise OSError("disk full")
    monkeypatch.setattr(lib, "update_path", fail_once)
    with pytest.raises(OSError, match="disk full"):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")
    assert lib.relative_path == "old.wav"
    assert lib.path("song").read_bytes() == original
    assert not (lib.library_dir / preview["destination"]).exists()


def test_committed_recovery_keeps_original_and_backup_when_result_disappears(editor, monkeypatch):
    service, lib, store = editor
    preview = service.preview("song", action="title")
    finish = service._finish
    monkeypatch.setattr(service, "_finish", lambda _: (_ for _ in ()).throw(SystemExit("power loss")))
    with pytest.raises(SystemExit):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")
    result = lib.path("song"); result.unlink()
    monkeypatch.setattr(service, "_finish", finish)
    with pytest.raises(RuntimeError, match="wynikowy"):
        service.recover(store)
    assert (lib.library_dir / "old.wav").is_file()
    assert (service.state_dir / preview["preview_token"] / "original.wav").is_file()


def test_recovery_restores_uncommitted_publish_then_same_token_can_retry(editor, monkeypatch):
    service, lib, store = editor
    preview = service.preview("song", action="title")
    original = lib.path("song").read_bytes()
    rollback = service._rollback
    sync = service._sync_beets
    monkeypatch.setattr(service, "_sync_beets", lambda *args: (_ for _ in ()).throw(RuntimeError("power loss")))
    monkeypatch.setattr(service, "_rollback", lambda *args: (_ for _ in ()).throw(SystemExit("process killed")))
    with pytest.raises(SystemExit):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")
    monkeypatch.setattr(service, "_rollback", rollback)
    monkeypatch.setattr(service, "_sync_beets", sync)
    service.recover(store)
    assert lib.path("song").read_bytes() == original
    assert not (lib.library_dir / preview["destination"]).exists()
    service.apply("song", preview["preview_token"], store=store, job_id="edit")
    assert MediaFile(lib.path("song")).album == "Song"


def test_partial_backup_before_journal_does_not_make_token_permanently_unusable(editor):
    service, lib, store = editor
    preview = service.preview("song", action="title")
    orphan = service.state_dir / preview["preview_token"]; orphan.mkdir()
    (orphan / "original.wav").write_bytes(b"incomplete copy")
    result = service.apply("song", preview["preview_token"], store=store, job_id="edit")
    assert result["library_changed"]


def test_external_change_during_staging_is_not_overwritten(editor, monkeypatch):
    service, lib, store = editor
    preview = service.preview("song", action="title")
    original_marker = music_editor._marker
    changed = []
    def marker(path, album):
        original_marker(path, album)
        media = MediaFile(lib.path("song")); media.title = "Changed elsewhere"; media.save()
        changed.append(lib.path("song").read_bytes())
    monkeypatch.setattr(music_editor, "_marker", marker)
    with pytest.raises(ValueError, match="zmieni"):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")
    assert lib.path("song").read_bytes() == changed[0]
    assert not (lib.library_dir / preview["destination"]).exists()
    assert not (service.state_dir / preview["preview_token"]).exists()


def test_musicbrainz_applies_saved_selected_track_and_album_without_refetch(editor, monkeypatch):
    service, lib, store = editor
    rid = "11111111-1111-4111-8111-111111111111"
    tid = "22222222-2222-4222-8222-222222222222"
    choice = {"kind": "track", "title": "Selected", "artist": "Band", "album": "Record", "track": 2, "disc": 1,
              "_track": {"title": "Selected", "artist": "Band", "track_id": tid, "index": 2, "medium_index": 2, "medium": 1},
              "_album": {"album": "Record", "album_id": rid, "artist": "Band", "year": 2020}, "_album_track_count": 5}
    monkeypatch.setattr(music_editor.musicbrainz_metadata, "resolve", lambda _: [choice])
    monkeypatch.setattr(music_editor.album_art, "enrich_artwork", lambda *args, **kwargs: False)
    signature = audio_packet_signature(lib.path("song"))
    choices = service.preview("song", url=f"https://musicbrainz.org/release/{rid}")
    assert "_track" not in choices["choices"][0]
    preview = service.preview("song", candidate_key=choices["choices"][0]["key"])
    assert preview["proposed"]["cover_url"] == f"https://coverartarchive.org/release/{rid}/front-250"
    monkeypatch.setattr(music_editor.musicbrainz_metadata, "resolve", lambda _: (_ for _ in ()).throw(AssertionError("Unexpected network lookup")))
    service.apply("song", preview["preview_token"], store=store, job_id="edit")
    media = MediaFile(lib.path("song"))
    assert (media.title, media.artist, media.album, media.mb_albumid) == ("Selected", "Band", "Record", rid)
    assert audio_packet_signature(lib.path("song")) == signature
    assert lib.accepted is False
    assert lib.relative_path == "Band/2020 - Record/02 Selected.wav"


def test_same_path_edit_and_failure_restore_original_bytes(editor, monkeypatch):
    service, lib, store = editor
    first = service.preview("song", action="title")
    service.apply("song", first["preview_token"], store=store, job_id="edit1")
    second = service.preview("song", action="title")
    original = lib.path("song").read_bytes()
    real_sync = service._sync_beets
    monkeypatch.setattr(service, "_sync_beets", lambda *args: (_ for _ in ()).throw(RuntimeError("save failed")))
    with pytest.raises(RuntimeError, match="save failed"):
        service.apply("song", second["preview_token"], store=store, job_id="edit2")
    assert lib.path("song").read_bytes() == original
    monkeypatch.setattr(service, "_sync_beets", real_sync)
    service.apply("song", second["preview_token"], store=store, job_id="edit2")
    assert lib.path("song").is_file()


def test_preview_expiry_cleanup_preserves_recovery_records(editor):
    service, lib, _ = editor
    preview = service.preview("song", action="title")
    recovery = service.state_dir / preview["preview_token"]; recovery.mkdir()
    (recovery / "journal.json").write_text('{}')
    with service._connect() as db:
        db.execute("UPDATE previews SET created=? WHERE token=?", (time.time() - 90000, preview["preview_token"]))
    service.preview("song", action="title")
    row, snapshot = service._get("song", preview["preview_token"])
    assert row and snapshot["kind"] == "preview"


def test_real_library_beets_and_acquisition_paths_follow_manual_edit(tmp_path):
    from backend import beets_importer, job_store
    from backend.music_library import MusicLibrary
    from beets.library import Item, Library as BeetsLibrary

    libdir = tmp_path / "library"; libdir.mkdir()
    config = tmp_path / "config"; config.mkdir()
    old = libdir / "old.wav"
    with wave.open(str(old), "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\0\0" * 800)
    media = MediaFile(old); media.title = "Song"; media.artist = "Artist"; media.save()
    beets = config / "beets"; beets.mkdir()
    database = BeetsLibrary(beets / "library.db", directory=str(libdir), set_music_dir=False)
    database.add(Item.from_path(old)); database._close()
    source = tmp_path / "incoming.wav"; source.write_bytes(old.read_bytes())
    identity = beets_importer._track_id(source)
    (beets / "library-state.json").write_text(json.dumps({"version": 1, "tracks": {
        identity: {"id": identity, "source": str(source), "destination": str(old), "imported": True,
                   "metadata_confirmed": False, "release_confirmed": False, "album_source": "fallback"}}}))
    library = MusicLibrary(config, libdir); library.refresh()
    track = library.list_tracks()["items"][0]
    store = job_store.JobStore(config / "jobs.sqlite3")
    store.remember_source("soulseek", "recording", [{"path": "old.wav", "audio_signature": audio_packet_signature(old)}])
    historic = store.enqueue("download", {}); store.update(historic["id"], status="done", result={"paths": ["old.wav"]})
    service = music_editor.MusicEditor(library)
    preview = service.preview(track["id"], action="title")
    service.apply(track["id"], preview["preview_token"], store=store, job_id="edit")
    assert store.source_files("soulseek", "recording")[0]["path"] == preview["destination"]
    assert store.get(historic["id"])["result"]["paths"] == [preview["destination"]]
    entry = beets_importer._load_library_state(config)["tracks"][identity]
    assert entry["destination"] == str(libdir / preview["destination"])
    assert entry["manual_album_accepted"] is True and entry["release_confirmed"] is False
    assert beets_importer.list_review(config) == []
    assert beets_importer.enrich_pending(config_dir=config, dest_dir=libdir, retry=True)["updated"] == 0
    database = BeetsLibrary(beets / "library.db", directory=str(libdir), set_music_dir=False)
    with database.music_dir_context():
        assert [os.fsdecode(item.path) for item in database.items()] == [str(libdir / preview["destination"])]
    database._close()
    library.refresh()
    assert library.get_track(track["id"])["album_accepted"] is True
    assert library.list_tracks(incomplete=True)["total"] == 0
    assert source.is_file()


def test_sidecar_only_cover_is_preserved_in_reorganized_file(tmp_path):
    import io
    from PIL import Image
    from backend.music_library import MusicLibrary

    lib = Library(tmp_path)
    with wave.open(str(lib.path("song")), "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\0\0" * 800)
    media = MediaFile(lib.path("song")); media.title = "Song"; media.artist = "Artist"; media.save()
    picture = io.BytesIO(); Image.new("RGB", (20, 20), "blue").save(picture, format="JPEG")
    sidecar = lib.library_dir / "cover.jpg"; sidecar.write_bytes(picture.getvalue())
    library = MusicLibrary(lib.config_dir, lib.library_dir); library.refresh()
    identity = library.list_tracks()["items"][0]["id"]
    service = music_editor.MusicEditor(library)
    preview = service.preview(identity, action="title")
    service.apply(identity, preview["preview_token"], store=Store(), job_id="edit")
    assert MediaFile(library.path(identity)).images[0].data == picture.getvalue()
    assert sidecar.read_bytes() == picture.getvalue()


def test_preview_does_not_write_beets_migration_backups_to_working_directory(editor, monkeypatch, tmp_path):
    service, _, _ = editor
    monkeypatch.chdir(tmp_path)
    service.preview("song", action="title")
    assert list(tmp_path.glob(":memory:*.bak")) == []


def test_multidisc_selection_uses_managed_per_disc_numbering(editor, monkeypatch):
    service, lib, store = editor
    choice = {"kind": "track", "title": "Disc two", "artist": "Band", "album": "Double", "track": 1, "disc": 2,
              "_track": {"title": "Disc two", "artist": "Band", "track_id": "22222222-2222-4222-8222-222222222222",
                         "index": 13, "medium_index": 1, "medium": 2, "medium_total": 12},
              "_album": {"album": "Double", "album_id": "11111111-1111-4111-8111-111111111111", "artist": "Band", "year": 2020, "mediums": 2},
              "_album_track_count": 24}
    monkeypatch.setattr(music_editor.musicbrainz_metadata, "resolve", lambda _: [choice])
    monkeypatch.setattr(music_editor.album_art, "enrich_artwork", lambda *args, **kwargs: False)
    choices = service.preview("song", url="https://musicbrainz.org/release/11111111-1111-4111-8111-111111111111")
    preview = service.preview("song", candidate_key=choices["choices"][0]["key"])
    assert preview["proposed"]["track"] == 1
    assert preview["proposed"]["tracktotal"] == 12
    assert preview["destination"].endswith("/02-01 Disc two.wav")
    service.apply("song", preview["preview_token"], store=store, job_id="edit")
    media = MediaFile(lib.path("song"))
    assert (media.disc, media.track, media.tracktotal) == (2, 1, 12)


def test_cancellation_during_staging_does_not_publish_metadata(editor, monkeypatch):
    from backend import job_store
    service, lib, store = editor
    preview = service.preview("song", action="title")
    original = lib.path("song").read_bytes()
    store.get = lambda _: {"status": "running"}
    marker = music_editor._marker
    def cancel_after_marker(*args):
        marker(*args)
        store.get = lambda _: {"status": "cancelled"}
    monkeypatch.setattr(music_editor, "_marker", cancel_after_marker)
    with job_store.cancellation_scope(store, "edit"), pytest.raises(job_store.JobCancelled):
        service.apply("song", preview["preview_token"], store=store, job_id="edit")
    assert lib.path("song").read_bytes() == original
    assert not (lib.library_dir / preview["destination"]).exists()
