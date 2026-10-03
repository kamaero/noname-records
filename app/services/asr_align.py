"""Что из сценария диктор произнёс, а что пропустил — и где именно в файле.

Диктор читает свою роль сверху вниз, поэтому реплики в записи идут в том же порядке,
что и в сценарии. Это главная опора выравнивания: искать каждую реплику по всему файлу
независимо — значит позволить второй «Да.» найтись раньше первой и перепутать всё, что
за ней. Поиск идёт вперёд от места, где кончилась предыдущая реплика.

Совпадение нечёткое. Распознавание слышит «Дгарнина» как «Джим вел», глотает предлоги
и дописывает знаки препинания; требовать точного совпадения — значит объявить
пропущенной каждую вторую реплику.
"""
from __future__ import annotations

import re

import Levenshtein

#: ниже этого реплика считается непроизнесённой. Подобрано так, чтобы пережить
#: перевранное имя собственное, но не принять чужой текст за свой
MATCH_THRESHOLD = 0.62
#: насколько далеко вперёд заглядывать в поисках реплики, в словах сверх её длины
LOOKAHEAD_WORDS = 80
#: сколько следующих реплик спросить, не им ли принадлежит найденный отрезок
RIVAL_DEPTH = 2
#: допуск в споре за отрезок. При первом поиске соперник должен быть настолько
#: увереннее, чтобы отобрать; при сборе подходов — настолько же ему прощается, чтобы
#: отрезок ему оставили. Знак разный, потому что при ничьей отрезок принадлежит той
#: реплике, которая в сценарии позже: порядок чтения — единственное, что различает
#: дословно одинаковые соседние реплики.
RIVAL_MARGIN = 0.05
#: насколько увереннее должно быть дальнее совпадение, чем ближнее. Дальний поиск
#: смотрит весь остаток записи, и на такой длине «похоже» находится почти всегда;
#: спрашивать с него как с ближнего — значит раздавать реплики кому попало.
RESYNC_THRESHOLD = 0.80
#: насколько далеко от конца предыдущего подхода может начаться следующий, в словах.
#: Подходы идут вплотную — замер по бою даёт между ними паузы в одну-две секунды, то
#: есть почти ноль слов. Окно нужно не для них, а против случайного повторения тех же
#: слов в другом конце главы: без него такое повторение слиплось бы с этой репликой.
TAKE_GAP_WORDS = 10
#: насколько похожим должен быть отрезок, чтобы считаться ПОВТОРНЫМ чтением той же
#: реплики, а не просто похожим местом. Порог сверки отвечает на другой вопрос — «эта
#: ли реплика здесь звучит»; повторное чтение это те же слова заново, и оно почти
#: всегда близко к единице. Замер по бою: из 405 собранных дополнительных подходов 357
#: имеют оценку 1.0, а соседние куски речи, которые сбор принимал за подходы, давали
#: 0.647 и 0.714. Курсор уходил за проглоченное, и дальше сыпалась вся глава.
TAKE_THRESHOLD = 0.90
#: короче этого реплика не даёт подходов вовсе. Два слова невозможно отличить от
#: похожего места дальше в потоке: «Что?..» звучит в главе десятки раз, и жадный сбор
#: набрал на ней восемь «подходов», потеряв следующую реплику.
MIN_TAKE_WORDS = 3

