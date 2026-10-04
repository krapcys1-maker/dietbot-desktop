# Porcja — instrukcja użytkownika

[← Strona główna](../README.md)

## Instalacja i aktualizacja

Pobierz `Porcja-Setup-0.4.0.exe` z [najnowszego wydania](https://github.com/krapcys1-maker/dietbot-desktop/releases/latest). Zamknij aplikację, uruchom instalator i otwórz **Porcję** w menu Start. Opcjonalnie zaznacz utworzenie skrótu na pulpicie.

Instalator zawiera program oraz jego zależności. **Nie kopiuj samego `DietBot.exe`** z katalogu kompilacji: potrzebuje również folderu `_internal`. Do przekazania aplikacji innej osobie służy instalator, nie Twój folder danych.

Aktualizacja zachowuje dane w `%LOCALAPPDATA%\DietBot`. Nazwa pliku wykonywalnego i identyfikator instalacji pozostają zgodne ze starszym DietBotem. Instalator nie jest podpisany certyfikatem; sprawdź źródło oraz SHA-256, nie wyłączaj ochrony Windows.

### Kopia zapasowa

Zamknij Porcję, a następnie skopiuj cały folder `%LOCALAPPDATA%\DietBot` w bezpieczne miejsce. Zawiera bazę planów, oceny, konfigurację i własne dane. Może też zawierać `.env` z kluczem — nie publikuj tej kopii. Przy odtwarzaniu zamknij aplikację i zachowaj osobną kopię aktualnego folderu przed jego zastąpieniem.

## Pierwszy plan

1. Otwórz **Cele i preferencje**. Sprawdź cele dla każdej osoby, zakazy, stałe pozycje i zasady tygodnia. Ustawienia startowe są przykładowe, nie dobrane do Ciebie.
2. Wybierz dietę tradycyjną, wegetariańską lub wegańską i zapisz ustawienia.
3. Przejdź do **Nowy plan**, wybierz tydzień oraz dni. Podstawowe planowanie działa bez AI.
4. Kliknij **Ułóż mój plan**. Po zakończeniu obejrzyj posiłki i komunikaty weryfikatora.
5. Jeśli plan ma uwagi, sprawdź wskazane reguły. Możesz zamienić danie albo skorzystać z dostępnej naprawy. Brak rozwiązania nie oznacza, że program może sam rozszerzyć Twoje limity.

Motyw jasny lub ciemny zmienisz ikoną księżyca/słońca w prawym górnym rogu; dopóki go nie zmienisz, Porcja stosuje motyw systemu Windows.

W **Plan tygodnia** wybierasz dzień z paska u góry. **Cały tydzień w skrócie** pokazuje zestaw dań na wszystkie dni. Szczegóły składników, przygotowania i rozliczeń są rozwijane. **Zamień** tworzy kolejną wersję planu; historia pozostaje dostępna w menu **Więcej**.

## Zapis i eksport

### Gotowanie na dwa dni

Przy pierwszym dniu dania na zapas widzisz **Całość na 2 dni dla 2 osób — 4 porcje**: jedną tabelę z pełną ilością każdego składnika i przygotowanie całej partii. Nie mnożysz tych ilości ponownie. Pieczywo oznaczone jako dodatek podajesz osobno. Drugiego dnia tylko odgrzewasz wcześniej przygotowane porcje — aplikacja wskazuje dzień z pełnym przepisem.

Rozwijany **Podział gotowej partii — porcje na dzisiaj** pokazuje, jak składniki są rozliczone między osoby; nie jest kolejną listą do gotowania. Eksport zawiera ten sam pełny przepis, a pod nim osobno opisany podział porcji. Zmiana działa też przy ponownym eksporcie zapisanych diet, bez zmiany ich gramatur. Już pobrany plik trzeba wyeksportować ponownie.

Jeśli historyczny przepis miał stały litraż wody bez określonej wydajności, przy całej partii podana jest wskazówka dodania wody do przykrycia składników, zamiast niepewnego przeliczenia litrów.

| Przycisk | Wynik |
|---|---|
| **Zapisz dietę** | Nazwany plan w zakładce **Moje diety**, bez tworzenia pliku. |
| **Eksportuj dietę** | TXT z wszystkimi dniami wybranego planu, gramaturami, przepisami i podsumowaniami. |
| **Eksportuj listę zakupów** | TXT z pełną listą dla wszystkich osób i dni, także pozycjami już odhaczonymi. |

W oknie Windows eksport otwiera **Zapisz jako**: wybierz folder i nazwę, kliknij **Zapisz**. Eksport działa również bez internetu i klucza AI. TXT otworzysz np. w Notatniku i możesz wydrukować z edytora. Nie ma osobnego przycisku drukowania pojedynczego dnia.

**Moje diety** zawierają zapisane tygodnie i historię. Usunięcie z zapisanych nie usuwa planu z historii. Nowe plany przechowują kopię tabel produktów, przepisów i założeń: późniejsza edycja bazy nie zmienia samoczynnie ich liczb. Przeliczenie tworzy nową wersję.

Plany ze starszych wersji bez takich kopii zabezpieczane są danymi dostępnymi podczas migracji, z odpowiednią informacją. Program nie odtwarza historycznych tabel, których wcześniej nie zapisał. Przed migracją tworzy kopię `wyniki/dietbot.sqlite3.before-snapshots.bak` w katalogu danych.

## Lista zakupów

Lista sumuje produkty ze wszystkich dni, osób, przekąsek i stałych pozycji. Łączy warianty np. mozzarelli, jogurtu, fety, mleka i twarogu; nie pokazuje marek, sklepów, przypraw ani ziół. **Skyr pozostaje osobno od jogurtu, a śmietanka od kwaśnej śmietany.** Ilości to gramy produktu, nie liczba opakowań.

Odhaczanie zapamiętuje kupioną ilość. Jeśli w kolejnej wersji potrzeba więcej, pozycja pojawi się ponownie do dokupienia. W **Więcej** możesz wybrać plan, skopiować pozostałe zakupy i wyczyścić odhaczenia. Eksport zawsze obejmuje całość.

Od 0.4.0 eksport TXT i kopiowanie podają pozycje bez „- [ ]” i „- ” (np. `Jogurt — 410 g`). Po wklejeniu do notatek w telefonie każdą linię zamienisz w punkt do odhaczania.

Uproszczona nazwa nie zmienia składnika użytego w obliczeniach. Wybór innego wariantu sklepowego może zmienić rzeczywiste kcal i makro — sprawdź etykietę oraz gramatury w przepisie.

## Oceny i biblioteka dań

- **Lubię** zwiększa szansę wybrania dania, nadal z uwzględnieniem celów i reguł. Nie gwarantuje go w każdym tygodniu.
- **Nie lubię** wyklucza konkretne danie z kolejnych planów, zamian i przekąsek bilansujących.
- Ponowne kliknięcie tej samej oceny ją usuwa. Oceny są wspólne dla osób z konfiguracji, zapisują się lokalnie i nie zmieniają starych planów.

## Notatnik preferencji (od 0.4.0)

W **Dopasuj do siebie → Notatnik preferencji** napisz uwagę zwykłym językiem, np. „na obiad za dużo ryżu — wolałbym 65 g żonie i 105 g mnie, w zamian więcej mięsa” albo „nie dawaj samych białek jaj”. Po kliknięciu **Zapisz i przeanalizuj** Porcja pokazuje, co zrozumiała, i zapisuje zasady:

| Zasada | Co robi |
|---|---|
| **Zakaz produktu** | Produkt (albo cała kategoria) nie trafia do nowych planów; dania, w których jest obowiązkowy, odpadają. |
| **Limit porcji** | Największa porcja produktu lub kategorii na posiłek, osobno dla każdej osoby (np. ryż suchy 65 g i 105 g na obiad). Silnik nie przekroczy limitu, a weryfikator to sprawdza. |
| **Mniej węglowodanów, więcej białka** | Przesuwa widełki dzienne bez zmiany kalorii: dolna granica węglowodanów w dół, górna granica białka w górę. Silnik ma więcej swobody i chętniej dokłada mięsa niż ryżu. |
| **Wskazówka** | Miękka preferencja dla układającego tydzień (np. „mniej dań z jajkami na kolację”). |

Liczby w zasadach pochodzą z Twojej uwagi. Gdy ich nie podasz, liczy je program, np. limit „mniej niż dotąd” na podstawie ostatnich planów. Model językowy tylko rozpoznaje treść uwagi. Bez klucza AI uwaga zapisze się jako notatka, a zasadę dodasz ręcznie w **Dodaj zasadę ręcznie**.

Zasady obowiązują w nowych planach i zamianach. Zapisany wcześniej tydzień pokaże informację o zmianie ustawień. **Przelicz i napraw** utworzy nową wersję: najpierw wymieni dania, które przestały pasować (np. z białkami jaj), potem przeliczy gramatury. Plik założeń się nie zmienia — zasady można wyłączyć albo usunąć.

Uwagi o samej aplikacji (np. „lista zakupów ma niewygodny format”) trafiają na listę **Do poprawy w programie**. Program ich sam nie wprowadza; skopiuj je albo zapisz notatnik do TXT i przekaż twórcy. **Przeanalizuj nawyki** opisuje wzorce z ocen, zamian dań i uwag oraz proponuje zasady — dodajesz tylko te, które chcesz. W czacie i Telegramie wystarczy napisać „zapamiętaj, że…”.

To zapamiętywanie konkretnych ocen i zasad, nie automatyczne rozpoznawanie alergii. Podczas układania planu zmiana ocen jest zablokowana. Biblioteka pozwala filtrować dietę, ocenę i czas przygotowania; filtr **do 15 minut** sprawdza rzeczywisty czas w przepisie.

## Wege i wegan

Rodzaj diety dotyczy wspólnego planu wszystkich osób. Zmiana ustawienia nie przerabia wcześniej zapisanych tygodni. Zgodność sprawdzana jest po składnikach, także opcjonalnych, przekąskach i stałych pozycjach.

Jeśli np. mleko do kawy koliduje z dietą wegańską, w **Cele i preferencje → Dzień → Stałe pozycje** wybierz zamiast niego **Napój sojowy niesłodzony (z wapniem)** albo **Napój owsiany niesłodzony (z wapniem)** (od 0.3.3) lub usuń tę pozycję. Produkty z nieznanym składem są traktowane ostrożnie: nie zakładamy np. wegetariańskiej podpuszczki w dowolnym serze. Pieczywo i makaron do przepisów wegańskich muszą być bez jaj i mleka. Własne produkty można oznaczyć deklaracją zgodności diety w formularzu dodawania produktu.

Baza została rozszerzona o 34 autorskie przepisy roślinne w 0.2.0 (26 wegańskich i 8 wegetariańskich) oraz 14 wegańskich w 0.3.3 — m.in. z granulatem sojowym, edamame i napojem sojowym, w tym dwie zupy na zapas. Dzięki temu tygodnie wegańskie rzadziej się powtarzają. Nie jest to pełny bilans mikroelementów ani gwarancja odpowiedniości diety. Informacje o planowaniu diety wegańskiej, w tym B12: [NHS](https://www.nhs.uk/live-well/eat-well/how-to-eat-a-balanced-diet/the-vegan-diet/).

## Proporcje podczas gotowania (od 0.3.1)

Pieczywo i główne nadzienie kanapek są powiązane. Zamknięte tosty z chleba tostowego mają parzystą liczbę kromek i minimum 20 g sera na 50 g pieczywa. Masy wymagające przepisu — np. ciasto naleśnikowe, farsz czy panierka — są skalowane razem, z kontrolą udziałów składników. Silnik nie może złamać tych reguł tylko dlatego, że poprawiłoby to wynik kcal.

W eksporcie znajdziesz **łączną ilość składników masy dla wszystkich osób**. Roztrzep jajko i odważ podaną ilość do wspólnego ciasta; porcje w tabeli opisują podział, nie osobne mieszanie ciasta na każdy talerz. Składniki i miary domowe nadal są przybliżeniem konkretnego produktu.

Zapisana wcześniej dieta nie zmienia się sama. W **Plan tygodnia → Przelicz i napraw** utworzysz nową wersję według aktualnych przepisów. Oryginał pozostaje w historii. Jeśli nowych warunków nie da się spełnić, aplikacja powinna zmienić danie lub pokazać uwagi — nie usuwać potrzebnego sera albo mąki.

## Kalkulator celów

W **Cele i preferencje**, przy osobie, rozwiń **Nie znasz swoich celów?**. Wpisz dane, kliknij **Oblicz propozycję**, następnie **Wstaw propozycję do formularza** i **Zapisz**. Samo obliczenie nie zmienia celów. Możesz nadal wpisywać cele ręcznie.

Obliczenia są lokalne, bez AI. Surowe pomiary nie są zapisywane; zapisujesz dopiero zaakceptowane cele. Metoda: [Mifflin–St Jeor](https://pubmed.ncbi.nlm.nih.gov/2305711/) × przybliżony współczynnik aktywności 1,2 / 1,375 / 1,55 / 1,725. Opcjonalny deficyt wynosi 10% lub 15%, nie więcej niż 500 kcal. Propozycja makro to 20% energii z białka, 30% z tłuszczu i 50% z węglowodanów. To założenia funkcji, a nie pomiar indywidualnego zapotrzebowania lub gwarancja tempa chudnięcia.

Ograniczenia funkcji: wiek 18–75 lat, wzrost 130–220 cm, masa 40–200 kg, BMI wejściowe od 18,5 do poniżej 40, docelowe co najmniej 18,5; dolna granica proponowanej energii co najmniej 1500 kcal, środek do 4000 kcal. Są to granice produktu, nie uniwersalne normy kliniczne. Funkcja nie jest przeznaczona dla dzieci, osób w ciąży, karmiących, z zaburzeniami odżywiania lub chorobami wymagającymi indywidualnej diety. Poza zakresem skorzystaj z indywidualnie ustalonych celów.

Kalkulator nie zmienia automatycznie limitów wielkości talerza. Jeżeli pojawia się ostrzeżenie o porcji, sprawdź ustawienia zaawansowane. Aplikacja nie bilansuje kompletu witamin, minerałów ani alergenów.

## Własny klucz DeepSeek

W **Ustawienia → Twój pomocnik AI** wklej klucz z [panelu DeepSeek](https://platform.deepseek.com/api_keys) i kliknij **Zapisz klucz**. Każda osoba instalująca program korzysta ze swojego konta dostawcy. Klucz można usunąć przyciskiem w tej sekcji.

Klucz zapisuje się lokalnie jako jawny tekst w `.env`. Nie wklejaj go do GitHuba, zgłoszeń, czatu z innymi osobami ani filmów. AI otrzymuje kontekst zadania; bez klucza planowanie nadal działa algorytmicznie, ale czat, interpretacja swobodnych życzeń i generowanie nowych przepisów przez model są niedostępne. Porcja nie pobiera abonamentu; koszty API rozlicza DeepSeek.

## Gdy coś nie działa

- **Nie widzisz nowych przycisków?** Zamknij wszystkie stare okna i uruchom Porcję z menu Start. Sprawdź wersję 0.3.0 w tytule okna i Ustawieniach.
- **Puste okno?** Sprawdź obecność Microsoft Edge WebView2 Runtime i czy instalacja zawiera folder `_internal`.
- **Plan ma uwagi?** Otwórz raport weryfikatora. Nie każda kombinacja zakazów, dań, limitów porcji i makro ma rozwiązanie.
- **Brak AI?** Sprawdź klucz, dostępność usługi, środki i budżet. Podstawowe planowanie nie wymaga AI.
- **Eksport anulowany?** Ponów zapis i wskaż istniejący folder z prawem zapisu. Plik obejmuje wszystkie dni wybranego planu.

Zgłoszenia: [GitHub Issues](https://github.com/krapcys1-maker/dietbot-desktop/issues). Podaj wersję, kroki i treść błędu po usunięciu prywatnych danych. Nigdy nie wysyłaj `.env` ani całego folderu danych.
