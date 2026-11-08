# Downloadeusz (v2.12)

**A modern, self-hosted Web UI for SpotDL, yt-dlp, and Soulseek (slskd) with deep Navidrome integration**

---

**Downloadeusz** is a lightweight, Dockerized web application that allows you to easily download music (individual tracks, entire albums, and massive playlists from Spotify and YouTube) directly to your home server (e.g., NAS / Raspberry Pi).

The standout features of Downloadeusz are its **direct integration with Navidrome via the Subsonic API**, **Soulseek (slskd) library organization**, and **beets imports**. The app bypasses the common read-only `.m3u` playlist limitation by translating downloaded files into internal Navidrome IDs and generating native playlists. It can check Navidrome before downloading to avoid existing tracks, and import new music through beets for album matching, corrected tags, artwork, and consistent folders. Service credentials are read from server-side account files; browser preferences remain in local storage.

## ✨ Key Features

- **Zatrzymywanie zadań**: „Zatrzymaj wszystko” obok odświeżania kolejki i kosz przy aktywnym zadaniu anulują pracę bez usuwania plików. Anulowanie jest trwałe; ręczne „Ponów” zachowuje ukończone utwory. Zatrzymanie całej kolejki wstrzymuje automatyczny import i webhooki do ręcznego dodania lub ponowienia zadania. Kolejka pokazuje szczegóły błędu downloadera i nie ponawia jednoznacznie niedostępnych filmów.
- **Wyszukiwanie stopniowe i bieżący postęp**: Soulseek ponawia wyszukiwanie coraz ogólniejszymi zapytaniami, usuwając dodatki typu „Official Audio”, z zachowaniem kontroli wykonawcy, tytułu, wersji i długości. Kafelek między formularzem a kolejką pokazuje rzeczywiście wykonywane zadanie i zapytanie. Gdy album pozostaje nieznany po rozpoznawaniu, nowe importy używają nazwy utworu jako niepotwierdzonego albumu zastępczego.
- **Dokumentacja w aplikacji**: przycisk książki w lewym górnym rogu otwiera README, opis ostatniej wersji i instrukcję wszystkich funkcji. Widżet błędów znajduje się pod nim. Opisy wersji są przechowywane w `docs/vN.M.md`, a instrukcja w `docs/INSTRUCTION.md`.
- 🎨 **Modern & Responsive (Bento UI)**: A Glassmorphism interface for mobile devices and desktops, with custom wallpapers, background presets, and accent colors.
- 🎵 **SpotDL & YT-DLP Under the Hood**: Download from Spotify, YouTube, and SoundCloud with smart link auto-detection and full metadata preservation.
- 🔄 **Soulseek (slskd) Organizer**: Selects completed downloads from slskd. With beets enabled it matches albums, copies and tags music, and retains originals. The legacy tag-based organizer remains available when beets is disabled.
- 🔍 **Smart Navidrome Deduplication**: Automatically queries Navidrome before downloading tracks from Spotify, YouTube, or SoundCloud. Skips songs already in your library if a higher quality version (e.g. FLAC / Lossless) or existing file is present.
- 📡 **Live Terminal (SSE) & Status Bar**: Monitor downloads and Soulseek organization in real-time. Instant connection test buttons and health status indicators for Navidrome and slskd.
- 💾 **Configuration Persistence**: Service credentials live in mounted account files; appearance and download preferences are stored in browser `localStorage`.
- 🎶 **Native Navidrome Playlists**: Downloadeusz translates temporary `.m3u` files into internal Navidrome IDs and creates fully editable native playlists.
- ⚠️ **Smart Error Handling**: A slide-out panel alerts you if a specific song couldn't be found, with direct links to the original track.

## 🚀 Installation & Setup (Docker)

The recommended way to deploy Downloadeusz is using Docker Compose (`docker compose`).

1. Clone this repository to your server.
2. Create or edit your `docker-compose.yml` file:

