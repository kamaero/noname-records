# Справочник схемы базы данных

Получено интроспекцией `SQLAlchemy`-метаданных: импортируются `app.models` и `app.v2.models`, дальше используется `Base.metadata` (та же `Base`, что в `app/db.py`).

Всего таблиц: **53**.

_Сгенерировано из коммита 34793b0._

## Таблицы

### `ambient_tracks` — `app.models.AmbientTrack`

Фоновый трек ElevenLabs для одной сцены (`marker_id` — строка `sound_markers`, `kind='scene'`).

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `audio_file_id` | VARCHAR(36) | да |  |  |  |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `duration_seconds` | INTEGER | нет |  |  | python:0 |
| `error` | TEXT | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `marker_id` | VARCHAR(36) | нет |  |  |  |
| `prompt` | TEXT | нет |  |  | python:'' |
| `song_id` | VARCHAR(120) | нет |  |  | python:'' |
| `status` | VARCHAR(12) | нет |  |  | python:'pending' |
| `summary` | TEXT | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_ambient_tracks_chapter_status`: chapter_id, status
- `ix_ambient_tracks_marker`: marker_id

### `asr_jobs` — `app.models.AsrJob`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `alignment_json` | VARCHAR | нет |  |  | python:'' |
| `audio_file_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_id` | VARCHAR(36) | нет |  |  | python:'' |
| `confidence_score` | FLOAT | нет |  |  | python:0.0 |
| `coverage` | FLOAT | нет |  |  | python:0.0 |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `decision` | VARCHAR(40) | нет |  |  | python:'' |
| `detected_chapter_index` | INTEGER | нет |  |  | python:0 |
| `detected_role` | VARCHAR(120) | нет |  |  | python:'' |
| `error_message` | VARCHAR(255) | нет |  |  | python:'' |
| `expected_chapter_index` | INTEGER | нет |  |  | python:0 |
| `expected_role` | VARCHAR(120) | нет |  |  | python:'' |
| `heard_json` | VARCHAR | нет |  |  | python:''; server: |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `model` | VARCHAR(120) | нет |  |  | python:'whisper-1' |
| `provider` | VARCHAR(40) | нет |  |  | python:'openai' |
| `review_reason` | VARCHAR(255) | нет |  |  | python:'' |
| `similarity` | FLOAT | нет |  |  | python:0.0 |
| `status` | VARCHAR(40) | нет |  |  | python:'queued' |
| `timing_fit` | FLOAT | нет |  |  | python:0.0 |
| `transcript_text` | VARCHAR | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_asr_jobs_audio_file_id`: audio_file_id
- `ix_asr_jobs_chapter_id`: chapter_id
- `ix_asr_jobs_status`: status

### `audio_files` — `app.models.AudioFile`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_name` | VARCHAR(120) | да |  |  | python:'' |
| `book_code` | VARCHAR(40) | нет |  |  | python:'' |
| `canonical_filename` | VARCHAR(255) | нет |  |  | python:'' |
| `channels` | INTEGER | нет |  |  | python:0; server:0 |
| `chapter` | VARCHAR(60) | нет |  |  |  |
| `codec` | VARCHAR(40) | нет |  |  | python:''; server: |
| `duration_seconds` | FLOAT | нет |  |  | python:0.0; server:0 |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `kind` | VARCHAR(16) | нет |  |  | python:'take'; server:take |
| `line_index` | INTEGER | да |  |  |  |
| `location` | VARCHAR(8) | нет |  |  | python:'local'; server:nas |
| `md5` | VARCHAR(32) | нет |  |  | python:''; server: |
| `mime_type` | VARCHAR(120) | нет |  |  |  |
| `mirror_state` | VARCHAR(16) | нет |  |  | python:''; server: |
| `mirrored_at` | DATETIME | да |  |  |  |
| `original_filename` | VARCHAR(255) | нет |  |  |  |
| `role` | VARCHAR(120) | нет |  |  |  |
| `sample_rate` | INTEGER | нет |  |  | python:0; server:0 |
| `size_bytes` | INTEGER | нет |  |  |  |
| `status` | VARCHAR(40) | нет |  |  | python:'uploaded' |
| `stored_key` | VARCHAR(512) | нет |  |  |  |
| `uploaded_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `verified_at` | DATETIME | да |  |  |  |
| `verify_state` | VARCHAR(16) | нет |  |  | python:''; server: |

Индексы:

- `ix_audio_files_book_code`: book_code
- `ix_audio_files_canonical_filename`: canonical_filename

### `audit_log` — `app.models.AuditLog`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `action` | VARCHAR(60) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `entity_id` | VARCHAR(36) | нет |  |  |  |
| `entity_type` | VARCHAR(40) | нет |  |  |  |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `payload_json` | VARCHAR | нет |  |  | python:'' |
| `user_id` | VARCHAR(36) | нет |  |  | python:'' |

Индексы:

- `ix_audit_log_created_at`: created_at
- `ix_audit_log_entity`: entity_type, entity_id
- `ix_audit_log_user_id`: user_id

### `audition_reactions` — `app.models.AuditionReaction`

👍/👎 автора на одну пробу. Снятая реакция — удалённая строка, а не ноль.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `audio_file_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `value` | INTEGER | нет |  |  |  |
| `voter_name` | VARCHAR(120) | нет |  |  | python:'' |
| `voter_uid` | VARCHAR(36) | нет |  |  |  |

Индексы:

- `ix_audition_reactions_audio`: audio_file_id

### `audition_rejections` — `app.models.AuditionRejection`

Вежливый отказ одному актёру на одну роль книги — не больше одного на всю жизнь пары.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_name` | VARCHAR(120) | нет |  |  |  |
| `book_code` | VARCHAR(40) | нет |  |  | python:'' |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `due_at` | DATETIME | нет |  |  |  |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `role` | VARCHAR(120) | нет |  |  |  |
| `sent_at` | DATETIME | да |  |  |  |
| `status` | VARCHAR(12) | нет |  |  | python:'pending' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_audition_rejections_due`: status, due_at

