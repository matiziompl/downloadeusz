# Zasady pracy nad Downloadeuszem

## Zakres i weryfikacja

- Pracuj w `/workspace/downloadeusz`, zachowując istniejącą architekturę i wcześniejsze zmiany użytkownika. Nie modyfikuj HAPI ani innych projektów w ramach prac nad Downloadeuszem.
- Wprowadzaj wyłącznie zmiany potrzebne do zlecenia. Nie zapisuj planów ani specyfikacji prac na dysku; opis ukończonej wersji w `docs/` nie jest planem.
- Testuj API w pamięci przez pytest i FastAPI TestClient/httpx. Nie uruchamiaj przeglądarek ani Playwrighta. Frontend jest statycznym HTML/CSS/JS bez osobnego procesu budowania; sprawdzaj składnię JS i działanie zmienionej logiki.
- Nie deklaruj weryfikacji, której nie wykonano. Nie wdrażaj zmian, nie restartuj usług i nie ingeruj w dane, sekrety ani konfigurację produkcyjną bez wyraźnego zlecenia.

## Aktualność README

- Przy każdej trwałej zmianie sprawdzaj, czy README nadal opisuje rzeczywiste działanie, konfigurację i sposób uruchamiania aplikacji. Aktualizuj treść i przykłady, których dotyczy zmiana; samo podniesienie numeru wersji w nagłówku nie wystarcza.
- Przykład Docker Compose w README musi odpowiadać aktualnemu `docker-compose.yml`: tag obrazu, porty i adres otwierania aplikacji, wolumeny, zmienne środowiskowe z wartościami domyślnymi oraz etykiety. Po zmianie konfiguracji porównaj przykład z plikiem źródłowym przed zakończeniem zadania.
- Przy zmianach Dockerfile, zależności lub integracji aktualizuj również wymagania instalacyjne i opis współpracy z osobnymi usługami. Nie zostawiaj przestarzałych poleceń ani przykładów konfiguracji.

## Wersjonowanie

Logika przejęta z sekcji 7–8 `/workspace/hapi/AGENTS.md`, dostosowana do istniejącego Downloadeusza.

- Źródłem prawdy jest `APP_VERSION` w `backend/main.py`, bez prefiksu `v`. Zachowaj istniejącą numerację: przy przejęciu zasad aplikacja miała wersję `2.2`, a pierwsza zmiana objęta tymi zasadami to `2.3`. Nie resetuj jej do `1.0`.
- Każdy nowy prompt, w ramach którego wprowadzasz trwałe zmiany w projekcie, wymaga podniesienia wersji **dokładnie raz o jeden**: `2.3` → `2.4` itd. Po `2.9` następuje **`2.10`**, nie `3.0`.
- Dotyczy to również konfiguracji, dokumentacji, `AGENTS.md` oraz dodawania i usuwania plików. Kilka edycji, testów, poprawek, pytań o postęp i kontynuacji tego samego zadania nie oznacza kilku wersji.
- Prompt wyłącznie do odczytu, analizy lub odpowiedzi nie podnosi wersji i nie tworzy wpisu historii.
- W ramach tej samej zmiany synchronizuj numer w README, etykiecie wersji Dockerfile i tagu obrazu w `docker-compose.yml`. API i wersja w ustawieniach UI mają korzystać z `APP_VERSION`; tytuł karty przeglądarki pozostaje po prostu `Downloadeusz`.
- Aktualizacja numeru, jego prezentacji, README i historii jest częścią jednego podniesienia wersji, a nie osobnym powodem kolejnego podniesienia.

## Historia wersji w `docs/`

- Dla każdej nowej wersji twórz osobny plik `docs/vN.M.md`, np. `docs/v2.3.md`, `docs/v2.4.md`. Nie nadpisuj wcześniejszych wpisów.
- Każdy wpis musi zawierać numer wersji, datę i rzeczywiste zmiany **względem poprzedniej wersji**: co dodano, zmieniono, usunięto lub naprawiono. Pomijaj puste kategorie.
- Podawaj wyniki faktycznie wykonanej weryfikacji i jej ograniczenia. Nie przedstawiaj planów ani niewykonanych prac jako zmian ukończonych.
- Nie odtwarzaj fikcyjnej historii starych wersji. Historia prowadzona według tych zasad zaczyna się od `v2.3`; wcześniejsze opisy w README zachowują charakter historyczny.
- Numer wersji, README i nowy wpis historii aktualizuj łącznie. Zakładka „Ostatnia wersja” odczytuje dokładnie `docs/v{APP_VERSION}.md`, a nie plik wybrany alfabetycznie.
- Instrukcja użytkownika znajduje się w `docs/INSTRUCTION.md`. Przy zmianie funkcji aktualizuj odpowiednie instrukcje oraz uwzględnij to w opisie wersji. README i instrukcja używają Markdown bez surowego HTML, ponieważ dokumentacja w aplikacji wyłącza wykonywanie HTML.
- Zachowaj kopiowanie README i `docs/` do obrazu Dockera: te pliki są treścią okna dokumentacji w działającej aplikacji.

## Obowiązkowe komendy aktualizacji na końcu raportu