```yaml
services:
  downloadeusz:
    build: .
    image: downloadeusz:2.12
    container_name: downloadeusz
    restart: unless-stopped
    ports:
      - "9000:8000"
    volumes:
      # Wolumen dla pobranych plików - celuje do Twojego folderu muzyki
      - ~/media/music:/downloads
      # Wolumen na pliki konfiguracyjne spotdl (youtube_cookies.txt, błędy)
      - ~/spotdl:/spotdl_config
      # Prywatne pliki kont obok docker-compose.yml; nie są kopiowane do obrazu.
      - type: bind
        source: ./navidrome_accounts.txt
        target: /app/navidrome_accounts.txt
        read_only: true
        bind:
          create_host_path: false
      - type: bind
        source: ./slskd_accounts.txt
        target: /app/slskd_accounts.txt
        read_only: true
        bind:
          create_host_path: false
      # Wolumen na tapety
      - ~/media/wallpapers:/app/frontend/assets/wallpapers
      # Wolumen na pobrane pliki z Soulseeka (slskd) do uporządkowania
      - ~/media/slskd/complete:/slskd_downloads
    environment:
      - PUID=1000
      - PGID=1000
      - DOWNLOADS_DIR=/downloads
      - SPOTDL_CONFIG_DIR=/spotdl_config
      - ACCOUNTS_DIR=/app
      - WALLPAPERS_DIR=/app/frontend/assets/wallpapers
      - SLSKD_DOWNLOADS_DIR=/slskd_downloads
      # Beets kopiuje rozpoznaną muzykę do /downloads; oryginały zostają na miejscu.
      - BEETS_ENABLED=${BEETS_ENABLED:-true}
      - BEETS_AUTO_INTERVAL=${BEETS_AUTO_INTERVAL:-300}
      - BEETS_INBOX_DIR=/spotdl_config/inbox
      # Opcjonalnie login lub identyfikator konta Navidrome do automatycznego skanowania.
      - BEETS_NAVI_ACCOUNT_ID=${BEETS_NAVI_ACCOUNT_ID:-}
      # Normalizeusz pozostaje osobnym kontenerem, ze wspólną kolejką i biblioteką.
      - NORMALIZATION_ENABLED=${NORMALIZATION_ENABLED:-true}
      - NORMALIZATION_QUEUE_DIR=/spotdl_config/normalization
      # Ten sam sekret ustaw w webhooku DownloadFileComplete w slskd.
      - SLSKD_WEBHOOK_SECRET=${SLSKD_WEBHOOK_SECRET:-}
```

> **Important:** Adjust the paths on the left side of the colon to match your server's directory structure.
> - `/downloads` is the destination for downloaded music (mount your Navidrome music folder here).
> - `/slskd_downloads` is where your slskd (Soulseek) downloads complete.
> - `/spotdl_config` stores cookies like `youtube_cookies.txt`, the durable queue and beets state. Account files are mounted separately from the directory containing Compose.
> - `/app/frontend/assets/wallpapers` allows you to upload custom wallpapers.

Ten przykład odpowiada dołączonemu `docker-compose.yml`. Domyślnie włącza beets oraz współpracę z osobnym Normalizeuszem. Jeśli nie uruchamiasz Normalizeusza, ustaw `NORMALIZATION_ENABLED=false` w pliku `.env` obok Compose; w przeciwnym razie zadania będą czekać na potwierdzenie normalizacji. Konfigurację wspólnego katalogu opisuje sekcja „Potwierdzanie ReplayGain przez Normalizeusza”.

Przed uruchomieniem umieść oba pliki `navidrome_accounts.txt` i `slskd_accounts.txt` obok `docker-compose.yml`; plik dla nieużywanej usługi może pozostać pusty. Są montowane tylko do odczytu. Brak pliku zatrzymuje uruchomienie zamiast tworzyć katalog o jego nazwie. Format opisano w sekcji o kontach poniżej.

3. Start the container:
```bash
docker compose up --build -d
```
4. Open your browser and navigate to: `http://<YOUR-SERVER-IP>:9000`

