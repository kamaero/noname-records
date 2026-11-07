"""Что в куске разметки актёр обязан произнести, а что — авторские слова.

Кусок, приписанный роли, бывает составным: «— Речь, — ремарка. — Речь». Диктор
читает только речь, а сверка покрытия сравнивала кусок целиком — и рапортовала
пропуск там, где актёр всё сделал верно.

Разбор ошибается в сторону «не разделять». Не разделили там, где надо было, —
получим ложный пропуск: он виден в отчёте и раздражает. Разделили там, где не
надо, — молча перестанем ожидать настоящую речь, и пропуск актёра пройдёт
незамеченным. Первое лечится глазами, второе не лечится ничем.
"""
from __future__ import annotations

import re

#: Граница между речью и авторскими словами: тире, перед которым либо начало
#: куска, либо знак конца фразы, и за которым пробел. Требование знака перед
#: тире — то самое «ошибаться в сторону не разделять»: «Париж — столица» знака
#: не имеет и остаётся речью. Требование пробела после — защита от дефиса
#: внутри слова, где пробела нет.
#:
#: Закрывающая кавычка «»» в этот знак не входит: в «Карте переправы» она
#: перед тире держит не конец ремарки, а связку «"слово" - это..." внутри самой
#: речи персонажа (проверено — все вхождения этого паттерна в книге такие).
#: Если считать её знаком конца, эта связка ложно рвётся на речь и ремарку.
DASH_BOUNDARY = re.compile(r"(?:^|(?<=[,.!?…]))\s*[—–-]\s+")


def speech_and_narration(text: str, *, language: str = "ru") -> tuple[str, list[str]]:
    """Разделить кусок на речь и авторские части.

    Возвращает склеенную через пробел речь и список авторских частей. Пустая
    речь означает, что произносить в этом куске нечего — например, когда
    повествование ошибочно приписано персонажу.
    """
    raw = str(text or "").strip()
    if not raw:
        return "", []

    if language == "en":
        return _english_speech_and_narration(raw)

    starts_with_dash = bool(re.match(r"^\s*[—–-]\s+", raw))
    if not starts_with_dash:
        # Кусок без тире почти всегда — продолжение реплики следующим абзацем:
        # замер по «Карте переправы» дал 607 таких кусков живой речи против 2
        # настоящих повествований на все шестьдесят глав. Двоеточие в конце —
        # единственный надёжный признак второго случая: так рассказчик заканчивает
        # свою же реплику атрибуции («...сказал:»), а не персонаж — свою.
        if raw.endswith(":"):
            return "", [raw]
        return raw, []

    parts = DASH_BOUNDARY.split(raw)

    # После разделения по границам куски чередуются: речь, авторские, речь…
    # Первый элемент пуст, потому что кусок начинался с границы.
    chunks = [part.strip() for part in parts]
    if chunks and not chunks[0]:
        chunks = chunks[1:]

    speech_chunks = [chunk for index, chunk in enumerate(chunks) if index % 2 == 0 and chunk]
    narration_chunks = [chunk for index, chunk in enumerate(chunks) if index % 2 == 1 and chunk]
    return " ".join(speech_chunks), narration_chunks


def _english_speech_and_narration(raw: str) -> tuple[str, list[str]]:
    """Разделять только закрытые кавычки: сомнение не должно скрывать речь."""
    pairs = {'"': '"', '“': '”', "'": "'", '‘': '’'}
    tag = re.compile(r"\b(?:said|asked|replied|answered|whispered|shouted|murmured|"
                     r"added|exclaimed|muttered)\b", re.I)
    pieces: list[str] = []
    narration: list[str] = []
    if raw[0] in pairs:
        position = 0
    else:
        # A leading author tag is only removed when it contains a speech verb.
        opening = re.search(r"""["“‘]|(?<!\w)'(?=\S)""", raw)
        if not opening or not tag.search(raw[:opening.start()]):
            return raw, []
        position = opening.start()
        narration.append(raw[:position].strip())
    while position < len(raw):
        opener = raw[position]
        closer = pairs.get(opener)
        if closer is None:
            return raw, []
        end = position
        while True:
            end = raw.find(closer, end + 1)
            if end < 0:
                break
            # Apostrophes inside English words are not closing quotation marks.
            if opener in ("'", "‘") and end + 1 < len(raw) and raw[end + 1].isalnum():
                continue
            break
        if end < 0:
            return raw, []
        pieces.append(raw[position + 1:end].strip())
        position = end + 1
        following = re.search(r'["“‘]', raw[position:])
        if opener == "'":
            following = re.search(r"""["“‘']""", raw[position:])
        if following is None:
            tail = raw[position:].strip()
            if tail:
                if not tag.search(tail):
                    return raw, []
                narration.append(tail)
            break
        boundary = position + following.start()
        between = raw[position:boundary].strip()
        if not between or not tag.search(between):
            return raw, []
        narration.append(between)
        position = boundary
    return " ".join(part for part in pieces if part), narration
