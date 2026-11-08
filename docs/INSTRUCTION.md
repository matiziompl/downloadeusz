# Instrukcja Downloadeusza

Downloadeusz dodaje pobieranie i import muzyki do kolejki na serwerze. Pliki nie są pobierane na urządzenie, na którym otwierasz przeglądarkę. Muzyka trafia do biblioteki wskazanej przez administratora w `/downloads`.

## Dokumentacja i błędy

- **Książka w lewym górnym rogu** otwiera okno pomocy. Zakładka **README** pokazuje opis projektu i konfigurację, **Ostatnia wersja** opisuje zmiany względem poprzedniej wersji, a **Instrukcja** zawiera ten poradnik.
- Zakładki przełączysz kliknięciem lub strzałkami klawiatury; Home i End wybierają pierwszą i ostatnią. Tab przechodzi do treści i pozostałych przycisków. Okno zamkniesz przyciskiem zamknięcia, klawiszem Escape lub kliknięciem tła poza oknem.
- Jeśli dokumentu nie uda się wczytać, użyj przycisku ponowienia. Nie trzeba odświeżać całej aplikacji.
- **Czerwony widżet pod książką** pojawia się, gdy lista błędów nie jest pusta. Otwiera panel z nieodnalezionymi utworami i odsyłaczami do źródeł, jeśli są dostępne. Zamknij panel przyciskiem X lub kliknięciem jego tła.

## Szybki start

1. Otwórz ustawienia ikoną koła zębatego w prawym górnym rogu. Jeśli korzystasz z Navidrome, wybierz konto i sprawdź połączenie. Do Soulseeka potrzebne jest skonfigurowane slskd.
2. Wybierz źródło muzyki, wklej link do utworu, albumu lub playlisty i ustaw opcje opisane poniżej.
3. Kliknij **Dodaj do kolejki**. Przycisk jest nieaktywny dla pustego linku i podczas dodawania zlecenia.
4. W **Kolejce zadań** wybierz zadanie, aby sprawdzić postęp poszczególnych utworów. Możesz zamknąć stronę i wrócić później.

## Spotify (spotdl)

Wklej link ze Spotify i wybierz **Spotify (spotdl)**. SpotDL odczytuje listę utworów i metadane Spotify, a nagrań szuka w skonfigurowanych źródłach audio. Sam link Spotify nie jest źródłem pliku audio. Linki Spotify automatycznie wybierają tę zakładkę, jeśli nie masz aktywnego Soulseeka.

| Opcja | Jak jej używać |
| --- | --- |
| Pomiń już pobrane | Włącz, aby wykorzystać historię pobrań i zachowane pliki zamiast pobierać je ponownie. Nie zastępuje sprawdzania biblioteki Navidrome. |
| Nie filtruj wyników | Wyłącza filtr wyników SpotDL. Może pomóc znaleźć nagranie, ale wymaga większej uwagi przy ocenie dopasowania. |
| Ciasteczka YT | Włącz, jeśli administrator umieścił aktualny plik `youtube_cookies.txt` w `/spotdl_config`. Ta opcja nie przesyła ciasteczek z przeglądarki. |
| Zapisz błędy do pliku | Zapisuje brakujące pozycje SpotDL do `/spotdl_config/nadal_brakujace.txt`. |
| Wątki pobierania | Wybierz liczbę od 1 do 8. Więcej wątków zwiększa równoległość SpotDL, obciążenie serwera i ryzyko limitów źródła. Nie zwiększa liczby workerów kolejki. |
| Wygeneruj playlistę | Włącz dla playlisty; podaj własną nazwę albo pozostaw pole puste, aby użyć nazwy źródła. Natywna playlista Navidrome wymaga wybranego konta i włączonej synchronizacji w ustawieniach. |
| Playlista publiczna | Udostępnia utworzoną playlistę użytkownikom Navidrome. Wyłącz, jeśli ma pozostać prywatna. |
| Sprawdź duplikaty w Navidrome | Przed pobraniem sprawdza wybraną bibliotekę. Wybierz pomijanie tylko przy posiadanej wersji FLAC/lossless albo przy dowolnym formacie. Wymaga wybranego, dostępnego konta. |

## YT i SoundCloud (yt-dlp)

