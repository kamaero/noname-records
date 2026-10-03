# Архитектура

Как устроен код. Установка и обслуживание — [README.md](README.md). Полный список
маршрутов, таблиц и переменных — справочники в [`docs/reference/`](docs/reference).

## 1. Процессы

Система запускается через Docker Compose (`docker-compose.yml`), все службы — из одного образа.

| Служба | Что делает |
|---|---|
| `web` | uvicorn `app.main:app`; API, отдача собранного `frontend/dist`, при старте — миграции и фоновые потоки |
| `worker` | RQ, очереди `high`, `default`, `low` (`app/worker.py`) |
| `worker-consilium` | RQ, очередь `consilium` — долгие прогоны консилиума не держат основную очередь |
| `redis` | брокер очередей RQ и хранилище лимитов запросов |
| `scheduler` | `scripts/scheduler_loop.py`: сроки проб и ролей, вежливые отказы, сводки бота |
| `caddy` | только на сервере с доменом (профиль `https`): HTTPS через Let's Encrypt |

База и записи лежат в папке `data/` рядом с `docker-compose.yml`.

### Фоновые потоки веб-процесса

Стартуют в `startup` (`app/api/system_routes.py`), учитываются строками в
`background_runs`:

- **сторож зеркала** (`nas_probe_loop`) — раз в `NAS_PROBE_INTERVAL_SECONDS` пишет пробу
  на NAS и отвечает тремя состояниями: жив, мёртв, неизвестно;
- **зеркало** (`mirror_loop`, `app/workers/mirror.py`) — раз в
  `MIRROR_INTERVAL_MINUTES` копирует новые файлы на NAS, исполняет поручения стереть
  копии удалённых записей и кладёт готовые сессии глав в архив;

Потоки живут в единственном процессе uvicorn: тяжёлую синхронную работу в обработчиках
запросов делать нельзя — сайт замирает (так уже было со сметой консилиума).

### Очереди и задачи

Задачи ставятся через `app/workers/launcher.enqueue_tracked_task` — каждая получает
строку `background_runs` (вид задачи, сущность, сердцебиение, статус).

| Очередь | Задача (`app/worker_tasks.py`) | `job_kind` |
|---|---|---|
| `high` | `perform_v2_pipeline_task` — прогон книги | `v2_pipeline` |
| `high` | `perform_asr_chapter_task` — распознавание и сверка дублей главы | `asr_chapter` |
| `high` | `perform_realign_chapter_task` — пересчёт сверки после правки роли | `asr_realign_chapter` |
| `high` | `perform_verify_chapter_task` — сверка md5 файлов собранной главы | `verify_chapter` |
| `consilium` | `perform_consilium_task` — прогон консилиума | `consilium` |
| `consilium` | `perform_sound_task` — звуковая разметка | `sound` |
| `consilium` | `perform_ambient_task` — генерация эмбиента | `ambient` |

Очередь `low` слушается, но задач в неё сейчас никто не ставит. Консилиум, звук и
эмбиент вынесены в свою службу, потому что прогон по книге идёт часами и держал бы
распознавание дублей.

Воркер не возобновляет прерванные задачи при старте: прогон v2, убитый рестартом,
остаётся как записан, и его запускают заново из хаба (готовые главы пропускаются).
Поэтому обновлять систему (`update.sh`) лучше, когда прогонов нет.

## 2. Путь книги

```
загрузка файла ──► script_books + script_chapters (текст главы в source_text)
     │
     ▼  прогон v2 (очередь high)
сегментация ──► персонажи ──► роли ──► ударения
v2_segments     characters    v2_attributions   v2_stress_marks
     │
     ▼  status = author_review
проверка в Читалке (правки = новые версии строк), консилиум, каст и смета
     │
     ▼  публикация главы (chapter.status = published, book = published_to_dictor)
запись: дубли → audio_files (локальный диск) ──► зеркало (если задан AUDIO_NAS_PATH)
     │
     ▼  после каждого дубля (очередь high)
ASR: asr_jobs (heard_json + alignment_json) ──► письма о пропусках
     │
     ▼  у каждой говорящей роли есть дубль
сборка главы: архив + .sesx (+ маркеры звука, + эмбиент) ──► verify_chapter
```

