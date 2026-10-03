"""Одноразовый проход: вырезать слова автора из уже размеченной реплики.

Разметка «Крыльев полумрака» сделана по старому правилу — весь абзац-реплика,
включая «— впервые за вечер отозвался Бдеукс», уходил актёру персонажа. Владелец
сменил соглашение: слова автора внутри реплики читает Рассказчик. Это одноразовый
проход по уже размеченной книге, который просит модель процитировать ТОЛЬКО куски
слов автора — всё остальное `parts_to_spans` (не наш код, не трогаем) само оставляет
доминирующему говорящему, поэтому промах модели в худшем случае ничего не меняет.

Ошибка в опасную сторону — когда речь персонажа уезжает Рассказчику: диктор молча
недополучит текст, и заметить это будет некому. Поэтому каждый предложенный разрез
проходит через набор чистых guard-функций (`check_guards`), и при любом сомнении
абзац остаётся как был, а причина уходит в отчёт вместо базы.

Пишет через `store_attributions`: куску, отданному Рассказчику, — `source="llm_remarks"`
и уверенность модели ремарок; куску персонажа — исходные `source`/`confidence` той
атрибуции, которую он заменяет (это те же слова, что и раньше — стирать их уверенность
моделью ремарок нечестно и вымывает абзац из очереди ревью, см. `dispute_ops`). Версия
поднимается сама, действующей считается старшая.

Откат — НЕ «удалить строки с source=llm_remarks»: строки одной версии теперь несут
разные source (кусок персонажа — свой прежний), и удаление только llm_remarks-строк
оставит сегмент без атрибуции на части текста. Правильно: найти сегменты, у которых
на СТАРШЕЙ версии есть хотя бы одна строка с source=llm_remarks, и удалить у такого
сегмента ВСЕ строки этой версии целиком — тогда действующей снова станет предыдущая.

Никогда не запускать на боевой базе вне контролируемого прогона (см. PROD_DB ниже) —
это дело контроллера после ревью кода, а не этого скрипта самого по себе.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.shared_runtime import is_narrator_name  # noqa: E402
from app.v2.attribute import SCHEMA, parts_to_spans  # noqa: E402
from app.v2.attribution_ops import ReassignError, normalise_spans  # noqa: E402
from app.v2.cast_ops import is_placeholder_name  # noqa: E402
from app.v2.reader import effective_attributions  # noqa: E402
from app.v2.store import store_attributions  # noqa: E402

PROD_DB = "noname.db"
SOURCE = "llm_remarks"

# Абзац — кандидат, если внутри него после знака препинания или пробела стоит тире
# с пробелом после: это подозрение на «— сказал он», а не тире-дефис («кто-то»).
INNER_DASH_RE = re.compile(r"(?<=[\s,.!?…])[-–—]\s")

# Символы, которыми в этом тексте открывается прямая речь.
_DASH_CHARS = "-–—"

# Больше этой доли символов абзаца Рассказчику — почти наверняка модель приняла
# речь персонажа за ремарку, а не наоборот; разрез в такую сторону опаснее, чем
# оставить абзац как есть.
NARRATOR_SHARE_LIMIT = 0.85

# На десяти абзацах в порции модель стабильно теряет часть ответов (проверено
# холостым прогоном); на пяти — не теряет.
BATCH_SIZE = 5

# Короткая пауза перед единственным повтором: если первый вызов упал от перегрузки
# (шторм 503), долбить тем же мгновенно — только продлевать шторм.
RETRY_PAUSE_SECONDS = 1.5

# Взято дословно из холостого прогона (remark_dryrun.py), плюс одно уточнение про
# тире возобновлённой речи — единственная новая мысль, которой не было в прогоне.
SYSTEM_PROMPT = """Ты размечаешь текст для многоголосой аудиокниги.

В абзаце-реплике кроме прямой речи персонажа бывают СЛОВА АВТОРА — «— сказал он»,
«— прошептала она, отворачиваясь», «— впервые за вечер отозвался Бдеукс» — а также
описания, вставленные между репликами. Их читает Рассказчик, а не актёр персонажа.

Твоя задача: для каждого абзаца вернуть в parts ТОЛЬКО куски авторского текста,
speaker у них — «Рассказчик». Прямую речь персонажа НЕ цитируй: всё, что ты не
процитировал, останется за ним автоматически.

Правила:
- quote копируется ДОСЛОВНО из абзаца, символ в символ, включая тире и знаки препинания.
- Куски идут в порядке появления в тексте.
- Тире внутри предложения как знак препинания («Гнилая Трясина – это не шутка») —
  НЕ признак слов автора. Смотри на смысл: слова автора говорят О персонаже в третьем
  лице, обычно с глаголом речи или действия.