## Beets: automatyczne porządkowanie nowych pobrań

Dołączony `docker-compose.yml` włącza beets i sprawdzanie nowych plików co 300 sekund.
Po aktualizacji przebuduj aplikację: `docker compose up --build -d`.
Sam backend bez ustawienia `BEETS_ENABLED=true` zachowuje dotychczasowe działanie.

- **Soulseek:** potrzebne jest slskd, konto w `slskd_accounts.txt` i poprawny wolumen `/slskd_downloads`. Import obejmuje albumy, których wszystkie widoczne transfery zakończyły się pomyślnie; nieznane pliki audio lub oczekujące płyty wstrzymują album. Pliki usunięte z historii transferów slskd nie są automatycznie zgadywane.
- **SpotDL i yt-dlp:** nowe pobrania audio trafiają do `/spotdl_config/inbox/<identyfikator zadania>`. Zakończone powodzeniem zadanie dostaje znacznik `.ready` i uruchamia import. Nieudane pobrania pozostają bez tego znacznika i nie są importowane automatycznie. Pobrania wideo zachowują dotychczasowy katalog.
- **Pomijanie pobrań:** przy włączonym „Pomiń już pobrane” używane są trwałe archiwa `/spotdl_config/spotdl-archive.txt` i `/spotdl_config/yt-dlp-archive.txt`, aby kolejne zadania nie pobierały ponownie tych samych nagrań. Wyłączenie tej opcji pozwala wymusić ponowne pobranie.
- **Rozpoznawanie:** beets zaczyna od tagów i informacji z nazw plików (`fromfilename`). Jeśli nie dopasuje całego albumu, sprawdza utwory osobno. Dla nadal nierozpoznanych utworów uruchamia Chromaprint/AcoustID. Brak wyniku w katalogach nie blokuje importu: plik z rzeczywistymi, niepustymi tagami wykonawcy i tytułu może zostać uporządkowany według swoich danych. Teksty typu `Unknown Artist` nie są traktowane jako użyteczne tagi.
- **Nagranie a wydanie:** drobne różnice pisowni mogą być zaakceptowane, jeśli zgadzają się wykonawca, tytuł i długość, a wynik nie jest niejednoznaczny. Wersje koncertowe, remiksy, radio edit, acoustic, instrumental i demo są rozróżniane. Zgodność nagrania oraz zgodność danych wydania są zapisywane osobno; przy niepotwierdzonym wydaniu zachowywane są istniejące dane albumu.
- **Foldery i okładki:** rozpoznana muzyka trafia do `Wykonawca albumu/Rok - Album/Numer Tytuł.ext`; albumy wielopłytowe mają numer płyty przed numerem utworu. Beets poprawia tagi kopii i pobiera dostępną okładkę albumu. Oryginalne pliki i jakość dźwięku są zachowane; nie ma konwersji formatów.
- **Pojedyncze piosenki:** brak albumu oznacza ścieżkę `Wykonawca/_Single/Tytuł.ext`. Nie jest dopisywany wymyślony album ani rok. Istniejąca osadzona okładka pozostaje w kopii. Brakująca okładka może zostać uzupełniona później także dla pojedynczego utworu, jeśli jego tagi zawierają prawidłowy identyfikator wydania MusicBrainz; nie jest wybierany przypadkowy album wyłącznie na podstawie tytułu piosenki.
- **Do sprawdzenia:** panel rozdziela pliki czekające na import, dodane według tagów oraz nagrania rozpoznane z niepotwierdzonym wydaniem. Pokazuje powód, do trzech propozycji dopasowania i przycisk ponowienia dla konkretnego pliku. Propozycje nie są automatycznie traktowane jako zatwierdzony wybór użytkownika.
- **Późniejsze uzupełnianie:** niepotwierdzone wpisy są ponownie sprawdzane raz na dobę przez działający harmonogram. Przycisk „Ponów rozpoznawanie” lub ponowienie konkretnej pozycji nie czeka na ten termin. Wzbogacanie aktualizuje istniejącą kopię w jej bieżącej lokalizacji, bez ponownego kopiowania źródła; działa również po usunięciu oryginału z katalogu pobrań. Navidrome może zostać przeskanowany także po samej aktualizacji metadanych.
- **Historia:** baza beets, `import-state.json`, `library-state.json` i `review.log` są przechowywane w `/spotdl_config/beets`. Historia każdego nowego utworu zapisuje osobno ścieżkę kopii, dodanie do biblioteki oraz potwierdzenie danych. Kolejne uruchomienia i zmiany zawartości folderu nie kopiują ponownie śledzonych plików. Starsze udane importy bez mapowania źródło–kopia pozostają pominięte, aby aktualizacja nie utworzyła duplikatów. Zachowaj ten katalog przy aktualizacji kontenera.