### Разметка (`app/v2/`)

- **Сегментация** (`segmenter.py`) режет главу на абзацы со смещениями в исходном
  тексте. Сегменты не меняются; всё остальное — слои поверх них.
- **Персонажи** — один вызов модели по всей книге (`_run_char_extraction`, наследие
  v1), только если персонажей ещё нет.
- **Роли** (`attribute.py`, `run.py`) — модель получает пачку сегментов с легендой
  каста главы и возвращает, кто говорит в каких кусках, строгим JSON по схеме; второй
  проход (`llm_review`) пересматривает слабые места. Кусок, который модель не назвала
  уверенно, остаётся UNSURE и попадает в «Спорные».
- **Ударения** (`stress.py`) — цепочка источников: текст → автор → «ё» → словари →
  Викисловарь (`stress_forms.py`) → RUAccent (`stress_ruaccent.py`). Каждый слой видит
  только то, что оставили предыдущие.
- **Версионирование.** Исправление не перезаписывает строку, а добавляет новую версию
  атрибуции или ударения с источником (`operator`, `import`, `llm_remarks`…).
  Действующая версия — последняя по сегменту. Журнал действий человека — в
  `operator_interventions` и `script_logs`.
- **Читалка** получает главу одним запросом `GET /api/v2/chapters/{id}/script`
  (`reader.py`): сегменты, действующие куски, ударения, каст с цветами, флаги
  `can_edit` / `can_voice`.

### Запись и хранение

- Приём (`app/services/audio_uploads.py`, `app/api/dictor_uploads.py`) пишет поток на
  локальный диск, по дороге считая md5; NFS в пути приёма нет. Ключ хранения:
  `КНИГА/ChapterNN/имя`, пробы — `КНИГА/Auditions/`.
- Два корня: `AUDIO_STORAGE_PATH` (локальный) и `AUDIO_NAS_PATH` (зеркало). Они не
  должны лежать один в другом — цикл зеркала это проверяет и останавливается. Корень
  зеркала сам не создаётся: пустой каталог на месте точки монтирования — признак
  отвалившегося NAS.
- Строки `audio_files` помнят местоположение, суммы приёма и зеркала, вердикт сверки;
  `kind` отличает дубль, пробу и эмбиент. Удаление записи оставляет поручение
  зеркалу в `pending_mirror_deletions`.

### ASR и сессии

- `asr_run.py` сжимает файл, отдаёт распознавателю (`ASR_PROVIDER`: OpenAI-совместимый
  API — OpenAI или RouterAI, либо Azure), кладёт сегменты со словными таймингами в
  `asr_jobs.heard_json`, а итог сверки (`asr_align.py`) — в `alignment_json`.
  Распознавание — единственный платный и невоспроизводимый шаг; сверка своя и
  бесплатная, поэтому её можно переиграть по архиву (`asr_replay.py`).
- `asr_borrow.py` находит звук реплики, перенесённой в соседнюю роль того же актёра.
- `chapter_delivery.py` решает, собрана ли глава, собирает архив и `.sesx`
  (`audition_session.py`), помечает устаревшие сессии. Отпечаток сессии считается без
  случайных GUID, иначе он менялся бы на каждой сборке.

### Консилиум, звук, эмбиент

Три прогона одного устройства (`app/services/consilium_engine.py`, `sound_engine.py`,
`ambient_engine.py`): смета до запуска, проверка баланса RouterAI, ход в
`background_runs`, остановка, предохранитель по деньгам, артефакты ответов моделей в
`data/book_reports/<book>/consilium|sound/` с отпечатком текста — повторный прогон
не платит за неизменившиеся главы. Правки человека (решённая находка, маркер с
`source='human'`) главнее повторного прогона.

