"""A tag index of the actual music directory; indexing never changes audio files."""

import fcntl
import hashlib
import io
import json
import os
import sqlite3
import stat
import time
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

from mediafile import MediaFile
from mediafile.exceptions import MediaFileError
from PIL import Image

try:
    from . import beets_importer, music_metadata
    from .audio_identity import library_write_lock
except ImportError:
    import beets_importer
    import music_metadata
    from audio_identity import library_write_lock


AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.mp4', '.aac', '.wav', '.aiff', '.aif', '.ape', '.wv', '.wma'}


def search_key(value):
    value = str(value or '').casefold().translate(str.maketrans({'ł': 'l', 'đ': 'd', 'ø': 'o', 'æ': 'ae', 'œ': 'oe'}))
    return ''.join(char for char in unicodedata.normalize('NFKD', value) if not unicodedata.combining(char))


def file_version(path):
    info = Path(path).stat()
    return f'{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}'


def _public(record):
    return {key: value for key, value in record.items() if not key.startswith('_')}


class MusicLibrary:
    def __init__(self, config_dir, library_dir):
        self.config_dir = Path(config_dir).absolute()
        self.library_dir = Path(library_dir).absolute()
        if any(part.is_symlink() for root in (self.config_dir, self.library_dir) for part in (root, *root.parents)):
            raise ValueError('Dowiązanie zamiast katalogu biblioteki lub konfiguracji')
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.database = self.config_dir / 'music-library.sqlite3'
        descriptor = os.open(self.database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Nieprawidłowy indeks muzyki')
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with self._index_lock(), self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS music_tracks (
                    id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, version TEXT NOT NULL,
                    data TEXT NOT NULL, search TEXT NOT NULL, incomplete INTEGER NOT NULL,
                    decision_album TEXT NOT NULL DEFAULT '', decision_source TEXT NOT NULL DEFAULT '',
                    accepted INTEGER NOT NULL DEFAULT 0, trashed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS music_status (id INTEGER PRIMARY KEY, indexed_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS music_deletions (
                    track_id TEXT NOT NULL, version TEXT NOT NULL, path TEXT NOT NULL,
                    state TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(track_id,version)
                );
            ''')
            if 'trashed' not in {row['name'] for row in db.execute('PRAGMA table_info(music_tracks)')}:
                db.execute('ALTER TABLE music_tracks ADD COLUMN trashed INTEGER NOT NULL DEFAULT 0')

    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _index_lock(self):
        descriptor = os.open(self.config_dir / 'music-index.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Nieprawidłowa blokada indeksu muzyki')
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _safe_path(self, relative):
        raw = Path(relative)
        if raw.is_absolute() or '..' in raw.parts:
            raise ValueError('Ścieżka poza biblioteką muzyki')
        path = self.library_dir / raw
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError('Dowiązanie zamiast pliku muzyki')
        if not path.resolve().is_relative_to(self.library_dir.resolve()) or not path.is_file():
            raise ValueError('Plik muzyki nie istnieje w bibliotece')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Muzyka musi być osobną kopią pliku')
        return path

    def path(self, track_id):
        with self.connect() as db:
            row = db.execute('SELECT path FROM music_tracks WHERE id=?', (track_id,)).fetchone()
        if row is None:
            raise KeyError('Nie znaleziono utworu')
        return self._safe_path(row['path'])

    def get_track(self, track_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM music_tracks WHERE id=?', (track_id,)).fetchone()
        if row is None:
            raise KeyError('Nie znaleziono utworu')
        if file_version(self._safe_path(row['path'])) != row['version']:
            raise ValueError('Plik zmienił się od odświeżenia biblioteki. Odśwież listę.')
        return {**_public(json.loads(row['data'])), 'trashed': bool(row['trashed'])}

    def _read_tags(self, path):
        media = MediaFile(path)
        marker = music_metadata.album_fallback_details(path)
        return {**{field: getattr(media, field, None) or '' for field in
                   ('title', 'artist', 'album', 'albumartist', 'year', 'track', 'disc', 'mb_albumid')},
                'title': media.title or path.stem, 'duration': float(media.length or 0),
                'format': path.suffix.lstrip('.').lower(),
                '_portable_fallback': bool(marker), '_portable_accepted': marker.get('accepted') is True}

    def _record(self, path, identity, metadata, entry, decision_album='', decision_source='', accepted=False):
        album = str(metadata.get('album') or '')
        has_decision = bool(decision_source and decision_album == album)
        source = decision_source if has_decision else entry.get('album_source', '')
        if not source and metadata.get('_portable_fallback'):
            source = 'fallback'
        accepted = bool(has_decision and accepted or source == 'fallback' and metadata.get('_portable_accepted'))
        unconfirmed = bool(entry and not entry.get('release_confirmed'))
        needs_album = not accepted and (not music_metadata.usable_album(album) or source == 'fallback' or
                                        (unconfirmed and source != 'musicbrainz'))
        version = file_version(path)
        return {**metadata, 'id': identity, 'relative_path': path.relative_to(self.library_dir).as_posix(),
                'version': version, 'album_source': source, 'album_accepted': accepted,
                'needs_album': needs_album,
                'incomplete_reason': ('Album zastępczy' if source == 'fallback' else 'Album niepotwierdzony' if album else 'Brak albumu') if needs_album else '',
                'cover_url': f'/api/music/tracks/{identity}/cover?v={hashlib.sha256(version.encode()).hexdigest()[:16]}'}

    @staticmethod
    def _store(db, record, *, decision_album='', decision_source='', accepted=False):
        text = search_key(' '.join(str(record.get(field) or '') for field in ('title', 'artist', 'album', 'albumartist')))
        db.execute('''INSERT INTO music_tracks(id,path,version,data,search,incomplete,decision_album,decision_source,accepted)
                      VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path,version=excluded.version,
                      data=excluded.data,search=excluded.search,incomplete=excluded.incomplete,
                      decision_album=excluded.decision_album,decision_source=excluded.decision_source,accepted=excluded.accepted''',
                   (record['id'], record['relative_path'], record['version'], json.dumps(record, ensure_ascii=False),
                    text, int(record['needs_album']), decision_album, decision_source, int(accepted)))

    def refresh(self):
        skipped = 0
        with library_write_lock(self.library_dir), self._index_lock(), self.connect() as db:
            if any((self.config_dir / 'music-edits').glob('*/journal.json')):
                raise ValueError('Trwa odzyskiwanie przerwanej edycji muzyki. Spróbuj po zakończeniu zadania.')
            state = beets_importer._load_library_state(self.config_dir)
            entries = {str(Path(entry['destination']).absolute()): entry for entry in state['tracks'].values() if entry.get('destination')}
            previous = {row['path']: row for row in db.execute('SELECT * FROM music_tracks')}
            seen = set()
            if self.library_dir.is_symlink():
                raise ValueError('Dowiązanie zamiast katalogu biblioteki')
            def unreadable(error):
                raise error

            for directory, dirs, files in os.walk(self.library_dir, followlinks=False, onerror=unreadable):
                dirs[:] = [name for name in dirs if not name.startswith('.') and not (Path(directory) / name).is_symlink()]
                for filename in files:
                    if filename.startswith('.') or Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    raw = Path(directory) / filename
                    relative = raw.relative_to(self.library_dir).as_posix()
                    old = previous.get(relative)
                    try:
                        path = self._safe_path(relative)
                        version = file_version(path)
                        metadata = json.loads(old['data']) if old and old['version'] == version else self._read_tags(path)
                        identity = old['id'] if old else uuid.uuid4().hex
                        record = self._record(path, identity, metadata, entries.get(str(path), {}),
                                              old['decision_album'] if old else '', old['decision_source'] if old else '',
                                              bool(old['accepted']) if old else False)
                        if file_version(path) != version:
                            raise ValueError('Plik zmienił się podczas indeksowania')
                        self._store(db, record, decision_album=old['decision_album'] if old else '',
                                    decision_source=old['decision_source'] if old else '', accepted=bool(old['accepted']) if old else False)
                        seen.add(identity)
                    except (OSError, ValueError, MediaFileError):
                        # A corrupt tag must not hide the remaining library; report the skipped count.
                        skipped += 1
            for row in previous.values():
                if row['id'] not in seen:
                    db.execute('DELETE FROM music_tracks WHERE id=?', (row['id'],))
            now = time.time()
            db.execute('INSERT OR REPLACE INTO music_status VALUES(1,?)', (now,))
            summary = db.execute('SELECT COUNT(*) total,COALESCE(SUM(incomplete),0) incomplete FROM music_tracks WHERE trashed=0').fetchone()
        return {**dict(summary), 'indexed_at': now, 'skipped': skipped}

    def list_tracks(self, query='', incomplete=False, offset=0, limit=48, trashed=False):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError('Nieprawidłowy zakres biblioteki')
        conditions, values = ['trashed=?'], [int(trashed)]
        if incomplete:
            conditions.append('incomplete=1')
        for word in search_key(query).split():
            conditions.append("search LIKE ? ESCAPE '\\'")
            values.append('%' + word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM music_tracks' + where, values).fetchone()[0]
            rows = db.execute('SELECT data,trashed FROM music_tracks' + where + ' ORDER BY search,id LIMIT ? OFFSET ?',
                              (*values, limit, offset)).fetchall()
            timestamp = db.execute('SELECT indexed_at FROM music_status WHERE id=1').fetchone()
        return {'items': [{**_public(json.loads(row['data'])), 'trashed': bool(row['trashed'])} for row in rows],
                'total': total, 'offset': offset, 'limit': limit, 'indexed_at': timestamp[0] if timestamp else None}

    def set_trashed(self, track_id, version, trashed):
        with self._index_lock(), self.connect() as db:
            if not trashed and db.execute("SELECT 1 FROM music_deletions WHERE track_id=? AND state='pending'",
                                         (track_id,)).fetchone():
                raise ValueError('Niedokończone usuwanie tego utworu blokuje przywracanie. Odśwież listę i sprawdź stan operacji.')
            row = db.execute('SELECT * FROM music_tracks WHERE id=?', (track_id,)).fetchone()
            if row is None:
                raise KeyError('Nie znaleziono utworu')
            if row['version'] != version or file_version(self._safe_path(row['path'])) != version:
                raise ValueError('Plik zmienił się od wyświetlenia listy. Odśwież bibliotekę.')
            db.execute('UPDATE music_tracks SET trashed=? WHERE id=?', (int(trashed), track_id))
        return {**_public(json.loads(row['data'])), 'trashed': bool(trashed)}

    def update_path(self, track_id, new_relative_path, *, album_accepted=False, album_source=''):
        with self._index_lock(), self.connect() as db:
            old = db.execute('SELECT id,trashed FROM music_tracks WHERE id=?', (track_id,)).fetchone()
            if old is None:
                raise KeyError('Nie znaleziono utworu')
            path = self._safe_path(new_relative_path)
            metadata = self._read_tags(path)
            record = self._record(path, track_id, metadata, {}, str(metadata['album']), album_source, album_accepted)
            self._store(db, record, decision_album=str(metadata['album']), decision_source=album_source, accepted=album_accepted)
        return {**_public(record), 'trashed': bool(old['trashed'])}

    def cover(self, track_id, *, thumbnail=True):
        path = self.path(track_id)
        try:
            images = MediaFile(path).images
        except MediaFileError as error:
            raise KeyError('Brak czytelnej okładki') from error
        data = images[0].data if images else None
        if data is None:
            for filename in ('cover.jpg', 'cover.jpeg', 'cover.png', 'cover.webp', 'folder.jpg'):
                local = path.parent / filename
                if local.is_file() and not local.is_symlink() and local.stat().st_nlink == 1 and local.stat().st_size <= 8 * 1024 * 1024:
                    data = local.read_bytes()
                    break
        if not data or len(data) > 8 * 1024 * 1024:
            raise KeyError('Brak okładki')
        with Image.open(io.BytesIO(data)) as picture:
            if picture.width * picture.height > 40_000_000:
                raise ValueError('Zbyt duża okładka')
            if not thumbnail:
                mime = Image.MIME.get(picture.format)
                if mime not in {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}:
                    raise ValueError('Nieobsługiwana okładka')
                picture.verify()
                return data, mime
            picture.thumbnail((320, 320))
            output = io.BytesIO()
            picture.convert('RGB').save(output, format='JPEG', quality=85)
        return output.getvalue(), 'image/jpeg'
