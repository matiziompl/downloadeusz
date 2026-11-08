"""Protect existing library files before beets writes an imported album."""

import os
import json
import shutil
import tempfile
import sys
from pathlib import Path

from beets import plugins, ui
from beets.plugins import BeetsPlugin
from beets.importer import Action

try:
    from backend.music_metadata import clean_title, usable_album, album_fallback_from_tags
except ImportError:
    # The beets CLI loads this package from pluginpath, outside the API package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from music_metadata import clean_title, usable_album, album_fallback_from_tags

from .downloadeusz_policy import candidate_summaries, choose_recording, release_agrees, versions_conflict


RELEASE_FIELDS = ("album", "albumartist", "albumartists", "year", "month", "day",
                  "original_year", "original_month", "original_day", "track", "tracktotal",
                  "disc", "disctotal", "mb_albumid", "mb_releasegroupid", "albumdisambig",
                  "label", "catalognum", "country", "media")


class DownloadeuszPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()
        self.early_import_stages = [self.protect_destinations]
        self.preserved_art = set()
        self.originals = {}
        self.decisions = {}
        self.sources = {}
        self.written_paths = set()
        self.fallback_writes = set()
        self.register_listener("import_task_start", self.snapshot_sources)
        self.register_listener("import_task_choice", self.review_choice)
        # Preserved-art embedding emits after_write inside our own wrapped callback.
        self.listeners["after_write"].append(self.metadata_written)
        self.register_listener("import_task_files", self.imported_files)

    def metadata_written(self, item, path):
        self.written_paths.add(os.path.abspath(os.fsdecode(path)))

    def restore_source_copy(self, session, source, destination):
        source, destination = Path(source).absolute(), Path(destination).absolute()
        root = Path(os.fsdecode(session.lib.directory)).resolve()
        if (not destination.resolve().is_relative_to(root) or not source.is_file()
                or any(part.is_symlink() for part in [source, *source.parents, destination, *destination.parents])
                or (destination.exists() and (destination.stat().st_nlink > 1 or os.path.samefile(source, destination)))):
            raise ui.UserError("Nie można bezpiecznie przywrócić kopii po błędzie zapisu metadanych")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".downloadeusz-recover-", suffix=".bak", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def snapshot_sources(self, session, task):
        for item in task.items:
            source = os.fsdecode(item.path)
            self.originals.setdefault(source, {
                **{name: item.get(name) for name in RELEASE_FIELDS},
                "album_source": (os.getenv("DOWNLOADEUSZ_BEETS_ALBUM_SOURCE") or item.get("downloadeusz_album_source", "")
                                 or ("fallback" if album_fallback_from_tags(source) else "")),
            })

    def report(self, record):
        report_path = os.getenv("DOWNLOADEUSZ_BEETS_REPORT")
        if report_path:
            descriptor = os.open(report_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")

    def review_choice(self, session, task):
        self.snapshot_sources(session, task)
        candidates = candidate_summaries(task.candidates)
        reason = "Pewne dopasowanie nagrania"
        if task.apply:
            pairs = task.match.item_info_pairs if task.is_album else [(task.item, task.match.info)]
            if any(versions_conflict(item.title, info.title) for item, info in pairs):
                task.set_choice(Action.SKIP)
                reason = "Proponowana wersja nagrania różni się od źródła (np. koncert lub remiks)"
        elif task.skip and not task.is_album:
            chosen, reason = choose_recording(task.item, task.candidates or [])
            if chosen is not None:
                task.set_choice(chosen)
        if task.skip:
            if not task.candidates:
                reason = "Nie znaleziono propozycji w bazach muzycznych"
            elif task.is_album and not reason.startswith("Proponowana"):
                reason = "Brak pewnego dopasowania całego albumu; można sprawdzić utwory osobno"
            for item in task.items:
                source = os.fsdecode(item.path)
                self.decisions[source] = {"metadata_confirmed": False, "release_confirmed": False,
                                          "reason": reason, "candidates": candidates}
                self.report({"event": "review", "source": source, "reason": reason, "candidates": candidates})
            return
        for item in task.imported_items():
            source = os.fsdecode(item.path)
            confirmed = bool(task.apply)
            release = bool(confirmed and (task.is_album or release_agrees(self.originals[source], task.match.info)))
            self.decisions[source] = {"metadata_confirmed": confirmed, "release_confirmed": release,
                                      "reason": reason if confirmed else "Zachowano istniejące tagi",
                                      "candidates": candidates}

    def protect_destinations(self, session, task):
        root = Path(os.fsdecode(session.lib.directory)).absolute()
        destinations = []
        copying = session.config["copy"].get(bool) or session.config["move"].get(bool)
        self.snapshot_sources(session, task)
        for item in task.imported_items():
            source = os.fsdecode(item.path)
            self.sources[item.id] = source
            decision = self.decisions.setdefault(source, {"metadata_confirmed": False, "release_confirmed": False,
                                                         "reason": "Zachowano istniejące tagi", "candidates": []})
            if task.apply and not task.is_album and not decision["release_confirmed"]:
                for name in RELEASE_FIELDS:
                    item[name] = self.originals[source][name]
            provenance = self.originals[source].get("album_source", "")
            if decision["release_confirmed"]:
                provenance = "confirmed"
            elif not usable_album(item.album) and os.getenv("DOWNLOADEUSZ_BEETS_ALLOW_FALLBACK", "1") == "1":
                title = clean_title(item.title, item.artist)
                if title and item.artist.strip() and item.artist.casefold() not in {"unknown", "unknown artist", "nieznany wykonawca"}:
                    item.title = title
                    item.album = title
                    provenance = "fallback"
                    self.fallback_writes.add(source)
            elif not provenance and usable_album(item.album):
                provenance = "tags"
            item["downloadeusz_album_source"] = provenance
            decision["album_source"] = provenance
            decision["album_fallback_written"] = source in self.fallback_writes
            item.store()
            destination = Path(os.fsdecode(item.destination() if copying else item.path))
            if not destination.resolve().is_relative_to(root.resolve()):
                raise ui.UserError(f"Cel importu wychodzi poza bibliotekę muzyki: {destination}")
            for part in [destination, *destination.parents]:
                if part == root:
                    break
                if part.is_symlink():
                    raise ui.UserError(f"Dowiązanie symboliczne w docelowej bibliotece: {part}")
            if destination.exists() and destination.stat().st_nlink > 1:
                raise ui.UserError(f"Plik docelowy ma dowiązania twarde: {destination}")
            if copying and destination.exists() and os.path.samefile(destination, source):
                raise ui.UserError(f"Plik docelowy wskazuje na źródłowy plik audio: {destination}")
            destinations.append(destination)
        if task.is_album and destinations:
            for extension in (".jpg", ".jpeg", ".png", ".webp"):
                cover = destinations[0].parent / ("cover" + extension)
                if cover.is_symlink():
                    raise ui.UserError(f"Dowiązanie symboliczne zamiast okładki: {cover}")
                if cover.is_file():
                    task.album.artpath = os.fsencode(cover)
                    task.album.store()
                    self.preserved_art.add(task)
                    break

    def imported_files(self, session, task):
        # Beets ASIS copies audio but does not write tags. Write the synthetic
        # album only after copying, under the same safety checks as matched tags.
        for item in task.imported_items():
            if self.sources.get(item.id) in self.fallback_writes and not task.apply:
                item.try_write()
        # Beets logs failed try_write calls and still emits import_task_files.
        failed = [item for item in task.imported_items() if (task.apply or self.sources.get(item.id) in self.fallback_writes)
                  and os.path.abspath(os.fsdecode(item.path)) not in self.written_paths]
        if failed:
            copying = session.config["copy"].get(bool) or session.config["move"].get(bool)
            for item in task.imported_items():
                source = self.sources.get(item.id, os.fsdecode(item.path))
                decision = self.decisions.get(source, {})
                if item in failed:
                    if session.config["copy"].get(bool):
                        self.restore_source_copy(session, source, os.fsdecode(item.path))
                    decision = {**decision, "metadata_confirmed": False, "release_confirmed": False,
                                "album_source": self.originals.get(source, {}).get("album_source", ""),
                                "album_fallback_written": False,
                                "reason": "Nie udało się zapisać metadanych; plik wymaga ponownego sprawdzenia"}
                self.report({"event": "imported" if copying or item not in failed else "review",
                             "source": source, "destination": os.fsdecode(item.path), **decision})
            raise ui.UserError("Nie udało się zapisać metadanych; import nie został potwierdzony")
        if task in self.preserved_art:
            self.preserved_art.remove(task)
            plugins.send("art_set", album=task.album)
        for item in task.imported_items():
            source = self.sources.get(item.id, os.fsdecode(item.path))
            decision = self.decisions.get(source, {"metadata_confirmed": False, "release_confirmed": False,
                                                   "reason": "Zachowano istniejące tagi", "candidates": []})
            self.report({"event": "imported", "source": source, "destination": os.fsdecode(item.path), **decision})
