# Porcja — rozwój i budowanie

[← Strona główna](../README.md)

## Uruchomienie ze źródeł

Python 3.12+, Node.js z npm, Windows z WebView2. Polecenia w PowerShell:

```powershell
git clone https://github.com/krapcys1-maker/dietbot-desktop.git
cd dietbot-desktop
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,app,build]"
cd web
npm ci
npm run build
cd ..
.\.venv\Scripts\dietbot.exe aplikacja
```

Bez klucza działa lokalny planer. Opcjonalnie skopiuj `.env.example` do `.env` i wpisz własny klucz lub dodaj go w Ustawieniach aplikacji. Nigdy nie commituj `.env`. W trybie źródłowym dane są w katalogu projektu; instalowana wersja używa `%LOCALAPPDATA%\DietBot`.

Inne wejścia: `dietbot serwer` (przeglądarka, domyślnie `127.0.0.1:8765`), `dietbot telegram`, `dietbot plan generuj`. Dostępne argumenty pokaże `dietbot --help` oraz pomoc konkretnej komendy. Nazwy pakietu i CLI pozostają technicznie `dietbot`.

## Weryfikacja

```powershell
.\.venv\Scripts\python.exe -m pytest
cd web
npm ci
npm run build
cd ..
```

Te same kroki (plus `ruff check` i `dietbot dane sprawdz`) wykonuje GitHub Actions dla każdego pushu i pull requesta — [workflow `testy.yml`](../.github/workflows/testy.yml). Testy obejmują m.in. obliczenia z tabel, zakupy, oceny, zapisane diety, filtry diet, kalkulator, kopie danych planu oraz tożsamość instalatora. Nie zastępują testu uruchomienia gotowego EXE. Przed wydaniem sprawdź również nowy plan, zamianę dania, zapis w bibliotece i oba eksporty przez systemowy dialog.

## Budowa Windows

Zainstaluj Inno Setup 6, potem:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\zbuduj_instalator.ps1
```

Wyniki: `release\Porcja-Setup-0.5.0.exe` i folder `dist\DietBot`. Do uruchomienia paczki potrzebny jest cały folder, nie sam EXE. Instalator jest niepodpisany. Wersję utrzymuj spójnie w `pyproject.toml`, `src/dietbot/__init__.py`, `web/package.json`, lockfile, skryptach oraz metadanych Windows.

Nie zmieniaj AppId, technicznej nazwy `DietBot.exe` ani katalogu danych bez migracji: obecna zgodność pozwala aktualizować wcześniejsze instalacje. Nie nadpisuj własnych plików użytkownika przy dodawaniu danych startowych.

## Przed publikacją

1. Sprawdź testy, kompilację, numer wersji i działanie instalatora.
2. Przejrzyj dokładnie staging oraz historię pod kątem sekretów. Nie publikuj `.env`, SQLite, `wyniki`, `dane/raw` ani prywatnych eksportów.
3. Do ilustracji używaj osobnego profilu z przykładowymi danymi, nigdy skopiowanej bazy użytkownika.
4. Sprawdź, że paczka nie zawiera prywatnych danych, a lokalne fonty i licencje są dołączone.
5. Do GitHub Releases dodaj instalator, opis zmian i SHA-256. Dużych plików EXE/MP4 nie umieszczaj w historii Git.

## Materiały prezentacyjne 0.3.0

Zrzuty w `docs/media` pochodzą z prawdziwego interfejsu na osobnym profilu demo, bez klucza AI i bez prywatnych planów. Film MP4 w wydaniu jest montażem tych ekranów z polskimi podpisami — nie nagraniem czasu generowania. Nie ma ścieżki muzycznej ani lektora. GIF w README jest krótkim, lżejszym podglądem.

## Dane i zależności

Produkty przechowują źródło w YAML; uwzględniaj je przy zmianach i dystrybucji. [Research](RESEARCH.md) opisuje wykorzystane bazy; [oprawa 0.3.0](PORCJA_0.3.0.md#materiały-i-licencje) wskazuje fonty i ikony. Pełne licencje materiałów interfejsu są w `web/public/licenses`. Nie zakładaj, że jedna licencja obejmuje kod, dane i wszystkie zależności. Nie dodano nowej licencji na własny kod projektu.
