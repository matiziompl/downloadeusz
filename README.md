<div align="center">
  <img src="frontend/assets/logo_small.png" alt="Downloadeusz Logo" width="150" />
  <h1>Downloadeusz (v2.1)</h1>
  <p><strong>A modern, self-hosted Web UI for SpotDL, yt-dlp, and Soulseek (slskd) with deep Navidrome integration</strong></p>
</div>

---

**Downloadeusz** is a lightweight, Dockerized web application that allows you to easily download music (individual tracks, entire albums, and massive playlists from Spotify and YouTube) directly to your home server (e.g., NAS / Raspberry Pi).

The standout features of Downloadeusz are its **direct integration with Navidrome via the Subsonic API** and **Soulseek (slskd) library organization**. The app bypasses the common read-only `.m3u` playlist limitation by translating downloaded files into internal Navidrome IDs and generating native playlists. In v2.1, it automatically checks Navidrome before downloading to prevent redownloading high-quality tracks (e.g., FLACs), organizes Soulseek downloads into the same uniform folder hierarchy with simple account login, and saves credentials in your browser's local storage so container restarts never wipe your settings.

## ✨ Key Features

- 🎨 **Modern & Responsive (Bento UI)**: Designed with a sleek Glassmorphism aesthetic. It looks stunning on both mobile devices and desktops. Fully customizable (custom wallpapers, background blur intensity, gradient saturation, and accent colors).
- 🎵 **SpotDL & YT-DLP Under the Hood**: Download from YouTube, YouTube Music, Spotify, and Soundcloud while preserving full metadata (album art, ID3 tags).
- 🔄 **Soulseek (slskd) Auto-Organizer**: Automatically pulls completed downloads from your slskd daemon using your account login (`slskd`/`slskd`), parses audio tags (FLAC, MP3, M4A) via `mutagen`, and moves them into `{artist}/{year} - {album}/{track} {title}.{ext}`, then notifies Navidrome!
- 🔍 **Smart Navidrome Deduplication**: Automatically queries Navidrome before downloading Spotify/YouTube tracks. Skip songs already in your library (toggle between "FLAC / Lossless only" or "Any format").
- 📡 **Live Terminal (SSE) & Status Bar**: Monitor downloads and Soulseek organization in real-time. Instant connection test buttons and health status indicators for Navidrome and slskd.
- 💾 **Client Configuration Persistence**: Service URLs, logins, wallpapers, and all preferences are safely stored in your browser's `localStorage` (passwords are kept in session memory only for security) — restarting or updating the Docker container won't wipe your configuration.
- 🎶 **Native Navidrome Playlists**: Downloadeusz translates temporary `.m3u` files into internal Navidrome IDs and creates fully editable native playlists.
- ⚠️ **Smart Error Handling**: A slide-out panel alerts you if a specific song couldn't be found, with direct links to the original track.

## 🚀 Installation & Setup (Docker)

The recommended way to deploy Downloadeusz is using `docker-compose`.

1. Clone this repository to your server.
2. Create or edit your `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  downloadeusz:
    build: .
    container_name: downloadeusz
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ~/media/music:/downloads
      - ~/media/slskd/complete:/slskd_downloads
      - ~/spotdl:/spotdl_config
      - ~/media/wallpapers:/app/frontend/assets/wallpapers
    environment:
      - PUID=1000
      - PGID=1000
      - DOWNLOADS_DIR=/downloads
      - SLSKD_DOWNLOADS_DIR=/slskd_downloads
      - SPOTDL_CONFIG_DIR=/spotdl_config
      - WALLPAPERS_DIR=/app/frontend/assets/wallpapers
```

> **Important:** Adjust the paths on the left side of the colon to match your server's directory structure.
> - `/downloads` is the destination for downloaded music (mount your Navidrome music folder here).
> - `/slskd_downloads` is where your slskd (Soulseek) downloads complete.
> - `/spotdl_config` stores cookies like `youtube_cookies.txt` and logs.
> - `/app/frontend/assets/wallpapers` allows you to upload custom wallpapers.

3. Start the container:
```bash
docker compose up --build -d
```
4. Open your browser and navigate to: `http://<YOUR-SERVER-IP>:8000`

## ⚙️ Navidrome & Soulseek Integration

1. Open settings (the gear icon in the top right corner).
2. **Navidrome Integration**:
   - Enter your Navidrome `URL` (e.g., `http://your-server-ip:4533`), `Username`, and `Password`.
   - Click **"Testuj połączenie"** to verify connectivity.
   - (Optional) Toggle "Wymuś skanowanie i usuń .m3u".
3. **Soulseek (slskd) Integration**:
   - Enter your slskd `URL` (e.g., `http://your-server-ip:5030`), `Username` (default: `slskd`), and `Password` (default: `slskd`).
   - Click **"Testuj połączenie"** to verify login and transfers access.
   - Click **"Uporządkuj pliki"** under the link tile to import and organize downloaded FLAC/MP3 files into your library.
4. **Client Configuration Persistence**:
   - URLs, logins, themes, and wallpapers are automatically saved in your browser (`localStorage`). For security, passwords are kept only in the active session memory and are never written to disk or storage.

## 🛠️ Tech Stack
- **Backend:** Python (FastAPI, asyncio, Uvicorn, Mutagen, Subsonic & slskd API clients)
- **Frontend:** HTML5, Alpine.js, Tailwind CSS (via CDN), Phosphor Icons
- **Engines:** SpotDL, yt-dlp, Deno (for youtube-music modules)

