import os
import json
import subprocess
import asyncio
import uuid
import urllib.parse
import time
import hashlib
import secrets
import glob
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

try:
    from backend import navidrome_dedup, slskd_organizer, accounts, spotify_meta
except ImportError:
    import navidrome_dedup
    import slskd_organizer
    import accounts
    import spotify_meta


app = FastAPI()

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))
WALLPAPERS_DIR = os.getenv("WALLPAPERS_DIR", os.path.join(FRONTEND_DIR, "assets", "wallpapers"))
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "/downloads")
SPOTDL_CONFIG_DIR = os.getenv("SPOTDL_CONFIG_DIR", "/spotdl_config")
SLSKD_DOWNLOADS_DIR = os.getenv("SLSKD_DOWNLOADS_DIR", "/slskd_downloads")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
PUID = int(os.getenv("PUID", "1000"))
PGID = int(os.getenv("PGID", "1000"))

# Ensure wallpaper and log directories exist
os.makedirs(WALLPAPERS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Set default umask to 0002 so new files and directories are group-writable
try:
    os.umask(0o002)
except Exception:
    pass

def fix_download_permissions(target_dir: str):
    """Ensure all downloaded files/folders belong to PUID:PGID with proper read/write permissions."""
    if not os.path.exists(target_dir):
        return
    try:
        subprocess.run(
            ["chown", "-R", f"{PUID}:{PGID}", target_dir],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        subprocess.run(
            ["chmod", "-R", "u+rwX,g+rwX,o+rX", target_dir],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[Permissions] Warning: Failed to adjust ownership in {target_dir}: {e}")

# Ensure local directories exist (mostly for logs)
os.makedirs(LOGS_DIR, exist_ok=True)
# Only create if running locally without Docker (in Docker they are mounted)
if not os.path.exists(DOWNLOADS_DIR):
    try: os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    except: pass
fix_download_permissions(DOWNLOADS_DIR)

class DownloadRequest(BaseModel):
    tool: str # 'ytdl' or 'spotdl'
    url: str
    flags: dict = {}
    navi_account_id: str = ""
    navi_url: str = ""
    navi_user: str = ""
    navi_pass: str = ""
    navi_sync: bool = False

class NaviTestRequest(BaseModel):
    account_id: str = ""
    url: str = ""
    user: str = ""
    password: str = ""

class SlskdTestRequest(BaseModel):
    url: str = ""
    username: str = ""
    password: str = ""
    api_key: str = ""

class SpotifyMetaRequest(BaseModel):
    url: str

class SlskdOrganizeRequest(BaseModel):
    slskd_url: str = ""
    slskd_user: str = ""
    slskd_pass: str = ""
    slskd_api_key: str = ""
    navi_sync: bool = False
    navi_account_id: str = ""
    navi_url: str = ""
    navi_user: str = ""
    navi_pass: str = ""

@app.get("/api/accounts/navidrome")
async def get_navidrome_accounts_endpoint():
    accs = accounts.get_navidrome_accounts(SPOTDL_CONFIG_DIR)
    safe_accs = [
        {"id": a["id"], "server": a["server"], "username": a["username"]}
        for a in accs
    ]
    return {"accounts": safe_accs}

@app.get("/api/accounts/slskd")
async def get_slskd_account_endpoint():
    acc = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
    if acc:
        return {"configured": True, "server": acc["server"], "username": acc["username"]}
    return {"configured": False}

@app.post("/api/spotify/metadata")
async def get_spotify_metadata_endpoint(req: SpotifyMetaRequest):
    res = spotify_meta.fetch_spotify_metadata(
        url=req.url,
        downloads_dir=DOWNLOADS_DIR,
        config_dir=SPOTDL_CONFIG_DIR
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("message", "Błąd pobierania metadanych"))
    return res

@app.post("/api/test/navidrome")
async def test_navidrome(req: NaviTestRequest):
    url = req.url
    user = req.user
    password = req.password

    # If account_id provided or password empty, lookup from navidrome_accounts.txt
    if (req.account_id or not password):
        lookup_id = req.account_id or req.user
        if lookup_id:
            acc = accounts.get_navidrome_account_by_id(lookup_id, SPOTDL_CONFIG_DIR)
            if acc:
                url = acc.get("server") or url
                user = acc.get("username") or user
                password = acc.get("password") or password

    if not url:
        return {"ok": False, "message": "Brak wybranego konta lub adresu URL Navidrome"}
    try:
        data = navidrome_dedup.subsonic_request(
            base_url=url,
            user=user,
            password=password,
            endpoint="ping.view",
            timeout=5
        )
        sub_resp = data.get("subsonic-response", {})
        if sub_resp.get("status") == "ok":
            server_version = sub_resp.get("serverVersion", sub_resp.get("version", ""))
            return {
                "ok": True,
                "message": f"Połączono pomyślnie z Navidrome ({user})!",
                "server_version": server_version
            }
        else:
            error = sub_resp.get("error", {})
            return {"ok": False, "message": f"Błąd Navidrome: {error.get('message', 'Nieznany błąd')}"}
    except Exception as e:
        return {"ok": False, "message": f"Nie udało się połączyć z Navidrome: {str(e)}"}

@app.post("/api/test/slskd")
async def test_slskd(req: SlskdTestRequest):
    url = req.url
    username = req.username
    password = req.password
    api_key = req.api_key

    # If credentials not provided, auto-load active account from slskd_accounts.txt
    if not password and not api_key:
        acc = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
        if acc:
            url = url or acc.get("server", "")
            username = username or acc.get("username", "")
            password = acc.get("password", "")

    if not url:
        return {"ok": False, "message": "Brak skonfigurowanego konta Soulseek (slskd) w slskd_accounts.txt"}
    try:
        import urllib.request
        import urllib.error
        slskd_url = url
        if not slskd_url.startswith(('http://', 'https://')):
            slskd_url = f"http://{slskd_url}"

        headers = slskd_organizer.get_slskd_auth_header(
            slskd_url,
            username=username,
            password=password,
            api_key=api_key,
            timeout=5
        )

        api_url = f"{slskd_url.rstrip('/')}/api/v0/transfers/downloads"
        request_obj = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(request_obj, timeout=5) as response:
            status_code = getattr(response, "status", None)
            if status_code is None and hasattr(response, "getcode"):
                status_code = response.getcode()
            if status_code is None or status_code == 200:
                return {"ok": True, "message": f"Połączono i zalogowano pomyślnie do Soulseek ({username or 'slskd'})!"}
            return {"ok": False, "message": f"Odpowiedź serwera: {status_code}"}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"ok": False, "message": "Błąd autoryzacji Soulseek (slskd): Nieprawidłowy login lub hasło"}
        return {"ok": False, "message": f"Błąd Soulseek (slskd): HTTP {e.code}"}
    except RuntimeError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"Nie udało się połączyć z Soulseek (slskd): {str(e)}"}


def run_slskd_organize_task(task_id: str, request: SlskdOrganizeRequest):
    log_file_path = os.path.join(LOGS_DIR, f"{task_id}.log")
    with open(log_file_path, "w", encoding="utf-8") as f:
        def log_cb(msg: str):
            f.write(f"{msg}\n")
            f.flush()

        log_cb("=== Rozpoczynanie porządkowania plików Soulseek (slskd) ===")
        try:
            slskd_url = request.slskd_url
            slskd_user = request.slskd_user
            slskd_pass = request.slskd_pass
            slskd_api_key = request.slskd_api_key
            if not slskd_url or (not slskd_pass and not slskd_api_key):
                acc = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
                if acc:
                    slskd_url = slskd_url or acc.get("server", "")
                    slskd_user = slskd_user or acc.get("username", "")
                    slskd_pass = acc.get("password", "")

            navi_url = request.navi_url
            navi_user = request.navi_user
            navi_pass = request.navi_pass
            if request.navi_sync and (request.navi_account_id or not navi_pass):
                n_acc = accounts.get_navidrome_account_by_id(request.navi_account_id or request.navi_user, SPOTDL_CONFIG_DIR)
                if n_acc:
                    navi_url = n_acc.get("server", navi_url)
                    navi_user = n_acc.get("username", navi_user)
                    navi_pass = n_acc.get("password", navi_pass)

            res = slskd_organizer.organize_slskd_downloads(
                slskd_url=slskd_url,
                slskd_api_key=slskd_api_key,
                slskd_username=slskd_user,
                slskd_password=slskd_pass,
                source_dir=SLSKD_DOWNLOADS_DIR,
                dest_dir=DOWNLOADS_DIR,
                log_callback=log_cb
            )
            log_cb(f"\n[Uprawnienia] Ustawianie praw dostępu (PUID:PGID {PUID}:{PGID})...")
            fix_download_permissions(DOWNLOADS_DIR)

            if request.navi_sync and navi_url and navi_user and navi_pass:
                log_cb("\n[Navidrome] Uruchamianie skanowania biblioteki po uporządkowaniu...")
                try:
                    navidrome_dedup.subsonic_request(
                        base_url=navi_url,
                        user=navi_user,
                        password=navi_pass,
                        endpoint="startScan.view",
                        timeout=10
                    )
                    log_cb("[Navidrome] Skanowanie zostało pomyślnie zlecone!")
                except Exception as e:
                    log_cb(f"[Navidrome] Nie udało się wywołać skanowania: {e}")

            log_cb(f"\n[Zakończono] Uporządkowano: {res['organized']} utworów.")
        except Exception as e:
            log_cb(f"\n[Błąd]: {str(e)}")




def run_download_task(task_id: str, request: DownloadRequest):
    log_file_path = os.path.join(LOGS_DIR, f"{task_id}.log")
    temp_spotdl_file = os.path.join(SPOTDL_CONFIG_DIR, f"temp_{task_id}.spotdl")
    
    with open(log_file_path, "w", encoding="utf-8") as f:
        cmd = []
        flags = request.flags

        # Resolve navidrome credentials
        navi_url = request.navi_url
        navi_user = request.navi_user
        navi_pass = request.navi_pass
        if request.navi_account_id or not navi_pass:
            n_acc = accounts.get_navidrome_account_by_id(request.navi_account_id or request.navi_user, SPOTDL_CONFIG_DIR)
            if n_acc:
                navi_url = n_acc.get("server", navi_url)
                navi_user = n_acc.get("username", navi_user)
                navi_pass = n_acc.get("password", navi_pass)

        if request.tool == "spotdl":
            # Wyczyść stary plik błędów przed nowym pobieraniem
            error_file = os.path.join(SPOTDL_CONFIG_DIR, "nadal_brakujace.txt")
            if os.path.exists(error_file):
                try:
                    os.remove(error_file)
                except:
                    pass

            target_source = request.url

            # Navidrome deduplication pre-check
            if flags.get("check_navidrome") and navi_url and navi_user and navi_pass:
                f.write("[Deduplikacja] Pobieranie metadanych utworów do weryfikacji w bazie Navidrome...\n")
                f.flush()
                try:
                    subprocess.run(
                        ["spotdl", "save", request.url, "--save-file", temp_spotdl_file],
                        capture_output=True,
                        text=True,
                        cwd=DOWNLOADS_DIR
                    )
                    if os.path.exists(temp_spotdl_file):
                        with open(temp_spotdl_file, "r", encoding="utf-8") as sf:
                            song_list = json.load(sf)
                        
                        skip_mode = flags.get("navidrome_skip_mode", "flac_only")
                        def d_log(msg):
                            f.write(f"{msg}\n")
                            f.flush()

                        kept, skipped = navidrome_dedup.filter_existing_songs(
                            songs=song_list,
                            navi_url=navi_url,
                            navi_user=navi_user,
                            navi_pass=navi_pass,
                            skip_mode=skip_mode,
                            log_callback=d_log
                        )

                        if not kept:
                            f.write(f"\n[Deduplikacja] Wszystkie utwory ({len(skipped)}) znajdują się już w Twojej bibliotece Navidrome! Pomijam pobieranie.\n")
                            if flags.get("generate_m3u") and request.navi_sync and navi_url and navi_user and navi_pass:
                                f.write("[Navidrome] Tworzenie natywnej playlisty ze zindeksowanych utworów...\n")
                                navi_song_ids = [s.get("_navidrome_id") for s in skipped if s.get("_navidrome_id")]
                                if navi_song_ids:
                                    derived_name = flags.get("m3u_name", "").strip()
                                    if not derived_name or derived_name == "{list}":
                                        derived_name = (song_list[0].get("list_name") if song_list else "") or "Nowa Playlista"
                                    try:
                                        navidrome_dedup.subsonic_request(
                                            base_url=navi_url,
                                            user=navi_user,
                                            password=navi_pass,
                                            endpoint="createPlaylist.view",
                                            extra_params={"name": derived_name, "songId": navi_song_ids}
                                        )
                                        f.write(f"[Navidrome] Natywna playlista '{derived_name}' została utworzona pomyślnie z {len(navi_song_ids)} utworów!\n")
                                    except Exception as pe:
                                        f.write(f"[Navidrome] Ostrzeżenie przy tworzeniu playlisty: {pe}\n")
                            f.write("\n[Zakończono z kodem 0]\n")
                            try:
                                os.remove(temp_spotdl_file)
                            except:
                                pass
                            return

                        if skipped:
                            f.write(f"[Deduplikacja] Pominięto {len(skipped)} istniejących utworów. Pobieranie {len(kept)} brakujących pozycji...\n")
                            with open(temp_spotdl_file, "w", encoding="utf-8") as sf:
                                json.dump(kept, sf)
                            target_source = temp_spotdl_file
                except Exception as e:
                    f.write(f"[Deduplikacja] Ostrzeżenie: Błąd podczas sprawdzania duplikatów: {e}. Kontynuuję standardowe pobieranie.\n")
                    f.flush()

            if target_source == temp_spotdl_file:
                cmd = ["spotdl", "download", temp_spotdl_file]
            else:
                cmd = ["spotdl", target_source]

            # Hardcoded hierarchy
            cmd.extend(["--output", "{artist}/{year} - {album}/{track-number} {title}.{output-ext}"])
            
            if flags.get("skip_downloaded"):
                cmd.extend(["--overwrite", "skip"])
                
            if flags.get("use_cookies"):
                cmd.extend(["--cookie-file", os.path.join(SPOTDL_CONFIG_DIR, "youtube_cookies.txt")])
                
            if flags.get("dont_filter"):
                cmd.append("--dont-filter-results")
                
            if flags.get("audio_sources"):
                cmd.extend(["--audio", "youtube-music", "youtube", "soundcloud"])
                
            if flags.get("threads"):
                cmd.extend(["--threads", str(flags.get("threads"))])
                
            if flags.get("save_errors"):
                cmd.extend(["--save-errors", os.path.join(SPOTDL_CONFIG_DIR, "nadal_brakujace.txt")])
                
            if flags.get("yt_dlp_args"):
                cmd.extend(["--yt-dlp-args", "--sleep-interval 2 --max-sleep-interval 4"])
                
            if flags.get("generate_m3u"):
                m3u_name = flags.get("m3u_name", "{list}")
                if not m3u_name.strip() or m3u_name == "{list}":
                    if target_source == temp_spotdl_file and 'song_list' in locals() and song_list:
                        m3u_name = song_list[0].get("list_name") or song_list[0].get("album_name") or "{list}"
                    else:
                        m3u_name = "{list}"
                cmd.extend(["--m3u", f"temp_dl_{m3u_name}.m3u"])
                
        elif request.tool == "ytdl":
            batch_file_path = None
            if flags.get("check_navidrome") and navi_url and navi_user and navi_pass:
                f.write("[Deduplikacja] Pobieranie metadanych z YouTube / SoundCloud do weryfikacji w Navidrome...\n")
                f.flush()
                try:
                    meta_entries = navidrome_dedup.extract_ytdl_metadata(request.url)
                    if meta_entries:
                        skip_mode = flags.get("navidrome_skip_mode", "flac_only")
                        def d_log(msg):
                            f.write(f"{msg}\n")
                            f.flush()

                        kept, skipped = navidrome_dedup.filter_existing_songs(
                            songs=meta_entries,
                            navi_url=navi_url,
                            navi_user=navi_user,
                            navi_pass=navi_pass,
                            skip_mode=skip_mode,
                            log_callback=d_log
                        )


                        if not kept:
                            mode_desc = "lepszej jakości (FLAC/Lossless)" if skip_mode == "flac_only" else "bibliotece"
                            f.write(f"\n[Deduplikacja] Wszystkie utwory ({len(skipped)}) znajdują się już w Twojej bibliotece Navidrome w {mode_desc}! Pomijam pobieranie.\n")
                            return

                        if skipped:
                            batch_file_path = os.path.join(SPOTDL_CONFIG_DIR, f"temp_ytdl_{task_id}.txt")
                            with open(batch_file_path, "w", encoding="utf-8") as bf:
                                for item in kept:
                                    if item.get("url"):
                                        bf.write(f"{item['url']}\n")
                            f.write(f"[Deduplikacja] Pobieranie pozostałych {len(kept)} z {len(meta_entries)} utworów...\n")
                            f.flush()
                except Exception as e:
                    f.write(f"[Deduplikacja] Ostrzeżenie przy sprawdzaniu duplikatów: {e}. Kontynuuję normalne pobieranie...\n")
                    f.flush()

            if 'batch_file_path' in locals() and batch_file_path and os.path.exists(batch_file_path):
                cmd = ["yt-dlp", "-a", batch_file_path]
            else:
                cmd = ["yt-dlp", request.url]

            if request.flags.get("audio_only"):
                cmd.extend(["-x", "--audio-format", "mp3"])
            if request.flags.get("skip_downloaded"):
                cmd.append("--no-overwrites")
        else:
            return

        f.write(f"Wykonywanie komendy: {' '.join(cmd)}\n")
        f.flush()
        
        try:
            # Setting PYTHONUNBUFFERED can help, but subprocess stdout parsing is what we do here
            process = subprocess.Popen(
                cmd,
                cwd=DOWNLOADS_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            for line in process.stdout:
                f.write(line)
                f.flush()
                
            process.wait()
            
            # Ensure downloaded files have correct PUID:PGID and permissions
            f.write(f"\n[Uprawnienia] Ustawianie praw dostepu (PUID:PGID {PUID}:{PGID})...\n")
            f.flush()
            fix_download_permissions(DOWNLOADS_DIR)
            
            # Navidrome Integration
            if request.navi_sync and navi_url and navi_user and navi_pass:
                f.write("\n[Navidrome] Rozpoczynanie integracji tworzenia natywnej playlisty...\n")
                f.flush()
                try:
                    def call_navi(endpoint, extra_params=None):
                        return navidrome_dedup.subsonic_request(
                            base_url=navi_url,
                            user=navi_user,
                            password=navi_pass,
                            endpoint=endpoint,
                            extra_params=extra_params,
                            timeout=10
                        )

                    # 1. Start Scan
                    call_navi("startScan.view")
                    f.write("[Navidrome] Skanowanie rozpoczete. Oczekuje 15 sekund...\n")

                    f.flush()
                    time.sleep(15)
                    
                    if request.flags.get("generate_m3u"):
                        f.write("[Navidrome] Szukam tymczasowej playlisty z pliku .m3u...\n")
                        f.flush()
                        
                        # 2. Get Playlists
                        res_playlists = call_navi("getPlaylists.view")
                        playlists = res_playlists.get("subsonic-response", {}).get("playlists", {}).get("playlist", [])
                        
                        target_playlist_id = None
                        actual_playlist_name = ""
                        for p in playlists:
                            name = p.get("name", "")
                            if name.startswith("temp_dl_"):
                                target_playlist_id = p.get("id")
                                actual_playlist_name = name[8:] # strip 'temp_dl_'
                                break
                                
                        if target_playlist_id:
                            f.write(f"[Navidrome] Znaleziono playliste (ID: {target_playlist_id}). Pobieranie utworow...\n")
                            # 3. Get Playlist tracks
                            res_tracks = call_navi("getPlaylist.view", {"id": target_playlist_id})
                            tracks = res_tracks.get("subsonic-response", {}).get("playlist", {}).get("entry", [])
                            song_ids = [t.get("id") for t in tracks]
                            
                            if song_ids:
                                f.write(f"[Navidrome] Znaleziono {len(song_ids)} utworow. Tworzenie natywnej playlisty '{actual_playlist_name}'...\n")
                                # 4. Create Native Playlist
                                res_create = call_navi("createPlaylist.view", {
                                    "name": actual_playlist_name,
                                    "songId": song_ids
                                })
                                new_playlist = res_create.get("subsonic-response", {}).get("playlist", {})
                                new_id = new_playlist.get("id")
                                    
                                f.write("[Navidrome] Natywna playlista zostala utworzona pomyslnie!\n")
                                
                                if request.flags.get("public_playlist") and new_id:
                                    f.write(f"[Navidrome] Ustawianie playlisty (ID: {new_id}) jako publicznej...\n")
                                    call_navi("updatePlaylist.view", {"playlistId": new_id, "public": "true"})
                                        
                                time.sleep(2)
                                
                                # Explicitly delete the temporary imported playlist from Navidrome DB
                                f.write(f"[Navidrome] Usuwanie tymczasowej playlisty z bazy (ID: {target_playlist_id})...\n")
                                call_navi("deletePlaylist.view", {"id": target_playlist_id})
                            else:
                                f.write("[Navidrome] Tymczasowa playlista jest pusta, pomijam tworzenie natywnej.\n")
                                # Still try to delete it from DB if empty
                                call_navi("deletePlaylist.view", {"id": target_playlist_id})
                        else:
                            f.write("[Navidrome] Nie znaleziono tymczasowej playlisty w bazie Navidrome.\n")
                            
                        # 5. Delete m3u files
                        f.write("[Navidrome] Usuwanie plikow tymczasowych z dysku...\n")
                        m3u_files = glob.glob(os.path.join(DOWNLOADS_DIR, "*.m3u"))
                        for m3u in m3u_files:
                            try:
                                os.remove(m3u)
                                f.write(f" -> Usunieto: {os.path.basename(m3u)}\n")
                            except:
                                pass
                            
                        # 6. Final Scan to prune old playlist references
                        f.write("[Navidrome] Koncowe skanowanie sprzatajace...\n")
                        call_navi("startScan.view")
                    else:
                        f.write("[Navidrome] Zakonczono pobieranie. (Brak playlist do konwersji)\n")
                        
                except Exception as e:
                    f.write(f"[Navidrome] Blad integracji: {str(e)}\n")
            
            # Clean up temporary files if present
            if os.path.exists(temp_spotdl_file):
                try:
                    os.remove(temp_spotdl_file)
                except:
                    pass
            if 'batch_file_path' in locals() and batch_file_path and os.path.exists(batch_file_path):
                try:
                    os.remove(batch_file_path)
                except:
                    pass

            f.write(f"\n[Zakończono z kodem {process.returncode}]\n")
            
        except Exception as e:
            if os.path.exists(temp_spotdl_file):
                try:
                    os.remove(temp_spotdl_file)
                except:
                    pass
            if 'batch_file_path' in locals() and batch_file_path and os.path.exists(batch_file_path):
                try:
                    os.remove(batch_file_path)
                except:
                    pass
            f.write(f"\n[Błąd]: {str(e)}\n")


@app.post("/api/download")
async def start_download(request: DownloadRequest, background_tasks: BackgroundTasks):
    if request.tool not in ["ytdl", "spotdl"]:
        raise HTTPException(status_code=400, detail="Invalid tool selected")
    if not request.url:
        raise HTTPException(status_code=400, detail="URL is required")
        
    task_id = str(uuid.uuid4())
    background_tasks.add_task(run_download_task, task_id, request)
    
    return {"status": "started", "task_id": task_id, "message": f"Rozpoczęto pobieranie w tle"}
 
@app.post("/api/slskd/organize")
async def start_slskd_organize(request: SlskdOrganizeRequest, background_tasks: BackgroundTasks):
    effective_url = request.slskd_url
    if not effective_url:
        acc = accounts.get_active_slskd_account(SPOTDL_CONFIG_DIR)
        if acc:
            effective_url = acc.get("server", "")

    if not effective_url:
        raise HTTPException(status_code=400, detail="Brak skonfigurowanego serwera Soulseek (slskd) w slskd_accounts.txt ani w żądaniu")
        
    task_id = str(uuid.uuid4())
    background_tasks.add_task(run_slskd_organize_task, task_id, request)
    
    return {"status": "started", "task_id": task_id, "message": "Rozpoczęto porządkowanie plików z Soulseeka"}


@app.get("/api/logs/{task_id}")
async def stream_logs(task_id: str):
    log_file_path = os.path.join(LOGS_DIR, f"{task_id}.log")
    
    async def event_generator():
        # wait a bit for file to be created
        for _ in range(20):
            if os.path.exists(log_file_path):
                break
            await asyncio.sleep(0.1)
            
        if not os.path.exists(log_file_path):
            yield f"data: Brak pliku logów\n\n"
            return
            
        with open(log_file_path, "r", encoding="utf-8") as f:
            while True:
                line = f.readline()
                if line:
                    # yield it in SSE format
                    yield f"data: {line}\n\n"
                    if "[Zakończono" in line or "[Błąd]" in line:
                        break
                else:
                    await asyncio.sleep(0.5)
                    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/errors")
async def get_errors():
    errors_file = os.path.join(SPOTDL_CONFIG_DIR, "nadal_brakujace.txt")
    if not os.path.exists(errors_file):
        return {"errors": []}
    try:
        with open(errors_file, "r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                return {"errors": []}
            
            # Try parsing as JSON (spotdl v4 uses JSON for --save-errors)
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    return {"errors": [{"name": item.get("name", "Nieznana nazwa"), "url": item.get("url", "")} for item in data]}
            except json.JSONDecodeError:
                # Fallback to text lines if not JSON
                lines = [line.strip() for line in content.split('\n') if line.strip()]
                return {"errors": [{"name": line, "url": ""} for line in lines]}
    except Exception as e:
        return {"errors": []}
        
@app.get("/api/wallpapers")
async def get_wallpapers():
    try:
        files = os.listdir(WALLPAPERS_DIR)
        images = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))]
        return {"wallpapers": images}
    except Exception as e:
        return {"wallpapers": [], "error": str(e)}

# Serve wallpapers separately so frontend can load them
app.mount("/wallpapers", StaticFiles(directory=WALLPAPERS_DIR), name="wallpapers")
# Serve static files (Frontend) - MUST be at the bottom!
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
