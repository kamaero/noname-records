# Справочник HTTP-маршрутов

Список получен через интроспекцию реального приложения — импортируется `app.main:app` и обходится `app.routes` (только `fastapi.routing.APIRoute`). Колонка «Обработчик» — модуль и имя функции, «Описание» — первая строка docstring обработчика, если она есть.

Всего маршрутов: **142**, групп: **35**.

_Сгенерировано из коммита unknown._

## `/` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/` | `app.api.system_routes:build_system_routes_handlers.<locals>.index_redirect` |  |

## `/admin` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/admin` | `app.api.system_routes:build_system_routes_handlers.<locals>.admin_redirect` |  |

## `/api/auditions` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/auditions/feed` | `app.api.auditions_feed:api_auditions_feed` |  |

## `/api/books` (4)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/books` | `app.api.books_list:build_books_list_handlers.<locals>.api_books` |  |
| POST | `/api/books/upload` | `app.api.book_actions:build_book_actions_handlers.<locals>.api_upload_book` |  |
| POST | `/api/books/{book_id}/delete` | `app.api.book_actions:build_book_actions_handlers.<locals>.api_delete_book` |  |
| POST | `/api/books/{book_id}/repair` | `app.api.book_actions:build_book_actions_handlers.<locals>.api_book_repair_action` |  |

## `/api/budget` (4)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/budget/character/{char_id}` | `app.api.budget_api:build_budget_api_handlers.<locals>.api_save_budget_character` | Карточка персонажа. Агенту здесь открыто одно поле — актёр, и то с вопросом. |
| GET | `/api/budget/{book_id}` | `app.api.budget_api:build_budget_api_handlers.<locals>.api_book_budget` |  |
| POST | `/api/budget/{book_id}` | `app.api.budget_api:build_budget_api_handlers.<locals>.api_save_book_budget` |  |
| POST | `/api/budget/{book_id}/card` | `app.api.budget_api:build_budget_api_handlers.<locals>.api_save_book_card` |  |

## `/api/deadlines` (2)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/deadlines/{deadline_id}/close` | `app.api.dictors:api_deadline_close` |  |
| POST | `/api/deadlines/{deadline_id}/extend` | `app.api.dictors:api_deadline_extend` | Продлить срок — только админ (решение владельца 01.10). Диктору — письмо с новой датой. |

## `/api/dictors` (11)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/dictors` | `app.api.dictors:api_dictors_list` |  |
| POST | `/api/dictors` | `app.api.dictors:api_dictor_create` |  |
| DELETE | `/api/dictors/demos/{demo_id}` | `app.api.dictors:api_dictor_demo_delete` |  |
| GET | `/api/dictors/demos/{demo_id}/audio` | `app.api.dictors:api_dictor_demo_audio` | MP3 демо с поддержкой `Range` — иначе у плеера не двигается ползунок. |
| GET | `/api/dictors/picker` | `app.api.dictors:api_dictors_picker` | Список для выбора актёра в касте. Агент (кастинг-директор) видит имена и «на связи»; |
| DELETE | `/api/dictors/{user_id}` | `app.api.dictors:api_dictor_delete` |  |
| GET | `/api/dictors/{user_id}` | `app.api.dictors:api_dictor_card` |  |
| PATCH | `/api/dictors/{user_id}` | `app.api.dictors:api_dictor_rename` |  |
| POST | `/api/dictors/{user_id}/demos` | `app.api.dictors:api_dictor_demo_upload` | Любое аудио или видео; в демо уходят первые две минуты, MP3 192 кбит/с (ffmpeg). |
| POST | `/api/dictors/{user_id}/main-demo` | `app.api.dictors:api_dictor_main_demo` |  |
| PATCH | `/api/dictors/{user_id}/note` | `app.api.dictors:api_dictor_note` |  |

## `/api/log` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/log` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_log` |  |