### `author_characters` — `app.models.AuthorCharacter`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_name` | VARCHAR(120) | нет |  |  | python:'' |
| `actor_user_id` | VARCHAR(36) | нет |  |  | python:'' |
| `aliases` | TEXT | нет |  |  | python:'[]' |
| `appears_in_books` | TEXT | нет |  |  | python:'[]' |
| `author_id` | VARCHAR(36) | нет |  |  | python:'' |
| `canonical_name` | VARCHAR(255) | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `description` | TEXT | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `reply_color` | VARCHAR(20) | нет |  |  | python:'' |
| `source_topic` | VARCHAR(255) | нет |  |  | python:'' |
| `status` | VARCHAR(20) | нет |  |  | python:'unconfirmed' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_author_characters_author_id`: author_id
- `ix_author_characters_author_name`: author_id, canonical_name

### `author_pronunciations` — `app.models.AuthorPronunciation`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `author_id` | VARCHAR(36) | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `source` | VARCHAR(40) | нет |  |  | python:'compendium' |
| `stressed` | VARCHAR(255) | нет |  |  | python:'' |
| `term` | VARCHAR(255) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `variants` | TEXT | нет |  |  | python:'[]' |

Индексы:

- `ix_author_pronunciations_author_id`: author_id

### `authors` — `app.models.Author`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `name` | VARCHAR(255) | нет |  |  | python:'' |
| `notes` | TEXT | нет |  |  | python:'' |
| `slug` | VARCHAR(120) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_authors_slug`: slug

### `background_runs` — `app.models.BackgroundRun`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `entity_id` | VARCHAR(36) | нет |  |  | python:'' |
| `entity_type` | VARCHAR(40) | нет |  |  | python:'' |
| `error_message` | VARCHAR(255) | нет |  |  | python:'' |
| `finished_at` | DATETIME | да |  |  |  |
| `heartbeat_at` | DATETIME | да |  |  |  |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `job_kind` | VARCHAR(40) | нет |  |  | python:'' |
| `meta_json` | VARCHAR | нет |  |  | python:'' |
| `run_key` | VARCHAR(120) | нет |  |  | python:'' |
| `started_at` | DATETIME | да |  |  |  |
| `status` | VARCHAR(20) | нет |  |  | python:'queued' |
| `thread_name` | VARCHAR(120) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_background_runs_entity_id`: entity_id
- `ix_background_runs_run_key`: run_key
- `ix_background_runs_status`: status

### `book_budget` — `app.models.BookBudget`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `extra_cost_rub` | INTEGER | нет |  |  | python:0 |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `narrative_cost_rub` | INTEGER | нет |  |  | python:0 |
| `narrator_actor_name` | VARCHAR(120) | нет |  |  | python:'' |
| `notes` | VARCHAR | нет |  |  | python:'' |
| `sound_engineer_cost_rub` | INTEGER | нет |  |  | python:0 |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `updated_by` | VARCHAR(36) | нет |  |  | python:'' |

### `book_illustrations` — `app.models.BookIllustration`

Авторская иллюстрация из fb2 книги — и кто на ней.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `author_id` | VARCHAR(36) | нет |  |  | python:'' |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `bound_at` | DATETIME | да |  |  |  |
| `bound_by` | VARCHAR(120) | нет |  |  | python:'' |
| `bytes_len` | INTEGER | нет |  |  | python:0 |
| `chapter_hint` | VARCHAR(200) | нет |  |  | python:'' |
| `character_id` | VARCHAR(36) | нет |  |  | python:'' |
| `content_type` | VARCHAR(60) | нет |  |  | python:'image/jpeg' |
| `context` | TEXT | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `image_key` | VARCHAR(120) | нет |  |  |  |
| `ordinal` | INTEGER | нет |  |  | python:0 |
| `status` | VARCHAR(12) | нет |  |  | python:'new' |
| `stored_path` | VARCHAR(512) | нет |  |  | python:'' |

Индексы:

- `ix_book_illustrations_book`: book_id, ordinal
- `ix_book_illustrations_character`: character_id

### `bot_broadcasts` — `app.models.BotBroadcast`

Рассылка из бота: админ выбирает книгу или всех, пишет текст, подтверждает.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `admin_tid` | VARCHAR(32) | нет |  |  |  |
| `book_id` | VARCHAR(36) | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `failed_json` | TEXT | нет |  |  | python:'[]' |
| `id` | VARCHAR(8) | нет | да |  | python:<lambda>() |
| `include_proposed` | BOOLEAN | нет |  |  | python:False |
| `scope` | VARCHAR(8) | нет |  |  | python:'' |
| `sent_at` | DATETIME | да |  |  |  |
| `sent_count` | INTEGER | нет |  |  | python:0 |
| `state` | VARCHAR(10) | нет |  |  | python:'choose' |
| `text` | TEXT | нет |  |  | python:'' |

### `bot_contacts` — `app.models.BotContact`

Кто писал боту: id, как назван в Telegram, когда впервые и когда последний раз.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `first_seen_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `last_seen_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `reported_at` | DATETIME | да |  |  |  |
| `telegram_user_id` | VARCHAR(32) | нет | да |  |  |
| `tg_name` | VARCHAR(200) | нет |  |  | python:'' |
| `username` | VARCHAR(64) | нет |  |  | python:'' |

### `bot_intros` — `app.models.BotIntro`

Бот спросил «Как вас зовут?» и ждёт ответа (см. `app/services/bot_intro.py`).

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `asked_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `telegram_user_id` | VARCHAR(32) | нет | да |  |  |

