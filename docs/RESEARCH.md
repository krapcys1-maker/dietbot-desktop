# DietBot — research (13.09.2026)

Notatki z przeglądu gotowych rozwiązań, tabel składu żywności, danych sklepowych i wyboru modelu.
Decyzje z tego dokumentu opisuje [ARCHITEKTURA.md](ARCHITEKTURA.md).

## 1. Gotowe projekty

| Projekt | Co robi | Dlaczego nie wystarcza / co bierzemy |
|---|---|---|
| Mealie, Tandoor Recipes, wger | menedżery przepisów i planów posiłków | brak optymalizacji gramatur pod cele makro dla dwóch osób; z Tandoora warto znać otwarte dane (`TandoorRecipes/open-tandoor-data`) |
| małe optymalizatory makro (np. „macro-solver”) | dobór ilości pod cele jednej osoby | bierzemy wzorce: **niezależny weryfikator** z testami „czy wykryje błąd” i jawne statusy niewykonalności |
| „meal-planner”, „llmn” i podobne | LLM generuje jadłospis z liczbami | pułapki do ominięcia: brak seeda, zaokrąglanie po fakcie, ciche limity zmiennych, liczby od LLM |

Wniosek: budujemy własny rdzeń (silnik + weryfikator + planer), korzystając ze sprawdzonych bibliotek
(OR-Tools, Pydantic, FastAPI, aiogram).

## 2. LLM a liczenie kalorii

- NutriBench (arXiv 2407.12843), polskie badanie 3(LM)Diet (jadłospisy od modeli 284–546 kcal poniżej celu)
  i przegląd w „Nutrients” (2025) pokazują duże błędy modeli językowych przy liczeniu kalorii i makroskładników.
- „Agentic Numeric Optimization for Nutrition Planning” (2026) — model wybiera, solver OR-Tools liczy, porażka
  wraca do modelu; trafienia w cele wyraźnie rosną. To jest układ DietBota.
- arXiv 2605.13849 (MIGP) — gramatury całkowite trzeba liczyć w modelu optymalizacyjnym; zaokrąglanie
  wyniku ciągłego po fakcie często psuje cele. Silnik liczy kroki gramatur jako zmienne całkowite.

## 3. Tabele składu żywności

| Baza | Licencja / dostęp | Decyzja |
|---|---|---|
| **BLS 4.0** (Bundeslebensmittelschlüssel, Max Rubner-Institut) — `blsdb.de/download` | CC BY 4.0, pełny plik XLSX (w naszym indeksie 7140 produktów) | **główne źródło** (kwasy organiczne i poliole do kontroli energii) |
| **CIQUAL 2025** (ANSES, doi 10.57745/RDMHWY) | otwarte dane | kontrola krzyżowa wybranych produktów |
| Tabele IŻŻ / NIZP-PZH (Polska) | publikacja książkowa (PZWL), brak otwartej licencji na dane | tylko porównanie typowych wartości, nie źródło |
| Fineli (Finlandia), Livsmedelsverket (Szwecja), Matvaretabellen (Norwegia) | otwarte | zapas; słabiej pasują do produktów z Europy Środkowej |
| NEVO (Holandia), CoFID (Wielka Brytania), BEDCA (Hiszpania), BDA (Włochy), naehrwertdaten.ch | dostęp na wniosek lub warunki licencyjne | nie używamy |
| EuroFIR | płatny dostęp | nie używamy |
| Tabele rumuńskie (FAO INFOODS) | brak aktualnej otwartej bazy | nie używamy |
| **Open Food Facts** | ODbL; wyszukiwarka Search-a-licious działa bez konta | **produkty markowe z Rumunii** (np. brânză de vaci Pilos) |
| **USDA FoodData Central** | CC0 | **wagi sztuk i miar** (np. jabłko 182 g, cebula 110 g) |
| Edamam, FatSecret | komercyjne API | nie używamy |

Ustalenia techniczne:
- Węglowodany w BLS/CIQUAL to węglowodany przyswajalne (definicja UE) — tak samo liczy aplikacja.
- Energia na etykietach UE (rozporządzenie 1169/2011): białko i węglowodany 4, tłuszcz 9, błonnik 2, kwasy
  organiczne 3, poliole 2,4, alkohol 7 kcal/g. Cele użytkownika są liczone 4/9/4, więc aplikacja domyślnie liczy
  tak samo, a współczynników UE używa do wykrywania błędów w danych.
- Niemiecki **Quark** ≠ polski **twaróg**: w Rumunii najbliżej twarogu jest brânză de vaci (Pilos 4,2%: 17 g białka).

## 4. Miary domowe

- Rozporządzenie (WE) 589/2008 i wytyczne UE dotyczące tolerancji wartości odżywczych na etykietach.
- USDA SR Legacy / FoodData Central — masy sztuk i porcji (np. jajko, banan, pomidor, ziemniak, cebula).
- NHS — porcja orzechów („garść” ≈ 30 g).
- Polska tradycja miar (łyżka, łyżeczka, szklanka 250 ml) — przeliczane gęstością produktu.

## 5. Sklepy w Rumunii

- **Monitorul Prețurilor** (monitorulpreturilor.info, Rada Konkurencji Rumunii) — ceny i nazwy produktów
  z półek sieci (Lidl, Kaufland, Carrefour, Mega Image, Auchan); z tego źródła pochodzą nazwy i opakowania
  w `dane/sklepy/rumunia.yaml`.
- Strony sklepów (lidl.ro, mega-image.ro, freshful.ro, sezamo.ro) i Open Food Facts — potwierdzanie nazw,
  gramatur i wartości z etykiet. Nie logujemy się i nie wysyłamy żadnych danych.
- Wnioski (93 produkty z półek w `dane/sklepy/`):
  - prawie wszystko jest w Lidlu; tylko w Kauflandzie: serek wiejski (cottage), czerwona soczewica, chleb żytni
    i pszenno-żytni, ryż brązowy, pełnotłuste mleczko kokosowe, ogórki kiszone w solance (w Lidlu tylko w occie),
    surowe krewetki;
  - kiełbasy krakowskiej nie ma w żadnej z sieci — zamiennik: kiełbasa śląska (Cârnați Silezia, Pikok),
    wartości prawie identyczne;
  - bulionu w płynie nie ma — kostki (Knorr, Maggi);
  - śmietana 10% → 12% (Pilos), śmietanka 30% → 36% w Lidlu albo 30% w Kauflandzie.

## 6. Model językowy

| Kandydat | Za | Przeciw |
|---|---|---|
| **DeepSeek V4.1 Flash** | bardzo tani (ok. 0,30 $ / 1 mln tokenów wejścia, 1,20 $ wyjścia), dobry polski, tryb JSON | czasem łamie zasady — stąd walidacja i rundy poprawek |
| OpenAI / Claude | wyższa jakość rozumowania | kilkanaście–kilkadziesiąt razy drożej przy tej skali |

Decyzja: DeepSeek V4.1 Flash z twardą walidacją odpowiedzi; dostawcę można zmienić w `konfiguracja/llm.yaml`
(dowolne API zgodne z OpenAI). Rzeczywiste koszty z rejestru: nowy plan 0,005–0,02 $, generowanie całej
biblioteki dań ok. 0,20 $, korekta językowa biblioteki 0,02 $.