## `/api/me` (3)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/me` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_me` |  |
| GET | `/api/me/bot-reach` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_me_bot_reach` | Кнопка «Проверить ещё раз» в окне онбординга: свежий ответ мимо кэша. |
| POST | `/api/me/onboarding-shown` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_onboarding_shown` | Автопоказ фиксируется сюда — ручное открытие из шапки этого не делает. |

## `/api/public` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/public/auth-config` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_public_auth_config` |  |

## `/api/recording` (5)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/recording/batch` | `app.api.recording:build_recording_handlers.<locals>.recording_batch` | Дубли утверждённых ролей — и, отдельным видом, пробы на роль. |
| GET | `/api/recording/filename` | `app.api.recording:build_recording_handlers.<locals>.recording_filename_hint` | Точное имя, которым диктору назвать этот файл. |
| POST | `/api/recording/files/{audio_id}/delete` | `app.api.recording:build_recording_handlers.<locals>.recording_delete_audio` | Стереть загруженную запись — кнопка «удалить» в разделе записи. |
| POST | `/api/recording/replica-patch` | `app.api.recording:build_recording_handlers.<locals>.recording_replica_patch` |  |
| GET | `/api/recording/workspace` | `app.api.recording:build_recording_handlers.<locals>.recording_workspace` |  |

## `/api/settings` (2)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/settings/rate` | `app.api.budget_api:build_budget_api_handlers.<locals>.api_studio_rate` |  |
| POST | `/api/settings/rate` | `app.api.budget_api:build_budget_api_handlers.<locals>.api_save_studio_rate` | The studio's default rate. Books do not store it; they read it. |

## `/api/telegram` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/telegram/webhook` | `app.api.telegram_webhook:api_telegram_webhook` |  |

## `/api/users` (12)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/users` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_users` |  |
| POST | `/api/users` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_create_user` | A new account. The generated password comes back once and is never readable again. |
| GET | `/api/users/bot-reach` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_bot_reach` | Кому бот может написать. Вход через виджет и письма бота — разные каналы. |
| POST | `/api/users/bulk-roles` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_bulk_user_roles` | One role for many accounts — the 49 dictors who were all given `author`. |
| GET | `/api/users/export.txt` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_users_export` | Реестр доступов текстом. Паролей в нём нет — их нет и в системе. |
| POST | `/api/users/issue-passwords` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_users_issue_passwords` | Выдать пароли тем, у кого их нет, и вернуть реестр вместе с ними. |
| POST | `/api/users/merge` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_merge_users` | Two accounts of one person become one. The keeper is named by the caller. |
| POST | `/api/users/telegram-whitelist` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_save_telegram_whitelist` |  |
| DELETE | `/api/users/{user_id}` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_delete_user` |  |
| PATCH | `/api/users/{user_id}` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_update_user` |  |
| POST | `/api/users/{user_id}/password` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_reset_user_password` |  |
| POST | `/api/users/{user_id}/telegram` | `app.api.frontend_core:build_frontend_core_handlers.<locals>.api_attach_telegram` | Привязать телеграм к учётке — там, где владелец на неё и смотрит. |

## `/api/v2/ambient` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/v2/ambient/{audio_file_id}/audio` | `app.v2.ambient_api:api_v2_ambient_audio` | `GET /api/v2/ambient/{id}/audio` — сам трек, для прослушивания в карточке сцены. |

## `/api/v2/auditions` (2)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/v2/auditions/{audio_id}/audio` | `app.v2.api:api_v2_audition_audio` | Сама запись. Отдаётся с поддержкой `Range` — иначе у плеера не двигается ползунок. |
| POST | `/api/v2/auditions/{audio_id}/reaction` | `app.v2.api:api_v2_audition_reaction` | 👍/👎 на пробу. Ставит только автор — не «редактор»: `admin` тут не подразумевается, |

## `/api/v2/authors` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/v2/authors/{author_id}/cast-discrepancies` | `app.v2.api:api_v2_cast_discrepancies` | `GET /api/v2/authors/{id}/cast-discrepancies` — где у персонажа цикла разные актёры. |

