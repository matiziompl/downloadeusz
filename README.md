# Downloadeusz (v2.27)

**Interfejs przeglądarkowy dla SpotDL, yt-dlp i Soulseeka (slskd), uruchamiany na własnym serwerze i zintegrowany z Navidrome**

---

**Downloadeusz** to aplikacja przeglądarkowa uruchamiana w Dockerze. Pozwala pobierać pojedyncze utwory, całe albumy i duże playlisty na własny serwer, np. NAS lub Raspberry Pi. Odczytuje listy utworów ze Spotify, a plików audio szuka w obsługiwanych źródłach; obsługuje też bezpośrednie linki YouTube i SoundCloud.

Główne funkcje Downloadeusza to **integracja z Navidrome przez API Subsonic**, **porządkowanie pobrań Soulseeka (slskd)** i **import przez beets**. Aplikacja tworzy edytowalne playlisty Navidrome na podstawie identyfikatorów utworów w bibliotece, zamiast pozostawać przy ograniczeniach playlist `.m3u` tylko do odczytu. Przed pobraniem może sprawdzić, czy nagranie jest już w Navidrome. Beets odpowiada za dopasowanie albumu, poprawianie tagów, okładki i spójne foldery. Dane logowania są odczytywane z plików na serwerze, a preferencje przeglądarki pozostają w pamięci lokalnej.

## ✨ Najważniejsze funkcje

