# Porcja 0.3.0 — oprawa produktu

## Kierunek

„Porcja. Dobrze mieć plan.” to robocza nazwa nowej odsłony DietBota. Nie przeprowadzono badania znaków towarowych; przed publiczną kampanią należy sprawdzić nazwę.
Zamiast technicznego panelu: spokojna kuchnia, ciepła biel, ciemna zieleń, jasna szałwia i brzoskwiniowe akcenty. Nagłówki Fraunces, interfejs DM Sans.
Oryginalna ilustracja wektorowa talerza jest dekoracją — nie udaje zdjęcia konkretnego przepisu. Nie korzystamy z przypadkowych zdjęć z wyszukiwarki.

## Wdrożone

- Nowy znak, ikona Windows, nazwa okna, instalator i skróty Porcja. Stabilny AppId, foldery i DietBot.exe zachowane dla aktualizacji.
- Pulpit: aktualny plan z prawdziwych danych, dwa eksporty i skróty do codziennych działań. W pustej bibliotece: pierwszy plan i kroki konfiguracji.
- Uproszczona nawigacja, mniej używane narzędzia rozwijane, menu w wąskim oknie.
- Plan: wyraźne dni, porcje i makro, informacje pomocnicze w rozwijanych sekcjach; eksport całego planu i zakupów bez zmiany działania.
- Zakupy: dwie kolumny na większym ekranie, większe pola odhaczania, licznik i pasek postępu.
- Nowy plan: czytelne etapy, opis działania i link do celów. Biblioteka: spójne ikony i filtry, czytelny stan bez wybranego przepisu.
- Przewodnik w aplikacji, jasne rozróżnienie „Zapisz dietę” w bibliotece i „Eksportuj dietę” do pliku.
- Własny klucz AI pozostaje opcjonalny. Wyraźne rozdzielenie darmowej aplikacji od opłat dostawcy AI oraz informacja o lokalnym nieszyfrowanym kluczu.
- Jasny/ciemny motyw zapamiętywany lokalnie, fokus klawiatury, odnośnik pomijania nawigacji, komunikaty dla czytników, ograniczenie animacji.

## Materiały i licencje

- Logo i ilustracja: oryginalne SVG w `Brand.tsx`, `PlateArt.tsx`, `web/public/porcja.svg`; ikona Windows generowana przez istniejący skrypt.
- [Lucide](https://lucide.dev/license), ISC i MIT dla części ikon wywodzących się z Feather.
- [DM Sans](https://fontsource.org/fonts/dm-sans), [Fraunces](https://fontsource.org/fonts/fraunces): SIL OFL 1.1.
- Pełne licencje są w `web/public/licenses`, dołączane do kompilacji i dostępne w Ustawieniach. Fonty i grafiki nie wymagają połączenia z zewnętrznym CDN.

## Granice tej wersji

To przebudowa interfejsu, nie zmiana algorytmu żywieniowego. Nie dodano kont, subskrypcji, telemetrii ani synchronizacji. Nie zmieniono zapisanych diet, celów ani ocen użytkownika.
Instalator nadal nie jest podpisany certyfikatem. Szyfrowanie klucza przez Windows, kopie zapasowe z UI i trwałe zadania pozostają osobnymi punktami planu rozwoju.
Publikacja na GitHub nie odbywa się automatycznie podczas budowania lokalnej wersji.

## Odbiór 27.09.2026

- Kompilacja TypeScript/Vite, Ruff dla zmienionych plików Python i 22 testy regresji zakończone powodzeniem.
- Praktyczne ułożenie 7 dni w kopii danych z nowego formularza, bez API AI: zakończone; weryfikator zgłosił plan poprawny. Plan zapisano i odnaleziono w bibliotece.
- Przeklikano odhaczanie i ukrywanie zakupów, ocenę dania, wyszukiwanie, filtr wegański, pomoc, ustawienia, oba motywy i start z pustą biblioteką.
- Przy 420 px sprawdzono plan, zakupy, pulpit, pomoc, cele, bibliotekę i ustawienia. Usunięto poziome rozpychanie formularza celów; szerokie tabele mają własne przewijanie.
- Instalator zbudowany i zainstalowany. Skrót Porcja na pulpicie uruchamia wersję 0.3.0; plik EXE ma metadane produktu Porcja 0.3.0.
- Z gotowego EXE zapisano dietę oraz zakupy w natywnym oknie wyboru pliku. SHA-256 obu plików jest identyczne z eksportami tego samego planu z 0.2.0.
- Porównanie wszystkich tabel SQLite, plików danych, konfiguracji i skrótu kryptograficznego klucza: bez zmian. Zachowane 14 nieusuniętych planów, integralność SQLite OK.
- Dystrybucja nie zawiera `.env` ani bazy użytkownika. Fonty, ikona i pełne licencje są dostępne lokalnie.
- Nie wykonywano płatnych wywołań DeepSeek ani publikacji na GitHub. Testy funkcjonalne nie są pełnym audytem medycznym, bezpieczeństwa ani dostępności.

Dowody lokalne (nie przeznaczone do publikacji): `wyniki/acceptance-030.json`, `wyniki/test-030-final.log`, `wyniki/Porcja-0.3.0-pulpit.png`, `wyniki/install-030.log`.
