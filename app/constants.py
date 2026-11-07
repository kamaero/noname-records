from enum import Enum


class ChapterStatus(str, Enum):
    """Canonical chapter statuses. Compatible with plain string comparisons."""
    QUEUED = "queued"
    WAITING = "waiting"
    PROCESSING = "processing"
    STALLED = "stalled"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    APPROVED = "approved"
    PUBLISHED = "published"
    PENDING_REVIEW = "pending_review"
    FINAL_DONE = "final_done"
    NEEDS_FIX = "needs_fix"
    AUTHOR_REVIEW = "author_review"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CANCELED = "canceled"
    CHAR_EXTRACTING = "char_extracting"


class AudioFileStatus(str, Enum):
    """Audio file statuses in the recording workflow."""
    UPLOADED = "uploaded"
    ASR_QUEUED = "asr_queued"
    ASR_PROCESSING = "asr_processing"
    READY_FOR_MIX = "ready_for_mix"
    PUBLISHED_TO_DICTOR = "published_to_dictor"
    APPROVED_AUDIO = "approved_audio"
    NEEDS_REVIEW = "needs_review"
    REJECTED_AUDIO = "rejected_audio"


class UserRole(str, Enum):
    """Роли студии: `admin` ведёт студию, `author` владеет разметкой, `dictor` читает,
    `agent` ведёт актёров.

    Роль заводится под живого носителя и никак иначе. `dictor_pro` и `dictor_neo`
    не носила ни одна учётка — они жили в исходниках и тащили за собой режим
    «neo-only» для людей, которых не существует; проверка спрашивала роль, которой
    ни у кого нет, и молча отвечала 403 всем дикторам сразу.

    `agent` — кастинг-директор студии: получает задание, передаёт своим актёрам,
    приносит записи и отвечает за качество. Роль не открывает разделов — вход в студию и так даёт полный доступ, — а отнимает Подготовку и
    оставляет право вписывать актёров со знаком вопроса и грузить за них файлы.
    """
    ADMIN = "admin"
    AUTHOR = "author"
    DICTOR = "dictor"
    AGENT = "agent"


DICTOR_ROLES = frozenset({UserRole.DICTOR.value})


LLM_PROVIDERS = [
    ("openai", "OpenAI API"),
    ("claude", "Anthropic API"),
    ("deepseek", "DeepSeek API"),
    ("z.ai", "Z.AI API"),
    ("openrouter", "OpenRouter API"),
    ("routerai.ru", "RouterAI.ru API"),
]

CHAPTER_READY_FOR_REVIEW = {"done", "pending_review", "approved", "published", "skipped"}
RECORDING_PUBLISHED_CHAPTER_STATUSES = {"published"}

WORKSPACE_TABS = {
    "book_prep": {"label": "1. Book preparation"},
    "validation": {"label": "2. Review"},
    "recording": {"label": "3. Recording"},
    "asr_daw": {"label": "4. ASR / DAW"},
}

STATUS_LABELS = {
    "queued": "Queued",
    "waiting": "Waiting",
    "processing": "Processing",
    "stalled": "Stalled",
    "done": "Done",
    "skipped": "Skipped",
    "failed": "Failed",
    "approved": "Approved",
    "published": "Published",
    "pending_review": "Awaiting review",
    "final_done": "Final pass complete",
    "needs_fix": "Needs revision",
    "author_review": "Author review",
    "stopping": "Stopping",
    "stopped": "Stopped",
    "canceled": "Canceled",
    "uploaded": "Uploaded",
    "char_extracting": "Extracting characters",
    "asr_queued": "ASR queued",
    "asr_processing": "ASR processing",
    "ready_for_mix": "Ready for mixing",
    "published_to_dictor": "Available to voice actors",
    "approved_audio": "Audio approved",
    "needs_review": "Needs review",
    "rejected_audio": "Audio rejected",
}

LLM_PRICING = {
    "input_per_million_usd": 0.28,
    "output_per_million_usd": 0.42,
    "input_cache_hit_per_million_usd": 0.028,
}

MODEL_PRICING_RUB = {
    # Direct-API models for the two-model pipeline (deepseek-v4-pro + claude-sonnet-4-6).
    # deepseek-v4-pro rates are an estimate based on deepseek/deepseek-v3.2 — CONFIRM
    # against current api.deepseek.com pricing. claude-sonnet-4-6 mirrors sonnet-4.6.
    "deepseek-v4-pro": {"input": 26.0, "output": 39.0, "context": "1M", "endpoint": "https://api.deepseek.com/anthropic/v1"},
    "claude-sonnet-4-6": {"input": 308.0, "output": 1543.0, "context": "1M", "endpoint": "https://api.anthropic.com/v1"},
    "openai/gpt-5.4": {"input": 257.0, "output": 1543.0, "context": "1M", "endpoint": "https://routerai.ru/api/v1"},
    "google/gemini-3.1-pro-preview": {"input": 205.0, "output": 1234.0, "context": "1M", "endpoint": "https://routerai.ru/api/v1"},
    "deepseek/deepseek-v3.2": {"input": 26.0, "output": 39.0, "context": "164K", "endpoint": "https://api.deepseek.com/v1"},
    "qwen/qwen3.5-35b-a3b": {"input": 25.0, "output": 102.0, "context": "262K", "endpoint": "https://routerai.ru/api/v1"},
    "qwen/qwen3.5-flash-02-23": {"input": 10.0, "output": 41.0, "context": "1M", "endpoint": "https://routerai.ru/api/v1"},
    "moonshotai/kimi-k2.5": {"input": 46.0, "output": 226.0, "context": "262K", "endpoint": "https://routerai.ru/api/v1"},
    "google/gemini-2.5-pro-preview-05-06": {"input": 128.0, "output": 1028.0, "context": "1M", "endpoint": "https://routerai.ru/api/v1"},
    "google/gemini-2.0-flash-lite-001": {"input": 7.0, "output": 30.0, "context": "1M", "endpoint": "https://routerai.ru/api/v1"},
    "anthropic/claude-sonnet-4.6": {"input": 308.0, "output": 1543.0, "context": "1M", "endpoint": "https://routerai.ru/api/v1"},
    "anthropic/claude-opus-4.6": {"input": 514.0, "output": 2572.0, "context": "1M", "endpoint": "https://routerai.ru/api/v1"},
    "anthropic/claude-3.7-sonnet:thinking": {"input": 308.0, "output": 1543.0, "context": "200K", "endpoint": "https://routerai.ru/api/v1"},
    "anthropic/claude-3-haiku": {"input": 25.0, "output": 128.0, "context": "200K", "endpoint": "https://routerai.ru/api/v1"},
}

MODEL_PROVIDER_HINTS = {
    "deepseek/deepseek-v3.2": "deepseek",
    "deepseek-chat": "deepseek",
    "deepseek-reasoner": "deepseek",
    "gpt-4o-mini": "openai",
    "gpt-4o": "openai",
    "claude-3-5-sonnet-latest": "claude",
}

FULL_WORKSPACE_TAB_ORDER = ["book_prep", "validation", "recording", "asr_daw"]


def status_label(value: str) -> str:
    raw = str(value or "").strip()
    return STATUS_LABELS.get(raw, raw)