- **Zatrzymywanie zadań**: „Zatrzymaj wszystko” obok odświeżania kolejki i kosz przy aktywnym zadaniu anulują pracę bez usuwania plików. Anulowanie jest trwałe; ręczne „Ponów” zachowuje ukończone utwory. Zatrzymanie całej kolejki wstrzymuje automatyczny import i webhooki do ręcznego dodania lub ponowienia zadania. Kolejka pokazuje szczegóły błędu downloadera i nie ponawia jednoznacznie niedostępnych filmów.
- **Wyszukiwanie stopniowe i bieżący postęp**: Soulseek ponawia wyszukiwanie coraz ogólniejszymi zapytaniami, usuwając dodatki typu „Official Audio”, z zachowaniem kontroli wykonawcy, tytułu, wersji i długości. Kafelek „Aktualne zadanie” w oknie kolejki pokazuje rzeczywiście wykonywane zadanie i zapytanie. Gdy album pozostaje nieznany po rozpoznawaniu, nowe importy używają nazwy utworu jako niepotwierdzonego albumu zastępczego.
- **Kolejka w osobnym oknie**: widżet bezpośrednio pod ustawieniami, w prawym górnym rogu, otwiera aktualne zadanie, historię, sterowanie kolejką, szczegóły utworów i terminal na żywo. Zamknięcie okna nie zatrzymuje pracy ani odświeżania stanu.
- **Przeglądarka muzyki**: widżet **Muzyka** po prawej stronie, pod kolejką, otwiera bibliotekę z okładkami, wyszukiwaniem tytułów, wykonawców i albumów oraz wyborem 2, 3, 4 lub 6 utworów w rzędzie. Obejmuje pliki audio w `/downloads`, także wcześniejszą muzykę poza historią importów Downloadeusza.
- **Kosz muzyki jako filtr**: **Przenieś do kosza** ukrywa utwór na zwykłych listach Downloadeusza, pozostawiając plik audio w dotychczasowym miejscu i bez zmian. Zakładka **Muzyka → Kosz** pozwala go przywrócić na listy albo wybrać **Usuń z dysku**. Dopiero osobne potwierdzenie tej drugiej operacji trwale usuwa plik biblioteczny.
- **Ręczne uzupełnianie albumów**: zakładka **Nieuzupełnione** pokazuje brakujące i niepotwierdzone albumy, również automatyczne albumy zastępcze. Wskaż link MusicBrainz, wybierz konkretne wydanie i utwór, porównaj tagi i ścieżkę, a następnie zleć zmianę przez kolejkę. Osobna akcja **Nazwij album tytułem piosenki** zapisuje świadomie zaakceptowany album zastępczy.
- **Dokumentacja i widżety**: przycisk książki w lewym górnym rogu otwiera README, opis ostatniej wersji i instrukcję wszystkich funkcji. Bezpośrednio pod nim jest widżet Normalizeusza otwierający osobne okno, a niżej widżet błędów. Opisy wersji są przechowywane w `docs/vN.M.md`, a instrukcja w `docs/INSTRUCTION.md`.
- 🎨 **Interfejs na telefon i komputer**: układ kafelkowy z półprzezroczystymi panelami, własnymi tapetami, gotowymi tłami i wyborem koloru akcentu.
- **Dolny dock widżetów**: w ustawieniach, w sekcji **Wygląd**, opcja **Widżety w dolnym docku** przenosi dokumentację, Normalizeusza, kolejkę, muzykę, ustawienia i warunkowy przycisk błędów na wspólny pasek przy dolnej krawędzi ekranu. Muzyka znajduje się na jego lewym końcu, a ustawienia na prawym. Dock ma szerokość głównych kafelków i takie same zaokrąglenia. Ustawienie jest domyślnie wyłączone, działa na telefonie i komputerze oraz zapisuje się w tej przeglądarce. Opisy położenia przycisków w rogach dotyczą domyślnego układu.
- **Ustawienia w oknie pop-up**: koło zębate w prawym górnym rogu otwiera wyśrodkowane okno z przewijaną treścią, tak jak dokumentacja i Normalizeusz. Zamykasz je przyciskiem X, klawiszem Escape lub kliknięciem tła; preferencje zapisują się jak dotychczas.
- **Narzędzia pobierania**: osobny kafelek w ustawieniach pozwala niezależnie ukrywać podgląd komendy pobierania i przycisk pobierania samych metadanych Spotify. Oba przełączniki są domyślnie włączone i zapamiętywane w tej przeglądarce. Te opcje oraz dock korzystają z takich samych wycentrowanych przełączników suwakowych jak widoczność tytułu.
- 🎵 **Pobieranie przez SpotDL i yt-dlp**: obsługa list ze Spotify oraz źródeł YouTube i SoundCloud, automatyczne rozpoznawanie linków i obsługa metadanych.
- **Długie nazwy YouTube i SoundCloud**: yt-dlp skraca tytuł w nazwie pliku według liczby bajtów i zachowuje identyfikator źródła, z zapasem na pliki tymczasowe. Pełny tytuł w tagach pozostaje bez zmian. Mechanizm obejmuje również zapasowy YouTube w trybie Soulseek i kopiowane polecenie z podglądu komendy.
- 🔄 **Porządkowanie Soulseeka (slskd)**: w widżecie **Muzyka**, w zakładce **Porządkowanie**, znajdują się import, harmonogram, rozpoznawanie i zapisane propozycje beets. Przy włączonym beets aplikacja dopasowuje albumy, kopiuje muzykę, zapisuje tagi i zachowuje oryginały. Po wyłączeniu beets dostępny jest starszy mechanizm oparty na tagach.
- 🔍 **Sprawdzanie duplikatów w Navidrome**: weryfikacja biblioteki przed pobraniem pozwala pominąć posiadane utwory, np. gdy istnieje już kopia bezstratna lub inny akceptowany plik.
- 📡 **Terminal na żywo i stan usług**: logi pobierania i porządkowania przez SSE, przyciski testowania połączeń oraz wskaźniki stanu Navidrome i slskd.
- 💾 **Zapamiętywanie ustawień**: dane logowania pozostają w montowanych plikach kont, a wygląd i preferencje pobierania są przechowywane w `localStorage` przeglądarki.
- 🎶 **Natywne playlisty Navidrome**: powiązanie utworów z identyfikatorami biblioteki umożliwia tworzenie playlist edytowalnych w Navidrome.
- ⚠️ **Obsługa błędów**: wysuwany panel pokazuje nieodnalezione utwory i odsyłacze do źródeł, jeśli są dostępne.

## 🚀 Instalacja i konfiguracja w Dockerze

Zalecany sposób uruchamiania Downloadeusza to Docker Compose (`docker compose`).

1. Sklonuj repozytorium na swój serwer.
2. Utwórz lub zaktualizuj plik `docker-compose.yml`:

```yaml
services:
  downloadeusz:
    build: .
    image: downloadeusz:2.27
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

> **Ważne:** dopasuj ścieżki po stronie hosta do folderów na swoim serwerze. W skróconym zapisie montowania jest to część po lewej stronie dwukropka; w pełnym zapisie — pole `source`.
>
> - `/downloads` to docelowa biblioteka pobieranej muzyki; zamontuj tu folder używany przez Navidrome.
> - `/slskd_downloads` wskazuje ukończone pobrania slskd (Soulseek).
> - `/spotdl_config` przechowuje ciasteczka, np. `youtube_cookies.txt`, trwałą kolejkę i stan beets. Pliki kont są montowane osobno z katalogu zawierającego Compose.
> - `/app/frontend/assets/wallpapers` to miejsce na własne tapety.

Ten przykład odpowiada dołączonemu `docker-compose.yml`. Domyślnie włącza beets oraz współpracę z osobnym Normalizeuszem. Jeśli nie uruchamiasz Normalizeusza, wyłącz **ReplayGain po imporcie** w panelu Normalizeusza. Przed pierwszym uruchomieniem możesz też ustawić `NORMALIZATION_ENABLED=false` w pliku `.env` obok Compose; inaczej zadania będą czekać na potwierdzenie normalizacji. Zapisany przełącznik ma pierwszeństwo przed tą zmienną. Konfigurację wspólnego katalogu opisuje sekcja „Potwierdzanie ReplayGain przez Normalizeusza”.

Przed uruchomieniem umieść oba pliki `navidrome_accounts.txt` i `slskd_accounts.txt` obok `docker-compose.yml`; plik dla nieużywanej usługi może pozostać pusty. Są montowane tylko do odczytu. Brak pliku zatrzymuje uruchomienie zamiast tworzyć katalog o jego nazwie. Format opisano w sekcji o kontach poniżej.

3. Uruchom kontener:

```bash
docker compose up --build -d
```
4. Otwórz w przeglądarce `http://<ADRES-SERWERA>:9000`.

## Publikacja Git

Docelową gałęzią publikacji Downloadeusza jest `main` w repozytorium `origin`. Praca może pozostać na gałęzi roboczej; po uzgodnieniu historii z aktualnym `origin/main` publikacja używa `git push origin HEAD:main`, bez force push. Aktualizacja aplikacji jest niezależna od publikacji Git. Opcjonalny push wykonuj po ręcznym sprawdzeniu aplikacji.

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
- **Do sprawdzenia:** panel rozdziela pliki czekające na import, dodane według tagów oraz nagrania rozpoznane z niepotwierdzonym wydaniem. Pokazuje powód, do trzech propozycji dopasowania i przycisk ponowienia dla konkretnego pliku. Zaznacz widoczne kółko przy nazwie właściwej propozycji i kliknij „Zastosuj wybraną propozycję”. Osobno rozwiń „Szczegóły propozycji”, aby zobaczyć dostępne dane (długość, wydanie, wytwórnię, numer katalogowy) i link do MusicBrainz. Wybór zapisuje metadane dokładnie tej propozycji, bez nowego wyszukiwania dopasowania. Propozycja nagrania nie stanowi potwierdzenia wydania; propozycja utworu z wydania stosuje jego dane tylko do wskazanego pliku. Starsze skrócone propozycje mają widoczny komunikat i wymagają „Sprawdź ponownie”, zanim będzie można je wybrać.
- **Późniejsze uzupełnianie:** niepotwierdzone wpisy są ponownie sprawdzane raz na dobę przez działający harmonogram. Przycisk „Ponów rozpoznawanie” lub ponowienie konkretnej pozycji nie czeka na ten termin. Wzbogacanie aktualizuje istniejącą kopię w jej bieżącej lokalizacji, bez ponownego kopiowania źródła; działa również po usunięciu oryginału z katalogu pobrań. Navidrome może zostać przeskanowany także po samej aktualizacji metadanych.
- **Historia:** baza beets, `import-state.json`, `library-state.json` i `review.log` są przechowywane w `/spotdl_config/beets`. Historia każdego nowego utworu zapisuje osobno ścieżkę kopii, dodanie do biblioteki oraz potwierdzenie danych. Kolejne uruchomienia i zmiany zawartości folderu nie kopiują ponownie śledzonych plików. Starsze udane importy bez mapowania źródło–kopia pozostają pominięte, aby aktualizacja nie utworzyła duplikatów. Zachowaj ten katalog przy aktualizacji kontenera.

Przed wzbogaceniem aplikacja sprawdza SHA256 pakietów audio, bez tagów kontenera. Dopisanie ReplayGain lub okładki nie blokuje dalszego wzbogacania; podmiana audio nadal je zatrzymuje. Starsze wpisy bez bezpiecznej podstawy porównania pozostają do sprawdzenia. Zapis jest zabezpieczony kopią tymczasową, przywracaną po błędzie. Beets i nowy Normalizeusz korzystają ze wspólnej blokady `.downloadeusz-write.lock` w bibliotece.

