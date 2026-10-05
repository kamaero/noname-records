import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.time_utils import utcnow_naive


class AudioFile(Base):
    __tablename__ = "audio_files"
    __table_args__ = (
        Index("ix_audio_files_book_code", "book_code"),
        Index("ix_audio_files_canonical_filename", "canonical_filename"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_code: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter: Mapped[str] = mapped_column(String(60), nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(120), nullable=True, default="")
    line_index: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    canonical_filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: «take» — дубль утверждённой роли; «audition» — проба на роль, у неё нет главы
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="take", server_default="take")
    # Что сказал о себе сам файл. Размер в байтах не отличает речь от тишины и не даёт
    # длительности — а она нужна и смете, и описи главы, и сборке в монтажке.
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    channels: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    codec: Mapped[str] = mapped_column(String(40), nullable=False, default="", server_default="")
    #: md5 — из ДВУХ разных источников, и они доказывают разное. У большинства строк
    #: это сумма приёма: посчитана в момент, когда диктор прислал файл, и подтверждает,
    #: что записано именно то. У записей старше миграции 0020 суммы приёма не было
    #: вовсе — сверять было не с чем; переезд в новую раскладку
    #: (scripts/migrate_audio_layout.py) досчитывает таким сумму задним числом, с уже
    #: скопированной копии, — она доказывает только «файл не менялся с момента
    #: переезда», а НЕ то, что записал диктор: порча, случившаяся на NAS до переезда,
    #: войдёт в такую сумму как эталон, а не будет поймана. Пусто — сумма ещё не
    #: появилась ни одним из двух путей (переезд мог ещё не пройти или пройти частично).
    md5: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    #: «local» — файл принят на диск сервера (нынешняя норма); «nas» — легаси-строка
    #: с миграции: у такой записи единственная копия так и осталась на NAS.
    # Основное место файла — локальный диск, NAS ему зеркало. `server_default`
    # остаётся прежним: менять умолчание колонки в SQLite значит пересобирать
    # таблицу, а вставок мимо ORM здесь нет, и шестьдесят записей до переезда
    # лежат на NAS по-настоящему — их значение переписывает скрипт переезда.
    location: Mapped[str] = mapped_column(String(8), nullable=False, default="local", server_default="nas")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    #: «» не сверяли · «ok» · «mismatch» файл есть, сумма не та · «missing» файла нет
    verify_state: Mapped[str] = mapped_column(String(16), nullable=False, default="", server_default="")
    #: Когда копия этого файла подтверждена на NAS перечитыванием и сверкой суммы.
    #: Пусто — вторая копия ещё нет; окно, в котором запись существует в одном
    #: экземпляре, и его видно на экране главы.
    mirrored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    #: «» не копировали · «ok» сумма сошлась · «mismatch» копия на NAS битая
    mirror_state: Mapped[str] = mapped_column(String(16), nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="uploaded")
    #: когда файл прислали. Без `onupdate`: отметка о событии не меняется оттого, что мы
    #: правим строку, — с ним переклассификация проб задним числом сделала два десятка
    #: старых записей свежими, и «Последние файлы главы» показали не то.
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class PendingMirrorDeletion(Base):
    """Поручение фоновому циклу: стереть эту копию на NAS.

    Не корзина и не история удалений. Строка `audio_files` к этому моменту уже
    удалена, спросить «что стирать» будет не у кого, а трогать NAS из обработчика
    запроса нельзя: мёртвое `hard`-монтирование не отдаёт ошибку, а виснет.
    Поручение живёт минуты и исчезает, когда цикл отработал.
    """
    __tablename__ = "pending_mirror_deletions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    stored_key: Mapped[str] = mapped_column(String(512), nullable=False)
    book_code: Mapped[str] = mapped_column(String(40), nullable=False, default="", server_default="")
    #: чьё имя стоит в журнале вмешательств рядом с этим удалением
    deleted_by: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class AsrJob(Base):
    __tablename__ = "asr_jobs"
    __table_args__ = (
        Index("ix_asr_jobs_audio_file_id", "audio_file_id"),
        Index("ix_asr_jobs_chapter_id", "chapter_id"),
        Index("ix_asr_jobs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audio_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="openai")
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="whisper-1")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    decision: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    expected_role: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    detected_role: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    expected_chapter_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_chapter_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    similarity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    timing_fit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    transcript_text: Mapped[str] = mapped_column(String, nullable=False, default="")
    #: Что услышал робот, в нашем виде: текст целиком плюс сегменты со словными
    #: таймингами — ровно то, что уходит в `align_transcript`. Храним потому, что
    #: распознавание невоспроизводимо и платно (робот слушает файл один раз), а сверка
    #: со сценарием своя, бесплатная и меняется постоянно. Пока услышанное
    #: выбрасывалось, каждая правка сверки доставалась только будущим записям, а архив
    #: переигрывался лишь повторной оплатой распознавания.
    #: Пусто — джоба посчитана до появления графы: вход выброшен, переигрывать нечем.
    heard_json: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    alignment_json: Mapped[str] = mapped_column(String, nullable=False, default="")
    error_message: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class ScriptBook(Base):
    __tablename__ = "script_books"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    #: Чистое название произведения. Из него выводится код книги, который актёры
    #: пишут в именах файлов (`app.services.audio_uploads.derive_book_code`), —
    #: поэтому автору тут не место, для него есть `author_label`.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Автор так, как его показывают («Белозёровы»). Витрина, а не канон: канонический
    #: `Author` («Александр Белозёров») висит на `author_id` и тянет ростер и цвета.
    author_label: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    #: Собранная витрина «Автор - "Название"» — её читают все экраны и бот.
    #: Собирается при записи в `app.services.book_title.apply_book_title`.
    display_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)
    total_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    author_sheets_x1000: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Когда автор в последний раз прошёл карту персонажей и сказал «проверено».
    #: Не шаг конвейера: пайплайн этого не ждёт, а режиссёр по отметке судит,
    #: можно ли карте верить.
    cast_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cast_checked_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    has_chapters: Mapped[str] = mapped_column(String(5), nullable=False, default="false")
    pipeline_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="standard")
    validation_profile: Mapped[str] = mapped_column(String(40), nullable=False, default="operator_only")
    genre_guidelines: Mapped[str] = mapped_column(String, nullable=False, default="")
    book_annotation: Mapped[str] = mapped_column(String, nullable=False, default="")
    narrative_owner_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    narrative_owner_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    narrative_owner_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    domain_lexicon: Mapped[str] = mapped_column(String, nullable=False, default="")
    pronunciation_notes: Mapped[str] = mapped_column(String, nullable=False, default="")
    stop_requested: Mapped[str] = mapped_column(String(5), nullable=False, default="false")
    #: "true" — a chapter the author approves opens for recording at once, without
    #: waiting for anybody to press «Опубликовать». See `app.v2.review_ops`.
    auto_publish: Mapped[str] = mapped_column(String(5), nullable=False, default="false")
    needs_investigation: Mapped[str] = mapped_column(String(5), nullable=False, default="false")
    current_pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    previous_pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_by_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    author_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    char_map_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Which model marks this book up; empty means the studio default. The columns are
    # older than v2 — they were dropped from the mapping when the v1 stages went, and the
    # per-book model choice needs them back. See `app.v2.model_catalog`.
    llm_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    llm_model: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="uploaded")
    # Last terminal status announced to Telegram — used to send exactly one notification per
    # outcome (no spam from recovery oscillation). Re-armed ("") when a new pipeline run starts.
    last_notified_status: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class ScriptChapter(Base):
    __tablename__ = "script_chapters"
    __table_args__ = (
        Index("ix_script_chapters_book_id", "book_id"),
        Index("ix_script_chapters_book_status", "book_id", "status"),
        Index("ix_script_chapters_book_index", "book_id", "chapter_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_title: Mapped[str] = mapped_column(String(255), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_text: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    stress_report_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: когда главу забрали в сведение. Пусто — ещё ждёт.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    #: Когда в папку главы на NAS положен файл проекта для монтажки. Именно внутрь
    #: папки: относительные пути в сессии ссылаются на голые имена соседних файлов.
    session_archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    #: sha256 XML, положенного в архив последним. Пусто — архивировали до отпечатков.
    session_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    #: Когда архивная сессия перестала соответствовать сверке (правка роли, дозапись).
    #: Пусто — соответствует или архива нет. Снимает её фоновый круг архива.
    session_outdated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    #: Сколько маркеров звукорежиссёра не нашли себе места в последней собранной
    #: сессии (их абзац ещё не записан, дальше записанного тоже нет). Считается
    #: и кладётся сюда в момент архивации (`build_chapter_session_with_stats`),
    #: а не заново на каждом чтении таблицы ASR.
    session_markers_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Отпечаток состава дублей, по которому владельцу уже ушёл отчёт о сверке.
    #: Пусто — не уходил ни разу. Событие «глава собралась» нигде не хранится,
    #: статус считается на чтении, поэтому однократность держится на этом поле.
    coverage_reported_takes: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    coverage_reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class ScriptJob(Base):
    __tablename__ = "script_jobs"
    __table_args__ = (
        Index("ix_script_jobs_book_id", "book_id"),
        Index("ix_script_jobs_chapter_id", "chapter_id"),
        Index("ix_script_jobs_book_stage_status", "book_id", "stage", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="openai")
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    error_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    error_details_json: Mapped[str] = mapped_column(String, nullable=False, default="")
    error_message: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class ScriptLog(Base):
    __tablename__ = "script_logs"
    __table_args__ = (
        Index("ix_script_logs_book_id", "book_id"),
        Index("ix_script_logs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    event_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    event_details_json: Mapped[str] = mapped_column(String, nullable=False, default="")
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    login: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_active: Mapped[str] = mapped_column(String(5), nullable=False, default="true")
    # когда сервер в последний раз сам открыл дикторy приветственное окно; пусто — ещё
    # ни разу. Ручное открытие из шапки этого поля не трогает.
    onboarding_shown_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        Index("ix_user_roles_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class TelegramAuthAccount(Base):
    __tablename__ = "telegram_auth_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    telegram_user_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    access_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="full")
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_active: Mapped[str] = mapped_column(String(5), nullable=False, default="true")
    # the account this Telegram identity signs into; empty until the first login links it
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class BotIntro(Base):
    """Бот спросил «Как вас зовут?» и ждёт ответа (см. `app/services/bot_intro.py`)."""

    __tablename__ = "bot_intros"

    telegram_user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    asked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class RoleVote(Base):
    """Голос за то, кому читать роль. Один голос на человека, вес — по его роли в студии."""
    __tablename__ = "role_votes"
    __table_args__ = (
        Index("ix_role_votes_character", "character_id"),
        UniqueConstraint("character_id", "voter_uid", name="uq_role_votes_character_voter"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    character_id: Mapped[str] = mapped_column(String(36), nullable=False)
    voter_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    voter_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: пусто — голос за то, чтобы роль осталась ничьей
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class AuditionReaction(Base):
    """👍/👎 автора на одну пробу. Снятая реакция — удалённая строка, а не ноль."""
    __tablename__ = "audition_reactions"
    __table_args__ = (
        UniqueConstraint("audio_file_id", "voter_uid", name="uq_audition_reactions_audio_voter"),
        Index("ix_audition_reactions_audio", "audio_file_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audio_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    voter_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    voter_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: 1 — 👍, -1 — 👎
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class AuditionRejection(Base):
    """Вежливый отказ одному актёру на одну роль книги — не больше одного на всю жизнь пары.

    У актёра бывает до четырёх проб на роль; ключ по паре, а не по файлу, не даёт
    четырём 👎 стать четырьмя письмами. `book_code` — чтобы найти пробы пары: в
    `audio_files` книга названа кодом, а не идентификатором.
    """
    __tablename__ = "audition_rejections"
    __table_args__ = (
        UniqueConstraint("book_id", "role", "actor_name", name="uq_audition_rejections_pair"),
        Index("ix_audition_rejections_due", "status", "due_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    book_code: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: pending · sent · cancelled · no_account · ambiguous · failed
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (
        Index("ix_characters_book_id", "book_id"),
        Index("ix_characters_book_name", "book_id", "name"),
        Index("ix_characters_char_map_id", "char_map_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    char_map_id: Mapped[str] = mapped_column(String(36), nullable=False, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[str] = mapped_column(String, nullable=False, default="")
    appears_in: Mapped[str] = mapped_column(String, nullable=False, default="")
    race: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    temperament: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: Свободный текст, а не число: «около сорока», «юноша», «древний». Возраст роли
    #: нужен режиссёру для подбора голоса, а не для арифметики.
    age: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    character_color: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    character_text_color: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    character_font_weight: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    character_font_style: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    narrator_role: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    manual_rate_rub_per_min: Mapped[int] = mapped_column(Integer, nullable=True)
    manual_fixed_rub: Mapped[int] = mapped_column(Integer, nullable=True)
    author_character_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    operator_note: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class RolePairNote(Base):
    """«Так задумано» про пару ролей одного актёра.

    Пересечение ролей — арифметика, а его допустимость — режиссёрское решение:
    близнецы звучат одинаково намеренно, у демонов тяжёлая обработка, рассказчик
    в другом регистре. Система спрашивает один раз и запоминает ответ вместе с
    причиной — через полгода причина важнее самого решения.
    """

    __tablename__ = "role_pair_notes"
    __table_args__ = (Index("ix_role_pair_notes_book", "book_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: Имена в паре хранятся отсортированными: пара ненаправленная, и «А с Б» —
    #: та же пара, что «Б с А». Иначе одно решение записалось бы дважды.
    role_a: Mapped[str] = mapped_column(String(255), nullable=False)
    role_b: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="")
    actor_uid: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class BookBudget(Base):
    __tablename__ = "book_budget"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    narrative_cost_rub: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sound_engineer_cost_rub: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_cost_rub: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str] = mapped_column(String, nullable=False, default="")
    narrator_actor_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class CharacterBudgetSnapshot(Base):
    __tablename__ = "character_budget_snapshot"
    __table_args__ = (
        Index("ix_char_budget_snapshot_book_id", "book_id"),
        Index("ix_char_budget_snapshot_character_id", "character_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    character_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lines_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approx_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fact_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calc_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="rate_plan")
    total_rub: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    payload_json: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class LlmUsageLog(Base):
    __tablename__ = "llm_usage_logs"
    __table_args__ = (
        Index("ix_llm_usage_logs_book_id", "book_id"),
        Index("ix_llm_usage_logs_chapter_id", "chapter_id"),
        Index("ix_llm_usage_logs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_by_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated: Mapped[str] = mapped_column(String(5), nullable=False, default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class BackgroundRun(Base):
    __tablename__ = "background_runs"
    __table_args__ = (
        Index("ix_background_runs_run_key", "run_key"),
        Index("ix_background_runs_entity_id", "entity_id"),
        Index("ix_background_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    job_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    thread_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    meta_json: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_book_id", "book_id"),
        Index("ix_pipeline_runs_status", "status"),
        Index("ix_pipeline_runs_started_at", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    trigger_type: Mapped[str] = mapped_column(String(40), nullable=False, default="system")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_reason_code: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    ended_reason_text: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"
    __table_args__ = (
        Index("ix_pipeline_events_run_id", "pipeline_run_id"),
        Index("ix_pipeline_events_book_id", "book_id"),
        Index("ix_pipeline_events_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    event_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    code: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    message: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    details_json: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class OperatorIntervention(Base):

    __tablename__ = "operator_interventions"
    __table_args__ = (
        Index("ix_operator_interventions_book_id", "book_id"),
        Index("ix_operator_interventions_chapter_id", "chapter_id"),
        Index("ix_operator_interventions_run_id", "pipeline_run_id"),
        Index("ix_operator_interventions_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    actor_user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    action_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (Index("ix_authors_slug", "slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    slug: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class AuthorCharacter(Base):
    __tablename__ = "author_characters"
    __table_args__ = (
        Index("ix_author_characters_author_id", "author_id"),
        Index("ix_author_characters_author_name", "author_id", "canonical_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    author_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reply_color: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    actor_user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_topic: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    appears_in_books: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unconfirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class AuthorPronunciation(Base):
    __tablename__ = "author_pronunciations"
    __table_args__ = (Index("ix_author_pronunciations_author_id", "author_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    author_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    term: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    stressed: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    variants: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="compendium")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class StudioSettings(Base):
    """Studio-wide defaults, one row (`id == "default"`).

    The rate used to be a constant in two places — `cast_budget` and the cast table's
    own copy — so «сколько стоит минута» was a code change. It is a studio decision,
    not a product one, and a book's roles inherit it unless a role says otherwise.
    """

    __tablename__ = "studio_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    default_rate_rub_per_min: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    #: rubles per dollar, for providers that bill in dollars (DeepSeek). Set from the
    #: central bank rate on the day it was last touched; a number nobody updates in code.
    usd_rub_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: срок записи утверждённой роли, «ГГГГ-ММ-ДД», конец дня по Москве (решение владельца
    #: 01.10: один общий срок, продление — по договорённости)
    role_deadline_date: Mapped[str] = mapped_column(String(10), nullable=False, default="2026-12-31",
                                                    server_default="2026-12-31")
    #: день (МСК), когда уже ушла утренняя сводка сроков владельцу — чтобы не дважды
    deadline_digest_day: Mapped[str] = mapped_column(String(10), nullable=False, default="", server_default="")
    #: когда бот последний раз составлял владельцу сводку «кто вышел на связь»
    contacts_reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class ProviderKey(Base):
    """Ключ нейросети, введённый на сайте. Хранится зашифрованным: база уходит в бэкапы,
    и ключ, лежащий в ней открыто, утёк бы вместе с любой копией."""

    __tablename__ = "provider_keys"

    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(String, nullable=False, default="")
    last4: Mapped[str] = mapped_column(String(4), nullable=False, default="")
    check_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    check_detail: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class StepModel(Base):
    """Модель, выбранная студией для шага. Нет строки — значение по умолчанию из кода."""

    __tablename__ = "step_models"

    step: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class ConsiliumFinding(Base):
    """Спорное место, найденное консилиумом: где, какого рода, что решил человек.

    Находка — не приговор, а кандидат: два независимых чтеца прочли абзац иначе, чем
    стоит в сценарии. Решение всегда за человеком, поэтому `status` живёт отдельно от
    самого спора и переживает повторные прогоны.
    """

    __tablename__ = "consilium_findings"
    __table_args__ = (
        Index("ix_consilium_findings_book", "book_id", "status"),
        Index("ix_consilium_findings_segment", "segment_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segment_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    span_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: wrong_voice · identity_play · narrator_border · readers_split
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="wrong_voice")
    current_speaker: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: общее мнение чтецов; пусто у `readers_split` — там общего мнения нет по определению
    readers_speaker: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: ответ каждого чтеца порознь: у `readers_split` расхождение и есть весь смысл находки,
    #: а без этих двух полей строка не показывает человеку ничего и принять её нечем
    reader_opus: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    reader_sol: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    arbiter_verdict: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    arbiter_speaker: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    evidence_para: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_quote: Mapped[str] = mapped_column(String, nullable=False, default="")
    #: доказана ли цитата машинно — найдена дословно в названном абзаце и рядом с местом
    evidence_proven: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="")
    #: new · accepted · dismissed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    decided_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    #: чем кончилось: применённое имя при приёме, нынешнее — при «оставить как есть»
    decided_speaker: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    run_label: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


SOUND_KINDS = ("scene", "transition", "sound")


class SoundPlace(Base):
    """Место книги для звукорежиссёра: одно место — одна строка на всю книгу.

    Одно имя в главах 3, 17 и 42 — это одна подложка, подобранная один раз.
    `merged_into` — склеено с другим местом; такая строка больше не показывается.
    """

    __tablename__ = "sound_places"
    __table_args__ = (Index("ix_sound_places_book", "book_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ambience_queries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    merged_into: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class SoundPlacePair(Base):
    """Кандидат на склейку двух мест и решение человека: `candidate` · `merged` · `apart`.

    `apart` помнится, чтобы следующий прогон не предлагал ту же пару снова.
    """

    __tablename__ = "sound_place_pairs"
    __table_args__ = (Index("ix_sound_place_pairs_book", "book_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    place_a: Mapped[str] = mapped_column(String(36), nullable=False)
    place_b: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="candidate")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class SoundMarker(Base):
    """Маркер звукорежиссёра на абзаце: сцена, переход или значимый звук.

    Сценарий не трогается — это отдельный слой. `source='human'` прогон не переписывает,
    `status='dismissed'` не воскрешает; `lost` — текст главы сменился и цитата не нашлась.
    """

    __tablename__ = "sound_markers"
    __table_args__ = (
        Index("ix_sound_markers_chapter", "chapter_id", "status"),
        Index("ix_sound_markers_book", "book_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False)
    segment_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="llm")
    place_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    quote: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class AmbientTrack(Base):
    """Фоновый трек ElevenLabs для одной сцены (`marker_id` — строка `sound_markers`,
    `kind='scene'`).

    `audio_file_id` пуст, пока генерация не завершилась успехом. Перегенерация не трогает
    старую строку файла на диске — прежний трек получает `status='replaced'`, новый пишется
    отдельной строкой. `error` — короткий текст последнего сбоя, для строки «квота исчерпана»
    / «ошибка» в интерфейсе.
    """

    __tablename__ = "ambient_tracks"
    __table_args__ = (
        Index("ix_ambient_tracks_chapter_status", "chapter_id", "status"),
        Index("ix_ambient_tracks_marker", "marker_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False)
    marker_id: Mapped[str] = mapped_column(String(36), nullable=False)
    audio_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    song_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: 'pending' | 'done' | 'failed' | 'replaced'
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class LoreArticle(Base):
    """Статья авторской энциклопедии — шпаргалка диктору о мире книги.

    Привязана к АВТОРУ, а не к книге: у Белозёровых «Крылья» хронологически стоят между
    четвёртой и пятой «Семьёй волшебников», арки и персонажи сквозные, и делить лор по
    книгам значило бы прятать от актёра половину мира, который он озвучивает.

    Тело статьи здесь целиком (крупнейшая — «Магия», 131 КБ), поэтому список статей и
    их тела ходят разными ручками: оглавление лёгкое, тело приходит по клику.
    """

    __tablename__ = "lore_articles"
    __table_args__ = (
        Index("ix_lore_articles_author_topic", "author_id", "topic", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    author_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: заголовок раздела в энциклопедии, он же ключ обновления («Полумрак. Бароны.»)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: ключи картинок раздела через запятую — карты мира и схемы
    image_keys: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class LoreImage(Base):
    """Карта или схема из энциклопедии. Сам файл лежит на диске, тут — что это и где."""

    __tablename__ = "lore_images"
    __table_args__ = (
        Index("ix_lore_images_author_key", "author_id", "image_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    author_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: имя внутри fb2 («img14.jpg») — им же статья на картинку и ссылается
    image_key: Mapped[str] = mapped_column(String(120), nullable=False)
    content_type: Mapped[str] = mapped_column(String(60), nullable=False, default="image/jpeg")
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    bytes_len: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class BookIllustration(Base):
    """Авторская иллюстрация из fb2 книги — и кто на ней.

    В книге картинки не подписаны: кто изображён, видно только из текста вокруг. Поэтому
    строка знает свой контекст (абзацы рядом), по нему экран привязки предлагает
    кандидатов из каста книги, а решает человек — `character_id` ставится его рукой.

    Привязка идёт к канон-карточке автора (`author_characters`), а не к персонажу книги:
    портрет героя один на весь цикл, и, привязанный однажды в «Крыльях», он покажется и
    в «Семье волшебников».
    """

    __tablename__ = "book_illustrations"
    __table_args__ = (
        Index("ix_book_illustrations_book", "book_id", "ordinal"),
        Index("ix_book_illustrations_character", "character_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    author_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    #: имя внутри fb2 («img42.jpg») — им же картинка и опознаётся при перезаливке
    image_key: Mapped[str] = mapped_column(String(120), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(60), nullable=False, default="image/jpeg")
    bytes_len: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: текст вокруг картинки — по нему человек и узнаёт, кто на ней
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: заголовок ближайшей главы, чтобы понимать, где в книге эта сцена
    chapter_hint: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: id канон-карточки автора; пусто — ещё не привязано
    character_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    #: 'new' — ждёт человека, 'bound' — привязана, 'skipped' — не портрет, сцена
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="new")
    bound_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    bound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class DictorProfile(Base):
    """Карточка диктора — одна на пользователя с ролью `dictor`. Telegram id живёт в
    `telegram_auth_accounts`, здесь только то, чего там нет."""
    __tablename__ = "dictor_profiles"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    telegram_username: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    main_demo_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)


class DictorDemo(Base):
    """Демо голоса: MP3 для прослушивания (≤ 2 мин). `source_ref` — откуда пришло при
    импорте (`<telegram id>:<сообщение>` или url), по нему повторный запуск не дублирует."""
    __tablename__ = "dictor_demos"
    __table_args__ = (Index("ix_dictor_demos_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    stored_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    md5: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    trimmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class DictorLink(Base):
    """Портфолио — ссылка на страницу, а не на запись (канал, профиль ВК, КиноПоиск…)."""
    __tablename__ = "dictor_links"
    __table_args__ = (Index("ix_dictor_links_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class DictorAssignment(Base):
    """Агрегат «диктор → книга → роль»: пересчитывается `casting.rebuild_assignments`,
    руками не правится. Пустой `character_id` — рассказчик книги."""
    __tablename__ = "dictor_assignments"
    __table_args__ = (Index("ix_dictor_assignments_user", "user_id"),
                      Index("ix_dictor_assignments_book", "book_id"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    character_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    role_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="approved")
    recorded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class Recast(Base):
    """Журнал рекастов персонажа профиля автора: кто кого сменил, почему и где."""
    __tablename__ = "recasts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    author_character_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    role_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    from_actor: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    to_actor: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    books_changed: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    books_kept: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    actor_user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class RoleDeadline(Base):
    """Срок пробы или утверждённой роли (`app/services/role_deadlines.py`).

    Открытый срок у роли — не больше одного (`closed_at IS NULL`). Закрытые остаются:
    по ним видно историю и по ним же уходит письмо о рекасте.
    """

    __tablename__ = "role_deadlines"
    __table_args__ = (
        Index("ix_role_deadlines_character", "character_id"),
        Index("ix_role_deadlines_open", "closed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    character_id: Mapped[str] = mapped_column(String(36), nullable=False)
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # audition | role
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    #: done | manual | replaced | removed | changed | baseline
    close_reason: Mapped[str | None] = mapped_column(String(12), nullable=True, default=None)
    reminded_before_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    reminded_overdue_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    recast_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    extended_by: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")


class BotBroadcast(Base):
    """Рассылка из бота: админ выбирает книгу или всех, пишет текст, подтверждает.

    Строка — и состояние разговора (`state`), и итог для журнала. Короткий `id` — потому
    что он едет в `callback_data` кнопок, а там предел 64 байта.
    """

    __tablename__ = "bot_broadcasts"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=lambda: uuid.uuid4().hex[:8])
    admin_tid: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(8), nullable=False, default="")  # book | all
    book_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    include_proposed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: choose | audience | text | confirm | sent | cancelled
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="choose")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class BotContact(Base):
    """Кто писал боту: id, как назван в Telegram, когда впервые и когда последний раз.

    Тексты сообщений не храним — только факт контакта (01.10, сводка владельцу).
    """

    __tablename__ = "bot_contacts"

    telegram_user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tg_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
