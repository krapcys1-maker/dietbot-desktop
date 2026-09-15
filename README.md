# DietBot — jadłospis na tydzień dla dwóch osób

Aplikacja układa **tygodniowy jadłospis dla Kobiety i Mężczyzny**: to samo danie, różne gramatury.
Model językowy (DeepSeek) wybiera dania tak, żeby tydzień był smaczny i praktyczny, a **wszystkie liczby liczy
deterministyczny silnik** (OR-Tools CP-SAT) i sprawdza je **niezależny weryfikator**. Wszystko po polsku, z listą
zakupów podpowiadającą produkty z półek Lidla (i Kauflandu) w Rumunii.

> **Status projektu:** działająca aplikacja lokalna w wersji 0.1.0. Nie jest wyrobem medycznym ani zamiennikiem
> porady lekarza lub dietetyka. Przed użyciem sprawdź cele żywieniowe i wygenerowany plan pod kątem swoich potrzeb.

## Szybki start ze źródeł (Windows)

Wymagania: Python 3.12, Node.js z npm oraz Microsoft Edge WebView2 Runtime (standardowo obecny w Windows 10/11).

```powershell
git clone https://github.com/krapcys1-maker/dietbot-desktop.git
cd dietbot-desktop
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,app,build]"
cd web
npm ci
npm run build
cd ..
Copy-Item .env.example .env
.\.venv\Scripts\dietbot.exe aplikacja
```

Gotowy instalator Windows nie jest jeszcze publikowany. Lokalną paczkę aplikacji można zbudować poleceniem:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\zbuduj.ps1
```

Wynikiem jest **cały folder** `dist\DietBot`. Nie kopiuj samego `DietBot.exe` — potrzebuje on plików z
`dist\DietBot\_internal` oraz katalogów `dane` i `konfiguracja`.

## Własny klucz API DeepSeek

Każdy użytkownik korzysta ze swojego klucza. Po skopiowaniu `.env.example` do `.env` wpisz:

```dotenv
DEEPSEEK_API_KEY=tu_wpisz_wlasny_klucz
```

Plik `.env` jest ignorowany przez Git. Nie dodawaj go do commitów, nie wysyłaj razem z paczką i nie wklejaj
klucza do zgłoszeń na GitHubie. Bez klucza aplikacja nadal potrafi ułożyć plan algorytmem lokalnym, ale czat,
rozumienie życzeń i wymyślanie nowych dań są wyłączone. Klucz można utworzyć w
[panelu DeepSeek](https://platform.deepseek.com/api_keys).

## Uruchamianie

| Sposób | Jak |
|---|---|
| **Okno aplikacji** (zalecane) | `.venv\Scripts\dietbot aplikacja` albo, po zbudowaniu paczki, `dist\DietBot\DietBot.exe` |
| Z terminala, w oknie | `.venv\Scripts\dietbot aplikacja` |
| W przeglądarce | `.venv\Scripts\dietbot serwer` → otwiera `http://127.0.0.1:8765` |
| Tylko bot Telegram | `.venv\Scripts\dietbot telegram` |
| Plan z terminala | `.venv\Scripts\dietbot plan generuj --zyczenia "więcej ryb"` |