### `character_budget_snapshot` — `app.models.CharacterBudgetSnapshot`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `approx_seconds` | INTEGER | нет |  |  | python:0 |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `calc_mode` | VARCHAR(20) | нет |  |  | python:'rate_plan' |
| `character_id` | VARCHAR(36) | нет |  |  |  |
| `fact_seconds` | INTEGER | нет |  |  | python:0 |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `lines_count` | INTEGER | нет |  |  | python:0 |
| `total_rub` | INTEGER | нет |  |  | python:0 |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_char_budget_snapshot_book_id`: book_id
- `ix_char_budget_snapshot_character_id`: character_id

### `characters` — `app.models.Character`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_name` | VARCHAR(120) | нет |  |  | python:'' |
| `age` | VARCHAR(120) | нет |  |  | python:'' |
| `aliases` | VARCHAR | нет |  |  | python:'' |
| `appears_in` | VARCHAR | нет |  |  | python:'' |
| `author_character_id` | VARCHAR(36) | нет |  |  | python:'' |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `char_map_id` | VARCHAR(36) | нет |  |  | python:<lambda>() |
| `character_color` | VARCHAR(20) | нет |  |  | python:'' |
| `character_font_style` | VARCHAR(20) | нет |  |  | python:'' |
| `character_font_weight` | VARCHAR(20) | нет |  |  | python:'' |
| `character_text_color` | VARCHAR(20) | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `manual_fixed_rub` | INTEGER | да |  |  |  |
| `manual_rate_rub_per_min` | INTEGER | да |  |  |  |
| `name` | VARCHAR(255) | нет |  |  |  |
| `narrator_role` | VARCHAR(120) | нет |  |  | python:'' |
| `operator_note` | VARCHAR | нет |  |  | python:'' |
| `race` | VARCHAR(120) | нет |  |  | python:'' |
| `temperament` | VARCHAR(255) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_characters_book_id`: book_id
- `ix_characters_book_name`: book_id, name
- `ix_characters_char_map_id`: char_map_id

### `consilium_findings` — `app.models.ConsiliumFinding`

Спорное место, найденное консилиумом: где, какого рода, что решил человек.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `arbiter_speaker` | VARCHAR(200) | нет |  |  | python:'' |
| `arbiter_verdict` | VARCHAR(20) | нет |  |  | python:'' |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_index` | INTEGER | нет |  |  | python:0 |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `current_speaker` | VARCHAR(200) | нет |  |  | python:'' |
| `decided_at` | DATETIME | да |  |  |  |
| `decided_by` | VARCHAR(120) | нет |  |  | python:'' |
| `decided_speaker` | VARCHAR(200) | нет |  |  | python:'' |
| `evidence_para` | INTEGER | нет |  |  | python:0 |
| `evidence_proven` | BOOLEAN | нет |  |  | python:False |
| `evidence_quote` | VARCHAR | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `kind` | VARCHAR(20) | нет |  |  | python:'wrong_voice' |
| `ordinal` | INTEGER | нет |  |  | python:0 |
| `reader_opus` | VARCHAR(200) | нет |  |  | python:'' |
| `reader_sol` | VARCHAR(200) | нет |  |  | python:'' |
| `readers_speaker` | VARCHAR(200) | нет |  |  | python:'' |
| `reason` | VARCHAR | нет |  |  | python:'' |
| `run_label` | VARCHAR(40) | нет |  |  | python:'' |
| `segment_id` | VARCHAR(80) | нет |  |  | python:'' |
| `span_end` | INTEGER | нет |  |  | python:0 |
| `span_start` | INTEGER | нет |  |  | python:0 |
| `status` | VARCHAR(16) | нет |  |  | python:'new' |

Индексы:

- `ix_consilium_findings_book`: book_id, status
- `ix_consilium_findings_segment`: segment_id

### `dictor_assignments` — `app.models.DictorAssignment`

Агрегат «диктор → книга → роль»: пересчитывается `casting.rebuild_assignments`, руками не правится. Пустой `character_id` — рассказчик книги.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `character_id` | VARCHAR(36) | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `recorded` | BOOLEAN | нет |  |  | python:False |
| `role_name` | VARCHAR(255) | нет |  |  | python:'' |
| `state` | VARCHAR(20) | нет |  |  | python:'approved' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `user_id` | VARCHAR(36) | нет |  |  |  |

Индексы:

- `ix_dictor_assignments_book`: book_id
- `ix_dictor_assignments_user`: user_id

### `dictor_demos` — `app.models.DictorDemo`

Демо голоса: MP3 для прослушивания (≤ 2 мин). `source_ref` — откуда пришло при импорте (`<telegram id>:<сообщение>` или url), по нему повторный запуск не дублирует.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `duration_seconds` | FLOAT | нет |  |  | python:0.0 |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `md5` | VARCHAR(32) | нет |  |  | python:'' |
| `size_bytes` | INTEGER | нет |  |  | python:0 |
| `source` | VARCHAR(20) | нет |  |  | python:'upload' |
| `source_ref` | VARCHAR(255) | нет |  |  | python:'' |
| `source_url` | VARCHAR(1024) | нет |  |  | python:'' |
| `stored_key` | VARCHAR(512) | нет |  |  | python:'' |
| `title` | VARCHAR(255) | нет |  |  | python:'' |
| `trimmed` | BOOLEAN | нет |  |  | python:False |
| `user_id` | VARCHAR(36) | нет |  |  |  |

Индексы:

- `ix_dictor_demos_user`: user_id

### `dictor_links` — `app.models.DictorLink`

Портфолио — ссылка на страницу, а не на запись (канал, профиль ВК, КиноПоиск…).

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `title` | VARCHAR(255) | нет |  |  | python:'' |
| `url` | VARCHAR(1024) | нет |  |  |  |
| `user_id` | VARCHAR(36) | нет |  |  |  |

