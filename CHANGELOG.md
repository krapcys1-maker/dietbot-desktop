# Historia zmian

## 0.3.0 — Porcja (27.09.2026)

Pierwsze publiczne wydanie pod nazwą Porcja. Zawiera także funkcje rozwijane lokalnie po publicznej wersji 0.1.1.

### Wygląd i codzienna obsługa

- Nowa nazwa, znak, ikona Windows i instalator; zgodność ze starszą instalacją DietBota.
- Pulpit z aktualnym tygodniem, jasny/ciemny motyw, spójna typografia i grafika dostępne offline.
- Dwa główne eksporty: dieta i lista zakupów; pełny plan do TXT z wyborem miejsca zapisu w aplikacji Windows.
- Prostsza nawigacja, rozwijane szczegóły, przewodnik oraz mniej używane narzędzia schowane w menu.

### Plany, preferencje i zakupy

- Nazwane zapisane diety, historia i wersje tygodni.
- Oceny „Lubię” / „Nie lubię” uwzględniane w nowych planach, zamianach i przekąskach.
- Połączone warianty zakupów bez marek, sklepów, przypraw i ziół; zapamiętywanie kupionych gramów.
- Tryby tradycyjny, wegetariański i wegański sprawdzane po składnikach.
- 34 dodatkowe przepisy roślinne: 26 wegańskich i 8 wegetariańskich.
- Opcjonalny lokalny kalkulator celów oraz filtry dań i czasu przygotowania.

### Obliczenia i trwałość danych

- Kalorie sumowane z pola kcal produktów, niezależnie od sum białka, tłuszczów i węglowodanów.
- Zgodna metoda obliczeń w generatorze, weryfikatorze, widokach i eksportach.
- Kopie danych źródłowych dla nowych planów; zmiana tabel nie przelicza historycznego planu po cichu.
- Zabezpieczenie starszych planów danymi dostępnymi przy migracji, z informacją o ograniczeniach i kopią bazy przed migracją.
- Kontrola źródła żądań modyfikujących lokalne API i blokady zmian kolidujących z generowaniem.

### Publikacja

- Polska strona projektu, instrukcja, dokumentacja dla rozwijających i prezentacja MP4 na profilu demonstracyjnym.
- Instalator Windows i sumy SHA-256 w GitHub Releases.

### Znane ograniczenia

- Instalator nie jest podpisany; klucz DeepSeek jest zapisany lokalnie jawnym tekstem.
- Brak synchronizacji, automatycznej aktualizacji i przycisku kopii zapasowej.
- Oceny i rodzaj diety są wspólne dla domowników; nie ma osobnych profili preferencji.
- Kalkulator jest orientacyjny, a status zgodności nie jest oceną medyczną ani pełnym bilansem mikroelementów.
- Bez klucza AI nie działa czat, interpretacja swobodnych życzeń i wymyślanie dań przez model.

## 0.1.1 — publiczne wydanie (25.09.2026)

- Zapis całego tygodnia i listy zakupów do plików TXT.

## 0.1.0 — pierwsze publiczne wydanie

- Lokalny planer, okno Windows, instalator i ustawienie własnego klucza DeepSeek.