Aplikacja działa lokalnie (serwer tylko na `127.0.0.1`, okno na porcie 8765 — gdy zajęty, na dowolnym wolnym).
Plany zapisują się w `wyniki\dietbot.sqlite3`, eksporty Markdown w `wyniki\`, dziennik błędów w `wyniki\dietbot.log`.

## Co jest w aplikacji

- **Tydzień** — dni z posiłkami, gramatury dla obu osób z miarami domowymi („≈ ¾ łyżki”, „2 kromki”),
  pod każdym dniem suma (każdy posiłek, przekąski i stałe pozycje → razem dzień na tle widełek, sprawdzona
  z weryfikatorem), na ostatnim dniu podsumowanie tygodnia (razem, średnio dziennie, dni w widełkach),
  przekąski bilansujące z powodem, przygotowanie, gotowanie na zapas
  („gotujesz raz, na wszystkie porcje”). Przycisk **Zamień** przelicza tylko dotknięte dni — reszta tygodnia
  zostaje co do grama. Każda zmiana tworzy nową wersję planu (można wrócić).
- **Nowy plan** — życzenia słowami („kuchnia włoska 2 razy, zupa, resztę wymyśl”, „bez ryb, w piątek pizza
  z pizzerii”), postęp na żywo. Konkretne dania z życzeń („w 2 dni zupa pomidorowa, w piątek schabowy z piersi
  kurczaka z ziemniakami i surówką”) program znajduje w bibliotece, a gdy ich nie ma — wymyśla, i przypina do
  wskazanego dnia. Przekąski („w sobotę lody”, „popcorn do filmu”) trafiają do tego dnia jako przekąska z puli 20%. Opcja **Wymyśl nowe dania do tego tygodnia**: model wymyśla przepisy, kod, silnik
  i kucharz je sprawdzają, a nowe dania trafiają do biblioteki i od razu do planu. Liczby z życzeń („2 razy”) są
  pilnowane dokładnie.
- **Zakupy** — lista z odhaczaniem, liczbą opakowań i nazwami z rumuńskich półek, kopiowanie do schowka, druk.
  Jedzenie z pizzerii czy budki trafia do osobnej grupy „Na wynos — do zamówienia” (z liczbą kawałków).
- **Czat** — „ile białka ma żona w środę?”, „zamień czwartkową kolację na coś z kurczakiem”,
  „wymyśl mi naleśniki z serem na kolację”, „zmniejsz mi kalorie o 100” (zmiany założeń zawsze wymagają
  kliknięcia **Zatwierdź**).
- **Dania** — biblioteka ponad 650 dań z 13 kuchni (182 śniadania, 232 obiady i 38 zup na zapas, 193 kolacje,
  12 przekąsek z puli 20%: lody, chipsy, popcorn, baton, sernik…;
  także jedzenie na wynos: pizza z pizzerii, kebab, burger, sushi), przycisk **Wymyśl nowe dania**, zatwierdzanie
  i odrzucanie, stałe proporcje mas przy przepisie.
- **Produkty** — wartości na 100 g ze źródłem (BLS 4.0, Open Food Facts, etykiety), dodawanie produktu z etykiety.
- **Założenia** — cele i widełki, białko na posiłek, limity masy talerza, przekąski, zasady tygodnia, zakazy, kuchnie.
- **Ustawienia** — budżet modelu językowego, stan danych, instrukcja Telegrama.

## Zasady, których pilnuje program

Z pliku `konfiguracja/zalozenia.yaml` (edycja w aplikacji albo w pliku):

- dzienne widełki kcal / białko / tłuszcz / węglowodany dla każdej osoby (twarde),
- białko na posiłek: Kobieta 30–40 g, Mężczyzna 45–55 g (± tolerancja, posiłki z puli 20% zwolnione),
- 3 posiłki + najwyżej 1 przekąska bilansująca na osobę dziennie, tylko gdy potrzebna (szejk białkowy WPC,
  serek wiejski, skyr, orzechy, migdały, jabłko, banan),
- stałe pozycje: mleko do kawy, kawa, siemię lniane,
- 2–3 rzeczy z puli 20% w tygodniu (posiłki jak pizza albo przekąski jak lody, chipsy, popcorn; najwyżej
  jedna dziennie), najwyżej 2 słodkie kolacje,
- zupa na zapas we wtorek i środę, bez powtórek dań, najwyżej jeden posiłek z jajkami dziennie,
- kolacja lżejsza niż obiad i bez „obiadowych” węglowodanów (ryż, ziemniaki, makaron; słodka kolacja jak ryż
  na mleku z owocami może je mieć),
- zakazy (rukola, płatki owsiane, słodkie śniadania i obiady), dozwolone źródła węglowodanów,
- limit masy talerza (np. żeby zupa nie urosła do 2 kg),
- **proporcje przepisu**: ciasto naleśnikowe, panierka, masa mielona czy surówka to „grupa” — silnik może zrobić
  jej więcej albo mniej, ale udział każdego składnika zostaje jak w przepisie (±5% i 1 g na zaokrąglenie),
- bez powtarzania dań z ostatnich 3 tygodni (dopóki w każdej puli zostaje co najmniej 40 dań),
- **bilans makro dnia**: dania jednego dnia razem mają mieć rozkład energii zbliżony do celów (ok. 25% białka,
  30% tłuszczu, 45% węglowodanów) — dzień z trzech tłustych dań nie zmieściłby się w celach bez okrajania porcji.

Gdy czegoś nie da się spełnić, plan dostaje status z uwagami i dokładny opis problemu — program nie udaje,
że jest dobrze.

## Jak powstaje tydzień (w skrócie)

1. Model językowy wybiera dania z banku (albo najpierw wymyśla nowe) — kod pilnuje zasad tygodnia i życzeń,
   a w dniach o złym rozkładzie makro wymienia jedno danie na podobne, lepiej zbalansowane.
2. Silnik (OR-Tools CP-SAT) liczy gramatury obu osób w widełkach, kotwicach białka, limitach talerza i proporcjach mas.
3. Dni, które się nie mieszczą, naprawia zamiana dania sprawdzona silnikiem.
4. Niezależny weryfikator przelicza wszystko od zera.
5. **Kontrola kucharza**: model czyta gotowe gramatury jak szef kuchni („30 g sosu sojowego to za dużo”); poważna
   uwaga zawęża ten składnik i silnik liczy dzień od nowa — nowa wersja przechodzi przez weryfikator.

## Model językowy i koszty

Klucz trzymamy **wyłącznie lokalnie** w pliku `.env` (`DEEPSEEK_API_KEY=…`). Model, ceny i budżet są w
`konfiguracja/llm.yaml` (budżet 5 $, rejestr wydatków `wyniki/llm_koszty.jsonl`). Typowy nowy plan kosztuje
ok. 0,02–0,03 $, z wymyślaniem nowych dań ok. 0,05 $. Bez klucza aplikacja działa dalej: tydzień układa algorytm,
czat i wymyślanie dań są wyłączone.

## Telegram (opcjonalnie)

1. W Telegramie napisz do **@BotFather** `/newbot` i skopiuj token.
2. W `.env` dopisz `TELEGRAM_BOT_TOKEN=…`.
3. Uruchom aplikację, napisz do bota `/start` — odpowie Twoim numerem ID.
4. W `.env` dopisz `TELEGRAM_ALLOWED_USERS=Twoje_ID,ID_żony` i uruchom aplikację ponownie.

Komendy: `/dzis`, `/jutro`, `/tydzien`, `/zakupy`, `/nowyplan życzenia`; zwykłe wiadomości trafiają do czatu.
Bot odpowiada tylko osobom z listy.

## Dane

| Folder | Zawartość |
|---|---|
| `dane/produkty/` | produkty z wartościami na 100 g i źródłem; `wlasne.yaml` — dodane z etykiety |
| `dane/dania/` | biblioteka dań (składniki z rolami, typowymi porcjami i grupami proporcji, przygotowanie); `gen_*.yaml` — dania wymyślone przez model |
| `dane/sklepy/` | produkty z półek w Rumunii (Lidl RO, Kaufland RO), opakowania |
| `konfiguracja/` | `zalozenia.yaml` (cele i zasady), `llm.yaml` (model, budżet) |
| `wyniki/` | plany (SQLite), eksporty, koszty LLM, dzienniki — poza gitem; `wyniki/cache/` to pamięć podręczna sparsowanych plików YAML (start w 0,3 s zamiast 3 s; można ją usunąć) |

Więcej: [docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md) i [docs/RESEARCH.md](docs/RESEARCH.md).

## Dla programisty

```bash
python -m venv .venv
.venv\Scripts\pip install -e .[dev,app]
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check src tests
cd web && npm install && npm run build
powershell -File packaging\zbuduj.ps1
```

Przydatne: `dietbot dane sprawdz` (spójność danych), `dietbot dane szukaj "twaróg półtłusty"` (dopasowanie nazwy),
`dietbot dane audyt-dan` (test silnika dla każdego dania: stałe zestawy towarzyszy i 10 losowych, zbilansowanych
dni; `--wycofaj` odrzuca dania, z którymi nie da się ułożyć poprawnego dnia), `dietbot dane wymysl-dania "pięć obiadów kuchni meksykańskiej"` albo `--plik zamowienia.yaml`
(rozbudowa biblioteki), `dietbot dane przelicz-przyklady` (przelicza jadłospisy z folderu `diety`),
`dietbot llm koszty`.