Wybierz tę zakładkę i wklej link YouTube lub SoundCloud. Takie linki automatycznie wybierają yt-dlp, jeśli nie masz aktywnego Soulseeka.

- **Tylko Audio (MP3)** wyodrębnia dźwięk do MP3. Wyłącz tę opcję, jeśli chcesz zachować pobieranie w domyślnym trybie yt-dlp, również z wideo, gdy źródło je udostępnia.
- **Pomiń już pobrane** wykorzystuje zapisane wyniki i zapobiega nadpisywaniu istniejących pobrań.
- **Sprawdź duplikaty w Navidrome** działa z wybranym kontem i trybem **FLAC / Lossless** albo **dowolny format**, tak jak dla Spotify.
- Przy włączonym beets pobrania audio przechodzą przez import biblioteki. Pobrania wideo pozostają w zwykłym katalogu pobrań.

## Soulseek

1. W ustawieniach sprawdź, czy aplikacja widzi konfigurację slskd. Kliknij **Testuj połączenie**.
2. Wybierz **Soulseek**, a następnie wklej link Spotify lub YouTube. W tej zakładce link określa szukane nagrania; pliki są wyszukiwane u użytkowników Soulseeka. SoundCloud nie jest obsługiwanym linkiem wejściowym tego trybu.
3. Wybierz **Preferowany format**: FLAC lub MP3. To preferencja wyboru pliku, nie gwarancja jakości oryginalnego nagrania.
4. Opcjonalnie włącz **Pobierz z YouTube, jeśli utworu nie uda się znaleźć w Soulseek**. Domyślnie źródło zapasowe jest wyłączone i może mieć niższą jakość.
5. Opcjonalnie wybierz **Utwórz playlistę w Navidrome**. Najpierw wybierz konto Navidrome i włącz synchronizację w ustawieniach; później podaj nazwę playlisty i wybierz jej widoczność.
6. Kliknij **Dodaj do kolejki** i obserwuj wynik wyszukiwania oraz transferu.

Aplikacja porównuje wykonawcę, tytuł i wersję nagrania, a następnie ocenia format i jakość. Wyniki niejednoznaczne wymagają sprawdzenia. Gdy źródło jest niedostępne, możliwa jest próba u innego użytkownika. Niepewny stan zlecenia transferu nie jest przedstawiany jako udane pobranie.

Wyszukiwanie zaczyna od oczyszczonego wykonawcy i pełnego tytułu, następnie używa charakterystycznego członu wykonawcy z tytułem, a później samego tytułu. Wszystkie zapytania są wysyłane małymi literami; oryginalna pisownia metadanych i nazw plików pozostaje zachowana. Dla długich tytułów ostatnią próbą może być wykonawca i kilka charakterystycznych słów. Powtórzone zapytania są pomijane; obowiązuje limit czterech zapytań i wspólny budżet około minuty na wyszukiwanie. Błąd połączenia uruchamia obsługę ponowień, nie jest uznawany za brak nagrania.

Każde wyszukiwanie slskd otrzymuje limit bezczynności 5 sekund, przekazany jako `5000` milisekund. Aplikacja czeka na zapis końcowych odpowiedzi, a nie sam znacznik zakończenia. Jeśli nie znajdzie dopasowania, komunikat podaje łączną liczbę odpowiedzi i plików: zera oznaczają, że slskd nie zwrócił plików do oceny; wartości dodatnie wskazują, że wyniki nadeszły, ale nie przeszły sprawdzania zgodności lub zostały wykluczone po wcześniejszych próbach.

Dodatki opisujące publikację, np. „Official Audio”, oraz rozpoznane dopiski wydania, np. „Remastered 2011”, są usuwane z tożsamości wyszukiwania. Oznaczenia nagrania, np. „Live”, „Remix”, „Acoustic” czy „Instrumental”, pozostają istotne przy porównaniu wyników. Mieszane, niejednoznaczne dopiski są zachowywane. Szersze zapytanie nie oznacza zgody na pobranie innego wykonawcy lub innej wersji. Brak danych potrzebnych do potwierdzenia wyniku pozostawia utwór do sprawdzenia.

## Opcjonalne powiadomienia po pobraniu w slskd

