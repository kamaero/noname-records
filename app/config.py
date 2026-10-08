import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Значения SECRET_KEY, которые считаются небезопасными: пустое или любой из
# плейсхолдеров, поставляемых в репозитории (.env.example и т.п.). С таким ключом
# подписанные session-cookie можно подделать (URLSafeSerializer), вплоть до admin.
PLACEHOLDER_SECRET_KEYS = {"", "change-me", "change-this-secret", "secret", "changeme"}
MIN_SECRET_KEY_LENGTH = 32


def classify_secret_key(secret_key: str) -> str:
    """Чистая функция-классификатор для SECRET_KEY: 'placeholder' | 'short' | 'ok'."""
    value = (secret_key or "").strip()
    if value in PLACEHOLDER_SECRET_KEYS:
        return "placeholder"
    if len(value) < MIN_SECRET_KEY_LENGTH:
        return "short"
    return "ok"


def _load_local_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):  # нечитаемый .env — живём на переменных окружения
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            continue
        # Заданное окружением — даже пустое — главнее файла: пустота бывает намеренной
        # («зеркала нет», AUDIO_NAS_PATH=), и вписать поверх неё путь значит молча
        # отменить выключатель.
        if key in os.environ:
            continue
        os.environ[key] = value.strip()


_load_local_env_file()


