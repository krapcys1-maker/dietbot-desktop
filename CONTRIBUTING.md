# Jak pomóc w rozwoju Porcji

Dziękuję za zainteresowanie! Zgłoszenia i propozycje po polsku są mile widziane.

## Zgłoszenia

- **Błąd:** [formularz „Zgłoś błąd”](https://github.com/krapcys1-maker/dietbot-desktop/issues/new?template=blad.yml) — wersja Porcji, kroki odtworzenia, oczekiwany i faktyczny wynik.
- **Pomysł:** [formularz „Propozycja”](https://github.com/krapcys1-maker/dietbot-desktop/issues/new?template=propozycja.yml) — jaki problem rozwiązuje zmiana.
- **Luka bezpieczeństwa:** nie zgłaszaj publicznie — zobacz [SECURITY.md](SECURITY.md).

Nigdy nie dołączaj klucza API, pliku `.env`, bazy `dietbot.sqlite3`, eksportów z prywatnymi planami ani danych zdrowotnych.

## Zmiany w kodzie

1. Uruchom projekt ze źródeł według [docs/ROZWOJ.md](docs/ROZWOJ.md).
2. Przed wysłaniem zmian:

   ```powershell
   .\.venv\Scripts\python.exe -m ruff check src tests
   .\.venv\Scripts\python.exe -m pytest
   .\.venv\Scripts\dietbot.exe dane sprawdz
   cd web; npm run build
   ```

   Te same kroki wykonuje automatycznie GitHub Actions dla każdego pull requesta.
3. Opisz w PR, co się zmienia dla użytkownika i jak to sprawdzono.

## Zasady projektu

- **Liczby liczy kod, nie model językowy.** Gramatury wylicza silnik CP-SAT, a niezależny weryfikator przelicza wynik. AI może wybierać dania i pisać teksty, ale nie podaje kalorii ani gramów.
- **Każda wartość odżywcza ma źródło** (np. kod BLS 4.0, EAN z Open Food Facts). Do nowych produktów dodaj `zrodlo`; wartości z BLS sprawdza `dietbot dane odswiez-bls --no-zapisz`.
- **Nowe dane w nowych plikach.** Aplikacja kopiuje do danych użytkownika tylko brakujące pliki, więc poprawki istniejących plików YAML nie docierają do starszych instalacji. Nowe produkty i przepisy dodawaj w osobnych plikach (np. `dane/dania/roslinne_033.yaml`).
- **Historyczne plany się nie zmieniają.** Zapisana dieta zachowuje kopię danych użytych do obliczeń; zmiany reguł działają przy nowym planie albo po świadomym „Przelicz”.
- **Interfejs i komunikaty po polsku**, bez identyfikatorów technicznych w tekstach dla użytkownika.

## Licencja

Repozytorium nie ma jeszcze licencji dla własnego kodu. Zanim zaproponujesz większy wkład, zapytaj w zgłoszeniu o warunki jego włączenia.