Webhook to wpis w konfiguracji `slskd.yml`, który kieruje powiadomienia `DownloadFileComplete` na istniejący adres Downloadeusza `/api/slskd/events`. Nie trzeba uzyskiwać go z zewnętrznej usługi. Pełny adres przy domyślnym porcie to `http://ADRES_DOWNLOADEUSZA:9000/api/slskd/events`; musi być dostępny z kontenera slskd.

Własny sekret wygenerujesz poleceniem `openssl rand -hex 32`. Tę samą wartość wpisz do `SLSKD_WEBHOOK_SECRET` w `.env` obok Compose Downloadeusza oraz do nagłówka `X-Webhook-Key` w konfiguracji webhooka slskd. Pełny przykład sekcji `integrations.webhooks` i kolejne kroki znajdują się w README, w części „Powiadomienia slskd”. Zmiana sekretu Downloadeusza wymaga odtworzenia jego kontenera.

Webhook jest opcjonalny: przy włączonym harmonogramie i niewstrzymanej kolejce import sprawdza ukończone pobrania domyślnie co 300 sekund. Bez webhooka możesz pozostawić sekret pusty.

## Aktualne zadanie

Pomiędzy głównym formularzem a kolejką pojawia się kafelek **Aktualne zadanie**. Śledzi pracę serwera niezależnie od tego, które zadanie zaznaczysz w historii. Pokazuje etap pracy, aktualny utwór i jego pozycję, liczbę zakończonych utworów oraz błędy lub pozycje wymagające sprawdzenia.

Podczas wyszukiwania Soulseek zobaczysz rzeczywiste zapytanie i numer próby. Oczekiwanie na transfer, normalizację, indeksowanie albo ponowienie jest opisane jako oczekiwanie. Liczniki obejmują całe zadanie, także utwory poza aktualnie widoczną stroną listy. **Pokaż szczegóły** wybiera bieżące zadanie w kolejce. Gdy nie ma zadania wykonywanego ani oczekującego, kafelek znika; błąd odświeżenia ukrywa nieaktualne dane.

## Kolejka zadań

- Kolejka przechowuje postęp na serwerze w `/spotdl_config/jobs.sqlite3`; odświeżenie strony ani zamknięcie przeglądarki nie usuwa zleceń.
- Lista odświeża się automatycznie co około 5 sekund. Przycisk ze strzałką odświeża ją ręcznie.
- **Zatrzymaj wszystko** obok odświeżania anuluje wszystkie zadania w kolejce, wykonywane i oczekujące. Ikona **kosza po prawej stronie wiersza** zatrzymuje tylko wybrane aktywne zadanie. Historia i pliki pozostają na dysku; stan **Zatrzymano** przetrwa restart aplikacji.
- Zatrzymanie całej kolejki wstrzymuje także automatyczny import beets oraz przyjmowanie webhooków slskd. Ręczne dodanie lub ponowienie zadania włącza je ponownie; pojedynczy kosz nie wyłącza automatyki.
- Aktywny downloader i jego procesy potomne są przerywane. Import beets kończy bieżącą operację zapisu i zatrzymuje się przy kolejnym punkcie kontrolnym, aby zachować stan biblioteki. Trwające wywołanie sieciowe może wymagać czasu na zakończenie. Zadanie już przekazane do zewnętrznego slskd, Normalizeusza lub Navidrome może nadal działać w tamtej usłudze; te przyciski anulują jego dalszą obsługę w Downloadeuszu.
- Kliknij zadanie, aby zobaczyć postęp utworów i komunikaty błędów. Przy dłuższej liście korzystaj z **Poprzednie utwory** i **Następne utwory**.
- Jeden worker obsługuje pracę kolejno. Status może oznaczać oczekiwanie na pobranie, import, normalizację lub indeks Navidrome; dodanie zadania nie oznacza zakończenia pobierania.
- Przejściowe błędy są ponawiane z opóźnieniem od 30 sekund do godziny. Po ośmiu kolejnych błędach potrzebne jest ręczne ponowienie.
- Błąd pobierania zawiera końcowy komunikat programu oraz kod zakończenia. Terminal podaje numer próby i opóźnienie następnej. Jednoznaczne błędy, np. prywatny lub usunięty film, wymagają sprawdzenia bez kolejnych automatycznych prób. Sam ogólny błąd ze starszych wersji nie pozwala ustalić przyczyny problemu ze źródłem.
- Gdy pojawi się **Ponów**, najpierw przeczytaj powód błędu i usuń jego przyczynę. Ponowienie zachowuje wyniki zakończonych utworów.
- **Ponów** działa także dla zatrzymanych zadań. Jeżeli poprzednie wykonanie jeszcze się zatrzymuje, odczekaj chwilę i spróbuj ponownie.
- Zatrzymane zadanie Normalizeusza można ponowić dopiero po otrzymaniu potwierdzenia jego zakończenia z tej usługi, aby nie powielić nadal działającej operacji.
- Status wymagający sprawdzenia oznacza, że aplikacja nie może bezpiecznie potwierdzić wyniku. Zapoznaj się z opisem zamiast traktować go jak sukces.

