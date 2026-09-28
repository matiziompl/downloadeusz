<div align="center">
  <img src="frontend/assets/logo_small.png" alt="Downloadeusz Logo" width="150" />
  <h1>Downloadeusz (v1.3)</h1>
  <p><strong>Nowoczesny, self-hosted web-ui dla SpotDL i yt-dlp z głęboką integracją z Navidrome</strong></p>
</div>

---

**Downloadeusz** to lekka aplikacja w kontenerze Docker, która pozwala na łatwe pobieranie muzyki (pojedynczych utworów, całych albumów oraz potężnych playlist ze Spotify i YouTube) bezpośrednio na Twój domowy serwer (np. NAS / Raspberry Pi). 

Największą zaletą Downloadeusza jest jego **bezpośrednia integracja z Navidrome poprzez API Subsonic**. Aplikacja omija problem playlist "tylko do odczytu" (z plików .m3u) i potrafi automatycznie, w tle utworzyć dla Ciebie 100% natywną, edytowalną playlistę w Navidrome, gotową do słuchania w aplikacjach takich jak Substreamer!

## ✨ Główne funkcje

- 🎨 **Nowoczesny i responsywny interfejs (Bento UI)**: Stylizowany w klimacie Glassmorphism. Świetnie wygląda zarówno na telefonie, jak i komputerze. Pełna możliwość personalizacji (tapety, stopień rozmycia tła, nasycenie gradientów, dowolne kolory główne).
- 🎵 **SpotDL & YT-DLP pod maską**: Pobieraj z YouTube, YouTube Music, Spotify oraz Soundcloud z zachowaniem pełnych metadanych (okładki, tagi ID3).
- 📡 **Live Terminal (SSE)**: Śledź proces pobierania w czasie rzeczywistym dzięki oknu terminala strumieniowanemu wprost do przeglądarki. Nie musisz odświeżać strony!
- 🎶 **Native Navidrome Playlists**: Downloadeusz sprytnie wykorzystuje pliki `.m3u` jako mapy tymczasowe, by połączyć się z Twoim Navidrome, przetłumaczyć pobrane pliki na wewnętrzne ID i stworzyć dla Ciebie edytowalną, pełnoprawną playlistę (z opcją ustawienia jej jako Publiczną!).
- ⚠️ **Inteligentne logowanie błędów**: Lewy wysuwany panel (na kliknięcie pływającej ikony) powiadomi Cię, jeśli z jakiegoś powodu konkretna piosenka z playlisty Spotify nie mogła zostać znaleziona, podając Ci bezpośredni link.

## 🚀 Instalacja i uruchomienie (Docker)

Zalecanym sposobem uruchomienia jest użycie `docker-compose`.

1. Sklonuj to repozytorium na swój serwer.
2. Utwórz / edytuj plik `docker-compose.yml`:

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

> **Ważne:** Zmień mapowania po lewej stronie dwukropka na właściwe ścieżki na swoim serwerze.
> - `/downloads` to miejsce, gdzie ląduje pobrana muzyka (najlepiej podpiąć tu swój folder obserwowany przez Navidrome).
> - `/spotdl_config` przechowuje ciasteczka `youtube_cookies.txt` (jeśli używasz) i tymczasowe logi.
> - `/app/frontend/assets/wallpapers` służy do wgrania własnych tapet widocznych w menu konfiguracji wyglądu.

3. Uruchom kontener:
```bash
docker compose up --build -d
```
4. Otwórz w przeglądarce: `http://<IP-TWOJEGO-SERWERA>:8000`

## ⚙️ Integracja z Navidrome

Aby w pełni wykorzystać integrację playlist z Navidrome:
1. Otwórz ustawienia (ikona zębatki w prawym górnym rogu).
2. Zjedź do sekcji **Integracja z Navidrome**.
3. Wpisz `URL` (np. `http://192.168.1.102:4533`), Twój `Login` i `Hasło` z Navidrome.
4. (Opcjonalne) Zaznacz "Playlista publiczna", by automatycznie udostępnić pobraną playlistę innym użytkownikom na Twoim serwerze.
*(Dane logowania są zapisywane wyłącznie lokalnie, w Twojej przeglądarce).*

## 🛠️ Stack Technologiczny
- **Backend:** Python (FastAPI, asyncio, Uvicorn, Subsonic API wrappers)
- **Frontend:** HTML5, Alpine.js, Tailwind CSS (wariant via CDN)
- **Engines:** SpotDL, yt-dlp, Deno (dla modułów youtube-music)
