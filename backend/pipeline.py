"""Finalize imported work after acknowledged ReplayGain and a completed server scan."""

import json
import os
import tempfile
import time
from pathlib import Path


def relative_audio_path(path, library_dir):
    root = Path(library_dir).resolve()
    raw = Path(path)
    target = raw if raw.is_absolute() else root / raw
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise ValueError("Dowiązanie w ścieżce muzyki")
    resolved = target.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Plik poza biblioteką muzyki lub nie istnieje")
    return resolved.relative_to(root).as_posix()


def finalize(store, job, *, library_dir, normalization_dir=None, navi_call=None, lookup=None, timeout=600):
    identity, payload = job["id"], job["payload"]
    progress = dict(job["result"])
    tracks = store.tracks(identity)
    paths = list(dict.fromkeys(relative_audio_path(path, library_dir)
                              for track in tracks for path in track["result"].get("paths", [])))
    paths.extend(path for path in progress.get("paths", []) if path not in paths)
    paths = [relative_audio_path(path, library_dir) for path in paths]

    def waiting(stage, seconds=3):
        store.update(identity, status="waiting", stage=stage, result=progress, next_attempt=time.time() + seconds)
        return False

    if normalization_dir and paths and not progress.get("normalized"):
        queue = Path(normalization_dir)
        if any(part.is_symlink() for part in (queue, *queue.parents)):
            raise ValueError("Dowiązanie zamiast kolejki normalizacji")
        queue.mkdir(parents=True, exist_ok=True)
        for name in ("pending", "processing", "done"):
            directory = queue / name
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise ValueError("Nieprawidłowy katalog kolejki normalizacji")
        request_id = progress.setdefault("normalization_id", identity + "-" + str(job["attempts"]))
        store.update(identity, result=progress)
        ack = queue / "done" / (request_id + ".json")
        if ack.is_symlink():
            raise ValueError("Dowiązanie zamiast potwierdzenia normalizacji")
        if ack.is_file():
            response = json.loads(ack.read_text(encoding="utf-8"))
            if response.get("id") != request_id or response.get("status") not in ("done", "failed"):
                raise ValueError("Nieprawidłowe potwierdzenie normalizacji")
            if response["status"] == "failed":
                progress.pop("normalization_id", None)
                store.update(identity, result=progress)
                raise RuntimeError(response.get("error") or "Normalizacja nie powiodła się")
            progress["normalized"] = True
        else:
            pending = queue / "pending"
            pending.mkdir(exist_ok=True)
            request = pending / (request_id + ".json")
            processing = queue / "processing" / request.name
            if not request.exists() and not processing.exists():
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="." + request_id,
                                                 suffix=".tmp", dir=pending, delete=False) as output:
                    temporary = Path(output.name)
                    json.dump({"id": request_id, "paths": paths}, output, ensure_ascii=False)
                    # Requests contain paths only; the sidecar may use a different UID.
                    os.fchmod(output.fileno(), 0o644)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(request)
                descriptor = os.open(pending, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return waiting("normalizing", 5)

    def call(endpoint, params=None):
        value = navi_call(endpoint, params).get("subsonic-response", {})
        if value.get("status") != "ok":
            raise RuntimeError("Navidrome: " + str(value.get("error", {}).get("message", "Nieprawidłowa odpowiedź")))
        return value

    if payload.get("navi_sync"):
        if navi_call is None:
            raise RuntimeError("Brak konfiguracji Navidrome do zakończenia zadania")
        if not progress.get("scan_started"):
            call("startScan.view")
            progress["scan_started"] = time.time()
            store.update(identity, stage="scanning", result=progress)
        response = call("getScanStatus.view")
        if "scanStatus" not in response:
            raise RuntimeError("Navidrome nie zwrócił stanu skanowania")
        if response["scanStatus"].get("scanning"):
            if time.time() - progress["scan_started"] > timeout:
                progress.pop("scan_started", None)
                store.update(identity, result=progress)
                raise RuntimeError("Navidrome nie zakończył indeksowania w wyznaczonym czasie")
            return waiting("scanning")
        if payload.get("flags", {}).get("generate_m3u"):
            song_ids, missing = [], []
            for track in tracks:
                result = dict(track["result"])
                song_id = result.get("navidrome_id")
                if not song_id and lookup:
                    song = lookup(track["payload"], result.get("paths", []))
                    song_id = (song or {}).get("id")
                    if song_id:
                        result["navidrome_id"] = song_id
                        store.update_track(identity, track["position"], status=track["status"], result=result)
                if song_id:
                    song_ids.append(song_id)
                else:
                    missing.append(track["position"])
            progress["missing_tracks"] = missing
            if missing and time.time() - progress["scan_started"] < timeout:
                return waiting("playlist", 5)
            if song_ids:
                playlist_id = progress.get("playlist_id")
                temporary_name = "downloadeusz-" + identity
                if not playlist_id:
                    playlists = call("getPlaylists.view").get("playlists", {}).get("playlist", [])
                    playlist_id = next((item["id"] for item in playlists if item.get("name") == temporary_name), None)
                params = {"songId": song_ids}
                params["playlistId" if playlist_id else "name"] = playlist_id or temporary_name
                created = call("createPlaylist.view", params).get("playlist", {})
                playlist_id = created.get("id") or playlist_id
                if not playlist_id:
                    raise RuntimeError("Navidrome nie zwrócił identyfikatora playlisty")
                progress["playlist_id"] = playlist_id
                store.update(identity, result=progress)
                flags = payload.get("flags", {})
                name = flags.get("m3u_name", "").strip()
                if not name or name == "{list}":
                    name = payload.get("playlist_name") or "Downloadeusz"
                call("updatePlaylist.view", {"playlistId": playlist_id, "name": name,
                                             "public": str(bool(flags.get("public_playlist"))).lower()})
            if missing:
                store.update(identity, status="needs_review", stage="playlist", result=progress,
                             error=f"Brak {len(missing)} utworów w indeksie Navidrome")
                return True
    unfinished = [track for track in store.tracks(identity) if track["status"] in {"failed", "needs_review"}]
    store.update(identity, status="needs_review" if unfinished else "done", stage="done", result=progress,
                 error=f"{len(unfinished)} utworów wymaga sprawdzenia" if unfinished else "")
    return True