## Porządkowanie muzyki — beets

Panel pokazuje gotowość importera, ostatni błąd i harmonogram. **Odśwież stan** ręcznie aktualizuje informacje; normalnie są odświeżane okresowo.

- **Importuj nowe pliki** dodaje ręczny import nowych pobrań z inboxa i ukończonych transferów slskd.
- **Ponów rozpoznawanie** ponownie sprawdza pliki wymagające rozpoznania lub uzupełnienia danych.
- Rozwiń **Do sprawdzenia**, aby zobaczyć nazwę pliku, stan rozpoznania, powód i dostępne propozycje nagrań. **Sprawdź ponownie** ponawia rozpoznawanie wskazanego pliku; nie wybiera automatycznie dowolnej propozycji z listy.
- **Pokaż kolejne 20 plików** rozwija następne wpisy listy.
- Przy włączonym beets import kopiuje pliki do biblioteki, uzupełnia tagi i okładki oraz zachowuje oryginały. Nierozpoznane pliki z wystarczającymi tagami mogą zostać dodane według tych tagów; nadal mogą wymagać sprawdzenia.
- Nie zmieniaj istniejącej biblioteki przez samo oczekiwanie na import: importer obsługuje nowe pobrania, a nie automatyczną przebudowę całej kolekcji. Duplikaty są rozpoznawane według bazy beets i zapisanego źródła.
- Jeśli po próbach rozpoznania album pozostaje nieznany, nowy import z poprawnym wykonawcą i tytułem otrzymuje album zastępczy o nazwie utworu, np. `Wykonawca/Tytuł/Tytuł.flac`. Taki album pozostaje niepotwierdzony. Istniejące wiarygodne tagi albumu są zachowywane; nazwa zastępcza nie jest dowodem dopasowania wydania. Jawne ponowienie może uzupełnić brakujący album już zaimportowanej kopii bez przebudowy całej biblioteki.
- Gdy beets jest wyłączony lub niedostępny, sprawdź komunikat panelu. Włączenie wymaga ustawienia `BEETS_ENABLED` na serwerze i dostępnych narzędzi; przełącznika tej funkcji nie ma w przeglądarce.

## Porządkowanie ukończonych pobrań Soulseeka

Przycisk **Uporządkuj pliki** znajduje się przy statusie Soulseeka. Użyj go dla ukończonych transferów widocznych przez slskd i wspólny katalog `/slskd_downloads`.

Przy włączonym beets uruchamia import z zachowaniem oryginałów. Przy wyłączonym beets działa starszy organizer: **przenosi** pliki na podstawie tagów. Przed użyciem zwróć uwagę na stan beets. Niekompletne transfery nie powinny trafiać do importu.

## Metadane Spotify, komenda FFmpeg i okładka

1. W zakładce Spotify wklej link do utworu lub albumu i kliknij **Pobierz same metadane**. Nie pobiera to nagrania; dla albumu podgląd dotyczy pierwszego utworu.
2. W podglądzie sprawdź tytuł, wykonawcę, album, rok, numer utworu i okładkę, jeśli są dostępne.
3. **Kopiuj komendę FFmpeg** kopiuje wygenerowane polecenie i pokazuje je w konsoli aplikacji. Nie uruchamia polecenia na serwerze.
4. W skopiowanym poleceniu podmień wartość `FILE=...` na ścieżkę właściwego pliku i uruchom je samodzielnie w terminalu. Komenda aktualizuje tagi wskazanego pliku; przed uruchomieniem sprawdź ścieżkę i treść.
5. **Pobierz okładkę** otwiera oryginalną okładkę w nowej karcie. Możesz ją stamtąd zapisać. X zamyka podgląd metadanych.

