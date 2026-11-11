"""Reviewed single-file metadata edits with the importer's paths and write locks."""

import fcntl
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from beets.autotag.hooks import AlbumInfo, TrackInfo
from beets.library import Item, Library
from beets.util.pathformats import PF_KEY_QUERIES
from mediafile import MediaFile, Image as EmbeddedImage, ImageType

try:
    from backend import album_art, beets_importer, job_store, musicbrainz_metadata
    from backend.audio_identity import audio_packet_signature, library_write_lock
except ImportError:
    import album_art, beets_importer, job_store, musicbrainz_metadata
    from audio_identity import audio_packet_signature, library_write_lock


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False)
        output.flush(); os.fsync(output.fileno())
    temporary.replace(path)
    _sync_directory(path.parent)


def _marker(path, album=None):
    import mutagen
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp4 import MP4Tags

    audio = mutagen.File(path)
    if audio.tags is None:
        audio.add_tags()
    marker = json.dumps({"version": 1, "album": album, "accepted": True}, ensure_ascii=False)
    if isinstance(audio.tags, ID3):
        audio.tags.delall("TXXX:DOWNLOADEUSZ_ALBUM_FALLBACK")
        if album:
            audio.tags.add(TXXX(encoding=3, desc="DOWNLOADEUSZ_ALBUM_FALLBACK", text=[marker]))
    else:
        key = "----:com.apple.iTunes:DOWNLOADEUSZ_ALBUM_FALLBACK" if isinstance(audio.tags, MP4Tags) else "downloadeusz_album_fallback"
        if key in audio.tags:
            del audio.tags[key]
        if album:
            audio.tags[key] = [marker.encode()] if isinstance(audio.tags, MP4Tags) else [marker]
    audio.save()


class _MemoryLibrary(Library):
    # Fresh schema has no legacy rows to migrate or back up into the process cwd.
    _migrations = ()