def env_value(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None and str(value).strip():
        return str(value).strip()
    env_path = Path(".env")
    if env_path.exists():
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                if not raw.strip() or raw.lstrip().startswith("#") or "=" not in raw:
                    continue
                key, _, file_value = raw.partition("=")
                if key.strip() == name:
                    return file_value.strip()
        except Exception:
            # Import-time .env read (logging not configured yet): a malformed/unreadable
            # file intentionally falls through to the provided default.
            pass
    return default


@dataclass
class Settings:
    app_name: str = os.getenv("APP_NAME", "Noname Records")
    # Название студии в письмах бота и подписях; по умолчанию — имя системы.
    studio_name: str = os.getenv("STUDIO_NAME", os.getenv("APP_NAME", "Noname Records"))
    # Кому писать «нужно больше времени» и прочее: «напишите Ольге». Пусто — «напишите в студию».
    studio_contact_name: str = os.getenv("STUDIO_CONTACT_NAME", "")
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8080")
    secret_key: str = os.getenv("SECRET_KEY", "change-me")
    # Ключ шифрования ключей нейросетей в базе (Fernet). Пусто — выводится из SECRET_KEY.
    keys_encryption_key: str = os.getenv("KEYS_ENCRYPTION_KEY", "")
    admin_login: str = os.getenv("ADMIN_LOGIN", "admin")
    admin_password_hash: str = os.getenv("ADMIN_PASSWORD_HASH", "")
    #: папка пользовательских данных; на VPS — `data` рядом с кодом, как всегда
    data_dir: str = os.getenv("DATA_DIR", "data")
    # База и записи выводятся из DATA_DIR, только если не заданы явно: установщик VPS задаёт
    # их в .env, и для него ничего не меняется.
    database_url: str = os.getenv("DATABASE_URL") or (
        f"sqlite:///{Path(os.getenv('DATA_DIR', '')).expanduser().resolve() / 'noname.db'}"
        if os.getenv("DATA_DIR") else "sqlite:///./noname.db")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    audio_storage_path: str = os.getenv("AUDIO_STORAGE_PATH") or (
        str(Path(os.getenv("DATA_DIR", "")).expanduser().resolve() / "recordings") if os.getenv("DATA_DIR") else "")
    # Сторож NAS. С 28.09 монтирование `soft`: при пропаже сервера ошибка через ~30 с,
    # а не вечное ожидание. Файлы с NAS читаются только на подтверждённом «жив».
    nas_probe_interval_seconds: int = int(os.getenv("NAS_PROBE_INTERVAL_SECONDS", "15"))
    # Сколько после пропажи NAS ещё верить последнему «жив»: не дольше таймаута soft
    # (~30 с) и не короче двух кругов сторожа.
    nas_probe_stale_seconds: int = int(os.getenv("NAS_PROBE_STALE_SECONDS", "30"))
    # Зеркало записей на второй диск или NAS: тот же ключ, другой корень. Пусто — зеркалирование
    # выключено, приём при этом работает.
    audio_nas_path: str = os.getenv("AUDIO_NAS_PATH", "")
    mirror_interval_minutes: int = int(os.getenv("MIRROR_INTERVAL_MINUTES", "5"))
    local_reserve_gb: int = int(os.getenv("LOCAL_RESERVE_GB", "5"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "true").lower() in {"1", "true", "yes", "on"}
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    # Распознавание речи. Раньше эти переменные стояли в .env и не читались никем.
    asr_provider: str = os.getenv("ASR_PROVIDER", "openai")
    asr_model: str = os.getenv("ASR_MODEL", "whisper-1")
    asr_language: str = os.getenv("ASR_LANGUAGE", "ru")
    asr_prompt: str = os.getenv("ASR_PROMPT", "")
    azure_speech_key: str = os.getenv("AZURE_SPEECH_KEY", "")
    azure_speech_endpoint: str = os.getenv("AZURE_SPEECH_ENDPOINT", "")
    azure_speech_locale: str = os.getenv("AZURE_SPEECH_LOCALE", "ru-RU")
    claude_api_key: str = os.getenv("CLAUDE_API_KEY", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    zai_api_key: str = os.getenv("ZAI_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    routerai_api_key: str = os.getenv("ROUTERAI_API_KEY", "")
    # ElevenLabs: генерация фоновой музыки к главам (app/services/elevenlabs_client.py).
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    claude_base_url: str = os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com/v1")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    # DeepSeek's Anthropic-compatible endpoint (Messages format + tool-use). Lets
    # deepseek-v4-pro share the same client path as Claude. Verified working.
    deepseek_anthropic_base_url: str = os.getenv("DEEPSEEK_ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic/v1")
    zai_base_url: str = os.getenv("ZAI_BASE_URL", "https://api.z.ai/v1")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    routerai_base_url: str = os.getenv("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1")
    # DeepSeek V4 Pro runs every automatic stage: v2 attribution (DEFAULT_FINAL_*)
    # and book-level char_extraction. Claude Sonnet 4.6 is intentionally NOT an
    # automatic default anywhere — it drained the credit balance to $0 when baked
    # in (see project_deployment memory). Keeping these defaults on deepseek means
    # a lost .env line can never silently fall back to (paid) Claude.
    default_final_provider: str = os.getenv("DEFAULT_FINAL_PROVIDER", "deepseek")
    default_final_model: str = os.getenv("DEFAULT_FINAL_MODEL", "deepseek-v4-pro")
    default_char_extraction_provider: str = os.getenv("DEFAULT_CHAR_EXTRACTION_PROVIDER", "deepseek")
    default_char_extraction_model: str = os.getenv("DEFAULT_CHAR_EXTRACTION_MODEL", "deepseek-v4-pro")
    max_chapter_chars: int = int(os.getenv("MAX_CHAPTER_CHARS", "180000"))
    char_extraction_whole_book_max_chars: int = int(os.getenv("CHAR_EXTRACTION_WHOLE_BOOK_MAX_CHARS", "350000"))
    char_extraction_part_chars: int = int(os.getenv("CHAR_EXTRACTION_PART_CHARS", "180000"))
    llm_request_timeout_seconds: int = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "180"))
    # Hard ceiling on time-without-new-content within one stream. The per-chunk read
    # timeout resets on keepalive pings, so a stalled-but-pinging generation hangs
    # forever; this aborts (and retries) when no real content arrives for N seconds.
    llm_idle_content_deadline_seconds: int = int(os.getenv("LLM_IDLE_CONTENT_DEADLINE_SECONDS", "300"))
    # Hard wall-clock ceiling on one streaming call. A separate timer force-closes the
    # socket past this, so a read blocked in poll() (no lines yielded, idle-deadline
    # never runs) cannot hang the worker forever — the closed socket raises and retries.
    llm_stream_wall_deadline_seconds: int = int(os.getenv("LLM_STREAM_WALL_DEADLINE_SECONDS", "1200"))
    llm_transport_retries: int = int(os.getenv("LLM_TRANSPORT_RETRIES", "2"))
    llm_processing_stale_minutes: int = int(os.getenv("LLM_PROCESSING_STALE_MINUTES", "35"))
    pipeline_hotfix_max_manual_actions_24h: int = int(os.getenv("PIPELINE_HOTFIX_MAX_MANUAL_ACTIONS_24H", "6"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "8000"))
    # Char-map gate thresholds. In Belozerov's universe a character normally carries
    # 2 aliases, 4 at most (Вохпкогамкиуб = Тёмный Господин = Кор = малыш Кор), so
    # 6+ aliases is almost always a merge artifact — threshold lowered 8->6 to catch
    # residual snowballs early. Env-tunable per deployment.
    char_map_new_ratio_gate: float = float(os.getenv("CHAR_MAP_NEW_RATIO_GATE", "0.40"))
    char_map_suspect_aliases: int = int(os.getenv("CHAR_MAP_SUSPECT_ALIASES", "6"))
    default_genre_guidelines: str = os.getenv(
        "DEFAULT_GENRE_GUIDELINES",
        (
            "Художественная литература с преобладанием вымысла: фантастика, научная фантастика, "
            "классическое фэнтези и смежные жанры."
        ),
    )
    default_domain_lexicon: str = os.getenv("DEFAULT_DOMAIN_LEXICON", "")
    telegram_bot_username: str = os.getenv("TELEGRAM_BOT_USERNAME", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # Секрет webhook бота (заголовок X-Telegram-Bot-Api-Secret-Token). Пусто — адрес выключен.
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    telegram_auth_whitelist: str = os.getenv("TELEGRAM_AUTH_WHITELIST", "")
    telegram_auth_whitelist_default_role: str = os.getenv("TELEGRAM_AUTH_WHITELIST_DEFAULT_ROLE", "author")
    telegram_auth_whitelist_default_access_scope: str = os.getenv("TELEGRAM_AUTH_WHITELIST_DEFAULT_ACCESS_SCOPE", "full")
    telegram_notify_enabled: bool = os.getenv("TELEGRAM_NOTIFY_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    telegram_notify_chat_ids: str = os.getenv("TELEGRAM_NOTIFY_CHAT_IDS", "")
    owner_telegram_id: str = os.getenv("OWNER_TELEGRAM_ID", "")
    # Base stress dictionary for the v2 stress step (app/v2/stress_dict.py).
    stress_base_dict_path: str = os.getenv("STRESS_BASE_DICT_PATH", "")
    # Wiktionary word forms with stress (app/v2/stress_forms.py); empty → data/stress_forms.sqlite.
    stress_forms_db_path: str = os.getenv("STRESS_FORMS_DB_PATH", "")
    api_rate_limit_enabled: bool = os.getenv("API_RATE_LIMIT_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    api_rate_limit_default: str = os.getenv("API_RATE_LIMIT_DEFAULT", "240/minute")
    api_rate_limit_storage_uri: str = os.getenv("API_RATE_LIMIT_STORAGE_URI", "")
    api_max_request_bytes: int = int(os.getenv("API_MAX_REQUEST_BYTES", str(100 * 1024 * 1024)))
    # Не безлимит, а щедрый потолок: тело загрузки Starlette всё равно спулит во
    # временный файл на диске сервера, и один кривой запрос мог забить его целиком.
    upload_max_bytes: int = int(os.getenv("UPLOAD_MAX_BYTES", str(2 * 1024 * 1024 * 1024)))
    # Отдельный строгий лимит на эндпоинты входа (защита от перебора). Не влияет
    # на длительность уже выданной сессии — только на частоту попыток входа.
    auth_rate_limit: str = os.getenv("AUTH_RATE_LIMIT", "10/minute")
    # Окно свежести Telegram login (auth_date). Это окно рукопожатия входа, а не
    # срок жизни сессии. 24ч было слишком большим окном для replay.
    telegram_auth_max_age_seconds: int = int(os.getenv("TELEGRAM_AUTH_MAX_AGE_SECONDS", "3600"))

    def __post_init__(self) -> None:
        is_production = os.getenv("PRODUCTION", "").lower() in {"1", "true", "yes", "on"}
        verdict = classify_secret_key(self.secret_key)
        if verdict == "placeholder":
            message = (
                "SECRET_KEY не задан или использует небезопасное значение по умолчанию — "
                "session-cookie можно подделать. Задайте случайный ключ, например: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
            if is_production:
                print(f"FATAL: {message}", file=sys.stderr)
                sys.exit(1)
            print(f"WARNING: {message}", file=sys.stderr)
        elif verdict == "short":
            print(
                f"WARNING: SECRET_KEY короче {MIN_SECRET_KEY_LENGTH} символов — "
                f"рекомендуется не менее {MIN_SECRET_KEY_LENGTH} случайных символов.",
                file=sys.stderr,
            )


settings = Settings()