## `/api/v2/books` (45)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/v2/books/{book_id}/approve-all` | `app.v2.api:api_v2_approve_all` | «Прочитал всё»: every marked-up chapter of the book at once. |
| GET | `/api/v2/books/{book_id}/auditions` | `app.v2.api:api_v2_book_auditions` | Пробы этой книги — слушают все, утверждают на роль двое. |
| POST | `/api/v2/books/{book_id}/author` | `app.v2.api:api_v2_bind_author` | Point the book at an author — and pull that author's cast down onto it. |
| POST | `/api/v2/books/{book_id}/auto-publish` | `app.v2.api:api_v2_auto_publish` | Whether an approved chapter opens for recording at once. |
| GET | `/api/v2/books/{book_id}/cast` | `app.v2.api:api_v2_cast` |  |
| GET | `/api/v2/books/{book_id}/chapters` | `app.v2.api:api_v2_book_chapters` |  |
| GET | `/api/v2/books/{book_id}/character-map` | `app.v2.api:api_v2_character_map` | Карта персонажей вместе с разметкой — рабочий стол автора, не диктора. |
| POST | `/api/v2/books/{book_id}/character-map/acknowledge` | `app.v2.api:api_v2_character_map_acknowledge` | Подтвердить, что пересечение пары ролей одного актёра — так задумано. |
| POST | `/api/v2/books/{book_id}/character-map/adopt` | `app.v2.api:api_v2_character_map_adopt` | Завести в карте имя, которое уже говорит в разметке. |
| POST | `/api/v2/books/{book_id}/character-map/checked` | `app.v2.api:api_v2_character_map_checked` | Отметить, что автор проверил карту персонажей целиком. |
| POST | `/api/v2/books/{book_id}/character-map/delete` | `app.v2.api:api_v2_character_map_delete` | Убрать молчащего фантома из карты. Говорящих и записанных не трогает. |
| POST | `/api/v2/books/{book_id}/character-map/forget` | `app.v2.api:api_v2_character_map_forget` | Отменить подтверждение пары — предупреждение про пересечение вернётся. |
| POST | `/api/v2/books/{book_id}/character-map/merge` | `app.v2.api:api_v2_character_map_merge` | Переклеить реплики одного имени на другое и убрать исходное из карты. |
| POST | `/api/v2/books/{book_id}/character-map/rename` | `app.v2.api:api_v2_character_map_rename` | Переименовать роль. Согласие на переезд реплик приходит вторым заходом: |
| POST | `/api/v2/books/{book_id}/character-map/update` | `app.v2.api:api_v2_character_map_update` | Поменять поля роли: расу, возраст, характер, голос, алиасы, диктора. |
| POST | `/api/v2/books/{book_id}/characters` | `app.v2.api:api_v2_create_role` | Add a role to the book's cast — the reader's «новая роль». |
| GET | `/api/v2/books/{book_id}/consilium` | `app.v2.api:api_v2_consilium` | Находки консилиума по книге — сводка и список, при желании по одной главе. |
| GET | `/api/v2/books/{book_id}/consilium/estimate` | `app.v2.api:api_v2_consilium_estimate` |  |
| POST | `/api/v2/books/{book_id}/consilium/run` | `app.v2.api:api_v2_consilium_run` |  |
| POST | `/api/v2/books/{book_id}/consilium/stop` | `app.v2.api:api_v2_consilium_stop` |  |
| GET | `/api/v2/books/{book_id}/disputed` | `app.v2.api:api_v2_disputed` | Lines the model would not name a speaker for, or named with a hedge. |
| GET | `/api/v2/books/{book_id}/homographs` | `app.v2.api:api_v2_homographs` | «Спорные ударения»: what the model chose where two readings were possible. |
| GET | `/api/v2/books/{book_id}/illustrations` | `app.v2.illustrations_api:api_v2_book_illustrations` | Все иллюстрации книги с подсказками — вход экрана привязки. |
| GET | `/api/v2/books/{book_id}/lore` | `app.v2.lore_api:api_v2_book_lore` | Оглавление статей и карточки сущностей для книги этого автора. |
| GET | `/api/v2/books/{book_id}/lore/search` | `app.v2.lore_api:api_v2_lore_search` | Поиск по телам статей: слово и то, что вокруг него. |
| POST | `/api/v2/books/{book_id}/model` | `app.v2.api:api_v2_set_model` | Point the book at one of the catalogued models. The next run uses it. |
| GET | `/api/v2/books/{book_id}/profile` | `app.v2.api:api_v2_profile` |  |
| POST | `/api/v2/books/{book_id}/profile/apply` | `app.v2.api:api_v2_profile_apply` |  |
| POST | `/api/v2/books/{book_id}/profile/sync` | `app.v2.api:api_v2_profile_sync` |  |
| GET | `/api/v2/books/{book_id}/progress` | `app.v2.api:api_v2_progress` |  |
| POST | `/api/v2/books/{book_id}/publish` | `app.v2.api:api_v2_publish` | `POST /api/v2/books/{book_id}/publish` — approved chapters to the actors. |
| GET | `/api/v2/books/{book_id}/recording` | `app.v2.api:api_v2_book_recording` | Сколько ролей записано в каждой главе книги — и какие главы можно забирать. |
| GET | `/api/v2/books/{book_id}/role-script` | `app.v2.api:api_v2_role_script` | Every line of one role across the book, with the paragraphs around it. |
| GET | `/api/v2/books/{book_id}/role-traps` | `app.v2.api:api_v2_role_traps` | «Слова-ловушки»: редкие слова из реплик роли, по главам, с ударением. |
| POST | `/api/v2/books/{book_id}/run` | `app.v2.api:api_v2_run` | Put the v2 pipeline on the `high` queue for the book; 409 while a run is active. |
| GET | `/api/v2/books/{book_id}/sound` | `app.v2.sound_api:api_v2_sound_book` | `GET /api/v2/books/{id}/sound` — растущий список мест книги, пары на решение, прогон. |
| GET | `/api/v2/books/{book_id}/sound/estimate` | `app.v2.sound_api:api_v2_sound_estimate` | `GET /api/v2/books/{id}/sound/estimate` — смета, баланс, заблокированность кнопок. |
| POST | `/api/v2/books/{book_id}/sound/run` | `app.v2.sound_api:api_v2_sound_run` | `POST /api/v2/books/{id}/sound/run` — запустить чтение книги (`rest`/`all`). |
| POST | `/api/v2/books/{book_id}/sound/stop` | `app.v2.sound_api:api_v2_sound_stop` | `POST /api/v2/books/{id}/sound/stop` — попросить прогон остановиться на ближайшей главе. |
| POST | `/api/v2/books/{book_id}/stop` | `app.v2.api:api_v2_stop` |  |
| GET | `/api/v2/books/{book_id}/stress-queue` | `app.v2.api:api_v2_stress_queue` |  |
| POST | `/api/v2/books/{book_id}/stress-skip` | `app.v2.api:api_v2_stress_skip` | «Ударение тут не нужно» — the word leaves this book's queue, or comes back. |
| POST | `/api/v2/books/{book_id}/stress-term` | `app.v2.api:api_v2_stress_term` |  |
| POST | `/api/v2/books/{book_id}/title` | `app.v2.api:api_v2_set_book_title` | Переименовать книгу: автор и название порознь, витрина собирается сама. |
| POST | `/api/v2/books/{book_id}/unpublish` | `app.v2.api:api_v2_unpublish` |  |