_WORD = re.compile(r"[а-яёa-z0-9]+")
#: буквенные окончания порядкового после дефиса — от длинных к коротким. Порядок,
#: а не длина решает, что найдёт `re`: альтернативы проверяются по очереди, и первая
#: подошедшая побеждает. Со старым порядком (короткие раньше длинных) «3-его» отдавало
#: одну букву «е» и клеило «го» к следующему слову, «5-ми» — букву «м» и клеило «и».
_ORDINAL_ENDING = r"его|ого|ему|ому|ым|им|ом|ем|го|му|ми|мя|ти|й|я|е|м|ю|х"
#: число с необязательным дефисным окончанием порядкового: «22», «22-й». Дефис должен
#: стоять вплотную к цифре — раньше допускались пробелы вокруг него, и в «5 - я пошёл»
#: дефис глотал «я» как псевдоокончание, теряя местоимение целиком. После окончания —
#: отрицательный просмотр вперёд: без него «5-метровый» подошло бы под окончание «м»
#: и склеилось в «пятыйетровый»; с ним «метровый» окончанием не признаётся, дефис
#: остаётся литералом, а цифра читается количественно («пять метровый»).
_NUMBER = re.compile(rf"(\d+)(-(?:{_ORDINAL_ENDING})(?![а-яёa-z0-9]))?")
#: разделители тысяч цифрами — обычный, неразрывный и узкий неразрывный (U+202F, его
#: вставляют некоторые распознавания) пробел между произвольным числом групп по три
#: цифры: «67 000», «1 000 000», «2 000 000». Старая версия склеивала только одну пару
#: групп, и «1 000 000» после склейки становился «1000» + « 000» — читались раздельно
#: как «тысяча ноль».
_THOUSANDS_SEP = re.compile(r"\d{1,3}(?:[ \xa0 ]\d{3})+(?!\d)")
#: цифр в числе больше этого — не разложить по разрядам (`_three_digits` знает только
#: единицы, десятки и сотни внутри разряда) и не всегда безопасно даже передать в
#: `int()`: очень длинная цифровая строка в услышанном тексте (телефон, id) не должна
#: обрушивать сверку всей роли — такое число просто остаётся цифрами.
_MAX_SPELLABLE_DIGITS = 9


def _glue_thousands(match: re.Match) -> str:
    return re.sub(r"[ \xa0 ]", "", match.group(0))


_ONES = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]  # release-audit: ok — числительные
_ONES_FEM = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
          "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят",
         "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот",
             "восемьсот", "девятьсот"]

#: формы уже нормализованные (ё→е), как их даст `normalize_words` для слова из
#: сценария: сама нормализация к моменту вставки этих слов в текст уже позади
#: («четвёртый» в сценарии и «4-й» из расшифровки обязаны совпасть).
_ORDINAL_ONES = ["", "первый", "второй", "третий", "четвертый", "пятый", "шестой", "седьмой",
                 "восьмой", "девятый"]
_ORDINAL_TEENS = ["десятый", "одиннадцатый", "двенадцатый", "тринадцатый", "четырнадцатый",
                  "пятнадцатый", "шестнадцатый", "семнадцатый", "восемнадцатый", "девятнадцатый"]
_ORDINAL_TENS = ["", "", "двадцатый", "тридцатый", "сороковой", "пятидесятый", "шестидесятый",
                 "семидесятый", "восьмидесятый", "девяностый"]
_ORDINAL_HUNDREDS = ["", "сотый", "двухсотый", "трехсотый", "четырехсотый", "пятисотый",
                     "шестисотый", "семисотый", "восьмисотый", "девятисотый"]


def _three_digits(n: int, ones: list[str]) -> list[str]:
    """Число 0..999 словами: сотни, потом «дцать-teen-единицы» из своего словаря единиц.

    Один словарь единиц на вызов — вызывающий передаёт мужской, женский или
    порядковый набор, а сотни и десятки от рода не зависят (женский род есть только
    у «один»/«два», и там это как раз последняя, единичная позиция).
    """
    words = []
    hundred, rest = divmod(n, 100)
    if hundred:
        words.append(_HUNDREDS[hundred])
    if rest >= 10 and rest < 20:
        words.append(_TEENS[rest - 10])
    else:
        ten, one = divmod(rest, 10)
        if ten:
            words.append(_TENS[ten])
        if one:
            words.append(ones[one])
    return words


