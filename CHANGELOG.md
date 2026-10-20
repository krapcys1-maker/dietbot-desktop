# Historia zmian

## 0.5.1 — sos w daniach i więcej białka zamiast ryżu (20.10.2026)

- **Dania nie wychodzą suche.** Silnik nie może już obciąć sosu (mleczka kokosowego, śmietany, passaty, pomidorów, bulionu, jogurtu w daniu „z sosem”) poniżej 85% proporcji przepisu względem ryżu, makaronu, kaszy czy ziemniaków ani poniżej 80% względem mięsa, ryby, tofu czy strączków. Wcześniej dopełniał kalorie samym ryżem, a białko samym tofu. W teście 12 tygodni na trzech poziomach kalorii (ok. 1400–2600 kcal) 28 ze 102 porcji dań z sosem miało go mniej niż 85% przepisu, 13 — mniej niż 70%, a 16 miało o ponad 30% za dużo białka względem sosu; po zmianie poniżej 70% nie ma żadnej, poniżej 85% zostały pojedyncze wrapy i pity, a porcji z nadmiarem białka nie ma. Zupy i pieczywo podawane obok dania nie są objęte tą regułą. Czas planowania podobny jak wcześniej.
- **Poprawione przepisy:** curry z tofu i ciecierzycą (90 g mleczka kokosowego zamiast 35 g, mniej tofu), chili z fasolą i tofu (200 g pomidorów zamiast 100 g, mniej tofu), makaron z kurczakiem i szpinakiem w sosie śmietanowym (90 g śmietany zamiast 50 g), curry z ciecierzycy i tofu (mniej tofu).
- **Najwyżej 3 jajka** na osobę w jednym posiłku (było 6).
- **Zasada „mniej węglowodanów, więcej białka” z notatnika działa w pełni.** Cel białka rośnie, a cel węglowodanów spada o całą podaną ilość (wcześniej o połowę, a silnik trzymał się dolnej granicy — z „+25 g białka dziennie” wychodziło ok. +5 g). Na profilu 1650/2200 kcal: białko średnio 154 → 168 g (mężczyzna) i 107 → 112 g (kobieta), ryż i makaron na obiad nieco mniejsze.
- Poprawki przepisów i limitu jajek trafiają także do starszych instalacji (`dane/korekty`), jeśli nie zmieniałeś tych przepisów ani produktu. Zapisane plany zachowują swoje gramatury; aplikacja zaproponuje ich przeliczenie.

## 0.5.0 — bezpieczniej i pewniej (11.10.2026)

Poprawki z audytu z 09.10.2026 (`docs/AUDYT_2026-10-09.md`, sekcja 9 opisuje stan każdego punktu).

### Zdrowie i dane

- **Minimum kalorii:** cele poniżej ok. 1200 kcal (kobieta) albo 1500 kcal (mężczyzna) wymagają potwierdzenia, a plan pokazuje uwagę. Kalkulator ma próg zależny od płci — nie odrzuca już większości kobiet.
- **Zakazy obowiązują też w przekąskach bilansujących**, także zakazy z życzeń („bez orzechów”). Weryfikator sprawdza przekąski i stałe pozycje.
- **Szynka dojrzewająca** ma właściwe wartości (BLS: 307 kcal i 21,8 g tłuszczu zamiast 123 kcal). Poprawka trafia też do starszych instalacji, jeśli nie zmieniałeś tego produktu.
- **Sól i kwasy nasycone** w podsumowaniu dnia i w eksporcie, z zaleceniami (sól poniżej 5 g, kwasy nasycone poniżej 10% energii).
- Weryfikator sprawdza porcje względem przepisu: składniki spoza dania, brak obowiązkowego składnika, połówki sztuk, porcje „po równo” i garnek dania na zapas.
- Więcej dań w trybie wegetariańskim i wegańskim (sezam, zioła, hummus, tortilla i inne proste produkty roślinne). Plakietka diety wynika ze składników, nie z tagu.

### Koszty AI i bezpieczeństwo

- Czat i Telegram **pytają przed płatną akcją** (nowy plan, wymyślanie dań). „Nie wymyślaj…” i pytania o wymyślanie nie uruchamiają już niczego.
- **Limit kosztu na jedno polecenie** (domyślnie 0,50 $). Budżet rezerwowany przed wywołaniem — równoległe wywołania go nie przekroczą. Liczone są też nieudane próby.
- **Bezpiecznik:** gdy model nie odpowiada, aplikacja przez 5 minut pracuje bez AI, zamiast czekać kilkadziesiąt minut. Błędy klucza (401) i środków (402) nie są ponawiane.
- Dania bez oceny kucharza (awaria połączenia) nie trafiają do biblioteki. Wykluczenia z życzeń, które usunęłyby prawie wszystkie dania, są pomijane z komunikatem.
- **Token sesji API:** inne programy i konta na komputerze nie odczytają czatu ani nie zmienią celów. Nagłówki chroniące przed osadzeniem w obcej stronie.

### Niezawodność

