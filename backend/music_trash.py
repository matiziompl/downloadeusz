"""Trash is a persisted index filter; only explicit deletion unlinks a library copy."""

import json
import stat
import time
from pathlib import Path

try:
    from . import beets_importer
    from .music_editor import MusicEditor, _json, _sync_directory
    from .music_library import file_version
except ImportError:
    import beets_importer
    from music_editor import MusicEditor, _json, _sync_directory
    from music_library import file_version


def _regular(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Dowiązanie zamiast stanu biblioteki')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Nieprawidłowy plik stanu biblioteki')


class MusicTrash:
    def __init__(self, library, normalization_dir=None):
        self.library = library
        self.normalization_dir = normalization_dir

    def move(self, track_id, version, *, store=None):
        self.library.set_trashed(track_id, version, True)
        return {'status': 'trashed', 'message': 'Oznaczono utwór jako kosz. Plik pozostał na swoim miejscu.'}

    def restore(self, track_id, version, *, store=None):
        self.library.set_trashed(track_id, version, False)
        return {'status': 'restored', 'message': 'Przywrócono utwór na listę biblioteki.'}

    def _busy(self, path, track_id, store, editor):
        editor._busy(path, store, None)
        with store.connect() as db:
            for row in db.execute("SELECT kind,payload FROM jobs WHERE status IN ('queued','running','waiting')"):
                payload = json.loads(row['payload'])
                if payload.get('track_id') == track_id or row['kind'] == 'normalizer':
                    raise RuntimeError('Plik jest używany przez niedokończone zadanie; poczekaj na jego zakończenie')

    def _mark_beets(self, path, track_id):
        state_path = self.library.config_dir / 'beets' / 'library-state.json'
        if state_path.is_symlink():
            raise ValueError('Dowiązanie zamiast stanu biblioteki beets')
        if not state_path.exists():
            return None
        _regular(state_path)
        try:
            state = beets_importer._load_library_state(self.library.config_dir)
        except ValueError:
            raise ValueError('Stan biblioteki beets jest nieprawidłowy; nie usunięto utworu.') from None
        previous = json.loads(json.dumps(state))
        changed = False
        for entry in state['tracks'].values():
            if entry.get('destination') and Path(entry['destination']).absolute() == path:
                entry['trashed'] = {'track_id': track_id, 'deleted': True, 'at': time.time()}
                changed = True
        if changed:
            _json(state_path, state)
            return state_path, previous
        return None

    @staticmethod
    def _finish_delete(db, track_id, version):
        db.execute('DELETE FROM music_tracks WHERE id=? AND version=? AND trashed=1', (track_id, version))
        db.execute("UPDATE music_deletions SET state='deleted' WHERE track_id=? AND version=?", (track_id, version))

    def delete(self, track_id, version, *, store):
        result = {'status': 'deleted', 'message': 'Usunięto plik utworu z dysku.'}
        editor = MusicEditor(self.library, normalization_dir=self.normalization_dir)
        with editor._locks(), self.library._index_lock(), self.library.connect() as db:
            if any((self.library.config_dir / 'music-edits').glob('*/journal.json')):
                raise RuntimeError('Trwa odzyskiwanie edycji muzyki; poczekaj na zakończenie zadania')
            intent = db.execute('SELECT * FROM music_deletions WHERE track_id=? AND version=?', (track_id, version)).fetchone()
            if intent:
                if intent['state'] == 'deleted':
                    return result
                raw = Path(intent['path'])
                if raw.is_absolute() or '..' in raw.parts:
                    raise ValueError('Nieprawidłowa ścieżka utworu')
                path = self.library.library_dir / raw
                if any(part.is_symlink() for part in (path, *path.parents)):
                    raise ValueError('Dowiązanie zamiast pliku muzyki')
                if not path.exists():
                    # The committed intent permits safe replay after unlink or index cleanup.
                    _sync_directory(path.parent)
                    self._finish_delete(db, track_id, version)
                    return result
            row = db.execute('SELECT * FROM music_tracks WHERE id=?', (track_id,)).fetchone()
            if row is None:
                raise KeyError('Nie znaleziono utworu')
            if not row['trashed']:
                raise ValueError('Najpierw oznacz utwór jako kosz.')
            if row['version'] != version:
                raise ValueError('Plik zmienił się od wyświetlenia potwierdzenia. Odśwież listę.')
            path = self.library._safe_path(row['path'])
            if file_version(path) != version:
                raise ValueError('Plik zmienił się od wyświetlenia potwierdzenia. Odśwież listę.')
            self._busy(path, track_id, store, editor)
            db.execute("INSERT OR REPLACE INTO music_deletions VALUES(?,?,?,'pending',?)",
                       (track_id, version, row['path'], time.time()))
            db.commit()
            previous = self._mark_beets(path, track_id)
            try:
                if file_version(self.library._safe_path(row['path'])) != version:
                    raise ValueError('Plik zmienił się przed usunięciem. Odśwież listę.')
                path.unlink()
            except (OSError, ValueError):
                if previous:
                    _json(*previous)
                db.execute('DELETE FROM music_deletions WHERE track_id=? AND version=?', (track_id, version))
                db.commit()
                raise
            _sync_directory(path.parent)
            self._finish_delete(db, track_id, version)
            return result