def number_words(n: int) -> list[str]:
    """Количественное числительное, именительный падеж мужского рода: «67» → шестьдесят
    семь, «67000» → шестьдесят семь тысяч, «2000» → две тысячи (у «тысячи» счётное
    слово женского рода — «одна»/«две», а не «один»/«два»)."""
    if n == 0:
        return ["ноль"]
    words: list[str] = []
    millions, n = divmod(n, 1_000_000)
    if millions:
        words += _three_digits(millions, _ONES)
        words.append(_million_word(millions))
    thousands, n = divmod(n, 1000)
    if thousands:
        # «1000» → «тысяча», не «одна тысяча»: счётное слово само называет единицу.
        if thousands != 1:
            words += _three_digits(thousands, _ONES_FEM)
        words.append(_thousand_word(thousands))
    if n or not words:
        words += _three_digits(n, _ONES)
    return words


def _last_two_digits(n: int) -> int:
    return n % 100


def _million_word(n: int) -> str:
    """«миллион»/«миллиона»/«миллионов» — как «тысяча», но своё слово и род мужской."""
    tail = _last_two_digits(n)
    if 11 <= tail <= 14:
        return "миллионов"
    last = tail % 10
    if last == 1:
        return "миллион"
    if 2 <= last <= 4:
        return "миллиона"
    return "миллионов"


def _thousand_word(n: int) -> str:
    tail = _last_two_digits(n)
    if 11 <= tail <= 14:
        return "тысяч"
    last = tail % 10
    if last == 1:
        return "тысяча"
    if 2 <= last <= 4:
        return "тысячи"
    return "тысяч"


def ordinal_words(n: int) -> list[str]:
    """Порядковое числительное, именительный падеж мужского рода: только последнее
    слово меняет форму («сто двадцать второй»), достаточно для 0..9999 — дальше
    (томов, страниц за десять тысяч в сценарии не бывает) считаем количественным."""
    if n >= 10000:
        return number_words(n)
    if n == 0:
        return ["нулевой"]
    thousands, rest = divmod(n, 1000)
    words: list[str] = []
    if thousands:
        if rest == 0:
            if thousands == 1:
                return ["тысячный"]
            return number_words(thousands) + ["тысячный"]
        # «1941» → «тысяча девятьсот сорок первый», не «одна тысяча...» — как у
        # `number_words`, счётное слово само называет единицу.
        if thousands != 1:
            words += _three_digits(thousands, _ONES_FEM)
        words.append(_thousand_word(thousands))
    hundred, rest = divmod(rest, 100)
    if hundred:
        if rest == 0:
            words.append(_ORDINAL_HUNDREDS[hundred])
            return words
        words.append(_HUNDREDS[hundred])
    if 10 <= rest < 20:
        words.append(_ORDINAL_TEENS[rest - 10])
        return words
    ten, one = divmod(rest, 10)
    if ten:
        if one == 0:
            words.append(_ORDINAL_TENS[ten])
            return words
        words.append(_TENS[ten])
    if one or not words:
        words.append(_ORDINAL_ONES[one])
    return words


def _spell_numbers(text: str) -> str:
    """Заменить числа в тексте словами — до `_WORD.findall`, иначе цифры сравниваются
    буквально с распознанными и никогда не совпадут со словом сценария (см. модульный
    докстринг: инцидент 2026-09-14). Дефисное окончание («22-й») — порядковое, простая
    цифра — количественное; склеенные пробелом тысячи («67 000», «1 000 000») собираются
    в одно число заранее, чтобы не превратиться в «шестьдесят семь ноль». Слишком длинную
    цифровую строку (телефон, id) не трогаем вовсе — см. `_MAX_SPELLABLE_DIGITS`."""
    text = _THOUSANDS_SEP.sub(_glue_thousands, text)

    def replace(match: re.Match) -> str:
        digits = match.group(1)
        if len(digits) > _MAX_SPELLABLE_DIGITS:
            return match.group(0)
        n = int(digits)
        words = ordinal_words(n) if match.group(2) else number_words(n)
        return " ".join(words)

    return _NUMBER.sub(replace, text)