Индексы:

- `ix_dictor_links_user`: user_id

### `dictor_profiles` — `app.models.DictorProfile`

Карточка диктора — одна на пользователя с ролью `dictor`. Telegram id живёт в `telegram_auth_accounts`, здесь только то, чего там нет.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `main_demo_id` | VARCHAR(36) | нет |  |  | python:'' |
| `note` | TEXT | нет |  |  | python:'' |
| `telegram_username` | VARCHAR(64) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `user_id` | VARCHAR(36) | нет | да |  |  |

### `llm_usage_logs` — `app.models.LlmUsageLog`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  | python:'' |
| `chapter_id` | VARCHAR(36) | нет |  |  | python:'' |
| `chapter_index` | INTEGER | нет |  |  | python:0 |
| `completion_tokens` | INTEGER | нет |  |  | python:0 |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `created_by_name` | VARCHAR(120) | нет |  |  | python:'' |
| `created_by_user_id` | VARCHAR(36) | нет |  |  | python:'' |
| `estimated` | VARCHAR(5) | нет |  |  | python:'false' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `job_id` | VARCHAR(36) | нет |  |  | python:'' |
| `model` | VARCHAR(120) | нет |  |  | python:'' |
| `prompt_tokens` | INTEGER | нет |  |  | python:0 |
| `provider` | VARCHAR(40) | нет |  |  | python:'' |
| `stage` | VARCHAR(20) | нет |  |  | python:'' |
| `total_tokens` | INTEGER | нет |  |  | python:0 |

Индексы:

- `ix_llm_usage_logs_book_id`: book_id
- `ix_llm_usage_logs_chapter_id`: chapter_id
- `ix_llm_usage_logs_created_at`: created_at

### `lore_articles` — `app.models.LoreArticle`

Статья авторской энциклопедии — шпаргалка диктору о мире книги.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `author_id` | VARCHAR(36) | нет |  |  |  |
| `body` | TEXT | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `image_keys` | TEXT | нет |  |  | python:'' |
| `ordinal` | INTEGER | нет |  |  | python:0 |
| `title` | VARCHAR(200) | нет |  |  | python:'' |
| `topic` | VARCHAR(200) | нет |  |  |  |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_lore_articles_author_topic` (unique): author_id, topic

### `lore_images` — `app.models.LoreImage`

Карта или схема из энциклопедии. Сам файл лежит на диске, тут — что это и где.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `author_id` | VARCHAR(36) | нет |  |  |  |
| `bytes_len` | INTEGER | нет |  |  | python:0 |
| `content_type` | VARCHAR(60) | нет |  |  | python:'image/jpeg' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `image_key` | VARCHAR(120) | нет |  |  |  |
| `stored_path` | VARCHAR(512) | нет |  |  | python:'' |

Индексы:

- `ix_lore_images_author_key` (unique): author_id, image_key

### `model_prices` — `app.models.ModelPrice`

Цена модели, вписанная студией. Главнее встроенной цены из кода.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `currency` | VARCHAR(3) | нет |  |  | python:'RUB' |
| `model` | VARCHAR(160) | нет | да |  |  |
| `price_in` | FLOAT | да |  |  |  |
| `price_out` | FLOAT | да |  |  |  |
| `price_unit` | FLOAT | да |  |  |  |
| `provider` | VARCHAR(20) | нет | да |  |  |
| `unit` | VARCHAR(10) | нет |  |  | python:'tokens' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `updated_by` | VARCHAR(120) | нет |  |  | python:'' |

### `operator_interventions` — `app.models.OperatorIntervention`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `action_type` | VARCHAR(60) | нет |  |  | python:'' |
| `actor_name` | VARCHAR(120) | нет |  |  | python:'' |
| `actor_user_id` | VARCHAR(36) | нет |  |  | python:'' |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_id` | VARCHAR(36) | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `payload_json` | VARCHAR | нет |  |  | python:'' |
| `pipeline_run_id` | VARCHAR(36) | нет |  |  | python:'' |
| `reason` | VARCHAR(500) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_operator_interventions_book_id`: book_id
- `ix_operator_interventions_chapter_id`: chapter_id
- `ix_operator_interventions_created_at`: created_at
- `ix_operator_interventions_run_id`: pipeline_run_id

### `pending_mirror_deletions` — `app.models.PendingMirrorDeletion`

Поручение фоновому циклу: стереть эту копию на NAS.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_code` | VARCHAR(40) | нет |  |  | python:''; server: |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `deleted_by` | VARCHAR(120) | нет |  |  | python:''; server: |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `stored_key` | VARCHAR(512) | нет |  |  |  |