Przed wzbogaceniem aplikacja sprawdza SHA256 pakietów audio, bez tagów kontenera. Dopisanie ReplayGain lub okładki nie blokuje dalszego wzbogacania; podmiana audio nadal je zatrzymuje. Starsze wpisy bez bezpiecznej podstawy porównania pozostają do sprawdzenia. Zapis jest zabezpieczony kopią tymczasową, przywracaną po błędzie. Beets i nowy Normalizeusz korzystają ze wspólnej blokady `.downloadeusz-write.lock` w bibliotece.

| Zmienna | Znaczenie |
| --- | --- |
| `BEETS_ENABLED` | `true` w Compose; `false` przy uruchamianiu samego backendu. Wyłączenie przywraca dawny organizer, który **przenosi** pliki Soulseeka. |
| `BEETS_AUTO_INTERVAL` | Liczba sekund między importami; `0` wyłącza harmonogram, zachowując import ręczny i import po pobraniu. |
| `BEETS_INBOX_DIR` | Katalog nowych pobrań; domyślnie podkatalog `inbox` w `SPOTDL_CONFIG_DIR`. Musi być oddzielony od biblioteki muzyki i folderu Soulseeka. |
| `BEETS_NAVI_ACCOUNT_ID` | Opcjonalny login lub identyfikator konta z `navidrome_accounts.txt`. Umożliwia skanowanie po imporcie automatycznym; wybór konta w przeglądarce dotyczy działań ręcznych. |

Sam import nie przebudowuje istniejącej biblioteki. Wyszukiwanie brakujących piosenek wymaga wybrania źródła Soulseek w panelu.
Odcisk dźwięku jest obliczany lokalnie przez `fpcalc` i wyszukiwany w AcoustID; nie wymaga podawania własnego klucza API do odczytu. Narzędzia są instalowane w obrazie Dockera, więc po aktualizacji trzeba go przebudować, a nie tylko zrestartować kontener. Rozpoznawanie po dźwięku zużywa więcej procesora i działa wyłącznie jako kolejna próba dla nierozpoznanych utworów. Pliki bez wystarczających tagów i bez dopasowania pozostają w źródle i pojawiają się w kolejce.
Duplikaty są pomijane na podstawie bazy beets; stara biblioteka nie jest do niej automatycznie importowana, więc ten sam utwór może wystąpić ponownie. Istniejące pliki nie są celowo zastępowane lepszymi lub gorszymi kopiami.
Identyfikacja śledzonych kopii opiera się na zapisanym źródle. Inne źródło lub przeniesienie oryginału pod inną ścieżkę może być potraktowane jako nowy plik; automatyczne scalanie całej wcześniejszej biblioteki nie jest wykonywane.
Oryginały zajmują dodatkowe miejsce na dysku i nie są automatycznie usuwane.

Przy włączonym beets playlisty przechodzą przez ten sam import co pozostałe audio. Natywna playlista Navidrome powstaje z zapisanej kolejności utworów po zakończeniu skanowania; obejmuje też nagrania już posiadane i powtórzenia. Jeśli indeks nie zawiera wszystkich pozycji, aplikacja czeka do 10 minut, a następnie oznacza niepełny wynik jako wymagający sprawdzenia. Ponowienie aktualizuje zapisaną playlistę. Starszy przepływ bez beets sprząta wyłącznie znany plik `.m3u` danego zadania.