#: авторская растяжка гласной для выразительности: «попро-о-обую», «Ка-а-а-ак».
#: Диктор (Глеб Лапин, глава 30, роль Сенннуно) читает обычное слово, а расшифровка
#: даёт «попробую» — без схлопывания растяжки `_WORD` режет по дефисам на обрубки
#: («попро», «о», «обую»), и ни один из них не совпадает со сказанным; это вероятная
#: причина того, что сверка засчитала главу лишь на 33% (в изоляции воспроизвести
#: именно такое падение скора не удалось — возможно, дело ещё и во всей главе целиком).
#: Схлопываем ТОЛЬКО повтор одной и той же гласной через дефис — двойные гласные без
#: дефиса («зоолог», «поощрять») и дефис между разными буквами («кто-то», «по-моему»)
#: этим правилом не затрагиваются, туда дефис ставит не растяжка, а сама орфография.
#: Порог — от ДВУХ повторов: одного дефиса мало, чтобы отличить растяжку от стыка двух
#: одинаковых слов («еле-еле», «иди-иди») — там первое слово кончается и второе
#: начинается той же гласной, и по одной обратной ссылке эти случаи неразличимы;
#: у автора же растяжка почти всегда на дефис длиннее одного повтора.
_STRETCHED_VOWEL = re.compile(r"([аеиоуыэюя])(?:-\1){2,}")


def normalize_words(value: str) -> list[str]:
    """Слова без знаков, регистра, «ё» и цифр — то, что сравнимо между сценарием и
    слухом. Распознавание пишет числа цифрами («67», «22-й»), сценарий — словами
    («шестьдесят семь», «двадцать второй»): без перевода в слова они не совпадут
    никогда, и реплика с числом уходит в «пропущена» (см. докстринг модуля)."""
    text = str(value or "").lower().replace("ё", "е")
    text = _spell_numbers(text)
    text = _STRETCHED_VOWEL.sub(r"\1", text)
    return _WORD.findall(text)


def word_stream(spoken: list[dict]) -> list[tuple[str, float, float]]:
    """Распознанное — одним потоком слов со временем каждого.

    Пословные отметки берутся, если их дали. Если нет — время сегмента растягивается
    по словам поровну: для границ реплики этого достаточно, а модель их не всегда даёт.
    """
    stream: list[tuple[str, float, float]] = []
    for chunk in spoken or []:
        words = chunk.get("words") or []
        if words:
            for item in words:
                item_start = float(item.get("start") or 0.0)
                # `max` — на случай отсутствующего или нулевого `end`: без него
                # деление на число слов при развороте числа даёт отрицательный шаг,
                # и слова расходятся по времени в обратном порядке.
                item_end = max(float(item.get("end") or 0.0), item_start)
                # Число («67») разворачивается в несколько слов («шестьдесят», «семь»):
                # пословная отметка у распознавания на них одна, поэтому делим её
                # время поровну — как для целого сегмента без пословных отметок, а не
                # отдаём обоим одинаковые start/end, будто они звучали одновременно.
                parts = normalize_words(str(item.get("word") or ""))
                step = (item_end - item_start) / len(parts) if parts else 0.0
                for index, word in enumerate(parts):
                    stream.append((
                        word,
                        round(item_start + step * index, 3),
                        round(item_start + step * (index + 1), 3),
                    ))
            continue
        parts = normalize_words(str(chunk.get("text") or ""))
        if not parts:
            continue
        start = float(chunk.get("start") or 0.0)
        end = float(chunk.get("end") or start)
        step = (end - start) / len(parts) if len(parts) else 0.0
        for index, word in enumerate(parts):
            stream.append((word, round(start + step * index, 3), round(start + step * (index + 1), 3)))
    return stream


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return Levenshtein.ratio(left, right)