## 3. Хранилища

| Что | Где |
|---|---|
| Основная база | SQLite `data/noname.db` (`DATABASE_URL`), режим WAL |
| Схема | Alembic, `alembic/versions/` (39 ревизий); `upgrade head` при старте веба |
| Записи дикторов | локальный диск `AUDIO_STORAGE_PATH` → зеркало `AUDIO_NAS_PATH` (необязательно) |
| Таблица словоформ | `data/stress_forms.sqlite` (Викисловарь, необязательно; собирается `scripts/v2/build_stress_forms.py` из открытой выгрузки Wiktextract, CC BY-SA) |
| Канон автора | `data/canon_kb.sqlite` (роли и имена мира автора, эмбеддинги; необязательно) |
| Артефакты прогонов | `data/book_reports/<book>/…` |
| Резервные копии перед операциями | `data/pre-*.db` |

Резервная копия — `backup.sh` / `backup.ps1`: архив `data/` и `.env` в папку `backups/`
(перед обновлением делается сама). Хранить копии вне сервера — забота студии.

## 4. Модели

Единый клиент — `app/pipeline/llm_client.py` (`call_chat`): строгий JSON по схеме,
потоковые ответы, повторы на сетевые сбои и 429/5xx, дедлайны на тишину и на весь
поток. Провайдер выбирается по имени (`_resolve_provider`):

| Имя | Протокол | Ключ |
|---|---|---|
| `deepseek` | Anthropic Messages через `api.deepseek.com/anthropic` | `DEEPSEEK_API_KEY` |
| `routerai` | OpenAI-совместимый `routerai.ru/api/v1` | `ROUTERAI_API_KEY` |
| `claude` | Anthropic напрямую | `CLAUDE_API_KEY` |
| `openai`, `openrouter`, `zai` | OpenAI-совместимые, запасные | свои ключи |

Кто на чём работает:

| Задача | Модель | Откуда выбор |
|---|---|---|
| Извлечение персонажей | `deepseek-v4-pro` | `DEFAULT_CHAR_EXTRACTION_PROVIDER/MODEL` |
| Атрибуция реплик | DeepSeek V4 Pro (по умолчанию) или Muse Spark 1.3 Contributor через RouterAI | на книге (`script_books.llm_provider/llm_model`), каталог `app/v2/model_catalog.py`; запасной — `DEFAULT_FINAL_*` |
| Ударения | без LLM: словари, Викисловарь, RUAccent | — |
| Консилиум: чтецы | `anthropic/claude-opus-5` и `openai/gpt-5.6-sol` через RouterAI | `consilium_engine.READERS` |
| Консилиум: арбитр | `anthropic/claude-opus-5` через RouterAI | `consilium_engine.ARBITER_MODEL` |
| Звуковая разметка, промпты эмбиента | `anthropic/claude-opus-5` через RouterAI | `sound_engine.MODEL`, `ambient_engine.MODEL` |
| Музыка эмбиента | ElevenLabs Music `/v1/music` | `ELEVENLABS_API_KEY` |
| Распознавание речи | по `ASR_MODEL` | `ASR_PROVIDER` (`openai`, `routerai`, `azure`) |

Модель прогона фиксируется в момент запуска: смена `.env` или модели книги действует
со следующего прогона. Баланс RouterAI общий для
консилиума, звука и ASR; прогоны читают его (`routerai_credits.py`) до старта и по ходу.

## 5. Доступ

Сессия — подписанная cookie; вход по паролю или виджетом Telegram (одна учётка на
человека, `telegram_auth_accounts` привязывает Telegram к ней). Роли (`app/auth.py`,
`app/constants.py`):

- `admin` — всё;
- `author` — разметка и прогоны, публикация, каст, профиль автора, звук, консилиум;
- `dictor` — опубликованные главы, свои роли, ударения и палитра (право `can_voice`),
  загрузка своих записей;
