# Plan rozwoju DietBota

Plan wykonawczy po audycie, zaakceptowany 27.09.2026. Etapy są rozdzielone: wpis na liście nie oznacza wdrożenia.

## Wydanie A — cele i diety roślinne (wykonane, 0.2.0)

- [x] Opcjonalny kalkulator: wiek, wzrost, masa, płeć używana we wzorze, aktywność, masa docelowa. Wynik tylko jako propozycja; osobne zatwierdzenie i zapis. Bez wysyłania danych do AI.
- [x] Jawna metoda, ograniczenia i źródła. Brak obietnicy terminu utraty masy, brak automatycznych restrykcyjnych diet i kalkulacji dla niepełnoletnich / ciąży / karmienia / osób wymagających diety leczniczej.
- [x] Wybór tradycyjna / wegetariańska / wegańska. Kontrola składników, także przekąsek i stałych pozycji, nie same tagi nazw dań.
- [x] Nowe autorskie przepisy roślinne ze sposobem przygotowania, czasem i gramaturami; wartości wyłącznie z istniejących produktów. Dodano 34 przepisy, w tym 26 wegańskich.
- [x] Zapisane plany przechowują kopię danych wykorzystanych do obliczeń. Zmiana ustawień tworzy nową wersję dopiero po świadomym przeliczeniu. Stare rekordy oznaczone jako zabezpieczone w dniu migracji, nie jako historycznie odtworzone.
- [x] Bezpieczne dostarczenie nowych plików danych bez nadpisania własnych przepisów, ocen, planów i klucza. Kontrola skrótów istniejących danych po instalacji.
- [x] Odbiór: cztery rzeczywiste generacje bez AI na osobnej bazie, 224 niezależne porównania kcal/B/T/W, kontrola składników i zakupów, próba UI, nowy instalator i uruchomiony EXE. Szczegóły i ograniczenia: [raport odbioru](ODBIOR_0.2.0.md).

## Wydanie B — niezawodność i dane

- [ ] Kopia bezpieczeństwa / przywracanie z aplikacji (baza, własne przepisy, preferencje; domyślnie bez sekretów), automatyczna kopia przed migracją, kosz planów.
- [ ] Aktualizacje bazy: wersjonowany katalog bazowy i osobne zmiany użytkownika, raport konfliktów po ID; nigdy ciche zastępowanie wartości.
- [ ] Trwały postęp zadań, powrót po zmianie zakładki / ponownym uruchomieniu, anulowanie i bezpieczne zamykanie. Ustawienia zamrożone na czas zadania.
- [x] Zakupy zawsze z wybranego / aktywnego planu, także przy >100 planach. Ilości kupione zamiast samych znaczników; wzrost ilości ponownie widoczny. Przyspieszone do wydania 0.2.0.
- [ ] Klucz w magazynie chronionym Windows; token sesji API; rezerwowanie budżetu AI przed równoległymi żądaniami. Pełne porównanie originu (także portu) wykonane w 0.2.0; pozostałe części jeszcze nie.
- [ ] Odbiór: restart i przerwane zadanie, odtworzenie kopii, niezmienność starych eksportów, próba odrzucania obcego originu, brak sekretów w paczce.

## Wydanie C — wygodne planowanie

- [ ] Zablokuj wybrane posiłki, przelicz resztę; podgląd zmian przed akceptacją.
- [ ] Spiżarnia z ilościami i odejmowaniem od zakupów; tryb mniej różnych składników / wykorzystanie resztek.
- [ ] Oceny i powody niechęci osobno dla osób; preferencja zwiększa częstość, zakaz faktycznie wyklucza.
- [ ] Tryb gotowania, czytelne kroki, porcje i wspólny garnek. Po eksporcie otwórz plik / folder.
- [ ] Paginacja po stronie serwera, filtr 30/45 minut, ograniczony cache. W 0.2.0 wykonano opóźnienie wyszukiwania o 250 ms i filtr rzeczywistego czasu do 15 minut.
- [ ] Fokus w oknach dialogowych, obsługa klawiaturą i komunikaty dostępności; mniej ustawień na głównym ekranie.
- [ ] Odbiór: ręczna ścieżka wygeneruj → oceń → zablokuj → zmień → zakupy → eksport, szeroki i wąski ekran.

## Wydanie D — kontrola jakości

- [ ] Przegląd źródeł produktów i ich stanów surowy/gotowany, oznaczenie faktycznie sprawdzonych pozycji. Zgodność sum z bazą nie oznacza niezależnej weryfikacji całej bazy żywieniowej.
- [ ] Uzupełnienie brakujących kroków, realistycznych czasów, zgodności nazw i składników; nowe dania dodawane dla luk, nie samego licznika.
- [ ] Powtarzalne zależności, test uruchomienia zbudowanej paczki na kopii danych, wersja widoczna w aplikacji i zgodna z instalatorem.

## Zasady wykonania

Nie zmieniać aktywnej diety użytkownika w ramach prób. Przed migracją kopia SQLite. Nie pakować `.env`, bazy użytkownika ani logów AI. Testy automatyczne uzupełniają, a nie zastępują rzeczywiste generowanie. W raporcie oddzielać wykonane punkty od kolejnych etapów.