## `/api/v2/chapters` (15)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/v2/chapters/{chapter_id}/ambient` | `app.v2.ambient_api:api_v2_ambient_chapter` | `POST /api/v2/chapters/{id}/ambient` — сгенерировать треки сценам главы без готового. |
| GET | `/api/v2/chapters/{chapter_id}/ambient/plan` | `app.v2.ambient_api:api_v2_ambient_plan` | `GET /api/v2/chapters/{id}/ambient/plan` — смета для подтверждения запуска: сколько |
| POST | `/api/v2/chapters/{chapter_id}/ambient/stop` | `app.v2.ambient_api:api_v2_ambient_stop` | `POST /api/v2/chapters/{id}/ambient/stop` — попросить генерацию главы остановиться. |
| POST | `/api/v2/chapters/{chapter_id}/approve` | `app.v2.api:api_v2_chapter_approve` | The author's «проверено» on one chapter, or taking it back. |
| GET | `/api/v2/chapters/{chapter_id}/archive.zip` | `app.v2.api:api_v2_chapter_archive` | Дубли главы одним архивом — исходник для сведения. |
| POST | `/api/v2/chapters/{chapter_id}/asr` | `app.v2.api:api_v2_chapter_asr` | Поставить главу в очередь на распознавание. |
| GET | `/api/v2/chapters/{chapter_id}/recording` | `app.v2.api:api_v2_chapter_recording` | Кто из говорящих ролей главы уже записан, а кто ещё нет. |
| GET | `/api/v2/chapters/{chapter_id}/script` | `app.v2.api:api_v2_chapter_script` |  |
| POST | `/api/v2/chapters/{chapter_id}/session-archive` | `app.v2.api:api_v2_chapter_session_archive` | `POST /api/v2/chapters/{id}/session-archive` — положить .sesx на NAS. |
| GET | `/api/v2/chapters/{chapter_id}/session.sesx` | `app.v2.api:api_v2_chapter_session` | Сессия Audition для главы: треки по ролям, клипы по репликам. |
| GET | `/api/v2/chapters/{chapter_id}/sound` | `app.v2.sound_api:api_v2_sound_chapter` | `GET /api/v2/chapters/{id}/sound` — маркеры главы: список, потерянные, места. |
| POST | `/api/v2/chapters/{chapter_id}/sound/markers` | `app.v2.sound_api:api_v2_sound_marker_add` | `POST /api/v2/chapters/{id}/sound/markers` — ручная сцена/переход/звук. |
| POST | `/api/v2/chapters/{chapter_id}/sound/run` | `app.v2.sound_api:api_v2_sound_chapter_run` | `POST /api/v2/chapters/{id}/sound/run` — перечитать одну главу (`mode="chapter"`). |
| GET | `/api/v2/chapters/{chapter_id}/source` | `app.v2.api:api_v2_chapter_source` | The chapter's original text, blocked to line up with the script pane. |
| POST | `/api/v2/chapters/{chapter_id}/verify` | `app.v2.api:api_v2_chapter_verify` | Сверить главу по кнопке. Ответ — сразу, работа — в фоне. |

