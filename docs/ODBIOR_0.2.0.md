# Odbiór DietBot 0.2.0

Zakres: pierwszy etap planu rozwoju — kalkulator, diety roślinne, ochrona historii i poprawki zakupów.
To nie jest deklaracja wdrożenia całej listy kolejnych etapów.

## Wykonane próby

- Trzy pełne tygodnie bez AI: wegański (ziarno 420), wegetariański (421), wegański (422). Każdy dla dwóch osób, siedem dni. Wszystkie trzy bez błędów i ostrzeżeń weryfikatora.
- Czwarty tydzień uruchomiony przyciskiem w interfejsie, po obliczeniu i zatwierdzeniu celów z fikcyjnego profilu. Cel pierwszej osoby: 2230 kcal. Dzienne kcal i makro w granicach; jawne ostrzeżenie o dwóch porcjach obiadu 725 g przy pozostawionym ręcznym limicie 680 g. Kalkulator nie nadpisuje ręcznych limitów talerza.
- Dla czterech tygodni 224 porównania kcal/B/T/W: osobne sumowanie Decimal z gramów i wartości `na_100g`, z przekąskami i stałymi pozycjami. Zgodność z ekranem w dokładności wyświetlania 0,01.
- Listy zakupów: 44, 43, 42 i 41 pozycji. Każda ilość porównana z sumą składników źródłowych; brak zdublowanych kluczy produktu.
- Kontrola diety dla każdego faktycznie użytego produktu. Nowe 34 przepisy poprawne strukturalnie; 26 zgodnych z filtrem wegańskim, wszystkie zgodne z wegetariańskim.
- UI: wyliczenie nie zmienia celów; dopiero „Wstaw propozycję” aktualizuje formularz, a „Zapisz” konfigurację. Po zmianie celów stary tydzień nadal pokazywał 1650 kcal celu, nie nowe 2230 kcal.
- Filtr biblioteki: wegańskie + do 15 minut ograniczył wynik do 16 dań w bazie testowej.
- Pełna regresja: 255 testów zakończonych powodzeniem (jedno ostrzeżenie deprecacji biblioteki Starlette). Dodatkowe próby migracji, ochrony przed zmianą tabel, aktywnego planu poza pierwszą setką i originu lokalnego API.
- TypeScript, kompilacja interfejsu i Ruff bez błędów.

## Co dokładnie oznacza weryfikacja

Zgodność rachunkowa z zapisanymi tabelami produktów nie oznacza audytu wszystkich źródeł zewnętrznych ani gwarancji, że dowolny podobnie nazwany produkt ze sklepu ma identyczny skład. Tryb wegański nie bilansuje witamin/minerałów i nie zastępuje indywidualnego planowania źródła B12. Kalkulator jest orientacyjną opcją dla określonego zakresu osób dorosłych, nie poradą lekarską.

## Dane i odtwarzalność

Próby odbywały się w `wyniki/qa-020`, nie w bazie użytkownika. Wyniki obliczeń: `wyniki/qa-020/final-verification.json`.
Przed instalacją wykonano kopię SQLite `wyniki/dietbot-before-020.sqlite3` i manifest skrótów istniejących danych (bez wypisywania klucza).
Migracja aplikacji ma również własną kopię `dietbot.sqlite3.before-snapshots.bak`. Starsze rekordy są zamrażane z danymi dostępnymi w chwili migracji — brakujące historyczne tabele nie są odtwarzane z domysłów.

## Odbiór instalacji

- Zainstalowano i uruchomiono `%LOCALAPPDATA%\Programs\DietBot\DietBot.exe`, wersja 0.2.0 widoczna w tytule okna i API.
- SHA-256 zainstalowanego EXE zgodny z kompilacją; instalator `release/DietBot-Setup-0.2.0.exe`.
- Wszystkie wcześniejsze tabele użytkownika i pliki danych/konfiguracji oraz `.env` pozostały niezmienione. Nowe: tabela kopii 14 planów oraz plik 34 przepisów. SQLite `integrity_check`: ok.
- Z rzeczywistego EXE przy wyświetlonym czwartku wyeksportowano cały siedmiodniowy jadłospis i całą listę zakupów, każdorazowo wybierając własną ścieżkę w oknie Windows. Zawartość plików identyczna z pełnym eksportem usługi.
- Pliki próbne: `wyniki/qa-020-eksport-dieta.txt` i `wyniki/qa-020-eksport-zakupy.txt`. Podgląd okna: `wyniki/DietBot-0.2.0-kalkulator.png`.
- Paczka nie zawiera `.env` ani SQLite użytkownika. W tym etapie nie publikowano zmian na GitHubie.
