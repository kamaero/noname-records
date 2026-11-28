# Звуки маркеров в сборке главы

Команды запускают для одной главы. План и импорт используют окружение приложения. Рендеру нужны Python 3, Node.js 22.12+ и установленный Lumina SFX. Можно перенести план на машину рендера и вернуть каталог результата на сервер приложения.

```sh
python scripts/marker_fx.py plan --chapter CHAPTER_ID --out plan.json
python scripts/marker_fx.py render --plan plan.json --skill /path/to/lumina-sfx --out rendered
python scripts/marker_fx.py import --chapter CHAPTER_ID --manifest rendered/effects.json
python scripts/marker_fx.py import --chapter CHAPTER_ID --manifest rendered/effects.json --apply
```

План содержит активные маркеры типа `sound`. Рендер перебирает их поисковые запросы. Если Lumina требует ручного выбора, ID остаётся в `unresolved`. Можно указать точный идентификатор каталога в поле `recipe` нужного маркера в плане. Сведения об источниках и SHA-256 готового WAV сохраняются при импорте.

После импорта штатная сборка добавляет WAV и ставит клип на FX в начале размеченного абзаца. Если он пропущен, используется следующий записанный абзац. При отсутствии следующего абзаца клип ждёт размещения. Снятый маркер и изменённое описание выводят старый эффект из последующих сборок. Готовый монтаж сохраняется в своей версии.
