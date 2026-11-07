"""Какая модель работает на каждом шаге. Нет выбора на сайте — значение по умолчанию, то
самое, что было вшито в код: страница ничего не меняет, пока студия не выбрала сама.

Прогон фиксирует модель при старте (`step_model` зовут один раз в начале прогона):
смена на странице действует со следующего прогона.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import SessionLocal
from app.models import StepModel

PROVIDER_LABELS = {"deepseek": "DeepSeek", "claude": "Claude", "openai": "OpenAI", "routerai": "RouterAI",
                   "openrouter": "OpenRouter", "elevenlabs": "ElevenLabs", "azure": "Azure"}


@dataclass(frozen=True)
class Step:
    key: str
    label: str          # родительный падеж: «для звуковой разметки нужен ключ…»
    title: str          # на странице
    providers: tuple[str, ...]
    default: tuple[str, str]
    options: tuple[tuple[str, str, str], ...] = ()


_ROUTED = ("routerai", "openrouter")
STEPS: dict[str, Step] = {s.key: s for s in (
    Step("attribution", "attribution", "Dialogue attribution", ("deepseek", "claude", "routerai", "openrouter", "openai"),
         ("deepseek", "deepseek-v4-pro")),
    Step("characters", "character extraction", "Book characters", ("deepseek", "claude", "routerai", "openrouter", "openai"),
         ("deepseek", "deepseek-v4-pro")),
    Step("consilium_reader_1", "review", "Review: first reader", _ROUTED, ("routerai", "anthropic/claude-opus-5")),
    Step("consilium_reader_2", "review", "Review: second reader", _ROUTED, ("routerai", "openai/gpt-5.6-sol")),
    Step("consilium_arbiter", "review", "Review: arbiter", _ROUTED, ("routerai", "anthropic/claude-opus-5")),
    Step("sound", "sound planning", "Sound planning", _ROUTED, ("routerai", "anthropic/claude-opus-5")),
    Step("ambient_text", "ambience", "Ambience: scene description", _ROUTED, ("routerai", "anthropic/claude-opus-5")),
    Step("ambient_audio", "ambience", "Ambience: audio", ("elevenlabs",), ("elevenlabs", "music_v2")),
    Step("asr", "recording alignment", "Speech recognition", ("openai", "routerai", "azure"), ("openai", "whisper-1")),
)}

CACHE_SECONDS = 30
_CACHE: dict[str, tuple[float, tuple[str, str]]] = {}


class MissingKeyError(RuntimeError):
    pass


def clear_cache() -> None:
    _CACHE.clear()


def _default(step: Step) -> tuple[str, str]:
    # шаги, которыми раньше управлял .env, сохраняют его как значение по умолчанию
    if step.key == "asr":
        return (str(settings.asr_provider or "openai").strip().lower(), str(settings.asr_model or "whisper-1"))
    if step.key == "attribution" and settings.default_final_model:
        return (settings.default_final_provider or "deepseek", settings.default_final_model)
    if step.key == "characters" and settings.default_char_extraction_model:
        return (settings.default_char_extraction_provider or "deepseek", settings.default_char_extraction_model)
    return step.default


def step_model(key: str) -> tuple[str, str]:
    step = STEPS[key]
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        with SessionLocal() as db:
            row = db.get(StepModel, key)
    except SQLAlchemyError:
        row = None  # таблицы ещё нет (до миграции) — значение по умолчанию
    value = (row.provider, row.model) if row is not None and row.model else _default(step)
    _CACHE[key] = (time.monotonic(), value)
    return value


def set_step_model(db, key: str, provider: str, model: str, *, actor: str) -> None:
    step = STEPS.get(key)
    if step is None:
        raise ValueError("Unknown step")
    provider, model = str(provider or "").strip(), str(model or "").strip()
    if provider not in step.providers or not model:
        raise ValueError("This step does not support that provider")
    row = db.get(StepModel, key) or StepModel(step=key)
    row.provider, row.model, row.updated_by = provider, model, actor
    db.add(row)
    db.flush()
    _CACHE.pop(key, None)


def reset_step_model(db, key: str) -> None:
    row = db.get(StepModel, key)
    if row is not None:
        db.delete(row)
        db.flush()
    _CACHE.pop(key, None)


def require_key_for(key: str) -> None:
    from app.services.provider_keys import provider_key

    provider, _model = step_model(key)
    if not provider_key(provider):
        raise MissingKeyError(f"{STEPS[key].title} requires a {PROVIDER_LABELS.get(provider, provider)} key. "
                              "Add it in Settings → AI providers.")


_CURRENCY = {"RUB": "₽", "USD": "$"}


def _options(step: Step) -> list[dict]:
    """Что предложить в списке: для разметки — каталог с ценой и пометкой «учится на
    текстах» (это решение автора, а не наше), для остальных — значение по умолчанию.
    Поле «своя модель» на странице остаётся всегда."""
    if step.key in ("attribution", "characters"):
        from app.v2.model_catalog import CATALOG

        return [{"provider": item.provider, "model": item.model, "label": item.label,
                 "price": (f"{item.price_in:g} / {item.price_out:g}".replace(".", ",")
                           + f" {_CURRENCY.get(item.currency, item.currency)} per 1M tokens (input / output)"),
                 "trains_on_text": item.trains_on_text, "note": item.note}
                for item in CATALOG if item.provider in step.providers]
    provider, model = _default(step)
    return [{"provider": provider, "model": model, "label": model, "price": "", "trains_on_text": False, "note": ""}]


def _price(db, provider: str, model: str) -> dict:
    from app.services.spend import price_view

    return price_view(db, provider, model)


def steps_view(db) -> list[dict]:
    out = []
    for step in STEPS.values():
        row = db.get(StepModel, step.key)
        provider, model = (row.provider, row.model) if row is not None and row.model else _default(step)
        out.append({"step": step.key, "title": step.title, "provider": provider, "model": model,
                    "source": "site" if row is not None and row.model else "default",
                    "default": {"provider": _default(step)[0], "model": _default(step)[1]},
                    "providers": list(step.providers), "options": _options(step),
                    "price": _price(db, provider, model)})
    return out
