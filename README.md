<div align="center">
  <img src="frontend/assets/logo_small.png" alt="Downloadeusz Logo" width="150" />
  <h1>Downloadeusz (v1.3)</h1>
  <p><strong>A modern, self-hosted Web UI for SpotDL and yt-dlp with deep Navidrome integration</strong></p>
</div>

---

**Downloadeusz** is a lightweight, Dockerized web application that allows you to easily download music (individual tracks, entire albums, and massive playlists from Spotify and YouTube) directly to your home server (e.g., NAS / Raspberry Pi).

The standout feature of Downloadeusz is its **direct integration with Navidrome via the Subsonic API**. The app bypasses the common read-only `.m3u` playlist limitation. It dynamically translates downloaded files into internal Navidrome IDs and automatically creates a 100% native, fully editable playlist in the background, ready to be enjoyed on clients like Substreamer!

## ✨ Key Features

- 🎨 **Modern & Responsive (Bento UI)**: Designed with a sleek Glassmorphism aesthetic. It looks stunning on both mobile devices and desktops. Fully customizable (custom wallpapers, background blur intensity, gradient saturation, and accent colors).
- 🎵 **SpotDL & YT-DLP Under the Hood**: Download from YouTube, YouTube Music, Spotify, and Soundcloud while preserving full metadata (album art, ID3 tags).
- 📡 **Live Terminal (SSE)**: Monitor your download process in real-time with a terminal window streamed directly to your browser. No page refreshes required!
- 🎶 **Native Navidrome Playlists**: Downloadeusz cleverly uses `.m3u` files as temporary maps to connect with your Navidrome instance, translating files to internal IDs and generating a fully-fledged native playlist (with an option to make it Public!).
- ⚠️ **Smart Error Handling**: A slide-out panel (accessible via a floating warning icon) alerts you if a specific song from a Spotify playlist couldn't be found, providing a direct link to the original track.

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
      - ~/spotdl:/spotdl_config
      - ~/media/wallpapers:/app/frontend/assets/wallpapers
```

> **Important:** Adjust the paths on the left side of the colon to match your server's directory structure.
> - `/downloads` is the destination for downloaded music (ideally, mount your Navidrome music folder here).
> - `/spotdl_config` stores cookies like `youtube_cookies.txt` (if you use them) and temporary logs.
> - `/app/frontend/assets/wallpapers` allows you to upload custom wallpapers available in the appearance settings menu.

3. Start the container:
```bash
docker compose up --build -d
```
4. Open your browser and navigate to: `http://<YOUR-SERVER-IP>:8000`

## ⚙️ Navidrome Integration

To take full advantage of the Navidrome playlist integration:
1. Open settings (the gear icon in the top right corner).
2. Scroll down to the **Navidrome Integration** section.
3. Enter your Navidrome `URL` (e.g., `http://your-server-ip:4533`), `Username`, and `Password`.
4. (Optional) Check the "Public playlist" toggle to automatically share the downloaded playlist with other users on your server.
*(Your credentials are saved securely and locally in your browser's Local Storage).*

## 🛠️ Tech Stack
- **Backend:** Python (FastAPI, asyncio, Uvicorn, Subsonic API wrappers)
- **Frontend:** HTML5, Alpine.js, Tailwind CSS (via CDN)
- **Engines:** SpotDL, yt-dlp, Deno (for youtube-music modules)
