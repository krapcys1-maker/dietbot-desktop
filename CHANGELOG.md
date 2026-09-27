# Historia zmian

## 0.3.3 — więcej kuchni roślinnej i czytelniejsze komunikaty (27.09.2026)

### Dieta wegańska i wegetariańska

- Napój sojowy i owsiany (niesłodzone, z wapniem) w bazie produktów — mleko do kawy w stałych pozycjach można zastąpić zamiast je usuwać. Komunikat przy zmianie diety podpowiada zamiennik.
- 14 nowych przepisów wegańskich, w tym dwie zupy na zapas. Nowe roślinne źródła białka: granulat sojowy, edamame i odżywka białkowa sojowa. Wartości z BLS 4.0.
- Pule wegańskie: śniadania 8 → 12, obiady 12 → 16, kolacje 12 → 19, zupy na zapas 2 → 4. Każde nowe danie przeszło test silnika z losowymi, zbilansowanymi dniami.

### Wygoda

- Pole życzeń i opcje AI wyglądają na wyłączone, gdy nie ma klucza, a komunikat mówi wprost, czego brakuje.
- Po kliknięciu **Ułóż mój plan** strona przewija się do postępu.
- Motyw jasny/ciemny domyślnie idzie za ustawieniem Windows, dopóki nie wybierzesz go ręcznie.
- Dziennik „Jak powstał ten plan” pokazuje nazwy dań i dni zamiast identyfikatorów technicznych.
- Nazwy diet w komunikatach po polsku („nie pasuje do diety wegańskiej”), uwagi o danych z nazwami dań.
- Przepis na zapas wspomina o pieczywie tylko wtedy, gdy jest w daniu; poprawna odmiana liczby porcji (np. „6 porcji”).

### Projekt

- Automatyczne testy w GitHub Actions (ruff, spójność danych, pytest na Windows, build interfejsu).
- Nowy README ze schematem działania, galerią i streszczeniem po angielsku; CONTRIBUTING, SECURITY i szablony zgłoszeń.

## 0.3.2 — jeden przepis na całe gotowanie (27.09.2026)

- Dania na zapas pokazują od razu jedną tabelę ilości na wszystkie dni i osoby oraz przygotowanie całej partii.
- Następnego dnia jest informacja o odgrzewaniu i wskazanie dnia z pełnym przepisem. Podział na osoby jest opisany osobno, a nie jako kolejna lista do gotowania.
- Pełna ilość jest sumowana z faktycznie zapisanych porcji. Pieczywo podawane obok jest uwzględnione i oznaczone jako dodatek.
- Historyczne przepisy z nieprzeliczonym litrażem wody otrzymują wskazówkę napełnienia całego garnka do przykrycia składników; aplikacja nie zgaduje litrów bez wydajności bazowego przepisu.
- Ten sam układ działa w aplikacji i eksporcie, również dla wcześniej zapisanych diet. Nie zmienia gramatur, makr ani zakupów.

## 0.3.1 — proporcje przepisu przed dopasowaniem kalorii (27.09.2026)

- Pieczywo i główne nadzienie kanapek, wrapów i tostów są powiązane proporcją przepisu (±25%).
- Zamknięte tosty z chleba tostowego: pary kromek i minimum 20 g sera na 50 g pieczywa. Poprawiona baza sera w tostach z szynką.
- Uzupełnione grupy składników placków, krokietów i panierki; ścisłe masy mają maksymalnie 5% tolerancji udziału i 1 g na zaokrąglenie.
- Nowe przepisy z rozpoznanym ciastem lub panierką bez wymaganych proporcji są odrzucane, zamiast swobodnie dobierać mąkę do kcal.
- Niezależna kontrola proporcji nadzienia, brakujących składników i jednostek. Ograniczeń kulinarnych nie można „opłacić” dopasowaniem kalorii.
- Eksport podaje wspólne ilości składników masy dla wszystkich osób, z instrukcją odważania roztrzepanego jajka.
- Zmiany dotyczą nowych i przeliczonych diet. Historyczne wersje i lista zakupów pozostają bez zmian; „Przelicz i napraw” tworzy nową wersję.

To kontrola zadeklarowanych proporcji, nie gwarancja jakości każdego przepisu ani próba praktycznego ugotowania wszystkich dań. Złożone przepisy nie są używane jako uproszczone przekąski bilansujące.

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
