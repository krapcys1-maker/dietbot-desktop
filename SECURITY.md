# Bezpieczeństwo

## Zgłaszanie podatności

Nie opisuj luki w publicznym zgłoszeniu. Użyj prywatnego formularza GitHub:
**[Security → Report a vulnerability](https://github.com/krapcys1-maker/dietbot-desktop/security/advisories/new)**.

Podaj wersję Porcji, kroki odtworzenia i możliwy skutek. Nie dołączaj własnego klucza API, pliku `.env` ani prywatnej bazy.

## Wspierane wersje

Poprawki bezpieczeństwa trafiają do najnowszego wydania z [GitHub Releases](https://github.com/krapcys1-maker/dietbot-desktop/releases/latest).

## Jak Porcja chroni dane (i czego nie robi)

- Lokalny serwer aplikacji nasłuchuje wyłącznie na `127.0.0.1`, odrzuca obce nagłówki `Host`, a żądania zmieniające dane przyjmuje tylko z własnej strony (kontrola `Origin`).
- Plany, oceny i ustawienia są na Twoim komputerze. Porcja nie ma konta, telemetrii ani synchronizacji.
- Klucz DeepSeek jest zapisany **jawnym tekstem** w pliku `.env` w katalogu danych (`%LOCALAPPDATA%\DietBot`), a nie w magazynie Windows. Nie udostępniaj tego folderu.
- Przy włączonym AI potrzebny kontekst zadania trafia do DeepSeek; opcjonalny bot Telegram korzysta z usługi Telegram.
- Instalator nie jest podpisany certyfikatem — pobieraj go tylko z oficjalnych wydań i porównuj sumę SHA-256.
