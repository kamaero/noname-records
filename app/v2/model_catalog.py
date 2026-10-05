"""The models a book may be marked up with, and what each one costs and consents to.

The choice is not only technical. The cheap tier of Muse Spark is cheap because the
provider may train on what it is sent, and that is the author's decision to make, not
ours — so it is stated on the row rather than buried in a provider's terms. The
prices are the ones the router charges in rubles per million tokens; they are here so
the hub can show what a run cost instead of a token count nobody can price.

Adding a model means adding a row: the provider name resolves through
`llm_client._resolve_provider`, and `extra_body` carries whatever that provider needs
to keep its reasoning from running away with the bill.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DEFAULT_KEY = "deepseek-v4-pro"

#: DeepSeek's peak windows, in UTC hours, on weekdays: [01:00, 04:00) and [06:00, 10:00).
PEAK_WINDOWS_UTC = ((1, 4), (6, 10))


def is_peak(moment: datetime | None) -> bool:
    """True when `moment` (UTC, naive) falls in a provider's expensive window."""
    if moment is None:
        return False
    if moment.weekday() >= 5:
        return False
    return any(start <= moment.hour < end for start, end in PEAK_WINDOWS_UTC)


@dataclass(frozen=True)
class RunModel:
    key: str
    label: str
    provider: str
    model: str
    #: per million tokens, in `currency`; the off-peak rate where a provider has one
    price_in: float
    price_out: float
    price_cache_read: float
    #: "RUB" or "USD" — what the provider's invoice is in
    currency: str
    #: the provider may keep and train on what it is sent
    trains_on_text: bool
    note: str
    #: what the bench says, in the owner's words; empty when the model was never measured
    accuracy: str = ""
    #: 2.0 when the provider doubles its price in a daily window, 1.0 when it does not
    peak_multiplier: float = 1.0
    #: when that window is, in the owner's timezone
    peak_hours: str = ""
    #: merged into every request (reasoning knobs live here, not in the pipeline)
    extra_body: dict | None = None

    def rub(self, usd_rate: float, *, peak: bool = False) -> dict:
        """This row's prices in rubles per million tokens at `usd_rate`."""
        factor = (float(usd_rate) if self.currency == "USD" else 1.0) * (self.peak_multiplier if peak else 1.0)
        return {
            "in": round(self.price_in * factor, 2),
            "out": round(self.price_out * factor, 2),
            "cache_read": round(self.price_cache_read * factor, 4),
        }

    def cost_rub(self, prompt_tokens: int, completion_tokens: int, usd_rate: float, *, peak: bool = False) -> float | None:
        """What a run of this size costs in rubles; None when the rate is unknown.

        Every input token is priced as a cache miss. Our usage numbers add cache reads
        into the prompt count, so the split is not ours to make — and an estimate that
        errs upwards is the safe direction for a bill.
        """
        if self.currency not in ("RUB", "USD") or (self.currency == "USD" and not usd_rate):
            return None  # своя модель студии: цена неизвестна, а не нулевая
        prices = self.rub(usd_rate, peak=peak)
        return (
            max(0, int(prompt_tokens or 0)) / 1_000_000 * prices["in"]
            + max(0, int(completion_tokens or 0)) / 1_000_000 * prices["out"]
        )

    def as_dict(self, usd_rate: float = 0.0) -> dict:
        prices = self.rub(usd_rate)
        peak = self.rub(usd_rate, peak=True) if self.peak_multiplier != 1.0 else None
        known = self.currency == "RUB" or (self.currency == "USD" and bool(usd_rate))
        return {
            "key": self.key,
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "currency": self.currency,
            "price_in": self.price_in,
            "price_out": self.price_out,
            "rub_in": prices["in"] if known else None,
            "rub_out": prices["out"] if known else None,
            "rub_cache_read": prices["cache_read"] if known else None,
            "peak_rub_in": peak["in"] if (peak and known) else None,
            "peak_rub_out": peak["out"] if (peak and known) else None,
            "peak_hours": self.peak_hours,
            "trains_on_text": self.trains_on_text,
            "note": self.note,
            "accuracy": self.accuracy,
        }


CATALOG: tuple[RunModel, ...] = (
    RunModel(
        key=DEFAULT_KEY,
        label="DeepSeek V4 Pro",
        provider="deepseek",
        model="deepseek-v4-pro",
        # https://api-docs.deepseek.com/quick_start/pricing — off-peak, per 1M tokens
        price_in=0.66,
        price_out=1.98,
        price_cache_read=0.022,
        currency="USD",
        peak_multiplier=2.0,
        peak_hours="в будни 04:00–07:00 и 09:00–13:00 МСК",
        trains_on_text=False,
        note="Счёт приходит от DeepSeek в долларах; рубли посчитаны по курсу студии. Размышления выключены: на нашем эталоне это не меняет точность, но сокращает ответ в 12 раз.",
        accuracy="точность 0,957 на эталоне из трёх размеченных автором глав (пол «всё Рассказчик» — 0,747)",
        # DeepSeek reads the Anthropic-style knob; see `llm_client._chat_anthropic`.
        extra_body={"thinking": {"type": "disabled"}},
    ),
    RunModel(
        key="muse-spark-1.3-contributor",
        label="Muse Spark 1.3 Contributor",
        provider="routerai",
        model="meta/muse-spark-1.3-contributor",
        # routerai's own catalogue, per 1M tokens
        price_in=11.26,
        price_out=22.51,
        price_cache_read=0.225,
        currency="RUB",
        trains_on_text=True,
        note="Дешёвый тариф: провайдер вправе учиться на присланном тексте. Размышления у этого эндпоинта отключить нельзя — исходящих токенов в 5,7 раза больше, а прогон книги идёт около пяти часов вместо полутора.",
        accuracy="точность 0,956 на том же эталоне — вровень с DeepSeek",
        # «Reasoning is mandatory for this endpoint and cannot be disabled» — the most
        # we can do is ask for the shortest one.
        extra_body={"reasoning": {"effort": "low"}},
    ),
)

BY_KEY = {item.key: item for item in CATALOG}


def get(key: str) -> RunModel:
    """A catalogue row by key; the default when the key is unknown or empty."""
    return BY_KEY.get(str(key or "").strip(), BY_KEY[DEFAULT_KEY])


def for_book(book) -> RunModel:
    """What a book is set to run on: its stored provider/model, else the studio's choice
    for the markup step (Настройки → Нейросети), else the catalogue default."""
    provider = str(getattr(book, "llm_provider", "") or "").strip()
    model = str(getattr(book, "llm_model", "") or "").strip()
    for item in CATALOG:
        if item.provider == provider and item.model == model:
            return item
    if not model:
        from app.services.step_models import step_model

        provider, model = step_model("attribution")
        for item in CATALOG:
            if item.provider == provider and item.model == model:
                return item
        return custom(provider, model)
    return BY_KEY[DEFAULT_KEY]


def custom(provider: str, model: str) -> RunModel:
    """Модель, которой нет в каталоге: студия выбрала её сама — цены мы не знаем."""
    return RunModel(key="custom", label=model, provider=provider, model=model, price_in=0.0, price_out=0.0,
                    price_cache_read=0.0, currency="", trains_on_text=False,
                    note="Своя модель — цена неизвестна, смета её не покажет.")
