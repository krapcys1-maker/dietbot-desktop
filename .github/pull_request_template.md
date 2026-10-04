## Co się zmienia

<!-- Krótko, z perspektywy użytkownika Porcji. -->

## Jak sprawdzono

- [ ] `python -m ruff check src tests`
- [ ] `python -m pytest`
- [ ] `dietbot dane sprawdz`
- [ ] `npm run build` w folderze `web` (jeśli zmienia się interfejs)
- [ ] Ręczna próba w aplikacji (jeśli zmienia się coś widocznego)

## Dane i prywatność

- [ ] Brak kluczy API, `.env`, baz SQLite i prywatnych eksportów w zmianach.
- [ ] Nowe produkty mają źródło wartości; nowe dane są w nowych plikach YAML.