## `/api/v2/characters` (4)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/v2/characters/{character_id}/about` | `app.v2.api:api_v2_character_about` | Who the character is and how he should sound. Dictors read it; they do not write it. |
| POST | `/api/v2/characters/{character_id}/palette` | `app.v2.api:api_v2_character_palette` | Colours only. |
| POST | `/api/v2/characters/{character_id}/recast` | `app.v2.api:api_v2_character_recast` | `POST /api/v2/characters/{id}/recast` — заменить актёра персонажа по циклу «вперёд». |
| GET | `/api/v2/characters/{character_id}/recast-preview` | `app.v2.api:api_v2_character_recast_preview` | `GET /api/v2/characters/{id}/recast-preview?to_actor=` — где сменится, где останется. |

## `/api/v2/consilium` (3)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/v2/consilium/{finding_id}/accept` | `app.v2.api:api_v2_consilium_accept` |  |
| POST | `/api/v2/consilium/{finding_id}/dismiss` | `app.v2.api:api_v2_consilium_dismiss` |  |
| GET | `/api/v2/consilium/{finding_id}/recording-impact` | `app.v2.api:api_v2_consilium_recording_impact` | `GET /api/v2/consilium/{id}/recording-impact` — что станет со звуком при каждом имени каста. |

## `/api/v2/illustrations` (2)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/v2/illustrations/{illustration_id}` | `app.v2.illustrations_api:api_v2_bind_illustration` | Сказать, кто на картинке, или отметить её сценой без портрета. |
| GET | `/api/v2/illustrations/{illustration_id}/image` | `app.v2.illustrations_api:api_v2_illustration_image` | Сама картинка. Видит любой вошедший — портрет для того и нужен. |