Ręczny wybór korzysta z tych samych zabezpieczeń i trwałej kolejki. Jawnie potwierdza propozycję także wtedy, gdy automatyka uznała ją za niepewną — porównaj wykonawcę, wersję i długość przed zastosowaniem. Istniejąca kopia pozostaje w bieżącej lokalizacji, a oryginał nie jest zmieniany. Plik jeszcze niezaimportowany czeka na potwierdzenie zakończenia pobierania. Nieaktualna propozycja lub błąd zapisu nie uruchamia wyboru innego dopasowania.

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

## Biblioteka muzyki i ręczne metadane

**Przenieś do kosza** jest oznaczeniem w bazie aplikacji. Nie kopiuje, nie przenosi ani nie usuwa pliku audio i nie zmienia jego tagów. Utwór znika z **Biblioteki** i **Nieuzupełnionych**, a pojawia się w zakładce **Kosz**. **Przywróć** usuwa tylko to oznaczenie. Stan kosza zapisuje się w `/spotdl_config/music-library.sqlite3`, przetrwa restart i ponowne skanowanie biblioteki. Kosz ma własne wyszukiwanie i strony. Ponieważ plik pozostaje w bibliotece na dysku, Navidrome nadal może go odtwarzać.

Dopiero **Usuń z dysku** w zakładce **Kosz**, po osobnym potwierdzeniu tytułu, wykonawcy i ścieżki, usuwa plik biblioteczny. API odrzuca tę operację dla utworu poza koszem. Przed usunięciem sprawdzane są wersja pliku, niedokończone zadania, blokady importera i Normalizeusza oraz dowiązania. Oddzielne źródłowe pobranie w Soulseeku lub inboxie pozostaje bez zmian. Po trwałym usunięciu importer zachowuje znacznik zapobiegający automatycznemu przywróceniu kopii; Navidrome uwzględni brak pliku po kolejnym skanowaniu.

API udostępnia `GET /api/music/tracks?trashed=true`, oznaczenie `POST /api/music/tracks/{id}/trash` i przywrócenie `POST /api/music/tracks/{id}/restore` z `{"version": "wersja z listy utworów"}`. Trwałe usunięcie to `POST /api/music/tracks/{id}/delete` z `{"confirmed": true, "version": "wersja z listy utworów"}`.

Widżet **Muzyka**, pod kolejką po prawej stronie lub na lewym końcu docka, zawiera zakładki **Biblioteka**, **Nieuzupełnione**, **Kosz** i **Porządkowanie**. Ostatnia skupia panel beets oraz przycisk porządkowania pobrań Soulseeka. Główny pasek statusu pozostaje pod formularzem.

Biblioteka indeksuje tagi plików w `DOWNLOADS_DIR`; nie wymaga konta Navidrome ani ponownego importu całej muzyki do beets. Otwarcie okna lub jawne odświeżenie sprawdza pliki, a kolejne wyszukiwania i strony korzystają z indeksu. Miniatury pochodzą z lokalnej okładki lub tagów pliku. Wyszukiwanie obejmuje tytuł, wykonawcę i album, niezależnie od wielkości liter i znaków diakrytycznych. Widok ma strony po 48 pozycji i zapamiętywany wybór liczby kolumn.

Ręczne uzupełnianie przyjmuje linki HTTPS do `musicbrainz.org` typu `release`, `recording` i `release-group`. Wymaga jawnego wyboru wydania i pozycji utworu oraz zatwierdzenia podglądu tagów i ścieżki. Wykorzystuje zainstalowany adapter MusicBrainz beets, bez dodatkowych zależności ani własnego klucza API. **Nazwij album tytułem piosenki** działa bez wyszukiwania MusicBrainz i zapisuje zaakceptowany album zastępczy, niepotwierdzający wydania.

Wersja 2.23 naprawia zapis obu operacji dla plików FLAC zatrzymujących się wcześniej z błędem `pop expected at most 1 argument, got 2`. Po aktualizacji zadania oczekujące ponowią próbę zgodnie z harmonogramem; dla zakończonych błędem użyj **Ponów**. Jeśli podgląd wygasł lub plik zmienił się po innym zleceniu, przygotuj nowe dopasowanie w edytorze.

Przy polu linku dostępne są **Szukaj w MusicBrainz** — wyszukiwanie nagrań po tytule i wykonawcy — oraz **Szukaj tylko tytułu**. Otwierają wyniki w nowej karcie z gotowym zapytaniem, zachowując wybrany utwór w Downloadeuszu. Album zastępczy nie zawęża wyszukiwania. Po znalezieniu właściwego nagrania lub wydania wklej jego adres do pola **Link MusicBrainz** i wybierz **Pokaż dopasowanie**.

