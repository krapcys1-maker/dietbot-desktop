# Porcja 0.3.0 — dobrze mieć plan

DietBot zmienia się w **Porcję**: bezpłatną aplikację Windows po polsku do planowania posiłków. Podstawowy planer działa lokalnie, bez konta i bez klucza AI.

## Pobierz i uruchom

**[Pobierz instalator Porcja-Setup-0.3.0.exe](https://github.com/krapcys1-maker/dietbot-desktop/releases/download/v0.3.0/Porcja-Setup-0.3.0.exe)**

Windows 10/11 x64, WebView2. Nie trzeba instalować Pythona ani Node.js. Uruchom instalator, potem **Porcję** z menu Start. Dla aktualizacji zamknij poprzednie okna; techniczny katalog DietBota pozostaje bez zmian, aby zachować istniejące dane. Przed aktualizacją warto zrobić kopię folderu danych.

Nie wybieraj „Source code”, jeśli chcesz po prostu korzystać z programu. Nie przenoś samego `DietBot.exe` bez jego zależności.

## Co nowego względem publicznej wersji 0.1.1

- Nowa oprawa, logo, pulpit, jasny i ciemny motyw oraz przewodnik.
- **Eksportuj dietę** i **Eksportuj listę zakupów** — cały wybrany plan do TXT z wyborem folderu.
- **Moje diety**: nazwane tygodnie, historia i wersje.
- Oceny dań: lubiane częściej, nielubiane wykluczone z nowych planów i zamian.
- Prostsza lista zakupów: połączone warianty, bez sklepów, marek, przypraw i ziół; odhaczanie ilości.
- Diety tradycyjna, wege i wegan; dodatkowe 34 przepisy roślinne.
- Opcjonalny kalkulator celów oraz filtry biblioteki.
- Kcal z tabel produktów, osobne sumy makro i kopie danych źródłowych dla nowych planów.
- Opcjonalny własny klucz DeepSeek w Ustawieniach. Opłaty za API rozlicza dostawca.

## Prezentacja po polsku

[Film MP4 — 44 sekundy, Full HD, polskie podpisy](https://github.com/krapcys1-maker/dietbot-desktop/releases/download/v0.3.0/Porcja-demo-pl.mp4)

Montaż prawdziwych ekranów na osobnym profilu demonstracyjnym, bez dźwięku, prywatnych diet i kluczy. Animowany podgląd i zdjęcia interfejsu znajdują się w [README](https://github.com/krapcys1-maker/dietbot-desktop#readme).

[Instrukcja użytkownika](https://github.com/krapcys1-maker/dietbot-desktop/blob/v0.3.0/docs/INSTRUKCJA.md) · [Pełna historia zmian](https://github.com/krapcys1-maker/dietbot-desktop/blob/v0.3.0/CHANGELOG.md)

## Kontrola wydania

- 260 testów zakończonych powodzeniem i poprawna kompilacja interfejsu.
- Gotowy instalator uruchomiony lokalnie; oba eksporty sprawdzone z EXE.
- Osobny przykładowy tydzień wygenerowany bez AI, bez naruszeń zgłoszonych przez weryfikator.
- Kontrola sekretów w plikach i historii Git: brak trafień użytych reguł oraz lokalnego klucza. Paczka bez `.env` i prywatnej bazy SQLite.
- SHA-256 instalatora i filmu w załączonym `SHA256SUMS.txt`.

## Ważne ograniczenia

Instalator nie jest podpisany certyfikatem. Sprawdź pochodzenie oraz SHA-256 pobranego pliku; nie wyłączaj zabezpieczeń Windows. Klucz API jest lokalnie zapisany jawnym tekstem w `.env`. Nie udostępniaj folderu danych.

Porcja nie jest wyrobem medycznym. Zgodność z tabelami i regułami nie gwarantuje dokładności konkretnego produktu sklepowego ani odpowiedniości diety dla zdrowia. Cele startowe są przykładem, kalkulator orientacyjny, a mikroelementy i alergeny nie są w pełni bilansowane. Nie wykonywano płatnych wywołań DeepSeek podczas odbioru tego wydania.