### `pipeline_events` — `app.models.PipelineEvent`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_id` | VARCHAR(36) | нет |  |  | python:'' |
| `chapter_index` | INTEGER | нет |  |  | python:0 |
| `code` | VARCHAR(120) | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `details_json` | VARCHAR | нет |  |  | python:'' |
| `event_type` | VARCHAR(60) | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `level` | VARCHAR(20) | нет |  |  | python:'info' |
| `message` | VARCHAR(1000) | нет |  |  | python:'' |
| `pipeline_run_id` | VARCHAR(36) | нет |  |  | python:'' |
| `stage` | VARCHAR(20) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_pipeline_events_book_id`: book_id
- `ix_pipeline_events_created_at`: created_at
- `ix_pipeline_events_run_id`: pipeline_run_id

### `pipeline_runs` — `app.models.PipelineRun`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `ended_at` | DATETIME | да |  |  |  |
| `ended_reason_code` | VARCHAR(120) | нет |  |  | python:'' |
| `ended_reason_text` | VARCHAR(500) | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `started_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `status` | VARCHAR(20) | нет |  |  | python:'running' |
| `trigger_type` | VARCHAR(40) | нет |  |  | python:'system' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_pipeline_runs_book_id`: book_id
- `ix_pipeline_runs_started_at`: started_at
- `ix_pipeline_runs_status`: status

### `provider_keys` — `app.models.ProviderKey`

Ключ нейросети, введённый на сайте. Хранится зашифрованным: база уходит в бэкапы, и ключ, лежащий в ней открыто, утёк бы вместе с любой копией.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `check_detail` | VARCHAR(200) | нет |  |  | python:'' |
| `check_status` | VARCHAR(20) | нет |  |  | python:'' |
| `checked_at` | DATETIME | да |  |  |  |
| `ciphertext` | VARCHAR | нет |  |  | python:'' |
| `last4` | VARCHAR(4) | нет |  |  | python:'' |
| `provider` | VARCHAR(20) | нет | да |  |  |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `updated_by` | VARCHAR(120) | нет |  |  | python:'' |

### `recasts` — `app.models.Recast`

Журнал рекастов персонажа профиля автора: кто кого сменил, почему и где.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_user_id` | VARCHAR(36) | нет |  |  | python:'' |
| `author_character_id` | VARCHAR(36) | нет |  |  | python:'' |
| `books_changed` | TEXT | нет |  |  | python:'[]' |
| `books_kept` | TEXT | нет |  |  | python:'[]' |
| `comment` | TEXT | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `from_actor` | VARCHAR(120) | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `reason` | VARCHAR(20) | нет |  |  | python:'other' |
| `role_name` | VARCHAR(255) | нет |  |  | python:'' |
| `to_actor` | VARCHAR(120) | нет |  |  | python:'' |

### `role_deadlines` — `app.models.RoleDeadline`

Срок пробы или утверждённой роли (`app/services/role_deadlines.py`).

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_name` | VARCHAR(120) | нет |  |  |  |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `character_id` | VARCHAR(36) | нет |  |  |  |
| `close_reason` | VARCHAR(12) | да |  |  |  |
| `closed_at` | DATETIME | да |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `due_at` | DATETIME | нет |  |  |  |
| `extended_by` | VARCHAR(120) | нет |  |  | python:''; server: |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `kind` | VARCHAR(10) | нет |  |  |  |
| `recast_notified_at` | DATETIME | да |  |  |  |
| `reminded_before_at` | DATETIME | да |  |  |  |
| `reminded_overdue_at` | DATETIME | да |  |  |  |

Индексы:

- `ix_role_deadlines_character`: character_id
- `ix_role_deadlines_open`: closed_at

### `role_pair_notes` — `app.models.RolePairNote`

«Так задумано» про пару ролей одного актёра.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_name` | VARCHAR(120) | нет |  |  | python:'' |
| `actor_uid` | VARCHAR(36) | нет |  |  | python:'' |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `reason` | VARCHAR | нет |  |  | python:'' |
| `role_a` | VARCHAR(255) | нет |  |  |  |
| `role_b` | VARCHAR(255) | нет |  |  |  |

Индексы:

- `ix_role_pair_notes_book`: book_id

### `role_votes` — `app.models.RoleVote`

Голос за то, кому читать роль. Один голос на человека, вес — по его роли в студии.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_name` | VARCHAR(120) | нет |  |  | python:'' |
| `character_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `voter_name` | VARCHAR(120) | нет |  |  | python:'' |
| `voter_uid` | VARCHAR(36) | нет |  |  |  |
| `weight` | INTEGER | нет |  |  | python:1 |

Индексы:

- `ix_role_votes_character`: character_id

### `script_books` — `app.models.ScriptBook`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `author_id` | VARCHAR(36) | нет |  |  | python:'' |
| `author_label` | VARCHAR(120) | нет |  |  | python:''; server: |
| `author_sheets_x1000` | INTEGER | нет |  |  | python:0 |
| `auto_publish` | VARCHAR(5) | нет |  |  | python:'false' |
| `book_annotation` | VARCHAR | нет |  |  | python:'' |
| `cast_checked_at` | DATETIME | да |  |  |  |
| `cast_checked_by` | VARCHAR(120) | нет |  |  | python:'' |
| `chapter_count` | INTEGER | нет |  |  | python:0 |
| `char_map_version` | INTEGER | нет |  |  | python:0 |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `created_by_name` | VARCHAR(120) | нет |  |  | python:'' |
| `created_by_user_id` | VARCHAR(36) | нет |  |  | python:'' |
| `current_pipeline_run_id` | VARCHAR(36) | нет |  |  | python:'' |
| `display_title` | VARCHAR(255) | нет |  |  | python:'' |
| `domain_lexicon` | VARCHAR | нет |  |  | python:'' |
| `genre_guidelines` | VARCHAR | нет |  |  | python:'' |
| `has_chapters` | VARCHAR(5) | нет |  |  | python:'false' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `last_notified_status` | VARCHAR(40) | нет |  |  | python:'' |
| `llm_model` | VARCHAR(120) | нет |  |  | python:'' |
| `llm_provider` | VARCHAR(40) | нет |  |  | python:'' |
| `narrative_owner_confidence` | FLOAT | нет |  |  | python:0.0 |
| `narrative_owner_name` | VARCHAR(120) | нет |  |  | python:'' |
| `narrative_owner_reason` | VARCHAR(255) | нет |  |  | python:'' |
| `needs_investigation` | VARCHAR(5) | нет |  |  | python:'false' |
| `pipeline_mode` | VARCHAR(40) | нет |  |  | python:'standard' |
| `previous_pipeline_run_id` | VARCHAR(36) | нет |  |  | python:'' |
| `pronunciation_notes` | VARCHAR | нет |  |  | python:'' |
| `source_filename` | VARCHAR(255) | нет |  |  |  |
| `source_format` | VARCHAR(20) | нет |  |  |  |
| `status` | VARCHAR(40) | нет |  |  | python:'uploaded' |
| `stop_requested` | VARCHAR(5) | нет |  |  | python:'false' |
| `title` | VARCHAR(255) | нет |  |  |  |
| `total_chars` | INTEGER | нет |  |  | python:0 |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `validation_profile` | VARCHAR(40) | нет |  |  | python:'operator_only' |