Zasady przejęte z sekcji „Obowiązkowe komendy aktualizacji na końcu raportu” i „Raport końcowy” `/workspace/hapi/AGENTS.md`, dostosowane do Downloadeusza.

- **Ustalony przepływ na Serweuszu:** źródła w `~/workspace/downloadeusz`, działająca aplikacja w `~/docker/downloadeusz`. Użytkownik potwierdził taki sam układ logiczny jak dla HAPI Huba. Komendy aktualizacji mają korzystać z tych ścieżek; nie pytaj o nie ponownie w zwykłych iteracjach.
- **Po każdej iteracji wprowadzającej trwałe zmiany na samym końcu odpowiedzi podawaj kompletne bloki komend Bash do wklejenia na Serweuszu.** Dotyczy to także dokumentacji, konfiguracji i numeru wersji. Nie czekaj na osobną prośbę o komendy.
- Komendy aktualizacji mają sprawdzić istnienie katalogu źródłowego, docelowego i wszystkich kopiowanych plików **przed zatrzymaniem usługi**, zatrzymać projekt Downloadeusza przez `docker compose down`, skopiować odpowiednie źródła z zachowaniem ścieżek względnych, a następnie wykonać `docker compose up -d --build` w katalogu docelowym. Błąd dowolnego kroku ma zatrzymać dalsze wykonanie. Wyraźnie zaznacz, że aktualizacja zatrzymuje aplikację na czas budowania.
- Podawaj rzeczywiste, potwierdzone ścieżki Downloadeusza. Nie przenoś automatycznie `~/docker/hapi`, nazw usług ani adresów HAPI. Jeśli brakuje informacji o źródle, celu lub sposobie kopiowania, poproś o te dane przed przygotowaniem zależnych komend; nie zgaduj ścieżek i nie wymagaj ponownego zatwierdzenia już zleconej pracy.
- Jeśli używasz istniejącego skryptu aktualizacji, zawsze podawaj **pełną komendę uruchomienia z rzeczywistą ścieżką i argumentami źródła oraz celu**. Sama nazwa skryptu, link lub opis kroków nie wystarcza.
- Nie zakładaj, że skrypt HAPI istnieje także w Downloadeuszu. Gdy odpowiedniego skryptu brakuje, podaj kompletny sprawdzony blok Bash bez tej zależności albo jego pełną treść i gotowe polecenia zapisania oraz uruchomienia. Nie zostawiaj fragmentów do samodzielnego uzupełnienia.
- Kopiuj wyłącznie jawnie wskazane pliki aplikacji i dokumentacji albo zweryfikowany kompletny zestaw źródeł. Jeśli stan wcześniejszych aktualizacji nie jest znany, nie zakładaj, że wybiórcze kopiowanie odtworzy brakujące moduły; sprawdź lub ustal wymagany stan przed podaniem komend.
- Zachowuj docelowe dane, pobraną muzykę, bazy, `.env`, pliki kont, ciasteczka i lokalne ustawienia. Nie usuwaj całego katalogu docelowego, nie stosuj `docker compose down -v` ani synchronizacji z masowym usuwaniem. Usunięcie pliku aplikacji uwzględniaj wyłącznie dla konkretnej ścieżki wynikającej ze zlecenia.
- Rzeczywiste pliki `navidrome_accounts.txt` i `slskd_accounts.txt` użytkownika są już w `~/docker/downloadeusz`, obok docelowego `docker-compose.yml`. Nie dodawaj ich do kopiowania z `~/workspace/downloadeusz`, nie nadpisuj ich przykładami ani nie twórz ich ponownie. Przy aktualizacji zachowuj istniejące pliki w katalogu docelowym; pliki `*.example` w workspace nie są ich źródłem.
- Kopiowanie `.git` i publikacja wymagają osobno ustalonego przepływu Git dla Downloadeusza; nie przejmuj uprawnień ani repozytorium HAPI. Przy zatwierdzonym kopiowaniu `.git` sprawdź commit, czystość źródeł i samowystarczalność historii, a poprzednie docelowe `.git` zachowaj w kopii poza projektem. Nie nadpisuj nieuzgodnionej historii.
- Jeśli publikacja Git jest skonfigurowana i objęta ustaleniami, podawaj **drugi, osobny blok opcjonalnego pusha**, wykonywany przez użytkownika dopiero po ręcznym sprawdzeniu aplikacji. Aktualizacja aplikacji nie może wymagać wcześniejszego pusha. Nie wymyślaj autora, remote ani gałęzi; gdy brakuje ustaleń, wskaż ograniczenie.
- Podanie komend **nie oznacza ich wykonania** i nie upoważnia agenta do samodzielnego wdrożenia, restartowania usług ani publikacji. Wyraźnie oddziel lokalne sprawdzenia od czynności do wykonania przez użytkownika.

## Granice przejęcia zasad

- Przejęcie zasad wersjonowania nie oznacza przejęcia infrastruktury HAPI, jego ścieżek modułów, komend Bun, repozytoriów, tożsamości autora, zasad commitowania ani skryptów wdrożenia.
- Samo podniesienie wersji nie upoważnia do commitowania, tagowania, pushowania, publikowania wydania ani wdrożenia.