- `agent` — кастинг-директор: видит всё, кроме подготовки, предлагает актёров
  (всегда предварительно), грузит записи за актёров, ничего не удаляет.

В API v2 две проверки: `_can_edit` (admin, author) и `_can_voice` (плюс dictor).
Обработчики старых модулей `app/api/` получают зависимости через реестр
(`app/handler_registry.py`): ключ зависимости объявляется и у поставщика
(`app/handler_dependency_runtime.py`), и в контракте обработчика
(`app/handler_factory_contracts.py`). Реестр ленивый — пропуск всплывёт только на
первом запросе; ловит его `tests/test_handler_dependency_contracts.py`. Ручки v2
подключены в `app/main.py` напрямую, минуя реестр.

## 6. Ключевые таблицы

| Таблица | Смысл |
|---|---|
| `script_books`, `script_chapters` | книга (статус, автор, модель разметки) и глава (исходный текст, статус публикации, отметки сборки и отчётов) |
| `v2_segments`, `v2_attributions`, `v2_stress_marks`, `v2_stress_skips`, `v2_runs` | разметка v2 и её прогоны |
| `characters` | роли книги: имя, актёр, цвета, возраст, главы |
| `role_votes`, `role_pair_notes` | голоса за актёра; решения по пересечениям ролей |
| `book_budget`, `character_budget_snapshot`, `studio_settings` | смета и студийные настройки (ставка, курс) |
| `authors`, `author_characters`, `author_pronunciations` | профиль автора: каст и ударения, общие для его книг |
| `dictor_profiles`, `dictor_demos`, `dictor_links` | карточка диктора, демо и ссылки-портфолио; данные для будущего раздела «Дикторы» |
| `dictor_assignments`, `recasts` | пересчитываемый агрегат назначений и журнал рекастов по циклу книг |
| `audio_files`, `pending_mirror_deletions`, `recording_progress` | записи, поручения зеркалу, прогресс записи |
| `asr_jobs` | распознавание дубля: услышанное и сверка |
| `consilium_findings` | находки консилиума и их решения |
| `sound_places`, `sound_place_pairs`, `sound_markers`, `ambient_tracks` | звуковая разметка и эмбиент |
| `users`, `user_roles`, `telegram_auth_accounts` | учётки, роли, вход через Telegram, отметка онбординга |
| `background_runs` | все фоновые задачи и потоки: статус, сердцебиение |
| `script_logs`, `audit_log`, `operator_interventions`, `llm_usage_logs` | журналы: книга, безопасность, действия человека, расход моделей |
| `script_jobs`, `pipeline_runs`, `pipeline_events` | задача извлечения персонажей и журнал прогонов; остальные таблицы v1 удалены миграцией 0038 |

## 7. Фронт

React 18 + TypeScript + Vite, `react-router-dom` с базой `/app/`, данные через
`@tanstack/react-query`. Разделы в шапке: Библиотека, Подготовка (Читалка), Запись,
ASR; справа — Пользователи и Лог (только admin), Помощь. Хаб книги
`/app/books/:id`, каст `/app/books/:id/cast`, Читалка `/app/reader/:chapterId`,
страница роли `/app/recording/role/:bookId`. Раздела `/app/dictors` пока нет:
таблицы и импорт базы голосов подготовлены, экран остаётся в плане. Старые адреса
v1 редиректят.

Код: `frontend/src/v2/` — Читалка и всё вокруг неё; `components/book/v2/` — хаб;
`components/cast/` — каст; `ui/` — общий словарь компонентов; `viewModels/` — чистые
функции, из которых экраны берут решения. Дизайн-система — [DESIGN.md](DESIGN.md).
Веб отдаёт `frontend/dist` с диска: точка входа перепроверяется браузером, файлы с
хешем в имени кэшируются надолго.

## 8. Направления

Открытые архитектурные вопросы:

- **Рассказчик от первого лица** — роль Рассказчика как персонажа книги.
