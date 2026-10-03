"""The one place pipeline v2 touches a model, and the one place its prompt lives.

Everything in `attribute.py` takes the model as a callable so it can be tested with a
fake; this module makes the real one. It resolves a provider name the way the rest
of the system does — same keys, same base URLs, same Anthropic-or-OpenAI routing —
and always asks for strict JSON against the schema it was handed, so a model that
drifts into prose fails loudly at the call rather than quietly in the parser.

The system prompt is read from `app/prompts/v2_attribution.txt` and nowhere else. An
inline fallback would be a second copy that drifts; a missing file is a broken
install and should say so.
"""
from __future__ import annotations

import pathlib
from typing import Callable

from app.pipeline.llm_client import _resolve_provider, call_chat

PROMPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "prompts" / "v2_attribution.txt"

Llm = Callable[[str, str, dict], dict]


def load_system_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"системный промпт атрибуции не найден: {PROMPT_PATH}")
    text = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise FileNotFoundError(f"системный промпт атрибуции пуст: {PROMPT_PATH}")
    return text


def make_llm(provider: str, model: str, *, extra_body: dict | None = None) -> Llm:
    """`(system_prompt, user_prompt, schema) -> call_chat result` for a named provider.

    `extra_body` goes into the request as-is — the way to switch a provider's
    thinking off for a task that is checked by the parser anyway.
    """
    api_key, base_url, mode = _resolve_provider(provider)
    if not api_key:
        raise RuntimeError(f"нет API-ключа для провайдера {provider!r} — проверь .env")

    def llm(system_prompt: str, user_prompt: str, schema: dict) -> dict:
        return call_chat(
            base_url, api_key, model, system_prompt, user_prompt,
            mode=mode, force_json=True, json_schema=schema, extra_body=extra_body,
        )

    return llm