def _best_span(
    target: list[str],
    stream: list[tuple[str, float, float]],
    cursor: int,
    *,
    lookahead: int = LOOKAHEAD_WORDS,
) -> tuple[float, int, int]:
    """Лучший отрезок потока для этой реплики: (сходство, начало, конец) в индексах слов.

    `lookahead` — сколько слов сверх длины реплики просматривать вперёд от курсора.
    Ближний поиск держит узкое окно нарочно: на всей записи «похоже» находится почти
    всегда, и без окна вторая «Да.» нашлась бы раньше первой.
    """
    if not target or cursor >= len(stream):
        return 0.0, cursor, cursor
    wanted = " ".join(target)
    size = len(target)
    # Длина сказанного гуляет: слово проглочено, слово услышано лишнее.
    widths = sorted({max(1, size - 2), max(1, size - 1), size, size + 1, size + 2})
    limit = min(len(stream), cursor + size + lookahead)

    best = (0.0, cursor, cursor)
    for start in range(cursor, limit):
        for width in widths:
            end = start + width
            if end > len(stream):
                continue
            score = _similarity(wanted, " ".join(word for word, _s, _e in stream[start:end]))
            if score > best[0]:
                best = (score, start, end)
        if best[0] > 0.97:
            break
    return best


def _rival_pull(
    expected: list[str],
    index: int,
    stream: list[tuple[str, float, float]],
    start: int,
    end: int,
) -> float:
    """Насколько сильно ближайшие следующие реплики претендуют на этот отрезок.

    Само по себе число ничего не решает: два места сравнивают его с разными порогами и
    в разные стороны — см. `_claimed_by_a_later_line` и `_needed_by_a_later_line`.
    Здесь только замер, чтобы правило в каждом из них осталось одной видимой строкой.
    """
    said = " ".join(word for word, _s, _e in stream[start:end])
    pull = 0.0
    for offset in range(1, RIVAL_DEPTH + 1):
        rival = index + offset
        if rival >= len(expected):
            break
        pull = max(pull, _similarity(" ".join(normalize_words(expected[rival])), said))
    return pull


def _claimed_by_a_later_line(
    expected: list[str],
    index: int,
    stream: list[tuple[str, float, float]],
    start: int,
    end: int,
    score: float,
) -> bool:
    """Не подходит ли найденный отрезок какой-нибудь из ближайших следующих реплик лучше.

    Соседние реплики бывают почти одинаковы («Вторая реплика героя» против «Третья
    реплика героя»), и жадный поиск отдаёт отрезок первой из них — то есть той, которую
    диктор как раз пропустил. Спрашиваем соперников, прежде чем отдать.
    """
    return _rival_pull(expected, index, stream, start, end) > score + RIVAL_MARGIN


def _needed_by_a_later_line(
    expected: list[str],
    index: int,
    stream: list[tuple[str, float, float]],
    start: int,
    end: int,
    score: float,
) -> bool:
    """Не ждёт ли этот отрезок какая-нибудь из ближайших следующих реплик.

    Зеркало `_claimed_by_a_later_line` с обратной асимметрией, и разница не вкусовая.
    Там соперник отбирает отрезок, только если подходит ЗАМЕТНО лучше: при ничьей
    отрезок остаётся текущей реплике, потому что она в сценарии раньше. Здесь наоборот —
    сопернику хватает подойти НЕ ХУЖЕ.

    Причина в том, что в сценарии есть подряд идущие реплики, совпадающие дословно:
    «Кто?» → «Кто?», «Я знаю,» → «Я знаю,». Для них оба вхождения похожи одинаково, и
    единственное, что их различает, — порядок чтения. Жадный сбор подходов забрал бы у
    второй реплики её собственное место и объявил бы её непроизнесённой.

    Ошибаемся сознательно в сторону недобора: подходов в сессии окажется меньше, чем
    записано, зато у следующей реплики её место не отнимут. Недобор портит черновик
    монтажа, отъём — ломает сверку и шлёт диктору ложное письмо.
    """
    return _rival_pull(expected, index, stream, start, end) >= score - RIVAL_MARGIN