### `script_chapters` — `app.models.ScriptChapter`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_index` | INTEGER | нет |  |  |  |
| `chapter_title` | VARCHAR(255) | нет |  |  |  |
| `char_count` | INTEGER | нет |  |  | python:0 |
| `coverage_reported_at` | DATETIME | да |  |  |  |
| `coverage_reported_takes` | VARCHAR(64) | нет |  |  | python:''; server: |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `delivered_at` | DATETIME | да |  |  |  |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `session_archived_at` | DATETIME | да |  |  |  |
| `session_markers_skipped` | INTEGER | нет |  |  | python:0; server:0 |
| `session_outdated_at` | DATETIME | да |  |  |  |
| `session_sha256` | VARCHAR(64) | нет |  |  | python:''; server: |
| `source_text` | VARCHAR | нет |  |  | python:'' |
| `status` | VARCHAR(40) | нет |  |  | python:'queued' |
| `stress_report_json` | TEXT | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_script_chapters_book_id`: book_id
- `ix_script_chapters_book_index`: book_id, chapter_index
- `ix_script_chapters_book_status`: book_id, status

### `script_jobs` — `app.models.ScriptJob`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_index` | INTEGER | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `error_code` | VARCHAR(80) | нет |  |  | python:'' |
| `error_details_json` | VARCHAR | нет |  |  | python:'' |
| `error_message` | VARCHAR(255) | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `model` | VARCHAR(120) | нет |  |  | python:'' |
| `pipeline_run_id` | VARCHAR(36) | нет |  |  | python:'' |
| `provider` | VARCHAR(40) | нет |  |  | python:'openai' |
| `stage` | VARCHAR(20) | нет |  |  | python:'draft' |
| `status` | VARCHAR(40) | нет |  |  | python:'queued' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_script_jobs_book_id`: book_id
- `ix_script_jobs_book_stage_status`: book_id, stage, status
- `ix_script_jobs_chapter_id`: chapter_id

### `script_logs` — `app.models.ScriptLog`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `event_code` | VARCHAR(80) | нет |  |  | python:'' |
| `event_details_json` | VARCHAR | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `level` | VARCHAR(20) | нет |  |  | python:'info' |
| `message` | VARCHAR(1000) | нет |  |  |  |
| `pipeline_run_id` | VARCHAR(36) | нет |  |  | python:'' |

Индексы:

- `ix_script_logs_book_id`: book_id
- `ix_script_logs_created_at`: created_at

### `sound_markers` — `app.models.SoundMarker`

Маркер звукорежиссёра на абзаце: сцена, переход или значимый звук.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `kind` | VARCHAR(12) | нет |  |  |  |
| `payload` | JSON | нет |  |  | python:dict() |
| `place_id` | VARCHAR(36) | да |  |  |  |
| `quote` | TEXT | нет |  |  | python:'' |
| `run_id` | VARCHAR(36) | нет |  |  | python:'' |
| `segment_id` | VARCHAR(80) | нет |  |  | python:'' |
| `source` | VARCHAR(8) | нет |  |  | python:'llm' |
| `status` | VARCHAR(12) | нет |  |  | python:'active' |
| `text_sha256` | VARCHAR(64) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_sound_markers_book`: book_id
- `ix_sound_markers_chapter`: chapter_id, status

### `sound_place_pairs` — `app.models.SoundPlacePair`

Кандидат на склейку двух мест и решение человека: `candidate` · `merged` · `apart`.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `place_a` | VARCHAR(36) | нет |  |  |  |
| `place_b` | VARCHAR(36) | нет |  |  |  |
| `reason` | TEXT | нет |  |  | python:'' |
| `status` | VARCHAR(12) | нет |  |  | python:'candidate' |

Индексы:

- `ix_sound_place_pairs_book`: book_id, status

### `sound_places` — `app.models.SoundPlace`

