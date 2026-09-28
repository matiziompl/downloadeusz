import os
import subprocess
import asyncio
import uuid
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI()

# Configuration
WALLPAPERS_DIR = os.getenv("WALLPAPERS_DIR", "../frontend/assets/wallpapers")
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "/downloads")
SPOTDL_CONFIG_DIR = os.getenv("SPOTDL_CONFIG_DIR", "/spotdl_config")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")

# Ensure local directories exist (mostly for logs)
os.makedirs(LOGS_DIR, exist_ok=True)
# Only create if running locally without Docker (in Docker they are mounted)
if not os.path.exists(DOWNLOADS_DIR):
    try: os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    except: pass

class DownloadRequest(BaseModel):
    tool: str # 'ytdl' or 'spotdl'
    url: str
    flags: dict = {}
    navi_url: str = ""
    navi_user: str = ""
    navi_pass: str = ""
    navi_sync: bool = False

def run_download_task(task_id: str, request: DownloadRequest):
    log_file_path = os.path.join(LOGS_DIR, f"{task_id}.log")
    
    cmd = []
    if request.tool == "spotdl":
        # Wyczyść stary plik błędów przed nowym pobieraniem
        error_file = os.path.join(SPOTDL_CONFIG_DIR, "nadal_brakujace.txt")
        if os.path.exists(error_file):
            try:
                os.remove(error_file)
            except:
                pass
                
        cmd = ["spotdl", request.url]
        # Hardcoded hierarchy
        cmd.extend(["--output", "{artist}/{year} - {album}/{track-number} {title}.{output-ext}"])
        
        flags = request.flags
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
            if not m3u_name.strip():
                m3u_name = "{list}"
            cmd.extend(["--m3u", f"temp_dl_{m3u_name}.m3u"])
            
    elif request.tool == "ytdl":
        cmd = ["yt-dlp", request.url]
        if request.flags.get("audio_only"):
            cmd.extend(["-x", "--audio-format", "mp3"])
        if request.flags.get("skip_downloaded"):
            cmd.append("--no-overwrites")
    else:
        return

    with open(log_file_path, "w", encoding="utf-8") as f:
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
            
            # Navidrome Integration
            if request.navi_sync and request.navi_url and request.navi_user and request.navi_pass:
                f.write("\n[Navidrome] Rozpoczynanie integracji tworzenia natywnej playlisty...\n")
                f.flush()
                try:
                    import urllib.request
                    import urllib.parse
                    import time
                    import hashlib
                    import secrets
                    import glob
                    import json
                    
                    def call_navi(endpoint, extra_params=None):
                        salt = secrets.token_hex(6)
                        token = hashlib.md5((request.navi_pass + salt).encode('utf-8')).hexdigest()
                        params = {
                            "u": request.navi_user,
                            "t": token,
                            "s": salt,
                            "v": "1.16.1",
                            "c": "downloadeusz",
                            "f": "json"
                        }
                        if extra_params:
                            params.update(extra_params)
                        query = urllib.parse.urlencode(params)
                        api_url = f"{request.navi_url.rstrip('/')}/rest/{endpoint}?{query}"
                        req = urllib.request.Request(api_url)
                        with urllib.request.urlopen(req, timeout=10) as response:
                            return json.loads(response.read().decode('utf-8'))

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
                                salt = secrets.token_hex(6)
                                token = hashlib.md5((request.navi_pass + salt).encode('utf-8')).hexdigest()
                                base_params = {
                                    "u": request.navi_user,
                                    "t": token,
                                    "s": salt,
                                    "v": "1.16.1",
                                    "c": "downloadeusz",
                                    "f": "json",
                                    "name": actual_playlist_name
                                }
                                query = urllib.parse.urlencode(base_params)
                                for sid in song_ids:
                                    query += f"&songId={sid}"
                                    
                                api_url = f"{request.navi_url.rstrip('/')}/rest/createPlaylist.view?{query}"
                                req = urllib.request.Request(api_url)
                                with urllib.request.urlopen(req, timeout=10) as response:
                                    res_create = json.loads(response.read().decode('utf-8'))
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
            
            f.write(f"\n[Zakończono z kodem {process.returncode}]\n")
            
        except Exception as e:
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
            import json
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
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