## Navidrome — konta, skanowanie i playlisty

W ustawieniach, w sekcji **Integracja z Navidrome**, wybierz konto z listy i kliknij **Testuj połączenie**. **Odśwież** ponownie odczytuje konta z pliku bez restartu aplikacji. Baner nad formularzem również pozwala testować połączenie i pokazuje wybrane konto.

**Wymuś skanowanie i usuń .m3u** włącza synchronizację po operacji: aplikacja zleca skan biblioteki i w odpowiednim przepływie usuwa znany tymczasowy plik playlisty. Sam przełącznik nie rozpoczyna pobierania. Dla playlisty potrzebne są dodatkowo opcje generowania playlisty w formularzu.

Przy włączonym beets natywna playlista powstaje po imporcie i skanie Navidrome z zachowaniem kolejności, posiadanych utworów oraz powtórzeń. Brakujące wpisy indeksu mogą opóźnić zakończenie do 10 minut; niepełny wynik wymaga sprawdzenia. Ponowienie aktualizuje zapisaną playlistę.

Konta dodaje administrator do `navidrome_accounts.txt` obok `docker-compose.yml` w katalogu działającej aplikacji (na Serweuszu: `~/docker/downloadeusz`). Compose montuje ten plik tylko do odczytu jako `/app/navidrome_accounts.txt`:

```text
Server: "http://NAVIDROME:4533"
user: "LOGIN"
password: "HASLO"
```

Dla kolejnych użytkowników tego serwera dodaj kolejne pary `user` i `password`. Nie wpisuj prawdziwych haseł w dokumentacji ani w linku pobierania. Przeglądarka wybiera identyfikator konta; hasła pozostają po stronie serwera.

## slskd — konfiguracja i status

Administrator umieszcza plik `slskd_accounts.txt` obok tego samego `docker-compose.yml`. Compose montuje go tylko do odczytu jako `/app/slskd_accounts.txt`:

```text
Server: "http://SLSKD:5030"
user: "LOGIN"
password: "HASLO"
```

W ustawieniach sekcja **Integracja z Soulseek (slskd)** pokazuje wykryty serwer i użytkownika. **Odśwież** odczytuje konfigurację ponownie, a **Testuj połączenie** sprawdza dostęp. Konto dotyczy API slskd. Usługa slskd musi mieć działające połączenie Soulseek i zamontowany wspólny katalog ukończonych pobrań.

Webhook ukończenia transferu jest opcjonalnym przyspieszeniem wykrywania plików; konfigurację sekretu i zdarzenia `DownloadFileComplete` opisuje README. Harmonogram nadal uzgadnia stan transferów.

## Normalizeusz i normalizacja głośności

Integracja jest domyślnie włączona w Compose (`NORMALIZATION_ENABLED=true`) po podłączeniu wspólnego katalogu kolejki i tej samej biblioteki muzyki w obu usługach. Ustawienie `false` wyłącza automatyczne zlecenia po imporcie. Przy uruchamianiu samego backendu domyślne pozostaje `false`. Szczegóły montowań są w README.

Przy włączonej integracji zadanie czeka na potwierdzenie ReplayGain przez Normalizeusza, a następnie skanuje Navidrome. Jeśli zadanie długo czeka, sprawdź działanie Normalizeusza i prawa dostępu do wspólnego katalogu. Wyłączona integracja pozwala korzystać z aplikacji bez tej usługi.

Panel **Normalizeusz** pod porządkowaniem beets pokazuje stan osobnego kontenera, kolejkę, postęp i raporty. **Odśwież stan** ponawia odczyt. Przy braku aktualnego połączenia przyciski są wyłączone, a zachowana historia jest opisana jako nieaktualna. Starszy Normalizeusz wymaga aktualizacji do wersji obsługującej protokół v2.