Место книги для звукорежиссёра: одно место — одна строка на всю книгу.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `ambience_queries` | JSON | нет |  |  | python:list() |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `description` | TEXT | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `merged_into` | VARCHAR(36) | да |  |  |  |
| `name` | VARCHAR(200) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_sound_places_book`: book_id

### `spend_entries` — `app.models.SpendEntry`

Один платный вызов нейросети и сколько он стоил.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  | python:'' |
| `chapter_id` | VARCHAR(36) | нет |  |  | python:'' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `input_units` | INTEGER | нет |  |  | python:0 |
| `model` | VARCHAR(160) | нет |  |  | python:'' |
| `output_units` | INTEGER | нет |  |  | python:0 |
| `price_known` | BOOLEAN | нет |  |  | python:False |
| `provider` | VARCHAR(20) | нет |  |  | python:'' |
| `rub` | FLOAT | да |  |  |  |
| `run_id` | VARCHAR(36) | нет |  |  | python:'' |
| `step` | VARCHAR(40) | нет |  |  | python:'other' |
| `unit` | VARCHAR(10) | нет |  |  | python:'tokens' |

Индексы:

- `ix_spend_entries_created_at`: created_at

### `step_models` — `app.models.StepModel`

Модель, выбранная студией для шага. Нет строки — значение по умолчанию из кода.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `model` | VARCHAR(160) | нет |  |  | python:'' |
| `provider` | VARCHAR(20) | нет |  |  | python:'' |
| `step` | VARCHAR(40) | нет | да |  |  |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `updated_by` | VARCHAR(120) | нет |  |  | python:'' |

### `studio_settings` — `app.models.StudioSettings`

Studio-wide defaults, one row (`id == "default"`).

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `contacts_reported_at` | DATETIME | да |  |  |  |
| `deadline_digest_day` | VARCHAR(10) | нет |  |  | python:''; server: |
| `default_rate_rub_per_min` | INTEGER | нет |  |  | python:1000 |
| `id` | VARCHAR(36) | нет | да |  | python:'default' |
| `monthly_limit_rub` | INTEGER | нет |  |  | python:0; server:0 |
| `role_deadline_date` | VARCHAR(10) | нет |  |  | python:'2026-12-31'; server:2026-12-31 |
| `spend_warned_month` | VARCHAR(16) | нет |  |  | python:''; server: |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `updated_by` | VARCHAR(120) | нет |  |  | python:'' |
| `usd_rub_rate` | FLOAT | нет |  |  | python:0.0 |

### `telegram_auth_accounts` — `app.models.TelegramAuthAccount`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `access_scope` | VARCHAR(20) | нет |  |  | python:'full' |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `display_name` | VARCHAR(120) | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `is_active` | VARCHAR(5) | нет |  |  | python:'true' |
| `role` | VARCHAR(20) | нет |  |  |  |
| `telegram_user_id` | VARCHAR(32) | нет |  |  |  |
| `user_id` | VARCHAR(36) | нет |  |  | python:'' |

### `user_roles` — `app.models.UserRole`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `role` | VARCHAR(20) | нет |  |  |  |
| `user_id` | VARCHAR(36) | нет |  |  |  |

Индексы:

- `ix_user_roles_user_id`: user_id

### `users` — `app.models.User`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `display_name` | VARCHAR(120) | нет |  |  | python:'' |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `is_active` | VARCHAR(5) | нет |  |  | python:'true' |
| `login` | VARCHAR(80) | нет |  |  |  |
| `onboarding_shown_at` | DATETIME | да |  |  |  |
| `password_hash` | VARCHAR(255) | нет |  |  |  |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

### `v2_attributions` — `app.v2.models.V2Attribution`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `confidence` | FLOAT | нет |  |  | python:0.0 |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  |  |
| `segment_id` | VARCHAR(80) | нет |  |  |  |
| `source` | VARCHAR(20) | нет |  |  | python:'llm' |
| `span_end` | INTEGER | нет |  |  | python:0 |
| `span_start` | INTEGER | нет |  |  | python:0 |
| `speaker` | VARCHAR(200) | нет |  |  | python:'' |
| `version` | INTEGER | нет |  |  | python:1 |

Индексы:

- `ix_v2_attributions_segment`: segment_id, version

### `v2_runs` — `app.v2.models.V2Run`

One pass of the v2 book pipeline: which step it is on and how far it got.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `calls` | INTEGER | нет |  |  | python:0 |
| `chapters_done` | INTEGER | нет |  |  | python:0 |
| `chapters_total` | INTEGER | нет |  |  | python:0 |
| `completion_tokens` | INTEGER | нет |  |  | python:0 |
| `error` | TEXT | нет |  |  | python:'' |
| `finished_at` | DATETIME | да |  |  |  |
| `id` | VARCHAR(36) | нет | да |  |  |
| `prompt_tokens` | INTEGER | нет |  |  | python:0 |
| `started_at` | DATETIME | да |  |  |  |
| `status` | VARCHAR(20) | нет |  |  | python:'queued' |
| `step` | VARCHAR(20) | нет |  |  | python:'' |
| `updated_at` | DATETIME | нет |  |  | python:utcnow_naive() |

Индексы:

- `ix_v2_runs_book`: book_id
- `ix_v2_runs_book_status`: book_id, status

### `v2_segments` — `app.v2.models.V2Segment`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `chapter_id` | VARCHAR(36) | нет |  |  |  |
| `char_end` | INTEGER | нет |  |  | python:0 |
| `char_start` | INTEGER | нет |  |  | python:0 |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(80) | нет | да |  |  |
| `kind` | VARCHAR(16) | нет |  |  | python:'paragraph' |
| `ordinal` | INTEGER | нет |  |  |  |
| `text` | TEXT | нет |  |  | python:'' |

Индексы:

- `ix_v2_segments_book`: book_id
- `ix_v2_segments_chapter` (unique): chapter_id, ordinal

### `v2_stress_marks` — `app.v2.models.V2StressMark`

The base class of the class hierarchy.

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  |  |
| `segment_id` | VARCHAR(80) | нет |  |  |  |
| `source` | VARCHAR(20) | нет |  |  | python:'dict' |
| `version` | INTEGER | нет |  |  | python:1 |
| `vowel_offset` | INTEGER | нет |  |  | python:0 |
| `word_end` | INTEGER | нет |  |  | python:0 |
| `word_start` | INTEGER | нет |  |  | python:0 |

Индексы:

- `ix_v2_stress_segment`: segment_id, version

### `v2_stress_skips` — `app.v2.models.V2StressSkip`

A word the author has waved off: «ударение тут не нужно, его и так все знают».

| Колонка | Тип | Nullable | PK | FK | Умолчание |
|---|---|---|---|---|---|
| `actor_uid` | VARCHAR(36) | нет |  |  | python:'' |
| `book_id` | VARCHAR(36) | нет |  |  |  |
| `created_at` | DATETIME | нет |  |  | python:utcnow_naive() |
| `id` | VARCHAR(36) | нет | да |  | python:<lambda>() |
| `word` | VARCHAR(120) | нет |  |  |  |

Индексы:

- `ix_v2_stress_skips_book_word` (unique): book_id, word

## Ревизии Alembic

По порядку (`revision` → `down_revision`, от `0001_baseline`), первая строка docstring файла ревизии.

| Revision | Первая строка docstring | Файл |
|---|---|---|
| `0001_baseline` | baseline schema | `0001_baseline.py` |
| `0002_author_profile` | author profile foundation | `0002_author_profile.py` |
| `0003_stress_report` | stress report column | `0003_stress_report.py` |
| `0004_additive_via_alembic` | run legacy additive migrations as a versioned step (retire startup coupling) | `0004_additive_via_alembic.py` |
| `0005_book_last_notified_status` | book.last_notified_status (telegram notification dedup) | `0005_book_last_notified_status.py` |
| `0006_audio_line_index` | audio_files.line_index (recording: NULL=role take, N=per-replica patch) | `0006_audio_line_index.py` |
| `0007_char_map_gate` | char-map gate: script_books.char_map_version + characters.operator_note | `0007_char_map_gate.py` |
| `0008_v2_segments` | pipeline v2: immutable segments and the annotations that point at them | `0008_v2_segments.py` |
| `0009_v2_runs` | pipeline v2: the run table the progress screen reads | `0009_v2_runs.py` |
| `0010_studio_settings` | studio-wide defaults: the rate a role inherits when it has none of its own | `0010_studio_settings.py` |
| `0011_book_model_choice` | per-book model choice: bring `llm_provider` / `llm_model` back into the mapping | `0011_book_model_choice.py` |
| `0012_usd_rate` | studio-wide dollar rate, for providers that bill in dollars | `0012_usd_rate.py` |
| `0013_auto_publish` | per-book switch: an approved chapter opens for recording at once | `0013_auto_publish.py` |
| `0014_telegram_account_link` | link a whitelisted Telegram identity to the account it belongs to | `0014_telegram_account_link.py` |
| `0015_stress_skips` | words the author has waved off: «ударение тут не нужно» | `0015_stress_skips.py` |
| `0016_audio_kind` | пробы на роль отличаются от дублей утверждённой роли одним полем | `0016_audio_kind.py` |
| `0017_role_votes` | назначение на роль — голос, а не запись поля | `0017_role_votes.py` |
| `0018_audio_probe` | что сказал о себе сам аудиофайл: длительность, частота, каналы, кодек | `0018_audio_probe.py` |
| `0019_chapter_delivered` | глава ушла в сведение — и больше не мельтешит среди тех, что ждут | `0019_chapter_delivered.py` |
| `0020_audio_integrity` | целостность аудио: сумма при приёме, место хранения, вердикт сверки | `0020_audio_integrity.py` |
| `0021_audio_mirror` | зеркало на NAS: отметка о второй копии и о файле проекта главы | `0021_audio_mirror.py` |
| `0022_character_map_tool` | карта персонажей режиссёра: возраст роли, отметка автора о проверке, заметки о паре | `0022_character_map_tool.py` |
| `0023_chapter_coverage_report` | отметка о составе главы, по которому владельцу уже ушёл отчёт о сверке | `0023_chapter_coverage_report.py` |
| `0024_pending_mirror_deletions` | таблица поручений фоновому циклу зеркала на стирание копии с NAS | `0024_pending_mirror_deletions.py` |
| `0025_asr_heard_json` | графа с услышанным: сверку можно пересчитать, не платя за распознавание заново | `0025_asr_heard_json.py` |
| `0026_consilium_findings` | таблица находок консилиума | `0026_consilium_findings.py` |
| `0027_consilium_reader_answers` | ответы чтецов порознь: без них находку «чтецы врозь» нечем показать и нечем принять | `0027_consilium_reader_answers.py` |
| `0028_consilium_decided_speaker` | чем кончилась находка: имя, которое применили или оставили | `0028_consilium_decided_speaker.py` |
| `0029_chapter_session_fingerprint` | отпечаток архивной сессии главы и пометка «устарела» | `0029_chapter_session_fingerprint.py` |
| `0030_sound_markers` | звуковая разметка: места, пары мест, маркеры | `0030_sound_markers.py` |
| `0031_session_markers_skipped` | счётчик маркеров без места в архивной сессии главы | `0031_session_markers_skipped.py` |
| `0032_ambient_tracks` | эмбиент: таблица треков ElevenLabs | `0032_ambient_tracks.py` |
| `0033_user_onboarding` | отметка «приветственное окно уже показывали» на учётке диктора | `0033_user_onboarding.py` |
| `0034_book_author_label` | автор книги отдельной графой — чтобы не течь в название | `0034_book_author_label.py` |
| `0035_lore_articles` | лор: статьи авторской энциклопедии и её карты | `0035_lore_articles.py` |
| `0036_book_illustrations` | авторские иллюстрации книги и привязка их к персонажам | `0036_book_illustrations.py` |
| `0037_audition_reactions` | реакции автора на пробы и вежливый отказ диктору | `0037_audition_reactions.py` |
| `0038_drop_v1_tables` | удалить таблицы и колонки конвейера v1 | `0038_drop_v1_tables.py` |
| `0039_dictors` | раздел «Дикторы»: карточки, демо, ссылки, агрегат назначений, журнал рекастов | `0039_dictors.py` |
| `0040_bot_intros` | бот: знакомство — кого бот спросил «Как вас зовут?» | `0040_bot_intros.py` |
| `0041_role_deadlines` | сроки проб и ролей: таблица role_deadlines, дата срока ролей в настройках студии | `0041_role_deadlines.py` |
| `0042_bot_broadcasts` | бот: рассылка касту книги или всем дикторам | `0042_bot_broadcasts.py` |
| `0043_bot_contacts` | бот: учёт контактов (кто вышел на связь) и время последней сводки владельцу | `0043_bot_contacts.py` |
| `0044_provider_keys_step_models` | Ключи нейросетей и модели шагов — на сайте. | `0044_provider_keys_step_models.py` |
| `0045_spend` | Журнал трат, цены моделей и месячный лимит. | `0045_spend.py` |