- Если авторских слов в абзаце нет — верни parts пустым списком.
- speaker верхнего уровня всегда оставляй тем, кто дан в «текущий голос».
- Если после слов автора речь персонажа возобновляется, тире перед возобновлённой
  репликой НЕ включай в цитату слов автора — оно останется у персонажа сам собой.
- id — номер абзаца РОВНО как дан перед ним в РАЗМЕТИТЬ, например «#a3f9c1». Это не
  порядковый номер и не счёт «сколько абзацев подряд» — верни его посимвольно, не
  переосмысляя и не пересчитывая.

Только JSON по схеме, без пояснений."""


@dataclass(frozen=True)
class Candidate:
    """Абзац, отобранный на разрез: где он лежит, кто в нём сейчас говорит целиком, и
    какие source/confidence были у его единственной действующей атрибуции — их
    унаследует кусок, который останется за персонажем (см. `_finalize_spans`)."""

    segment_id: str
    ordinal: int
    text: str
    speaker: str
    source: str = "llm"
    confidence: float = 0.0


# --- отбор кандидатов -------------------------------------------------------------


def has_inner_dash(text: str) -> bool:
    return bool(INNER_DASH_RE.search(text or ""))


def _opens_with_dash(text: str) -> bool:
    stripped = (text or "").lstrip()
    return bool(stripped) and stripped[0] in _DASH_CHARS


def is_split_candidate(spans: list[dict], text: str) -> bool:
    """Кандидат — ровно один действующий спан на весь текст, говорящий не Рассказчик
    и не UNSURE, и внутри текста есть подозрение на тире слов автора. Всё остальное —
    либо уже разрезано кем-то раньше, либо и так читает Рассказчик, либо нечего резать.
    """
    text = text or ""  # сегмент без текста не бывает кандидатом, а не падает
    if len(spans) != 1:
        return False
    span = spans[0]
    if span.get("start") != 0 or span.get("end") != len(text):
        return False
    speaker = span.get("speaker")
    # UNSURE и UNSURE: <уточнение> — обе полноправные формы «не знаю» (см.
    # app.v2.cast_ops.is_placeholder_name); Рассказчика сверяем предикатом
    # is_narrator_name, а не точной строкой — «рассказчик»/«автор» тоже он.
    if is_narrator_name(speaker) or is_placeholder_name(speaker):
        return False
    return has_inner_dash(text)


def select_candidates(paragraphs) -> list[Candidate]:
    """`paragraphs` — план словарей {segment_id, ordinal, text, spans}; отбор — чистая
    функция, вызывающий сам решает, откуда взять действующие (effective) спаны."""
    out: list[Candidate] = []
    for p in paragraphs:
        if is_split_candidate(p["spans"], p["text"]):
            span = p["spans"][0]
            out.append(Candidate(
                p["segment_id"], p["ordinal"], p["text"], span["speaker"],
                source=str(span.get("source") or "llm"), confidence=float(span.get("confidence") or 0.0),
            ))
    return out


# --- guards: единственная дверь между предложением модели и записью в базу --------


def guard_narrator_piece_has_no_inner_dash(spans: list[dict], text: str, character: str) -> str | None:
    """Критично: если кусок, отданный Рассказчику, сам содержит тире-реплику, модель,
    скорее всего, прихватила в «слова автора» начало возобновлённой речи персонажа —
    на примере Бдеукса это « – Это не похоже…». Порог в 85% и «персонажу что-то
    осталось» на таком разрезе промолчат: узнать это можно только заглянув внутрь
    самого куска той же регуляркой, что и при отборе кандидатов."""
    for sp in spans:
        if is_narrator_name(sp["speaker"]) and has_inner_dash(text[sp["start"]:sp["end"]]):
            return "кусок Рассказчика сам содержит тире-реплику — похоже, речь персонажа прихвачена"
    return None


def guard_first_reply_belongs_to_character(spans: list[dict], text: str, character: str) -> str | None:
    """Критично: абзац, открывающийся тире, начинается репликой — персонаж не может
    рассказывать о себе в третьем лице раньше собственных первых слов."""
    if not _opens_with_dash(text):
        return None
    for sp in spans:
        if sp["start"] == 0 and is_narrator_name(sp["speaker"]):
            return "абзац открывается тире — первая реплика принадлежит персонажу, не Рассказчику"
    return None


# Знаки конца предложения, после которых тире Рассказчика с прописной буквы —
# почти наверняка реплика, а не слова автора (см. guard_narrator_dash_after_sentence_end_uppercase).
_SENTENCE_END_PUNCT = ".?!…"


def guard_narrator_dash_after_sentence_end_uppercase(spans: list[dict], text: str, character: str) -> str | None:
    """Критично (найдено на перепроверке круга 1, расширено в круге 3): если модель
    объявляет словами автора ЦЕЛУЮ следующую реплику («– Полибнугак, это уже похоже
    на паранойю.» после «Петуунегр.»), кусок Рассказчика начинается СВОИМ СОБСТВЕННЫМ
    тире — у тире на нулевом смещении куска нет предшествующего знака в lookbehind,
    поэтому `guard_narrator_piece_has_no_inner_dash` его не видит; доля Рассказчика
    может быть небольшой, персонажу что-то остаётся, посторонних speaker нет, спаны
    валидны — молчат остальные шесть проверок. Форма «— А? — сказал Икс. — Б.» одна
    из самых частых в этой книге; та же форма встречается и после «?»/«!»/«…»
    («— Ну что ещё? — Полибнугак...»), а не только после точки.

    Порог отбит по живым данным книги, не выдуман: из 7433 внутренних тире в
    абзацах-кандидатах — после точки 2907 (39,1%), из них с ПРОПИСНОЙ 94% (реплика)
    против 5,8%/170 со строчной (слова автора при небрежной пунктуации); после «?»
    523 — с прописной 0; после «!» 377 — с прописной 0; после «…» 143 — с прописной 1.
    То есть после «?»/«!»/«…» тире в этой книге ВСЕГДА ведёт в слова автора со
    строчной буквы — расширение условия на все четыре знака не отнимает ни одного
    законного разреза, кроме одного-единственного случая после «…» на всю книгу.
    Тире после запятой (2782, с прописной 0) сюда сознательно не входит: там регистр
    не различает реплику от слов автора вовсе — это закрывается не запретом, а
    пометкой в отчёте (`_looks_like_genuine_remark`)."""
    for sp in spans:
        if not is_narrator_name(sp["speaker"]):
            continue
        piece = text[sp["start"]:sp["end"]]
        opening = piece.lstrip()
        if not opening or opening[0] not in _DASH_CHARS:
            continue
        before = text[:sp["start"]].rstrip()
        if not before or before[-1] not in _SENTENCE_END_PUNCT:
            continue
        after_dash = opening[1:].lstrip()
        if after_dash and after_dash[0].isupper():
            return "тире Рассказчика стоит после конца предложения и продолжается с большой буквы — похоже на реплику"
    return None


def guard_narrator_share(spans: list[dict], text: str, character: str) -> str | None:
    total = len(text)
    if total <= 0:
        return None
    narrator_chars = sum(max(0, sp["end"] - sp["start"]) for sp in spans if is_narrator_name(sp["speaker"]))
    share = narrator_chars / total
    if share > NARRATOR_SHARE_LIMIT:
        return f"Рассказчику отошло {share:.0%} абзаца — похоже, речь персонажа принята за ремарку"
    return None


def guard_character_kept_speech(spans: list[dict], text: str, character: str) -> str | None:
    if not any(sp["speaker"] == character for sp in spans):
        return f"персонажу «{character}» не осталось ни одного спана"
    return None


def guard_known_speakers(spans: list[dict], text: str, character: str) -> str | None:
    foreign = sorted({
        sp["speaker"] for sp in spans
        if sp["speaker"] != character and not is_narrator_name(sp["speaker"])
    })
    if foreign:
        return f"посторонние speaker в разрезе: {', '.join(foreign)}"
    return None


def guard_spans_valid(spans: list[dict], text: str, character: str) -> str | None:
    """`normalise_spans` не бросает исключение на спане, вылезающем за текст — она его
    молча подрезает (`min(text_length, end)`), а подрезанный результат мы не используем.
    Границы проверяем сами; `normalise_spans` остаётся источником правды только для
    пустых и налезающих друг на друга спанов, которые она действительно отвергает."""
    text_length = len(text)
    for sp in spans:
        if sp["start"] < 0 or sp["end"] > text_length or sp["end"] <= sp["start"]:
            return f"спан [{sp['start']}, {sp['end']}) выходит за текст длиной {text_length}"
    raw = [{"start": sp["start"], "end": sp["end"], "speaker": sp["speaker"],
            "confidence": sp.get("confidence", 0.0)} for sp in spans]
    try:
        normalise_spans(raw, text_length=text_length)
    except ReassignError as exc:
        return f"спаны не прошли normalise_spans: {exc.code}"
    return None


# Критичные проверки — первыми: они ловят самую опасную ошибку (речь персонажа тихо
# уезжает Рассказчику) на абзацах, где остальные проверки промолчали бы.
GUARDS: tuple[Callable[[list[dict], str, str], str | None], ...] = (
    guard_narrator_piece_has_no_inner_dash,
    guard_first_reply_belongs_to_character,
    guard_narrator_dash_after_sentence_end_uppercase,
    guard_narrator_share,
    guard_character_kept_speech,
    guard_known_speakers,
    guard_spans_valid,
)


def check_guards(spans: list[dict], text: str, character: str) -> str | None:
    for guard in GUARDS:
        reason = guard(spans, text, character)
        if reason:
            return reason
    return None


def propose_split(item: dict, *, segment_id: str, text: str, character: str) -> tuple[list[dict] | None, str | None]:
    """Ответ модели про один абзац → одобренные спаны либо причина отказа.

    `parts_to_spans` сам откатывает абзац на одного говорящего, если цитата не
    нашлась (тогда в problems есть запись) — и то же самое возвращает, если модель
    просто не увидела в абзаце слов автора (тогда parts пуст и problems тоже пуст).
    Разница важна только для отчёта: во втором случае это не ошибка, абзацу просто
    нечего резать.
    """
    problems: list[str] = []
    unit = SimpleNamespace(id=segment_id, text=text)
    result = parts_to_spans(item, unit, problems)
    spans = result["spans"]
    if len(spans) == 1:
        if problems:
            return None, "цитата не найдена — parts_to_spans оставил абзац целиком"
        return None, "модель не увидела в абзаце слов автора"
    reason = check_guards(spans, text, character)
    if reason:
        return None, reason
    return spans, None


def _finalize_spans(spans: list[dict], candidate: Candidate) -> list[dict]:
    """Спаны из `propose_split` → то, что реально уйдёт в базу. Кусок Рассказчика несёт
    source и confidence модели ремарок; кусок персонажа — исходные source/confidence
    той атрибуции, которую он заменяет: это те же слова, что и раньше, и стирать их
    уверенность моделью ремарок было бы нечестно и вымывало бы абзац из очереди
    ревью (`app/v2/dispute_ops.py`, `MODEL_SOURCES`)."""
    out: list[dict] = []
    for sp in spans:
        if is_narrator_name(sp["speaker"]):
            out.append({**sp, "source": SOURCE})
        else:
            out.append({**sp, "source": candidate.source, "confidence": candidate.confidence})
    return out


# --- модель: порции по 5, один повтор для пропущенных -----------------------------


# Стартовая длина хекс-токена и потолок (длина hex-дайджеста sha1) — см. `_batch_tokens`.
_TOKEN_LENGTH_START = 6
_TOKEN_HEX_DIGEST_LENGTH = 40


def _normalize_token(token) -> str:
    """Снимает ведущую решётку и пробелы с обеих сторон — и только это. Найдено на
    боевом прогоне 30-й главы: в 12 батчах из 28 модель СТАБИЛЬНО (и на первом
    вызове, и на повторе) отвечала id без решётки («00001» вместо «#00001»); строгий
    поиск по точному токену отбрасывал их все как чужие, и абзацы уходили в «модель
    не ответила», хотя модель на самом деле ответила. Не расширять дальше — никаких
    «взять число из строки»: посторонний токен как был посторонним, так и остаётся."""
    return str(token or "").strip().lstrip("#")


def _segment_token(segment_id: str, length: int) -> str:
    """`length` знаков хеша segment_id — не последовательный номер и не подстрока
    самого id (который может быть чем угодно), а нечто, что модель не может угадать
    пересчётом «от нуля» или «от единицы»."""
    return hashlib.sha1(str(segment_id).encode("utf-8")).hexdigest()[:length]


def _batch_tokens(batch: list[Candidate]) -> list[str]:
    """Токен на каждую позицию порции, в её порядке — ЕДИНСТВЕННОЕ место, где эти
    токены вычисляются; `render_batch_prompt` и `_tokens_for` берут их отсюда, а не
    пересчитывают каждый по-своему.

    Токен — хеш от `segment_id`, не порядковый номер: круг 4 показал, что модель
    иногда отвечает без решётки («00001» вместо «#00001») — это осталось нормальной
    формой (решётку снимает `_normalize_token`), но последовательные номера при этом
    сделали пересчёт «от единицы»/сдвиг ОПАСНЕЕ, чем было: индексы 1–4 совпадали бы с
    соседями, и только пятый отбрасывался бы как чужой. Хеш от содержимого делает
    такое совпадение практически невозможным: перенумеровавшая модель не попадёт ни
    на один выданный токен, и всё отбросится как чужое — абзацы останутся нетронутыми,
    а `stats["foreign_ids"]` покажет беду вместо того, чтобы её маскировать.

    Длина стартует с 6 знаков и растёт на 2, пока токены порции не станут уникальны
    (при 5 кандидатах в порции столкновение на 6 знаках практически невозможно, но
    проверяется явно, как и просил бриф); если бы даже вся длина sha1 совпала —
    это означало бы буквально одинаковый `segment_id`, отдельный баг в другом месте
    — тогда различаем позицией, а не рушим прогон."""
    length = _TOKEN_LENGTH_START
    while length <= _TOKEN_HEX_DIGEST_LENGTH:
        tokens = [_segment_token(c.segment_id, length) for c in batch]
        if len(set(tokens)) == len(batch):
            return tokens
        length += 2
    tokens = [_segment_token(c.segment_id, _TOKEN_HEX_DIGEST_LENGTH) for c in batch]
    return [f"{token}-{i}" for i, token in enumerate(tokens)]


def render_batch_prompt(batch: list[Candidate]) -> str:
    return "РАЗМЕТИТЬ:\n" + "\n\n".join(
        f"#{token} (текущий голос: {c.speaker})\n{c.text}" for token, c in zip(_batch_tokens(batch), batch)
    )


def _tokens_for(batch: list[Candidate]) -> dict[str, int]:
    """Нормализованный токен → локальный индекс — те же токены, что ушли в промпт
    (`render_batch_prompt`), из того же `_batch_tokens`, а не второй раз с нуля.
    Единственный источник правды о том, что мы САМИ попросили в этом запросе."""
    return {_normalize_token(token): i for i, token in enumerate(_batch_tokens(batch))}


def _parse_items(raw_content, expected_tokens: dict[str, int], stats: dict | None = None) -> dict[int, dict]:
    """Ответ модели → {локальный индекс: item}, но только по токенам, которые мы САМИ
    выдали в этом запросе (`expected_tokens`), после нормализации с обеих сторон
    (`_normalize_token`). Раньше индекс выдирался из строки (`int(token[1:])`) — что
    тихо принимало и посторонний формат («#7» вместо «#00007»), и id, который просто
    ПОХОЖ на валидный: при нумерации от единицы ответ мог молча и правдоподобно
    попасть на соседний абзац. Точный поиск по выданному токену — как было в
    remark_dryrun.py (`items.get(f"#{n:05d}")`): чего мы не просили (после
    нормализации), того нет в ответе. Отброшенный чужой id считается в `stats`,
    отдельно от «модель не ответила» — иначе следующий разъезд формата снова искать
    зондом по сырым логам."""
    try:
        payload = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
    except (TypeError, ValueError):
        return {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {}
    out: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        index = expected_tokens.get(_normalize_token(item.get("id")))
        if index is None:
            if stats is not None:
                stats["foreign_ids"] = stats.get("foreign_ids", 0) + 1
            continue
        out[index] = item
    return out


def _log_llm_error(stats: dict | None, exc: Exception) -> None:
    """Сбой модели считаем и печатаем отдельно от «модель не ответила» — иначе шторм
    503 в отчёте неотличим от вежливого отказа модели назвать говорящего."""
    if stats is not None:
        stats["llm_errors"] = stats.get("llm_errors", 0) + 1
    print(f"  ошибка модели: {type(exc).__name__}: {exc}", file=sys.stderr)


def ask_batch(
    llm: Callable, system_prompt: str, batch: list[Candidate], *,
    stats: dict | None = None, retry_pause: float = RETRY_PAUSE_SECONDS,
) -> dict[int, dict]:
    """Порция ≤5 абзацев; на пропущенные — один повтор, только по ним, после короткой
    паузы (чтобы не долбить тем же в шторм 503). Абзац, не получивший ответа и после
    повтора, в возврате отсутствует — его не трогают."""
    answers: dict[int, dict] = {}
    try:
        raw = llm(system_prompt, render_batch_prompt(batch), SCHEMA)
        content = raw.get("content") if isinstance(raw, dict) else raw
        answers.update(_parse_items(content, _tokens_for(batch), stats))
    except Exception as exc:  # noqa: BLE001 — сбой модели не должен ронять весь проход
        _log_llm_error(stats, exc)

    missing = [i for i in range(len(batch)) if i not in answers]
    if not missing:
        return answers

    if retry_pause:
        time.sleep(retry_pause)
    retry_batch = [batch[i] for i in missing]
    try:
        raw = llm(system_prompt, render_batch_prompt(retry_batch), SCHEMA)
        content = raw.get("content") if isinstance(raw, dict) else raw
        retry_answers = _parse_items(content, _tokens_for(retry_batch), stats)
    except Exception as exc:  # noqa: BLE001
        _log_llm_error(stats, exc)
        return answers
    for local_index, item in retry_answers.items():
        answers[missing[local_index]] = item
    return answers


# --- проход по кандидатам ----------------------------------------------------------


def process_candidates(
    candidates: list[Candidate], *, llm: Callable, system_prompt: str = SYSTEM_PROMPT,
    batch_size: int = BATCH_SIZE, retry_pause: float = RETRY_PAUSE_SECONDS,
    stats: dict | None = None, on_batch: Callable[[int, int], None] | None = None,
    after_batch: Callable[[dict[str, list[dict]], list[dict]], None] | None = None,
) -> tuple[dict[str, list[dict]], list[dict], dict]:
    """Порциями просит модель, прогоняет guards. Возвращает (approved, report_rows,
    stats).

    `approved` копит одобренное за ВЕСЬ проход (удобно для теста и для итогового
    отчёта); писать в базу и обновлять отчёт на диске сразу после каждой порции —
    дело `after_batch`, которую вызывающий передаёт с уже открытой базой. Вызывается
    ПОСЛЕ КАЖДОЙ порции, даже когда в ней самой ничего не одобрено: получает то, что
    одобрено именно в этой порции, и снимок ВСЕГО накопленного `report_rows` — чтобы
    отчёт на диске рос вместе с проходом, а не появлялся один раз в конце. На 4142
    абзацах и часах работы ждать конца всего прохода, чтобы записать хоть что-то (и
    оставить от него человекочитаемый след), слишком рискованно — это уже стоило
    прогонов дважды: автообновление и часовой таймаут воркера."""
    approved: dict[str, list[dict]] = {}
    report_rows: list[dict] = []
    stats = stats if stats is not None else {}
    stats.setdefault("llm_errors", 0)
    stats.setdefault("foreign_ids", 0)

    total_batches = (len(candidates) + batch_size - 1) // batch_size if candidates else 0
    for batch_no, start in enumerate(range(0, len(candidates), batch_size), start=1):
        batch = candidates[start:start + batch_size]
        batch_approved: dict[str, list[dict]] = {}
        answers = ask_batch(llm, system_prompt, batch, stats=stats, retry_pause=retry_pause)
        for local_index, candidate in enumerate(batch):
            item = answers.get(local_index)
            if item is None:
                report_rows.append({
                    "candidate": candidate, "outcome": "no_answer",
                    "reason": "модель не ответила — абзац оставлен как был", "pieces": None,
                })
                continue
            item = {**item, "id": candidate.segment_id, "speaker": candidate.speaker}
            spans, reason = propose_split(
                item, segment_id=candidate.segment_id, text=candidate.text, character=candidate.speaker,
            )
            if spans is None:
                report_rows.append({"candidate": candidate, "outcome": "rejected", "reason": reason, "pieces": None})
                continue
            final_spans = _finalize_spans(spans, candidate)
            approved[candidate.segment_id] = final_spans
            batch_approved[candidate.segment_id] = final_spans
            pieces = [(sp["speaker"], candidate.text[sp["start"]:sp["end"]]) for sp in final_spans]
            report_rows.append({"candidate": candidate, "outcome": "split", "reason": None, "pieces": pieces})

        if after_batch:
            after_batch(batch_approved, list(report_rows))
        if on_batch:
            on_batch(batch_no, total_batches)

    return approved, report_rows, stats


# --- запись -------------------------------------------------------------------------


def apply_plan(db, approved: dict[str, list[dict]], *, source: str = SOURCE, chunk_size: int = 50) -> int:
    """Пишет одобренные разрезы порциями с коммитом после каждой — обрыв на середине
    не должен стоить всей проделанной работы. `source` — только запасной вариант на
    случай, если у спана его нет; в обычном проходе его ставит `_finalize_spans`."""
    items = list(approved.items())
    stored_total = 0
    for start in range(0, len(items), chunk_size):
        chunk = items[start:start + chunk_size]
        records = [
            {
                "unit_id": segment_id, "span_start": span["start"], "span_end": span["end"],
                "speaker": span["speaker"], "confidence": span.get("confidence", 0.0),
                "source": span.get("source", source),
            }
            for segment_id, spans in chunk for span in spans
        ]
        stored_total += store_attributions(db, records)
        db.commit()  # без явного commit прогон уже однажды молча откатывался
    return stored_total


# --- чтение из базы (единственное место, где скрипт трогает базу для отбора) -------


def load_paragraphs_for_selection(db, *, book_id: str, chapter_id: str | None) -> list[dict]:
    from app.v2.models import V2Segment

    query = db.query(V2Segment).filter(V2Segment.book_id == book_id)
    if chapter_id:
        query = query.filter(V2Segment.chapter_id == chapter_id)
    segments = query.order_by(V2Segment.chapter_id.asc(), V2Segment.ordinal.asc()).all()
    effective = effective_attributions(db, [s.id for s in segments])

    paragraphs: list[dict] = []
    for segment in segments:
        rows = effective.get(segment.id) or []
        spans = [
            {"start": r.span_start, "end": r.span_end, "speaker": r.speaker,
             "confidence": r.confidence, "source": r.source}
            for r in rows
        ]
        paragraphs.append({
            "segment_id": segment.id, "chapter_id": segment.chapter_id,
            "ordinal": segment.ordinal, "text": segment.text, "spans": spans,
        })
    return paragraphs


# --- отчёт ---------------------------------------------------------------------------


def _reason_bucket(reason: str) -> str:
    """Свободный текст причины → короткий ключ для сводки «сколько по какой причине»."""
    if reason.startswith("модель не ответила"):
        return "модель не ответила"
    if reason.startswith("модель не увидела"):
        return "слов автора нет"
    if reason.startswith("цитата не найдена"):
        return "цитата не найдена"
    if "тире-реплику" in reason:
        return "Рассказчику досталась чужая реплика (тире внутри куска)"
    if "первая реплика принадлежит" in reason:
        return "первая реплика ошибочно у Рассказчика"
    if "продолжается с большой буквы" in reason:
        return "тире после конца предложения с большой буквы — похоже на реплику"
    if reason.startswith("Рассказчику отошло"):
        return ">85% Рассказчику"
    if "не осталось ни одного спана" in reason:
        return "персонажу ничего не осталось"
    if "посторонние speaker" in reason:
        return "посторонний speaker"
    if "выходит за текст" in reason:
        return "спан за пределами текста"
    if "normalise_spans" in reason:
        return "спаны не прошли normalise_spans"
    return "прочее"


def _flat_excerpt(text: str, limit: int = 200) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def _narrator_share(pieces: list[tuple[str, str]]) -> float:
    total = sum(len(chunk) for _, chunk in pieces)
    if not total:
        return 0.0
    narrator_chars = sum(len(chunk) for speaker, chunk in pieces if is_narrator_name(speaker))
    return narrator_chars / total


# Глаголы речи/действия, которыми в этой книге вводятся слова автора — список и сама
# эвристика посчитаны по живым данным (scratchpad/remarks2.py). Тире после запятой
# структурно неотличимо от слов автора регистром (в отличие от тире после точки —
# см. guard_narrator_dash_after_sentence_end_uppercase), поэтому здесь не запрет, а пометка.
SPEECH_VERB_RE = re.compile(
    r"\b(сказал|говор|ответ|спрос|хмыкн|усмехн|прошепт|шепн|крикн|воскликн|буркн|"
    r"пробормот|проворч|задумал|прищур|кивн|вздохн|улыбн|рассме|фыркн|протянул|"
    r"добав|перебил|отозв|раздал|произн|бросил|заметил|согласил|возраз|заговорил|"
    r"продолж|закончил|начал|повторил|уточн|подтверд|признал|пояснил)",
    re.IGNORECASE,
)


def _looks_like_genuine_remark(chunk: str, character: str) -> bool:
    """Глагол речи/действия или имя говорящего внутри куска — уверенный признак
    настоящих слов автора. Ни того ни другого не означает, что разрез неверен (он уже
    прошёл все семь guard-функций) — только то, что глазами эту причину не увидеть
    так же легко, как обычно."""
    if SPEECH_VERB_RE.search(chunk):
        return True
    first_word = (character or "").split()[:1]
    return bool(first_word and first_word[0].lower() in chunk.lower())


def _is_suspicious_split(row: dict) -> bool:
    candidate = row["candidate"]
    narrator_chunks = [chunk for speaker, chunk in row["pieces"] if is_narrator_name(speaker)]
    if not narrator_chunks:
        return False
    return not any(_looks_like_genuine_remark(chunk, candidate.speaker) for chunk in narrator_chunks)


def format_report(*, book_id: str, chapter_id: str | None, candidates: list[Candidate],
                   report_rows: list[dict], applied: bool, stored_rows: int, stats: dict | None = None) -> str:
    split_rows = [r for r in report_rows if r["outcome"] == "split"]
    left_rows = [r for r in report_rows if r["outcome"] != "split"]

    buckets: dict[str, int] = {}
    for row in left_rows:
        buckets[_reason_bucket(row["reason"])] = buckets.get(_reason_bucket(row["reason"]), 0) + 1

    scope = f"глава `{chapter_id}`" if chapter_id else "вся книга"
    mode = f"записано строк {stored_rows}" if applied else "режим: холостой прогон (--apply не задан)"
    suspect_count = sum(1 for row in split_rows if _is_suspicious_split(row))
    lines = [
        "# Разрез слов автора внутри реплик\n",
        f"Книга `{book_id}` · {scope} · кандидатов {len(candidates)} · разрезано {len(split_rows)} · "
        f"оставлено как есть {len(left_rows)} · {mode}\n",
    ]
    if stats is not None:
        # Рядом с «модель не ответила» в шапке — иначе следующий разъезд формата
        # ответа модели (id без решётки, боевой прогон 30-й главы) снова придётся
        # искать зондом по сырым логам, а не глазами по отчёту.
        lines.append(f"Идентификаторов отброшено как чужие: {stats.get('foreign_ids', 0)} · "
                      f"ошибок вызова модели: {stats.get('llm_errors', 0)}\n")
    if split_rows:
        # Тире после запятой запретом не закрыть (регистр там не различает реплику от
        # слов автора) — владелец должен увидеть эти разрезы первыми и глазами решить.
        lines.append(f"⚠ без глагола речи и имени персонажа в куске Рассказчика: "
                      f"{suspect_count} из {len(split_rows)} принятых разрезов — смотреть первыми\n")

    if buckets:
        lines.append("## Оставлено как есть — по причинам\n")
        for bucket, count in sorted(buckets.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {bucket}: {count}")
        lines.append("")

    if left_rows:
        # Без номера и текста «посмотреть распределение» нечем: причину видно, а
        # абзац — нет.
        lines.append("## Оставлено как есть — абзацы\n")
        for row in left_rows:
            candidate = row["candidate"]
            lines.append(f"- Абзац {candidate.ordinal} — {candidate.speaker}: {row['reason']}")
            lines.append(f"  «{_flat_excerpt(candidate.text)}»")
        lines.append("")

    for row in split_rows:
        candidate = row["candidate"]
        share = _narrator_share(row["pieces"])
        marker = " ⚠ без глагола/имени" if _is_suspicious_split(row) else ""
        lines.append(f"\n### Абзац {candidate.ordinal} — {candidate.speaker} (Рассказчику {share:.0%}){marker}\n")
        for speaker, chunk in row["pieces"]:
            tag = "**АВТОР**" if is_narrator_name(speaker) else f"**{speaker}**"
            lines.append(f"- {tag} │ {chunk.strip()}")

    return "\n".join(lines) + "\n"


def write_report(path: str, **kwargs) -> None:
    text = format_report(**kwargs)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# --- CLI ------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", required=True, help="id книги")
    ap.add_argument("--chapter", help="id главы (без него — вся книга)")
    ap.add_argument("--apply", action="store_true", help="писать в базу (без него — холостой прогон)")
    ap.add_argument("--limit", type=int, default=0, help="взять только первые N кандидатов")
    ap.add_argument("--out", required=True, help="путь для markdown-отчёта")
    ap.add_argument("--db", default="", help="путь к sqlite-файлу (по умолчанию — боевой, только на чтение)")
    args = ap.parse_args()
    if args.apply and not str(args.db or "").strip():
        # Боевая база по умолчанию — ловушка для следующей книги: холостой прогон
        # открывает её `mode=ro` и ничем не рискует, а тот же вызов с `--apply` пишет
        # разрезы прямо в прод. Пусть тот, кому правда нужна боевая, назовёт её вслух.
        ap.error(f"--apply требует явного --db: назовите файл (боевой — {PROD_DB})")
    db_path = str(args.db or "").strip() or PROD_DB

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.v2.llm import make_llm

    # Вхолостую база открывается mode=ro — синтаксическая гарантия против случайной
    # записи, если где-то в середине прогона забудут проверить args.apply. --db даёт
    # прогнать на копии (например, для проверки на живой модели без риска для базы).
    db_url = f"sqlite:///{db_path}" if args.apply else f"sqlite:///file:{db_path}?mode=ro&uri=true"
    engine = create_engine(db_url)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    llm = make_llm("deepseek", "deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

    with SessionLocal() as db:
        paragraphs = load_paragraphs_for_selection(db, book_id=args.book, chapter_id=args.chapter)
        candidates = select_candidates(paragraphs)
        if args.limit:
            candidates = candidates[:args.limit]
        print(f"кандидатов: {len(candidates)}")

        stored_rows = 0
        stats: dict = {}

        def _after_batch(batch_approved: dict[str, list[dict]], report_rows_so_far: list[dict]) -> None:
            # И запись, и отчёт — сразу после порции: прерванный прогон не должен
            # оставить применённые разрезы без человекочитаемого следа на диске.
            # `stats` — тот же словарь, что мутирует `process_candidates` по ходу
            # прохода, так что к этому моменту он уже отражает счёт до этой порции.
            nonlocal stored_rows
            if args.apply and batch_approved:
                stored_rows += apply_plan(db, batch_approved)
            write_report(
                args.out, book_id=args.book, chapter_id=args.chapter, candidates=candidates,
                report_rows=report_rows_so_far, applied=args.apply, stored_rows=stored_rows, stats=stats,
            )

        def _progress(done: int, total: int) -> None:
            print(f"  батч {done} из {total}", flush=True)

        approved, report_rows, stats = process_candidates(
            candidates, llm=llm, stats=stats, after_batch=_after_batch, on_batch=_progress,
        )

        # Финальная перезапись — на случай, если кандидатов не было вовсе (батчей
        # не было, _after_batch не вызывался ни разу) или отчёт нужно перечитать
        # с самым полным `report_rows`.
        write_report(
            args.out, book_id=args.book, chapter_id=args.chapter, candidates=candidates,
            report_rows=report_rows, applied=args.apply, stored_rows=stored_rows, stats=stats,
        )

    split_count = sum(1 for r in report_rows if r["outcome"] == "split")
    print(f"кандидатов {len(candidates)}, разрезано {split_count}, "
          f"оставлено {len(candidates) - split_count}, ошибок модели {stats['llm_errors']}, отчёт: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