def _take_at(stream: list[tuple[str, float, float]], start: int, end: int, score: float) -> dict:
    """Один подход метками времени, а не индексами слов: дальше с ними работает монтаж."""
    return {
        "start": round(stream[start][1], 3),
        "end": round(stream[end - 1][2], 3),
        "score": round(score, 3),
    }


def _collect_takes(
    target: list[str],
    stream: list[tuple[str, float, float]],
    start: int,
    end: int,
    score: float,
    expected: list[str],
    index: int,
) -> tuple[list[dict], int]:
    """Все подходы диктора к этой реплике и место, где кончился последний.

    Первый уже найден вызывающим; остальные ищем вплотную за ним, пока находятся.
    Возвращаемый курсор уходит за ПОСЛЕДНИЙ подход: оставленные впереди него подходы
    сбивали бы следующие реплики.
    """
    if start >= end or end > len(stream):
        return [], end
    if len(target) < MIN_TAKE_WORDS:
        return [_take_at(stream, start, end, score)], end
    takes = [_take_at(stream, start, end, score)]
    cursor = end
    while cursor < len(stream):
        next_score, next_start, next_end = _best_span(target, stream, cursor, lookahead=TAKE_GAP_WORDS)
        if next_end <= next_start or next_score < TAKE_THRESHOLD:
            break
        if next_start - cursor > TAKE_GAP_WORDS:
            break
        if _needed_by_a_later_line(expected, index, stream, next_start, next_end, next_score):
            break
        takes.append(_take_at(stream, next_start, next_end, next_score))
        cursor = next_end
    return takes, cursor


def align_transcript(expected: list[str], spoken: list[dict]) -> dict:
    """Сопоставить реплики роли с тем, что услышано в файле.

    `expected` — реплики в порядке сценария, `spoken` — сегменты распознавания.
    """
    stream = word_stream(spoken)
    lines: list[dict] = []
    missing: list[int] = []
    cursor = 0

    for index, text in enumerate(expected or []):
        target = normalize_words(text)
        score, start, end = _best_span(target, stream, cursor)
        matched = bool(target) and score >= MATCH_THRESHOLD
        if not matched and target:
            # Чтение ушло дальше окна — курсор отстал и сам не догонит: пропуск его
            # нарочно не двигает, иначе пропущенная реплика съела бы поток. Смотрим
            # весь остаток, но спрашиваем строже: на такой длине «похоже» найдётся
            # почти всегда, и по ближнему порогу сюда пролезла бы чужая реплика.
            far_score, far_start, far_end = _best_span(target, stream, cursor, lookahead=len(stream))
            if far_score >= RESYNC_THRESHOLD:
                score, start, end = far_score, far_start, far_end
                matched = True
        if matched and _claimed_by_a_later_line(expected, index, stream, start, end, score):
            # Отрезок похож на эту реплику, но на следующую похож заметно больше —
            # значит эту диктор пропустил, а мы едва не отдали ей чужое место и не
            # увели за собой все реплики до конца главы.
            matched = False
        takes: list[dict] = []
        if matched:
            # Курсор двигает только найденная реплика: пропущенная не должна съесть
            # кусок потока и увести за собой следующие. Двигаем за последний подход —
            # иначе подходы 2 и 3 остаются впереди курсора и сбивают следующие реплики.
            takes, cursor = _collect_takes(target, stream, start, end, score, expected or [], index)
            matched = bool(takes)
        lines.append({
            "index": index,
            "text": str(text or ""),
            "matched": matched,
            "score": round(score, 3),
            # Метки первого подхода: у них прежний смысл, и всё, что читает их сегодня,
            # продолжает работать.
            "start": takes[0]["start"] if takes else None,
            "end": takes[0]["end"] if takes else None,
            "takes": takes,
        })
        if not matched:
            missing.append(index)

    total = len(lines)
    return {
        "lines": lines,
        "missing": missing,
        "matched": total - len(missing),
        "total": total,
        # без округления: доля — число для расчёта, а не для показа; округлит тот, кто покажет
        "coverage": (total - len(missing)) / total if total else 0.0,
    }