1. Kliknij **Sprawdź brakujące albumy**. To podgląd całej biblioteki bez zmiany plików. Przeczytaj liczniki i próbki; brak tytułu oznacza pominięcie pliku.
2. Po udanym podglądzie z kandydatami użyj **Uzupełnij puste albumy**. Wpisany zostanie tytuł z istniejącego tagu, bez przenoszenia plików, zmiany dźwięku lub kasowania ReplayGain. Niepusty album jest zawsze zachowywany, również gdy zmienił go inny proces od czasu podglądu. Po poprzednim uzupełnianiu potrzebny jest nowy podgląd.
3. **Sprawdź ReplayGain całej biblioteki** to oddzielne zadanie normalizacji. Nie służy do masowego uzupełniania albumów. Prawidłowo znormalizowane pliki są pomijane bez ponownej analizy.
4. Rozwiń historię, aby zobaczyć próbki zmienionych/pominiętych plików oraz błędy. Raport zawiera ograniczoną liczbę próbek, ale liczniki dotyczą wszystkich plików. **Ponów** wysyła nowe zlecenie; błędy masowych zmian nie są automatycznie ponawiane w pętli.

Wybrane konto Navidrome jest dołączane do ręcznego zadania. Po potwierdzonym zapisie zmian Downloadeusz zleca jego skan. Sam podgląd nie uruchamia skanowania. Zlecenie kolejki nie jest potwierdzeniem ukończenia pracy; czekaj na raport. W razie komunikatu o niepotwierdzonej trwałości publikacji nie wysyłaj duplikatu — zadanie nadal jest śledzone.

Ręczne funkcje panelu działają niezależnie od przełącznika automatycznych zleceń `NORMALIZATION_ENABLED`. Musi jednak działać Normalizeusz i wspólna kolejka. Przy starcie usługi istniejące albumy nie są masowo uzupełniane; użyj jawnego podglądu i operacji naprawy. Obsługa nowych lub zmienionych plików może uzupełniać ich album przy okazji obserwowania biblioteki.

## Wygląd i ustawienia przeglądarki

Otwórz koło zębate, zmień ustawienia, a panel zamknij przyciskiem X, kołem zębatym lub kliknięciem tła.

| Ustawienie | Działanie |
| --- | --- |
| Pokaż tytuł „Downloadeusz” | Pokazuje lub ukrywa duży nagłówek formularza. Nazwa karty przeglądarki zawsze pozostaje „Downloadeusz”. |
| Motyw | Wybierz kolor akcentu. Przycisk różdżki dopasowuje kolor do tła. |
| Tło — Presety | Wybierz gotowy kolor tła. |
| Tło — Tapeta | Wybierz obraz z listy. Własne pliki dodaje administrator do katalogu tapet; nie jest to formularz przesyłania obrazów. |
| Wyświetlanie statusu usług | Wybierz „Zawsze” albo „Gdy niezalogowany lub błąd”, aby określić widoczność banera połączeń. |
| Tryb informacyjny — Proste info | Pokazuje krótkie komunikaty operacji. |
| Tryb informacyjny — Konsola na żywo | Pokazuje logi wybranego zadania w panelu terminala przez SSE. To podgląd, nie interaktywny terminal systemowy. |

Ustawienia wyglądu i opcje pobierania są zachowywane w `localStorage` tej przeglądarki. Inna przeglądarka lub wyczyszczenie danych witryny oznacza osobne ustawienia. Wersja aplikacji jest widoczna na dole panelu ustawień.

## Gdy coś nie działa

- **Brak kont:** sprawdź pliki konfiguracyjne na serwerze, ich format i montowanie `/spotdl_config`, a następnie użyj **Odśwież**.
- **Błąd połączenia:** sprawdź adres usługi osiągalny z kontenera Downloadeusza i dane konta. `localhost` w kontenerze oznacza ten kontener.
- **Utwór wymaga sprawdzenia:** przeczytaj komunikat w kolejce i panelu beets. Jeśli źródło lub dane poprawiono, użyj odpowiedniego przycisku ponowienia.
- **Brak pliku po transferze Soulseeka:** sprawdź zgodność montowania `/slskd_downloads` z faktycznym katalogiem zakończonych transferów slskd.
- **Zadanie czeka na normalizację lub skan:** sprawdź odpowiednią usługę i wspólne katalogi; samo odświeżanie przeglądarki nie usuwa tej zależności.
- **Ciasteczka YouTube:** odśwież plik na serwerze, jeśli wygasł. Nie publikuj jego zawartości w logach ani zgłoszeniach.