class MusicEditor:
    def __init__(self, library, normalization_dir=None):
        self.library = library
        self.normalization_dir = Path(normalization_dir) if normalization_dir else None
        self.state_dir = Path(library.config_dir) / "music-edits"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if any(part.is_symlink() for part in (self.state_dir, *self.state_dir.parents)):
            raise ValueError("Dowiązanie zamiast stanu edycji muzyki")
        self.database = self.state_dir / "previews.sqlite3"
        if self.database.is_symlink() or (self.database.exists() and self.database.stat().st_nlink != 1):
            raise ValueError("Dowiązanie zamiast bazy podglądów")
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS previews (token TEXT PRIMARY KEY, track_id TEXT NOT NULL, created REAL NOT NULL, data TEXT NOT NULL, result TEXT)")

    def _connect(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        return db

    def _put(self, track_id, value):
        token = uuid.uuid4().hex
        with self._connect() as db:
            for row in db.execute("SELECT token FROM previews WHERE created<? AND result IS NULL", (time.time() - 86400,)):
                if not (self.state_dir / row["token"]).exists():
                    db.execute("DELETE FROM previews WHERE token=?", (row["token"],))
            db.execute("INSERT INTO previews VALUES(?,?,?,?,NULL)", (token, track_id, time.time(), json.dumps(value, ensure_ascii=False)))
        return token

    def _get(self, track_id, token):
        if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("Nieprawidłowy podgląd; wczytaj propozycję ponownie")
        with self._connect() as db:
            row = db.execute("SELECT * FROM previews WHERE token=? AND track_id=?", (token, track_id)).fetchone()
        if row is None:
            raise ValueError("Nie znaleziono podglądu; wczytaj propozycję ponownie")
        return dict(row), json.loads(row["data"])

    def validate_preview(self, track_id, preview_token):
        row, snapshot = self._get(track_id, preview_token)
        if row["result"]:
            return snapshot
        if row["created"] < time.time() - 86400 or snapshot.get("kind") != "preview":
            raise ValueError("Podgląd wygasł; wczytaj propozycję ponownie")
        self.library.get_track(track_id)
        path = beets_importer._library_file(self.library.path(track_id), Path(self.library.library_dir))
        if (str(path.relative_to(self.library.library_dir)) != snapshot["current"]["relative_path"]
                or beets_importer._content_fingerprint(path) != snapshot["fingerprint"]):
            raise ValueError("Plik zmienił się od podglądu; wczytaj propozycję ponownie")
        return snapshot

    def _item(self, path, metadata, album_source):
        memory = _MemoryLibrary(":memory:", directory=str(self.library.library_dir), set_music_dir=False)
        with memory.music_dir_context():
            item = Item.from_path(path)
            memory.add(item)
            item.update(metadata)
            item["downloadeusz_album_source"] = album_source
        return memory, item

    def _destination(self, item):
        paths = beets_importer._managed_config(Path(self.library.config_dir) / "beets", Path(self.library.library_dir))["paths"]
        formats = [(PF_KEY_QUERIES.get(query, query), template) for query, template in paths.items()]
        with item.db.music_dir_context():
            return Path(os.fsdecode(item.destination(basedir=os.fsencode(self.library.library_dir), path_formats=formats)))

    def _safe_destination(self, path):
        root = Path(self.library.library_dir).resolve()
        if not path.resolve().is_relative_to(root) or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Docelowa ścieżka wychodzi poza bibliotekę lub zawiera dowiązanie")
        return path

    def preview(self, track_id, *, action="musicbrainz", url="", candidate_key=""):
        current = self.library.get_track(track_id)
        path = beets_importer._library_file(self.library.path(track_id), Path(self.library.library_dir))
        fingerprint = beets_importer._content_fingerprint(path)
        if action not in {"title", "musicbrainz"}:
            raise ValueError("Nieznana operacja metadanych")
        if action == "musicbrainz" and not candidate_key:
            choices = []
            for choice in musicbrainz_metadata.resolve(url):
                public = {key: value for key, value in choice.items() if not key.startswith("_")}
                public["key"] = self._put(track_id, {"kind": "choice", "fingerprint": fingerprint, "choice": choice})
                choices.append(public)
            return {"current": current, "choices": choices, "action": action}
        if action == "title":
            if not current.get("title") or not current.get("artist"):
                raise ValueError("Najpierw uzupełnij tytuł i wykonawcę utworu")
            metadata = {"album": current["title"], "albumartist": current["artist"],
                        **{field: 0 for field in ("year", "month", "day", "track", "tracktotal", "disc", "disctotal")},
                        **{field: "" for field in ("mb_albumid", "mb_releasegroupid", "albumdisambig", "label", "catalognum", "country", "media")}}
            album_source = "fallback"
        else:
            row, saved = self._get(track_id, candidate_key)
            if (row["created"] < time.time() - 86400 or saved.get("kind") != "choice"
                    or saved["fingerprint"] != fingerprint or saved["choice"].get("kind") != "track"):
                raise ValueError("Propozycja zmieniła się lub wygasła; wczytaj ją ponownie")
            choice = saved["choice"]
            info = TrackInfo(**choice["_track"])
            album = AlbumInfo(tracks=[info], **choice["_album"])
            metadata = info.merge_with_album(album)
            # Managed imports number tracks within each disc, independently of global beets config.
            metadata["track"] = info.medium_index or info.index or 0
            metadata["tracktotal"] = info.medium_total or choice["_album_track_count"]
            album_source = "confirmed"
        memory, item = self._item(path, metadata, album_source)
        try:
            destination = self._safe_destination(self._destination(item))
            proposed = {key: item.get(key) for key in ("title", "artist", "album", "albumartist", "year", "track", "tracktotal", "disc", "disctotal", "mb_albumid", "mb_trackid")}
        finally:
            memory._close()
        snapshot = {"kind": "preview", "current": current, "fingerprint": fingerprint, "metadata": metadata,
                    "proposed": proposed, "destination": destination.relative_to(self.library.library_dir).as_posix(),
                    "action": action, "album_source": album_source}
        token = self._put(track_id, snapshot)
        if proposed.get("mb_albumid"):
            proposed["cover_url"] = f"https://coverartarchive.org/release/{proposed['mb_albumid']}/front-250"
        return {"preview_token": token, "current": current, "proposed": proposed, "destination": snapshot["destination"], "action": action}

    def _busy(self, path, store, job_id):
        relative = path.relative_to(self.library.library_dir).as_posix()
        if store.library_path_in_use(relative, self.library.library_dir, exclude_job_id=job_id):
            raise RuntimeError("Plik jest używany przez inne niedokończone zadanie; poczekaj na jego zakończenie")
        if self.normalization_dir:
            for folder in ("pending", "processing"):
                directory = self.normalization_dir / folder
                if directory.is_symlink():
                    raise ValueError("Dowiązanie zamiast kolejki Normalizeusza")
                for request in directory.glob("*.json"):
                    if request.is_symlink():
                        raise ValueError("Dowiązanie zamiast zlecenia Normalizeusza")
                    data = json.loads(request.read_text(encoding="utf-8"))
                    if data.get("operation") in {"scan_library", "repair_albums", "preview_albums"} or any(
                        value in {relative, str(path)} for value in data.get("paths", [])):
                        raise RuntimeError("Normalizeusz ma niedokończone zlecenie dla tego pliku; poczekaj na zakończenie")

    def _sync_beets(self, old, new, snapshot):
        directory = Path(self.library.config_dir) / "beets"
        database = directory / "library.db"
        if database.exists():
            lib = Library(database, directory=str(self.library.library_dir), set_music_dir=False)
            try:
                with lib.music_dir_context():
                    item = next((value for value in lib.items() if Path(os.fsdecode(value.path)) == old), None)
                    if item is None:
                        item = Item.from_path(new); lib.add(item)
                    else:
                        item.path = os.fsencode(new); item.read()
                    item["downloadeusz_album_source"] = snapshot["album_source"]
                    item.store()
            finally:
                lib._close()
        state_path = directory / "library-state.json"
        if state_path.exists():
            state = beets_importer._load_library_state(self.library.config_dir)
            signature = audio_packet_signature(new)
            for entry in state["tracks"].values():
                if entry.get("destination") != str(old):
                    continue
                confirmed = snapshot["action"] == "musicbrainz"
                entry.update(destination=str(new), metadata_confirmed=confirmed, release_confirmed=confirmed,
                             manual_album_accepted=not confirmed, album_source=snapshot["album_source"],
                             destination_fingerprint=beets_importer._content_fingerprint(new),
                             destination_audio_signature=signature, last_attempt=time.time(), next_attempt=None,
                             artwork_pending=album_art.artwork_pending(new), candidates=[],
                             reason="Metadane zatwierdzone ręcznie" if confirmed else "Album zastępczy zaakceptowany ręcznie")
            _json(state_path, state)

    def _rollback(self, journal, store):
        old, new = Path(journal["old"]), Path(journal["new"])
        backup = Path(journal["backup"])
        self._safe_destination(old); self._safe_destination(new)
        if old.exists():
            info = old.stat()
            allowed = {journal["original_fingerprint"]}
            if old == new:
                allowed.add(journal.get("published_fingerprint"))
            if info.st_nlink != 1 or beets_importer._content_fingerprint(old) not in allowed:
                raise RuntimeError("Oryginalny plik zmienił się po przerwaniu edycji; zachowano kopię odzyskiwania")
        ours = False
        if new != old and new.exists():
            info = new.stat()
            ours = [info.st_dev, info.st_ino] == journal.get("published_inode")
            if ours and beets_importer._content_fingerprint(new) != journal.get("published_fingerprint"):
                raise RuntimeError("Plik docelowy zmienił się po przerwaniu edycji; zachowano kopię odzyskiwania")
        if backup.exists():
            if beets_importer._content_fingerprint(backup) != journal["original_fingerprint"]:
                raise RuntimeError("Kopia odzyskiwania jest uszkodzona; zachowano istniejące pliki")
            old.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".downloadeusz-restore-", suffix=old.suffix, dir=old.parent)
            os.close(fd)
            restored = Path(name)
            try:
                shutil.copy2(backup, restored)
                restored.replace(old)
            finally:
                restored.unlink(missing_ok=True)
            _sync_directory(old.parent)
        if ours:
            new.unlink()
        directory = Path(self.library.config_dir) / "beets"
        for name in ("library-state.json", "library.db"):
            saved = Path(journal["directory"]) / name
            if journal.get("sync_attempted") and saved.exists():
                if name.endswith(".db"):
                    beets_importer._copy_database(saved, directory / name)
                else:
                    shutil.copy2(saved, directory / name)
        if journal.get("remap_attempted"):
            store.remap_library_path(journal["new_relative"], journal["old_relative"], self.library.library_dir)
        self.library.update_path(journal["track_id"], journal["old_relative"],
                                 album_accepted=journal["current"].get("album_accepted", False),
                                 album_source=journal["current"].get("album_source", ""))
        shutil.rmtree(journal["directory"])
        _sync_directory(self.state_dir)

    def _recover_locked(self, store):
        for path in self.state_dir.glob("*/journal.json"):
            if path.is_symlink():
                raise ValueError("Dowiązanie zamiast dziennika edycji")
            journal = json.loads(path.read_text(encoding="utf-8"))
            row, _ = self._get(journal["track_id"], path.parent.name)
            if row["result"]:
                self._finish(journal)
            else:
                self._rollback(journal, store)

    def _finish(self, journal):
        old, new = Path(journal["old"]), Path(journal["new"])
        self._safe_destination(old); self._safe_destination(new)
        if (not new.is_file() or new.stat().st_nlink != 1
                or beets_importer._content_fingerprint(new) != journal.get("published_fingerprint")):
            raise RuntimeError("Plik wynikowy zmienił się lub zniknął; zachowano oryginał i kopię odzyskiwania")
        if old != new and old.exists():
            if beets_importer._content_fingerprint(old) != journal["original_fingerprint"]:
                raise RuntimeError("Oryginalna ścieżka zmieniła się po edycji; zachowano plik i kopię odzyskiwania")
            old.unlink()
            _sync_directory(old.parent)
        shutil.rmtree(journal["directory"])
        _sync_directory(self.state_dir)

    @contextmanager
    def _locks(self):
        directory = Path(self.library.config_dir) / "beets"
        directory.mkdir(parents=True, exist_ok=True)
        if any(part.is_symlink() for part in (directory, *directory.parents)):
            raise ValueError("Dowiązanie zamiast stanu importera")
        descriptor = os.open(directory / "import.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            if os.fstat(lock.fileno()).st_nlink != 1:
                raise ValueError("Dowiązanie zamiast blokady importu")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Trwa import beets; poczekaj na zakończenie") from error
            with library_write_lock(self.library.library_dir):
                yield directory

    def recover(self, store):
        with self._locks():
            self._recover_locked(store)

    def apply(self, track_id, preview_token, *, store, job_id):
        with self._locks() as directory:
            self._recover_locked(store)
            row, snapshot = self._get(track_id, preview_token)
            if row["result"]:
                return json.loads(row["result"])
            snapshot = self.validate_preview(track_id, preview_token)
            old = self.library.path(track_id)
            self._busy(old, store, job_id)
            new = self._safe_destination(Path(self.library.library_dir) / snapshot["destination"])
            if new != old and new.exists():
                raise ValueError("Plik docelowy już istnieje; nie został nadpisany")
            signature = audio_packet_signature(old)
            job_store.check_cancelled()
            recovery = self.state_dir / preview_token
            if recovery.exists() and not (recovery / "journal.json").exists():
                shutil.rmtree(recovery)
            recovery.mkdir()
            _sync_directory(self.state_dir)
            backup = recovery / ("original" + old.suffix)
            shutil.copy2(old, backup)
            with backup.open("rb") as output:
                os.fsync(output.fileno())
            for name in ("library-state.json", "library.db"):
                source = directory / name
                if source.exists():
                    if name.endswith(".db"):
                        beets_importer._copy_database(source, recovery / name)
                    else:
                        shutil.copy2(source, recovery / name)
                    with (recovery / name).open("rb") as output:
                        os.fsync(output.fileno())
            journal = {"track_id": track_id, "old": str(old), "new": str(new), "backup": str(backup),
                       "directory": str(recovery), "old_relative": snapshot["current"]["relative_path"],
                       "new_relative": snapshot["destination"], "current": snapshot["current"],
                       "original_fingerprint": snapshot["fingerprint"]}
            _json(recovery / "journal.json", journal)
            stage = None
            published = False
            try:
                fd, name = tempfile.mkstemp(prefix=".downloadeusz-edit-", suffix=old.suffix, dir=old.parent)
                os.close(fd); stage = Path(name); shutil.copy2(old, stage)
                memory, item = self._item(old, snapshot["metadata"], snapshot["album_source"])
                try:
                    item.write(path=os.fsencode(stage))
                finally:
                    memory._close()
                _marker(stage, snapshot["proposed"]["album"] if snapshot["action"] == "title" else None)
                media = MediaFile(stage)
                if not media.images:
                    try:
                        data, _ = self.library.cover(track_id, thumbnail=False)
                    except (KeyError, ValueError, OSError):
                        pass
                    else:
                        media.images = [EmbeddedImage(data, desc="Cover (front)", type=ImageType.front)]
                        media.save()
                if snapshot["action"] == "musicbrainz":
                    album_art.enrich_artwork(stage, replace=True)
                if audio_packet_signature(stage) != signature:
                    raise RuntimeError("Zapis metadanych zmienił audio; przywrócono oryginał")
                with stage.open("rb") as output:
                    os.fsync(output.fileno())
                new.parent.mkdir(parents=True, exist_ok=True)
                self._safe_destination(new)
                beets_importer._library_file(old, Path(self.library.library_dir))
                if beets_importer._content_fingerprint(old) != snapshot["fingerprint"]:
                    raise ValueError("Plik zmienił się podczas przygotowania zapisu; wczytaj podgląd ponownie")
                job_store.check_cancelled()
                journal["published_fingerprint"] = beets_importer._content_fingerprint(stage)
                info = stage.stat()
                journal["published_inode"] = [info.st_dev, info.st_ino]
                _json(recovery / "journal.json", journal)
                if new == old:
                    stage.replace(new)
                else:
                    os.link(stage, new)
                    stage.unlink()
                published = True
                stage = None
                for folder in (new.parent, *new.parent.parents):
                    _sync_directory(folder)
                    if folder == Path(self.library.library_dir):
                        break
                journal["sync_attempted"] = True
                _json(recovery / "journal.json", journal)
                self._sync_beets(old, new, snapshot)
                journal["remap_attempted"] = True
                _json(recovery / "journal.json", journal)
                store.remap_library_path(journal["old_relative"], journal["new_relative"], self.library.library_dir)
                self.library.update_path(track_id, journal["new_relative"], album_accepted=snapshot["action"] == "title",
                                         album_source=snapshot["album_source"])
                result = {"paths": [snapshot["destination"]], "library_changed": True}
                with self._connect() as db:
                    db.execute("UPDATE previews SET result=? WHERE token=?", (json.dumps(result), preview_token))
            except BaseException:
                if published:
                    self._rollback(journal, store)
                else:
                    shutil.rmtree(recovery)
                raise
            finally:
                if stage:
                    stage.unlink(missing_ok=True)
            self._finish(journal)
            return result