## Nowości v2.2: trwała kolejka i Soulseek

- Zadania i postęp poszczególnych utworów zapisują się w `/spotdl_config/jobs.sqlite3`. Zachowaj cały katalog konfiguracyjny przy aktualizacji; działającej bazy SQLite nie kopiuj bez plików WAL lub mechanizmu backupu SQLite.
- Jeden worker wykonuje pracę kolejno. Ponowienia błędów mają opóźnienie 30 s, 60 s i kolejne do 1 h; po ośmiu kolejnych błędach potrzebne jest ręczne ponowienie. Zakończone utwory zachowują swoje wyniki.
- Panel kolejki odtwarza postęp po odświeżeniu strony. Dane dostępne są także przez `/api/jobs` i `/api/jobs/{id}`; API nie zwraca zapisanych danych logowania.
- Wybierz **Soulseek**, wklej link Spotify lub YouTube i ustaw preferowany format. Zgodność wykonawcy, tytułu, wersji i dostępnej długości poprzedza ocenę jakości. Format jest preferencją, a nie dowodem jakości źródła. Niejednoznaczne wyniki wymagają sprawdzenia.
- Niedostępne źródło może zostać zastąpione innym użytkownikiem. YouTube jest używany jako źródło zapasowe wyłącznie po zaznaczeniu tej opcji. Przy niepewnym wyniku zlecenia transferu aplikacja nie zgaduje, czy plik został przyjęty.
- Sprawdzanie Navidrome rozróżnia nagrania koncertowe, remiksy i wersje studyjne oraz sprawdza późniejsze wyniki FLAC, gdy pierwszy wynik jest MP3.

### Powiadomienia slskd

Webhook jest opcjonalny. Nie pobierasz go ani nie generujesz w panelu: Downloadeusz już udostępnia odbiornik pod `/api/slskd/events`, a w konfiguracji slskd wskazujesz, aby wysyłało tam powiadomienia po pobraniu pliku. Bez webhooka automatyczny import nadal sprawdza ukończone pobrania domyślnie co 300 sekund, jeśli harmonogram jest włączony i kolejka nie została wstrzymana.

1. Wygeneruj własny sekret poleceniem `openssl rand -hex 32`.
2. W pliku `.env` obok Compose Downloadeusza ustaw `SLSKD_WEBHOOK_SECRET=` z wygenerowaną wartością. Jest to osobny sekret tej integracji, nie klucz API slskd. Nowa wartość zostanie odczytana po odtworzeniu kontenera Downloadeusza.
3. Poniższy fragment dodaj do **pliku `slskd.yml` usługi slskd**, nie do Compose Downloadeusza. Jeżeli sekcja `integrations` już istnieje, dodaj do niej `webhooks` bez tworzenia drugiej sekcji o tej samej nazwie. Pod `value` wstaw ten sam sekret.
4. Zastąp `ADRES_DOWNLOADEUSZA` adresem serwera, na którym działa Downloadeusz. Pełny URL to adres aplikacji z dopisanym `/api/slskd/events`; przy domyślnym mapowaniu portów jest to `http://ADRES_DOWNLOADEUSZA:9000/api/slskd/events`. Musi być osiągalny **z kontenera slskd**. `localhost` w osobnym kontenerze oznacza ten kontener, a nie Downloadeusza. Jeśli obie usługi są w tej samej sieci Docker, można użyć `http://downloadeusz:8000/api/slskd/events`.

```yaml
integrations:
  webhooks:
    downloadeusz:
      on: [DownloadFileComplete]
      call:
        url: http://ADRES_DOWNLOADEUSZA:9000/api/slskd/events
        headers:
          - name: X-Webhook-Key
            value: SEKRET_Z_SLSKD_WEBHOOK_SECRET
      retry:
        attempts: 3
```