- Dodanie produktu nie może już zablokować startu (unikalny identyfikator, wycofanie zapisu). Wadliwy wpis w pliku użytkownika jest pomijany z ostrzeżeniem.
- **Przerwanie zadania:** przycisk „Przerwij” w oknie postępu. Zamknięcie okna w trakcie liczenia pyta o przerwanie i nie zostawia ukrytego procesu. Jedna kopia aplikacji naraz.
- Dwie zmiany tej samej wersji planu nie gubią się nawzajem; zapis celów nie cofa zmian z czatu. Przyciski są zablokowane na czas zadania.
- Odrzucenie dania w bibliotece nie blokuje zamian w zapisanych dietach. Zmiana statusu dania i dodanie produktu czekają na koniec zadania.
- Silnik: przy nieosiągalnej kotwicy białka ponowna próba trzyma przynajmniej dzienne widełki. Kolacja dużo cięższa od obiadu daje karę, a nie „błąd modelu”.

### Wygoda

- Lista zakupów pokazuje warianty połączonej pozycji, gdy różnią się kalorycznością (np. jogurt naturalny i owocowy).
- Kontrast w ciemnym motywie, błędy zostają do zamknięcia, okna dialogowe obsługiwane klawiaturą, ostrzeżenie przed wyjściem z niezapisanymi celami, ekran błędu zamiast pustego okna, poprawne liczebniki („1 dzień”), motyw bez błysku przy starcie.
- Telegram działa tylko w rozmowie prywatnej; długie wiadomości nie psują formatowania.

### Projekt

- Dokładne wersje zależności w `requirements-lock.txt` (CI i budowanie). Instalator zawiera dane wyłącznie z zatwierdzonego stanu repozytorium, a skrypt sprawdza zawartość paczki.
- Pamięć podręczna danych w JSON zamiast pickle; baza SQLite z wersją schematu i kluczami obcymi; `npm audit` bez podatności.

## 0.4.0 — notatnik preferencji (04.10.2026)

### Notatnik preferencji

- Nowa zakładka **Notatnik preferencji**. Piszesz uwagę zwykłym językiem, a Porcja zapisuje z niej zasady stosowane w każdym nowym planie:
  - **zakaz produktu** albo całej kategorii (np. bez białek jaj kurzych);
  - **limit porcji na osobę**, np. ryż suchy najwyżej 65 g i 105 g na obiad;
  - **mniej węglowodanów, więcej białka** — widełki przesuwają się bez zmiany kalorii, więc silnik nie musi sztywno trzymać dotychczasowych węglowodanów;
  - **wskazówki** dla układającego tydzień (np. „mniej dań z jajkami na kolację”).
- Model językowy tylko rozpoznaje, czego dotyczy uwaga. Program dopasowuje produkty do bazy i przyjmuje wyłącznie liczby, które padły w uwadze. Brakujące liczby liczy sam: limit „mniej niż dotąd” z ostatnich planów, a zamianę makro jako ok. 4% energii dnia.
- Uwagi do działania aplikacji (np. wygląd eksportu) trafiają na osobną listę **Do poprawy w programie**. Można ją skopiować albo zapisać do TXT dla twórcy. Program sam niczego w sobie nie przeprogramowuje.
- **Analiza nawyków** zbiera oceny 👍/👎, ręczne zamiany dań, uwagi i życzenia. Opisuje wzorce i proponuje zasady — dodajesz tylko wybrane.
- Zasady można wyłączyć, usunąć albo dodać ręcznie (także bez klucza AI). Plik założeń pozostaje bez zmian; tabela pokazuje widełki przed zasadami i po nich.
- Czat i Telegram: „zapamiętaj, że…” zapisuje uwagę w notatniku.

### Silnik i naprawa planu

- Limity porcji osób są twardym ograniczeniem silnika i są sprawdzane przez niezależny weryfikator. Dania, których nie da się zrobić w limicie, odpadają z puli danego posiłku.
- **Przelicz i napraw** najpierw wymienia dania, które przestały pasować do zasad (zakaz, limit, 👎), na najbardziej podobne dozwolone, a dopiero potem liczy gramatury.
- Komunikaty o wykluczeniu dań podają nazwy produktów zamiast identyfikatorów.

### Lista zakupów

- Eksport TXT i „Kopiuj pozostałe zakupy” bez „- [ ]” i „- ” przed pozycjami — notatki w telefonie same tworzą z wierszy listę do odhaczania.

### Instalator

- Aktualizacja usuwa stary skrót „DietBot” z pulpitu i menu Start (zostaje „Porcja”).

Test 6 kolejnych tygodni bez AI (domyślne cele): bez zasad 6/6 poprawnych planów, ryż na obiad średnio 72 g i 110 g (maks. 95 g i 120 g), białka jaj 8 razy. Z zasadami 65/105 g ryżu, zamianą węglowodanów na białko i zakazem białek jaj: 6/6, ryż średnio 59 g i 91 g (maks. 65 g i 105 g), białko średnio 107 g i 157 g (wcześniej 103 g i 149 g), białka jaj 0 razy.

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