Zmiana jest osobnym zadaniem w istniejącej kolejce. Kopia biblioteczna otrzymuje tagi i ścieżkę według końcowych reguł beets; źródło pobrania pozostaje bez zmian. Zapis kontroluje tożsamość audio, wersję pliku i kolizje nazw oraz używa wspólnej blokady biblioteki z Normalizeuszem. Niedokończone zadania wskazujące plik opóźniają edycję. Po sukcesie aktualizowane są powiązania ścieżek, a finalizacja korzysta z dotychczasowych ustawień Normalizeusza i synchronizacji wybranego Navidrome. Dotychczasowe zapisane propozycje beets pozostają osobną akcją poprawiającą tagi bez zmiany lokalizacji.

Indeks `/spotdl_config/music-library.sqlite3` i podglądy oraz kopie odzyskiwania w `/spotdl_config/music-edits` korzystają z obecnego wolumenu konfiguracji. Nie usuwaj stanu edycji ani kopii odzyskiwania w trakcie zadania. API udostępnia stronicowaną listę `GET /api/music/tracks`, odświeżanie `POST /api/music/refresh`, lokalne okładki i operacje podglądu/zlecenia edycji dla identyfikatora utworu. Przeglądanie indeksu nie zapisuje tagów muzyki.

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

W Compose `NORMALIZATION_ENABLED` jest domyślnie `true`; przy uruchamianiu samego backendu poza Compose domyślne ustawienie pozostaje `false`. Wartość ta obowiązuje do pierwszego zapisu przełącznika **ReplayGain po imporcie** w panelu Normalizeusza. Przełącznik jest wspólny dla wszystkich użytkowników i zapisuje się w `/spotdl_config/jobs.sqlite3`, więc przetrwa restart i odtworzenie kontenera ze wspólnym wolumenem. Zapisana wartość ma pierwszeństwo przed `.env`. Ustawienie można zmieniać także przy niedostępnym Normalizeuszu. API udostępnia odczyt stanu w `GET /api/normalizer` i zapis przez `PUT /api/normalizer/settings` z JSON `{"enabled": false}` albo `{"enabled": true}`.

Przed zatrzymaniem Normalizeusza wyłącz **ReplayGain po imporcie** i poczekaj na potwierdzenie zapisu. Zmiana nie wymaga restartu: kolejne sprawdzenie finalizacji przez worker korzysta z bieżącego ustawienia. Pobieranie, beets, skany i playlisty Navidrome mogą wtedy działać bez potwierdzenia ReplayGain. Istniejące tagi głośności pozostają w plikach. Przełącznik nie zatrzymuje kontenera Normalizeusza ani jego niezależnego obserwowania biblioteki. Przy włączonej integracji oczekiwanie na potwierdzenie nie ma automatycznego limitu czasu; worker może w tym czasie obsługiwać inne gotowe zadania.

Wyłączenie automatycznej integracji pozwala także dokończyć oczekujące zwykłe zadania pobierania i importu przy ich kolejnym podjęciu. Nie kończy jednak ręcznych zleceń panelu Normalizeusza ani nie usuwa już wysłanych plików kolejki. Po ponownym uruchomieniu Normalizeusz może je obsłużyć; anulowanie w Downloadeuszu nie wycofuje zlecenia z osobnej usługi. Ręczne sprawdzanie ReplayGain i naprawa albumów wymagają działającego Normalizeusza.

Jeśli integrację włączono podczas trwającego skanu Navidrome, po potwierdzeniu normalizacji aplikacja zleca nowy skan, aby uwzględnił zmienione tagi.

Obie usługi muszą widzieć tę samą bibliotekę i mieć wspólny dostęp do `.downloadeusz-write.lock`. Zalecany `MAX_THREADS=1` ogranicza równoległą analizę audio. Nowa kolejka nie uruchamia analizy całej biblioteki przy każdym sprawdzeniu.

### Panel Normalizeusza i stare pliki

Cały panel Normalizeusza jest dostępny w oknie pop-up po kliknięciu jego widżetu w lewym górnym rogu, bezpośrednio pod przyciskiem książki. Zawiera przełącznik **ReplayGain po imporcie**, stan usługi, akcje ręczne, postęp i historię z raportami. Kontrolki przeniesiono z formularza strony głównej. Okno zamkniesz przyciskiem X, klawiszem Escape lub kliknięciem tła poza nim; zamknięcie nie przerywa zadań. Stan jest odświeżany przy otwieraniu okna.