## `/api/v2/lore` (2)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/v2/lore/articles/{article_id}` | `app.v2.lore_api:api_v2_lore_article` | Тело одной статьи — приходит по клику, а не списком. |
| GET | `/api/v2/lore/images/{author_id}/{image_key}` | `app.v2.lore_api:api_v2_lore_image` | Карта или схема из энциклопедии. |

## `/api/v2/segments` (2)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/api/v2/segments/{segment_id}/attribution` | `app.v2.api:api_v2_reassign_segment` | `POST /api/v2/segments/{segment_id}/attribution` — the operator's word on who speaks. |
| POST | `/api/v2/segments/{segment_id}/stress` | `app.v2.api:api_v2_segment_stress` | One place, one reading — the decision a homograph needs and a rule cannot give. |

## `/api/v2/sound` (5)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| DELETE | `/api/v2/sound/markers/{marker_id}` | `app.v2.sound_api:api_v2_sound_marker_dismiss` | `DELETE /api/v2/sound/markers/{id}` — отклонить маркер (не воскресает прогоном). |
| PATCH | `/api/v2/sound/markers/{marker_id}` | `app.v2.sound_api:api_v2_sound_marker_edit` | `PATCH /api/v2/sound/markers/{id}` — правка полей, перенос на другой абзац. |
| POST | `/api/v2/sound/markers/{marker_id}/ambient` | `app.v2.ambient_api:api_v2_ambient_scene` | `POST /api/v2/sound/markers/{id}/ambient` — перегенерировать трек одной сцены. |
| POST | `/api/v2/sound/pairs/{pair_id}` | `app.v2.sound_api:api_v2_sound_pair_decide` | `POST /api/v2/sound/pairs/{id}` — решить кандидата на склейку мест: слить или оставить порознь. |
| PATCH | `/api/v2/sound/places/{place_id}` | `app.v2.sound_api:api_v2_sound_place_edit` | `PATCH /api/v2/sound/places/{id}` — имя, описание, запросы подложки. |

## `/api/v2/takes` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/api/v2/takes/{audio_id}/audio` | `app.v2.api:api_v2_take_audio` | Дубль главы: послушать в описи или забрать файлом. |

## `/app` (2)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/app` | `app.main:_spa_index` |  |
| GET | `/app/{full_path:path}` | `app.main:_spa_catchall` |  |

## `/auth` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/auth/telegram` | `app.auth_routes:register_auth_routes.<locals>.login_telegram` |  |

## `/dashboard` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/dashboard` | `app.api.system_routes:build_system_routes_handlers.<locals>.dashboard_redirect` |  |

## `/dictor-pro` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/dictor-pro/batch-validate` | `app.api.dictor_uploads:build_dictor_upload_handlers.<locals>.dictor_pro_batch_validate` |  |

## `/health` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/health` | `app.api.system_routes:build_system_routes_handlers.<locals>.health` |  |

## `/login` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/login` | `app.auth_routes:register_auth_routes.<locals>.login` |  |

## `/logout` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| POST | `/logout` | `app.auth_routes:register_auth_routes.<locals>.logout` |  |

## `/validation` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/validation` | `app.api.system_routes:build_system_routes_handlers.<locals>.validation_redirect` |  |

## `/workspace` (1)

| Методы | Путь | Обработчик | Описание |
|---|---|---|---|
| GET | `/workspace` | `app.api.system_routes:build_system_routes_handlers.<locals>.workspace_redirect` |  |
