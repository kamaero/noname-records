# Справочник переменных окружения

Получено статическим разбором исходников (`ast`), без выполнения приложения с реальным окружением и без чтения `.env`. Раздел «Settings» — поля `app/config.py:Settings`, читаемые через `os.getenv`. Раздел «Прочее» — `os.getenv`/`os.environ.get` в `app/` и `scripts/` вне `app/config.py`.

Переменные, чьё имя оканчивается на `KEY`/`TOKEN`/`SECRET`/`PASSWORD`, помечены как секретные — для них показано только «задан ли непустой default в коде», само значение default никогда не печатается.

Всего переменных: **90** (Settings: 88, прочее: 2).

_Сгенерировано из коммита d1c9b32._

## Settings (`app/config.py`)

| Переменная | Default | Где читается | Описание |
|---|---|---|---|
| `ADMIN_LOGIN` | `'admin'` | `app/config.py:83` |  |
| `ADMIN_PASSWORD_HASH` | `''` | `app/config.py:84` |  |
| `API_MAX_REQUEST_BYTES` | `str(100 * 1024 * 1024)` | `app/config.py:203` |  |
| `API_RATE_LIMIT_DEFAULT` | `'240/minute'` | `app/config.py:201` |  |
| `API_RATE_LIMIT_ENABLED` | `'true'` | `app/config.py:200` |  |
| `API_RATE_LIMIT_STORAGE_URI` | `''` | `app/config.py:202` |  |
| `APP_BASE_URL` | `'http://localhost:8080'` | `app/config.py:79` |  |
| `APP_NAME` | `'Noname Records'` | `app/config.py:72` |  |
| `ASR_AUDIT_CONFIRM_MODEL` | `'microsoft/mai-transcribe-2'` | `app/config.py:124` |  |
| `ASR_AUDIT_ENABLED` | `'0'` | `app/config.py:122` | Публичная установка не должна начинать платные проверки без решения владельца. |
| `ASR_AUDIT_MODEL` | `'elevenlabs/scribe-v2'` | `app/config.py:123` |  |
| `ASR_LANGUAGE` | `'ru'` | `app/config.py:119` |  |
| `ASR_MODEL` | `'whisper-1'` | `app/config.py:118` |  |
| `ASR_PROMPT` | `''` | `app/config.py:120` |  |
| `ASR_PROVIDER` | `'openai'` | `app/config.py:117` | Распознавание речи. Раньше эти переменные стояли в .env и не читались никем. |
| `AUDIO_NAS_PATH` | `''` | `app/config.py:107` | Зеркало записей на второй диск или NAS: тот же ключ, другой корень. Пусто — зеркалирование выключено, приём при этом работает. |
| `AUDIO_STORAGE_PATH` |  | `app/config.py:97` |  |
| `AUTH_RATE_LIMIT` | `'10/minute'` | `app/config.py:209` | Отдельный строгий лимит на эндпоинты входа (защита от перебора). Не влияет на длительность уже выданной сессии — только на частоту попыток входа. |
| `AZURE_SPEECH_ENDPOINT` | `''` | `app/config.py:127` |  |
| `AZURE_SPEECH_KEY` | секрет — default пуст | `app/config.py:126` |  |
| `AZURE_SPEECH_LOCALE` | `'ru-RU'` | `app/config.py:128` |  |
| `CANON_AUTHOR_SLUG` | `''` | `app/config.py:78` | Выверенный канон подключается только явно выбранному автору; личных примеров нет. |
| `CHAR_EXTRACTION_PART_CHARS` | `'180000'` | `app/config.py:156` |  |
| `CHAR_EXTRACTION_WHOLE_BOOK_MAX_CHARS` | `'350000'` | `app/config.py:155` |  |
| `CHAR_MAP_NEW_RATIO_GATE` | `'0.40'` | `app/config.py:173` | Слишком много алиасов может означать ошибочное слияние разных персонажей. Порог позволяет отправить такие группы на проверку до применения карты; настраивается под книгу и не требует примеров из частных текстов. |
| `CHAR_MAP_SUSPECT_ALIASES` | `'6'` | `app/config.py:174` |  |
| `CLAUDE_API_KEY` | секрет — default пуст | `app/config.py:129` |  |
| `CLAUDE_BASE_URL` | `'https://api.anthropic.com/v1'` | `app/config.py:137` |  |
| `COOKIE_SECURE` | `'true'` | `app/config.py:110` |  |
| `DATABASE_URL` |  | `app/config.py:93` | База и записи выводятся из DATA_DIR, только если не заданы явно: установщик VPS задаёт их в .env, и для него ничего не меняется. |
| `DATA_DIR` | `'data'` | `app/config.py:86` | папка пользовательских данных; на VPS — `data` рядом с кодом, как всегда |
| `DEEPSEEK_ANTHROPIC_BASE_URL` | `'https://api.deepseek.com/anthropic/v1'` | `app/config.py:141` | DeepSeek's Anthropic-compatible endpoint (Messages format + tool-use). Lets deepseek-v4-pro share the same client path as Claude. Verified working. |
| `DEEPSEEK_API_KEY` | секрет — default пуст | `app/config.py:130` |  |
| `DEEPSEEK_BASE_URL` | `'https://api.deepseek.com/v1'` | `app/config.py:138` |  |
| `DEFAULT_CHAR_EXTRACTION_MODEL` | `'deepseek-v4-pro'` | `app/config.py:153` |  |
| `DEFAULT_CHAR_EXTRACTION_PROVIDER` | `'deepseek'` | `app/config.py:152` |  |
| `DEFAULT_DOMAIN_LEXICON` | `''` | `app/config.py:182` |  |
| `DEFAULT_FINAL_MODEL` | `'deepseek-v4-pro'` | `app/config.py:151` |  |
| `DEFAULT_FINAL_PROVIDER` | `'deepseek'` | `app/config.py:150` | DeepSeek V4 Pro runs every automatic stage: v2 attribution (DEFAULT_FINAL_*) and book-level char_extraction. Claude Sonnet 4.6 is intentionally NOT an automatic default anywhere — it drained the credit balance to $0 when baked in (see project_deployment memory). Keeping these defaults on deepseek means a lost .env line can never silently fall back to (paid) Claude. |
| `DEFAULT_GENRE_GUIDELINES` | `'Художественная литература с преобладанием вымысла: фантастика, научная фантастика, классическое фэнтези и смежные жанры.'` | `app/config.py:175` |  |
| `ELEVENLABS_API_KEY` | секрет — default пуст | `app/config.py:135` | ElevenLabs: генерация фоновой музыки к главам (app/services/elevenlabs_client.py). |
| `KEYS_ENCRYPTION_KEY` | секрет — default пуст | `app/config.py:82` | Ключ шифрования ключей нейросетей в базе (Fernet). Пусто — выводится из SECRET_KEY. |
| `LLM_API_KEY` | секрет — default пуст | `app/config.py:113` |  |
| `LLM_AUDIT_MODEL` | `'deepseek/deepseek-v4-pro'` | `app/config.py:125` |  |
| `LLM_BASE_URL` | `'https://api.openai.com/v1'` | `app/config.py:112` |  |
| `LLM_IDLE_CONTENT_DEADLINE_SECONDS` | `'300'` | `app/config.py:161` | Hard ceiling on time-without-new-content within one stream. The per-chunk read timeout resets on keepalive pings, so a stalled-but-pinging generation hangs forever; this aborts (and retries) when no real content arrives for N seconds. |
| `LLM_MAX_TOKENS` | `'8000'` | `app/config.py:169` |  |
| `LLM_MODEL` | `'gpt-4o-mini'` | `app/config.py:114` |  |
| `LLM_PROCESSING_STALE_MINUTES` | `'35'` | `app/config.py:167` |  |
| `LLM_PROVIDER` | `'openai'` | `app/config.py:111` |  |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `'180'` | `app/config.py:157` |  |
| `LLM_STREAM_WALL_DEADLINE_SECONDS` | `'1200'` | `app/config.py:165` | Hard wall-clock ceiling on one streaming call. A separate timer force-closes the socket past this, so a read blocked in poll() (no lines yielded, idle-deadline never runs) cannot hang the worker forever — the closed socket raises and retries. |
| `LLM_TRANSPORT_RETRIES` | `'2'` | `app/config.py:166` |  |
| `LOCAL_RESERVE_GB` | `'5'` | `app/config.py:109` |  |
| `MAX_CHAPTER_CHARS` | `'180000'` | `app/config.py:154` |  |
| `MIRROR_INTERVAL_MINUTES` | `'5'` | `app/config.py:108` |  |
| `NAS_PROBE_INTERVAL_SECONDS` | `'15'` | `app/config.py:101` | Сторож NAS. С 28.09 монтирование `soft`: при пропаже сервера ошибка через ~30 с, а не вечное ожидание. Файлы с NAS читаются только на подтверждённом «жив». |
| `NAS_PROBE_STALE_SECONDS` | `'30'` | `app/config.py:104` | Сколько после пропажи NAS ещё верить последнему «жив»: не дольше таймаута soft (~30 с) и не короче двух кругов сторожа. |
| `NONAME_SEAT_TOKEN` | секрет — default пуст | `app/config.py:90` | ключ запуска настольной версии; задаёт точка входа, на VPS пуст |
| `OPENAI_API_KEY` | секрет — default пуст | `app/config.py:115` |  |
| `OPENAI_BASE_URL` | `'https://api.openai.com/v1'` | `app/config.py:136` |  |
| `OPENROUTER_API_KEY` | секрет — default пуст | `app/config.py:132` |  |
| `OPENROUTER_BASE_URL` | `'https://openrouter.ai/api/v1'` | `app/config.py:143` |  |
| `OWNER_TELEGRAM_ID` | `''` | `app/config.py:195` |  |
| `PIPELINE_HOTFIX_MAX_MANUAL_ACTIONS_24H` | `'6'` | `app/config.py:168` |  |
| `PRODUCTION` | `''` | `app/config.py:215` |  |
| `REDIS_URL` | `'redis://localhost:6379/0'` | `app/config.py:96` |  |
| `ROUTERAI_API_KEY` | секрет — default пуст | `app/config.py:133` |  |
| `ROUTERAI_BASE_URL` | `'https://routerai.ru/api/v1'` | `app/config.py:144` |  |
| `SEAT_MODE` | `'studio'` | `app/config.py:88` | studio — VPS для студии (как всегда); one — настольная программа одного человека |
| `SECRET_KEY` | секрет — есть непустой default в коде | `app/config.py:80` |  |
| `STRESS_BASE_DICT_PATH` | `''` | `app/config.py:197` | Base stress dictionary for the v2 stress step (app/v2/stress_dict.py). |
| `STRESS_FORMS_DB_PATH` | `''` | `app/config.py:199` | Wiktionary word forms with stress (app/v2/stress_forms.py); empty → data/stress_forms.sqlite. |
| `STUDIO_CONTACT_NAME` | `''` | `app/config.py:76` | Контакт для вопросов и переноса сроков. Пусто — обращение к студии без имени. |
| `STUDIO_NAME` | `os.getenv('APP_NAME', 'Noname Records')` | `app/config.py:74` | Название студии в письмах бота и подписях; по умолчанию — имя системы. |
| `TELEGRAM_API_BASE` | `''` | `app/config.py:187` | Свой узел Bot API (`telegram-bot-api --local`): облако отдаёт ботам файлы только до 20 МБ, а демо и пробы в WAV больше. Пусто — облако Telegram, как было всегда. |
| `TELEGRAM_AUTH_MAX_AGE_SECONDS` | `'3600'` | `app/config.py:212` | Окно свежести Telegram login (auth_date). Это окно рукопожатия входа, а не срок жизни сессии. 24ч было слишком большим окном для replay. |
| `TELEGRAM_AUTH_WHITELIST` | `''` | `app/config.py:190` |  |
| `TELEGRAM_AUTH_WHITELIST_DEFAULT_ACCESS_SCOPE` | `'full'` | `app/config.py:192` |  |
| `TELEGRAM_AUTH_WHITELIST_DEFAULT_ROLE` | `'author'` | `app/config.py:191` |  |
| `TELEGRAM_BOT_TOKEN` | секрет — default пуст | `app/config.py:184` |  |
| `TELEGRAM_BOT_USERNAME` | `''` | `app/config.py:183` |  |
| `TELEGRAM_NOTIFY_CHAT_IDS` | `''` | `app/config.py:194` |  |
| `TELEGRAM_NOTIFY_ENABLED` | `'true'` | `app/config.py:193` |  |
| `TELEGRAM_WEBHOOK_SECRET` | секрет — default пуст | `app/config.py:189` | Секрет webhook бота (заголовок X-Telegram-Bot-Api-Secret-Token). Пусто — адрес выключен. |
| `UPLOAD_MAX_BYTES` | `str(2 * 1024 * 1024 * 1024)` | `app/config.py:206` | Не безлимит, а щедрый потолок: тело загрузки Starlette всё равно спулит во временный файл на диске сервера, и один кривой запрос мог забить его целиком. |
| `ZAI_API_KEY` | секрет — default пуст | `app/config.py:131` |  |
| `ZAI_BASE_URL` | `'https://api.z.ai/v1'` | `app/config.py:142` |  |

## Прочее (вне Settings)

| Переменная | Default | Где читается | Описание |
|---|---|---|---|
| `RQ_QUEUES` |  | `app/worker.py:31` |  |
| `TELEGRAM_WEBHOOK_URL` | `""` | `scripts/telegram_set_webhook.py:24` |  |