Normalizeusz pozostaje osobnym kontenerem. Wersja 2.3 udostępnia sygnał działania i operacje protokołu kolejki v2; panel Downloadeusza pokazuje dostępność, postęp, raporty i błędy bez bezpośredniego dostępu do Dockera. Obie aktualizacje są potrzebne do nowych funkcji panelu. Starsze zlecenia normalizacji konkretnych plików zachowują zgodność.

- **Sprawdź brakujące albumy** generuje podgląd całej biblioteki bez zapisu tagów.
- **Uzupełnij puste albumy** jest dostępne po udanym, aktualnym podglądzie. Normalizeusz wpisuje istniejący tytuł utworu do pustego albumu, również w starych i już znormalizowanych plikach. Zachowuje nazwy i foldery, istniejące albumy, dźwięk, ReplayGain i pozostałe tagi. Pliki bez tytułu są pomijane z opisem powodu.
- **Sprawdź ReplayGain całej biblioteki** uruchamia osobne zadanie. Istniejące tagi głośności są pomijane przez `rsgain -S`; samo uzupełnienie albumu nie uruchamia ponownej analizy audio.

Startowy skan Normalizeusza nie wykonuje masowego uzupełniania albumów. Nowe/zmienione pliki obserwowane przez Normalizeusza mogą być uzupełniane przy normalnej obsłudze zdarzeń. Album zastępczy ma przenośny znacznik pochodzenia, rozpoznawany również przez beets w Downloadeuszu; nie potwierdza konkretnego wydania. Ręczne zlecenia są dostępne także przy wyłączonej automatycznej integracji, jeżeli kolejka i Normalizeusz działają. Po operacji zmieniającej bibliotekę wybrane konto Navidrome jest skanowane dopiero po potwierdzeniu z Normalizeusza.

Historia panelu i raporty są ograniczone do najnowszych wpisów i próbek; liczniki obejmują cały przebieg. Długi ręczny skan może opóźnić inne zlecenia normalizacji w osobnym kontenerze. Downloadeusz nadal obsługuje kolejkę pobierania, a normalizacja oczekuje na swój wynik.

## Graf zależności kodu

`graphify-out/graph.html` zawiera interaktywny graf, `graphify-out/graph.json` dane grafu, a `graphify-out/GRAPH_REPORT.md` raport. Widok HTML pobiera bibliotekę wizualizacji z `unpkg.com` i wymaga dostępu do tej domeny. Graf powstaje strukturalnie z kodu Python i JavaScript aplikacji oraz testów; nie obejmuje semantycznej analizy dokumentacji, HTML/CSS, konfiguracji Compose ani danych i sekretów. Jest pomocą do analizy źródeł, a nie dowodem kompletności zależności między usługami. Istniejący graf przedstawia wersję 2.16 i nie obejmuje przełącznika z 2.17; po zmianach kodu wymaga odświeżenia. Nie jest potrzebny do uruchomienia aplikacji i nie jest kopiowany do obrazu Dockera.

## ⚙️ Integracja z Navidrome i Soulseekiem — konta w plikach

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

3. **Pobieranie samych metadanych ze Spotify i generator poleceń FFmpeg**:
   - Wklej link ze Spotify (utwór lub album) w zakładce Spotify.
   - Kliknij **„Pobierz same metadane”**. Widoczność tego przycisku zmienisz w ustawieniach, w kafelku **Narzędzia pobierania**.
   - Podgląd wyświetli metadane, okładkę i wygenerowaną komendę FFmpeg. Podmień wartość `FILE=...` na ścieżkę właściwego pliku przed samodzielnym uruchomieniem w terminalu.
   - Przycisk kopiowania skopiuje komendę do schowka i pokaże ją w terminalu w oknie kolejki; nie uruchamia jej na serwerze.

## 🛠️ Wykorzystane technologie

- **Część serwerowa:** Python, FastAPI, asyncio, Uvicorn, Mutagen oraz obsługa API Subsonic i slskd.
- **Interfejs przeglądarkowy:** HTML5, Alpine.js, Tailwind CSS przez CDN oraz ikony Phosphor.
- **Narzędzia pobierania:** SpotDL, yt-dlp i Deno do obsługi JavaScript przy odczytywaniu źródeł YouTube.