slskd obserwuje swój plik YAML i wczytuje jego zmiany; ewentualną potrzebę restartu sygnalizuje w interfejsie. Sposób zapisu webhooków opisuje [oficjalna dokumentacja slskd](https://github.com/slskd/slskd/blob/master/docs/config.md#webhooks). Gdy nie używasz webhooka, pozostaw `SLSKD_WEBHOOK_SECRET` pusty i nie dodawaj tej integracji w slskd.

Webhook jedynie zleca sprawdzenie ukończonych transferów; ścieżka z jego treści nie daje prawa do importu pliku. Powtórzenia identyfikatorów zdarzeń są pomijane, a harmonogram pozostaje mechanizmem uzgadniania stanu. `/slskd_downloads` nadal musi wskazywać faktyczny katalog pobrań slskd.

### Potwierdzanie ReplayGain przez Normalizeusza

Przebuduj także Normalizeusza z obsługą kolejki v2.2. Utwórz `~/spotdl/normalization` z prawami zapisu dla UID/GID obu usług i zamontuj go w Normalizeuszu jako `/normalization`. Downloadeusz widzi ten sam katalog przez istniejący wolumen jako `/spotdl_config/normalization`.

W Compose `NORMALIZATION_ENABLED` jest domyślnie `true`; ustaw `false`, jeżeli używasz aplikacji bez Normalizeusza. Przy uruchamianiu samego backendu poza Compose domyślne ustawienie pozostaje `false`. Przy włączonej integracji zadanie czeka na potwierdzenie normalizacji, a dopiero później zleca skan Navidrome. Brak działającego Normalizeusza oznacza oczekiwanie w kolejce, bez fałszywego sukcesu.

Obie usługi muszą widzieć tę samą bibliotekę i mieć wspólny dostęp do `.downloadeusz-write.lock`. Zalecany `MAX_THREADS=1` ogranicza równoległą analizę audio. Nowa kolejka nie uruchamia analizy całej biblioteki przy każdym sprawdzeniu.

### Panel Normalizeusza i stare pliki

Normalizeusz pozostaje osobnym kontenerem. Wersja 2.3 udostępnia sygnał działania i operacje protokołu kolejki v2; panel Downloadeusza pokazuje dostępność, postęp, raporty i błędy bez bezpośredniego dostępu do Dockera. Obie aktualizacje są potrzebne do nowych funkcji panelu. Starsze zlecenia normalizacji konkretnych plików zachowują zgodność.

- **Sprawdź brakujące albumy** generuje podgląd całej biblioteki bez zapisu tagów.
- **Uzupełnij puste albumy** jest dostępne po udanym, aktualnym podglądzie. Normalizeusz wpisuje istniejący tytuł utworu do pustego albumu, również w starych i już znormalizowanych plikach. Zachowuje nazwy i foldery, istniejące albumy, dźwięk, ReplayGain i pozostałe tagi. Pliki bez tytułu są pomijane z opisem powodu.
- **Sprawdź ReplayGain całej biblioteki** uruchamia osobne zadanie. Istniejące tagi głośności są pomijane przez `rsgain -S`; samo uzupełnienie albumu nie uruchamia ponownej analizy audio.

Startowy skan Normalizeusza nie wykonuje masowego uzupełniania albumów. Nowe/zmienione pliki obserwowane przez Normalizeusza mogą być uzupełniane przy normalnej obsłudze zdarzeń. Album zastępczy ma przenośny znacznik pochodzenia, rozpoznawany również przez beets w Downloadeuszu; nie potwierdza konkretnego wydania. Ręczne zlecenia są dostępne także przy wyłączonej automatycznej integracji, jeżeli kolejka i Normalizeusz działają. Po operacji zmieniającej bibliotekę wybrane konto Navidrome jest skanowane dopiero po potwierdzeniu z Normalizeusza.

Historia panelu i raporty są ograniczone do najnowszych wpisów i próbek; liczniki obejmują cały przebieg. Długi ręczny skan może opóźnić inne zlecenia normalizacji w osobnym kontenerze. Downloadeusz nadal obsługuje kolejkę pobierania, a normalizacja oczekuje na swój wynik.

## ⚙️ Navidrome & Soulseek Integration (File-Based Accounts)

W wersji 2.1 konta są definiowane w plikach konfiguracyjnych na serwerze i sczytywane dynamicznie w locie (bez zapisywania haseł w przeglądarce).

Pliki należy umieścić **obok `docker-compose.yml` w katalogu działającej aplikacji**. Na Serweuszu jest to `~/docker/downloadeusz/`. Compose używa ścieżek względnych `./navidrome_accounts.txt` i `./slskd_accounts.txt`, więc katalog można przenieść bez zmiany kodu. W kontenerze pliki są dostępne w `/app`, ustawionym przez `ACCOUNTS_DIR`.

Na istniejącym Serweuszu rzeczywiste pliki kont są już w tym katalogu. Aktualizacja zachowuje je na miejscu; nie kopiuje kont z workspace ani nie zastępuje ich plikami przykładowymi.

Zmiany zapisane w istniejącym pliku są odczytywane przy odświeżeniu kont. Jeśli edytor zastąpi cały plik nowym przez zmianę nazwy, montowanie pojedynczego pliku może nadal wskazywać poprzednią wersję; wtedy trzeba odtworzyć kontener, aby podpiąć nowy plik.

1. **`navidrome_accounts.txt` obok Compose**:
   ```text
   Server: "http://192.168.1.102:4533"
   user: "mateusz"
   password: "TwojeHaslo"
   user: "natka"
   password: "DrugieHaslo"
   ```
   W ustawieniach Downloadeusza konta pojawią się w rozwijanej liście (`<select>`). Wystarczy wybrać konto, którego chcesz używać. Kliknięcie ikony odświeżenia natychmiast zaktualizuje listę kont bez restartowania kontenera.

2. **`slskd_accounts.txt` obok Compose**:
   ```text
   Server: "http://192.168.1.102:5030"
   user: "twoj_uzytkownik"
   password: "twoje_haslo"
   ```
   Downloadeusz automatycznie wykryje serwer Soulseek i użytkownika, umożliwiając testowanie połączenia oraz uruchamianie automatycznego porządkowania pobranych plików („Uporządkuj pliki”).

Poza Dockerem domyślny katalog kont to główny katalog projektu, wyznaczany względem pliku modułu, a nie bieżącego katalogu terminala. Można go zmienić zmienną środowiskową `ACCOUNTS_DIR`. `SPOTDL_CONFIG_DIR` nie określa już lokalizacji kont; ciasteczka, kolejka i stan beets pozostają w swoim dotychczasowym katalogu. Stare pliki kont w `~/spotdl/` nie są automatycznie odczytywane. Oba prywatne pliki są wykluczone przez `.gitignore` i `.dockerignore`; nie są kopiowane do obrazu ani nadpisywane przy aktualizacji aplikacji.

3. **Pobieranie samych metadanych ze Spotify & Generator FFmpeg**:
   - Wklej link ze Spotify (utwór lub album) w zakładce Spotify.
   - Kliknij **„Pobierz same metadane”**.
   - Podgląd wyświetli metadane, okładkę i wygenerowaną komendę FFmpeg. Podmień wartość `FILE=...` na ścieżkę właściwego pliku przed samodzielnym uruchomieniem w terminalu.
   - Przycisk kopiowania skopiuje komendę do schowka i pokaże ją w konsoli aplikacji; nie uruchamia jej na serwerze.

## 🛠️ Tech Stack
- **Backend:** Python (FastAPI, asyncio, Uvicorn, Mutagen, Subsonic & slskd API clients)
- **Frontend:** HTML5, Alpine.js, Tailwind CSS (via CDN), Phosphor Icons
- **Engines:** SpotDL, yt-dlp, Deno (for youtube-music modules)

