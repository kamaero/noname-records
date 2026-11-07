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
import unicodedata
from datetime import date

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
#: короче или равна этому — реплика «короткая» для правила о перепрыгнутой соседке:
#: три слова тоже находятся где попало. 28.10 («Хубрхю», глава 55) «А… о… т…» (три
#: однобуквенных слова) нашлась на 0.8 внутри «„А“, „о“, „н“ и „т“» за полторы минуты
#: вперёд, и курсор перепрыгнул двенадцать прочитанных реплик.
SHORT_LINE_WORDS = 3
#: ниже этого найденное место короткой реплики — «слабое»: точное совпадение («Что
#: это?» на 1.0) правило не трогает, его и так берёт ближайшее место. Замер 20.10:
#: снимать точные короткие совпадения — значит терять их.
WEAK_SCORE = 0.85
#: насколько уверенно должна найтись в перепрыгнутом куске следующая реплика, чтобы
#: прыжок признать ошибкой. Почти дословно: та же планка, что у повторного подхода.
CONFIDENT_SCORE = TAKE_THRESHOLD

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


_EN_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_EN_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
             "seventeen", "eighteen", "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_EN_ORDINALS = {
    "zero": "zeroth", "one": "first", "two": "second", "three": "third", "four": "fourth",
    "five": "fifth", "six": "sixth", "seven": "seventh", "eight": "eighth", "nine": "ninth",
    "ten": "tenth", "eleven": "eleventh", "twelve": "twelfth", "thirteen": "thirteenth",
    "fourteen": "fourteenth", "fifteen": "fifteenth", "sixteen": "sixteenth",
    "seventeen": "seventeenth", "eighteen": "eighteenth", "nineteen": "nineteenth",
    "twenty": "twentieth", "thirty": "thirtieth", "forty": "fortieth", "fifty": "fiftieth",
    "sixty": "sixtieth", "seventy": "seventieth", "eighty": "eightieth", "ninety": "ninetieth",
    "hundred": "hundredth", "thousand": "thousandth", "million": "millionth",
}
_EN_NUMERIC_WORDS = set(_EN_ONES + _EN_TEENS + _EN_TENS + list(_EN_ORDINALS.values())) | {
    "hundred", "thousand", "million", "point", "minus", "plus",
}
_EN_NUMBER = re.compile(r"(?<![\w:/-])[+-]?\d[\d,.]*(?:st|nd|rd|th)?(?![\w.,:/-])")
_EN_WORD = re.compile(r"[+-]?\d+(?:[.,:/-]\d+)*(?!\w)|[^\W_]+(?:'[^\W_]+)*|[$€£]", re.UNICODE)
_EN_CONTRACTIONS = {
    "can't": "can not", "cannot": "can not", "won't": "will not", "shan't": "shall not",
    "i'm": "i am", "let's": "let us",
}
_EN_TITLES = {"mr": "mister", "mrs": "missus", "dr": "doctor"}
_EN_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
              "september", "october", "november", "december")
_EN_NUMERIC_WORDS.update(_EN_MONTHS)
_EN_NUMERIC_WORDS.update({"dollar", "dollars", "cent", "cents", "euro", "euros", "pound", "pounds",
                          "penny", "pence", "am", "pm", "oh", "$", "€", "£",
                          "usd", "eur", "gbp", "cad", "aud", "jpy", "rub", "cny"})
_EN_MONEY = re.compile(r"(?<!\w)([+-]?)([$€£])\s*(\d+(?:,\d{3})*(?:\.\d+)?)(?!(?:\w|,\d|\.\d))")
_EN_CLOCK = re.compile(r"(?<![\w:])(\d{1,2}):(\d{2})\s*([ap])\.?m\.?(?!\w)")
_EN_ISO_DATE = re.compile(r"(?<![\w-])(\d{4})-(\d{2})-(\d{2})(?![\w-])")
_EN_NAMED_DATE = re.compile(r"\b(" + "|".join(_EN_MONTHS) + r") (\d{1,2}),? (\d{4})(?!\w)")
_EN_YEAR_TAIL = "|".join(["hundred", r"oh (?:" + "|".join(_EN_ONES[1:]) + ")",
                            "(?:" + "|".join(_EN_TENS[2:]) + r")(?: (?:" + "|".join(_EN_ONES[1:]) + "))?",
                            *_EN_TEENS])
_EN_SPOKEN_YEAR = re.compile(r"\byear (nineteen|twenty) (" + _EN_YEAR_TAIL + r")(?![\w-])")
_EN_DATE_YEAR = re.compile(r"(\b(?:" + "|".join(_EN_MONTHS) + r") (?:(?:twenty|thirty) )?(?:"
                           + "|".join(_EN_ORDINALS.values()) + r")) (nineteen|twenty) ("
                           + _EN_YEAR_TAIL + r")(?![\w-])")


def _english_money(match: re.Match) -> str:
    sign, symbol, amount = match.groups()
    main, _, fraction = amount.replace(",", "").partition(".")
    unit, plural, minor, minors = {"$": ("dollar", "dollars", "cent", "cents"),
                                   "€": ("euro", "euros", "cent", "cents"),
                                   "£": ("pound", "pounds", "penny", "pence")}[symbol]
    if len(main) > _MAX_SPELLABLE_DIGITS or len(fraction) > 2 or (len(main) > 1 and main.startswith("0")):
        return f"{symbol} {sign}{amount}"
    text = f"{sign}{main} {unit if int(main) == 1 else plural}"
    if fraction and int(fraction):
        cents = int(fraction.ljust(2, "0"))
        text += f" and {cents} {minor if cents == 1 else minors}"
    return text


def _english_clock(match: re.Match) -> str:
    hour, minute, meridian = match.groups()
    if not 1 <= int(hour) <= 12 or int(minute) > 59:
        return match.group(0)
    minutes = "" if int(minute) == 0 else (f" oh {int(minute)}" if int(minute) < 10 else f" {int(minute)}")
    return f"{int(hour)}{minutes} {meridian}m"


def _english_date(match: re.Match) -> str:
    year, month, day = (int(value) for value in match.groups())
    try:
        date(year, month, day)
    except ValueError:
        return match.group(0)
    ordinal = _english_number_words(day)
    ordinal[-1] = _EN_ORDINALS[ordinal[-1]]
    return f"{_EN_MONTHS[month - 1]} {' '.join(ordinal)} {year}"


def _english_named_date(match: re.Match) -> str:
    month, day, year = match.groups()
    try:
        date(int(year), _EN_MONTHS.index(month) + 1, int(day))
    except ValueError:
        return match.group(0)
    ordinal = _english_number_words(int(day))
    ordinal[-1] = _EN_ORDINALS[ordinal[-1]]
    return f"{month} {' '.join(ordinal)} {year}"


def _canonical_english_years(text: str) -> str:
    # Только явное «year …»: голое «nineteen ninety-nine» может быть двумя
    # числами, а автоматическое склеивание прятало бы неверно прочитанные суммы.
    def replace(match):
        prefix, tail = match.groups()
        if tail == "hundred":
            rest = 0
        elif tail.startswith("oh "):
            rest = _EN_ONES.index(tail[3:])
        elif tail in _EN_TEENS:
            rest = _EN_TEENS.index(tail) + 10
        else:
            parts = tail.split()
            rest = _EN_TENS.index(parts[0]) * 10 + (_EN_ONES.index(parts[1]) if len(parts) > 1 else 0)
        return "year " + " ".join(_english_number_words((19 if prefix == "nineteen" else 20) * 100 + rest))
    text = _EN_SPOKEN_YEAR.sub(replace, text)
    return _EN_DATE_YEAR.sub(lambda match: match.group(1) + " " +
                            _EN_SPOKEN_YEAR.sub(replace, "year " + match.group(2) + " " + match.group(3))[5:], text)


def _english_number_words(n: int) -> list[str]:
    """Ограничение разрядности проверяет вызывающий: идентификаторы не читаем как числа."""
    for scale, name in ((1_000_000, "million"), (1000, "thousand"), (100, "hundred")):
        if n >= scale:
            count, rest = divmod(n, scale)
            return _english_number_words(count) + [name] + (_english_number_words(rest) if rest else [])
    if n < 10:
        return [_EN_ONES[n]]
    if n < 20:
        return [_EN_TEENS[n - 10]]
    ten, one = divmod(n, 10)
    return [_EN_TENS[ten]] + ([_EN_ONES[one]] if one else [])


def _spell_english_number(match: re.Match) -> str:
    # Точку/запятую в конце предложения сохраняем. Неверная группировка, ведущие
    # нули и огромные ID остаются цифрами: угадывание скрыло бы реальную разницу.
    raw = match.group(0)
    number = raw.rstrip(".,")
    punctuation = raw[len(number):]
    sign = []
    if number.startswith(("+", "-")):
        sign = ["minus" if number[0] == "-" else "plus"]
        number = number[1:]
    ordinal = re.search(r"(st|nd|rd|th)$", number)
    ending = ordinal.group(0) if ordinal else ""
    if ordinal:
        number = number[:-2]
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", number):
        return raw
    integer, separator, fractional = number.replace(",", "").partition(".")
    if (len(integer) > _MAX_SPELLABLE_DIGITS or len(fractional) > _MAX_SPELLABLE_DIGITS
            or (len(integer) > 1 and integer.startswith("0")) or (ending and separator)):
        return raw
    n = int(integer)
    if ending:
        expected_ending = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        if ending != expected_ending:
            return raw
    words = _english_number_words(n)
    if ending:
        words[-1] = _EN_ORDINALS[words[-1]]
    if separator:
        words += ["point"] + [_EN_ONES[int(digit)] for digit in fractional]
    return " ".join(sign + words) + punctuation


def _normalize_english_words(value: str) -> list[str]:
    # NFC сохраняет имена с диакритикой; удалять акценты и апострофы нельзя:
    # O'Neil и Oneil, we're и were — разные слова, даже если ASR их путает.
    text = unicodedata.normalize("NFC", str(value or "")).lower()
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"}))
    text = _EN_ISO_DATE.sub(_english_date, text)
    text = _EN_NAMED_DATE.sub(_english_named_date, text)
    text = _EN_CLOCK.sub(_english_clock, text)
    text = _EN_MONEY.sub(_english_money, text)
    text = re.sub(r"\b([ap])\.m\.(?!\w)", r"\1m", text)
    text = _EN_NUMBER.sub(_spell_english_number, text)
    result = []
    for word in _EN_WORD.findall(text):
        if word in _EN_CONTRACTIONS:
            result.extend(_EN_CONTRACTIONS[word].split())
        elif word.endswith("n't"):
            result.extend([word[:-3], "not"])
        elif re.search(r"'(re|ve|ll)$", word):
            stem, suffix = word.rsplit("'", 1)
            result.extend([stem, {"re": "are", "ve": "have", "ll": "will"}[suffix]])
        else:
            # 's (is/has/владение) и 'd (had/would) неоднозначны, их не раскрываем.
            result.append(_EN_TITLES.get(word, word))
    return _canonical_english_years(" ".join(result)).split()


def normalize_words(value: str, *, language: str = "ru") -> list[str]:
    """Слова без знаков, регистра, «ё» и цифр — то, что сравнимо между сценарием и
    слухом. Распознавание пишет числа цифрами («67», «22-й»), сценарий — словами
    («шестьдесят семь», «двадцать второй»): без перевода в слова они не совпадут
    никогда, и реплика с числом уходит в «пропущена» (см. докстринг модуля)."""
    if language == "en":
        return _normalize_english_words(value)
    if language != "ru":
        raise ValueError(f"Unsupported ASR alignment language: {language}")
    text = str(value or "").lower().replace("ё", "е")
    text = _spell_numbers(text)
    text = _STRETCHED_VOWEL.sub(r"\1", text)
    return _WORD.findall(text)


def word_stream(spoken: list[dict], *, language: str = "ru") -> list[tuple[str, float, float]]:
    """Распознанное — одним потоком слов со временем каждого.

    Пословные отметки берутся, если их дали. Если нет — время сегмента растягивается
    по словам поровну: для границ реплики этого достаточно, а модель их не всегда даёт.
    """
    stream: list[tuple[str, float, float]] = []
    for chunk in spoken or []:
        words = chunk.get("words") or []
        if words:
            date_days = {}
            if language == "en":
                for index in range(1, len(words) - 1):
                    day = str(words[index].get("word") or "").strip().rstrip(",")
                    year = str(words[index + 1].get("word") or "").strip().rstrip(".,")
                    if not 1 <= len(day) <= 2 or not day.isdigit() or len(year) != 4 or not year.isdigit():
                        continue
                    month = normalize_words(str(words[index - 1].get("word") or ""), language="en")
                    if len(month) != 1 or month[0] not in _EN_MONTHS:
                        continue
                    try:
                        date(int(year), _EN_MONTHS.index(month[0]) + 1, int(day))
                    except ValueError:
                        continue
                    ordinal = _english_number_words(int(day))
                    ordinal[-1] = _EN_ORDINALS[ordinal[-1]]
                    date_days[index] = ordinal
            for item_index, item in enumerate(words):
                item_start = float(item.get("start") or 0.0)
                # `max` — на случай отсутствующего или нулевого `end`: без него
                # деление на число слов при развороте числа даёт отрицательный шаг,
                # и слова расходятся по времени в обратном порядке.
                item_end = max(float(item.get("end") or 0.0), item_start)
                # Число («67») разворачивается в несколько слов («шестьдесят», «семь»):
                # пословная отметка у распознавания на них одна, поэтому делим её
                # время поровну — как для целого сегмента без пословных отметок, а не
                # отдаём обоим одинаковые start/end, будто они звучали одновременно.
                parts = date_days.get(item_index) or normalize_words(str(item.get("word") or ""), language=language)
                step = (item_end - item_start) / len(parts) if parts else 0.0
                for index, word in enumerate(parts):
                    stream.append((
                        word,
                        round(item_start + step * index, 3),
                        round(item_start + step * (index + 1), 3),
                    ))
            continue
        parts = normalize_words(str(chunk.get("text") or ""), language=language)
        if not parts:
            continue
        start = float(chunk.get("start") or 0.0)
        end = float(chunk.get("end") or start)
        step = (end - start) / len(parts) if len(parts) else 0.0
        for index, word in enumerate(parts):
            stream.append((word, round(start + step * index, 3), round(start + step * (index + 1), 3)))
    if language == "en":
        expanded = []
        index = 0
        while index < len(stream):
            word, start, end = stream[index]
            if (":" in word and re.fullmatch(r"\d{1,2}:\d{2}", word) and index + 1 < len(stream)
                    and stream[index + 1][0] in {"am", "pm"}):
                marker = stream[index + 1]
                parts = normalize_words(f"{word} {marker[0]}", language="en")
                if parts != [word, marker[0]] and parts[-1] == marker[0]:
                    # Провайдер разделяет «2:05» и «p.m.» на две метки. Цифры
                    # разворачиваем внутри первой, AM/PM остаётся на своей метке.
                    clock = parts[:-1]
                    step = (end - start) / len(clock)
                    expanded.extend((part, round(start + step * n, 3), round(start + step * (n + 1), 3))
                                    for n, part in enumerate(clock))
                    expanded.append(marker)
                    index += 2
                    continue
            expanded.append((word, start, end))
            index += 1
        return expanded
    return stream


def _similarity(left: str, right: str, *, language: str = "ru") -> float:
    if not left or not right:
        return 0.0
    if language == "en":
        def comparable(text):
            words = _canonical_english_years(text).split()
            return [word for index, word in enumerate(words)
                    if not (word == "and" and 0 < index < len(words) - 1
                            and words[index - 1] in {"hundred", "thousand", "million", "dollar", "dollars", "euro", "euros", "pound", "pounds"}
                            and words[index + 1] in _EN_NUMERIC_WORDS)]
        expected, heard = comparable(left), comparable(right)
        # На коротких английских словах посимвольная близость обманчива: «no»
        # находилось бы в «now». Число и отрицание нельзя прощать и в длинной фразе.
        if len(expected) <= SHORT_LINE_WORDS and expected != heard:
            return 0.0
        def protected(words):
            return [word for word in words if word in _EN_NUMERIC_WORDS
                    or word in {"not", "never", "no"} or any(char.isdigit() for char in word)]
        if protected(expected) != protected(heard):
            return 0.0
        return Levenshtein.ratio(" ".join(expected), " ".join(heard))
    return Levenshtein.ratio(left, right)


def _best_span(
    target: list[str],
    stream: list[tuple[str, float, float]],
    cursor: int,
    *,
    lookahead: int = LOOKAHEAD_WORDS,
    language: str = "ru",
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
    if language == "en" and ("year" in target or set(target) & set(_EN_MONTHS)):
        widths = sorted(set(widths) | {max(1, size - 3), size + 3})
    limit = min(len(stream), cursor + size + lookahead)

    best = (0.0, cursor, cursor)
    for start in range(cursor, limit):
        for width in widths:
            end = start + width
            if end > len(stream):
                continue
            candidate = " ".join(word for word, _s, _e in stream[start:end])
            score = _similarity(wanted, candidate, language=language)
            if language == "en" and score < 1.0:
                # Нельзя обойти отрицание, обрезав начало/конец кандидата:
                # «will not open the old door» иначе находилось как «open the old door».
                # Дословную реплику не снимаем из-за «Not...» в начале следующей.
                adjacent = ([stream[start - 1][0]] if start > cursor else []) + (
                    [stream[end][0]] if end < len(stream) else [])
                if "not" in adjacent:
                    continue
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
    *,
    language: str = "ru",
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
        pull = max(pull, _similarity(" ".join(normalize_words(expected[rival], language=language)), said,
                                     language=language))
    return pull


def _claimed_by_a_later_line(
    expected: list[str],
    index: int,
    stream: list[tuple[str, float, float]],
    start: int,
    end: int,
    score: float,
    *,
    language: str = "ru",
) -> bool:
    """Не подходит ли найденный отрезок какой-нибудь из ближайших следующих реплик лучше.

    Соседние реплики бывают почти одинаковы («Вторая реплика героя» против «Третья
    реплика героя»), и жадный поиск отдаёт отрезок первой из них — то есть той, которую
    диктор как раз пропустил. Спрашиваем соперников, прежде чем отдать.
    """
    return _rival_pull(expected, index, stream, start, end, language=language) > score + RIVAL_MARGIN


def _needed_by_a_later_line(
    expected: list[str],
    index: int,
    stream: list[tuple[str, float, float]],
    start: int,
    end: int,
    score: float,
    *,
    language: str = "ru",
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
    return _rival_pull(expected, index, stream, start, end, language=language) >= score - RIVAL_MARGIN


def _later_lines_in(
    expected: list[str],
    index: int,
    stretch: list[tuple[str, float, float]],
    threshold: float,
    *,
    min_words: int,
    language: str = "ru",
) -> list[int]:
    """Какие из ближайших следующих реплик звучат в этом куске потока.

    Сравнение — лучшим отрезком внутри куска, а не всем куском: перепрыгнутое бывает
    длиннее реплики-соседки. Соседки короче `min_words` не спрашиваем, когда кусок
    длинный: короткая реплика («А?..») «находится» почти в любом длинном куске.
    """
    found: list[int] = []
    if not stretch:
        return found
    for offset in range(1, RIVAL_DEPTH + 1):
        rival = index + offset
        if rival >= len(expected):
            break
        target = normalize_words(expected[rival], language=language)
        if len(target) < min_words:
            continue
        rival_score, _s, _e = _best_span(target, stretch, 0, lookahead=len(stretch), language=language)
        if rival_score >= threshold:
            found.append(rival)
    return found


def _reread_right_after(target: list[str], stream: list[tuple[str, float, float]], cursor: int,
                       *, language: str = "ru") -> bool:
    """Звучит ли реплика снова вплотную за этим местом — то есть читается ли пара заново."""
    score, start, _end = _best_span(target, stream, cursor, lookahead=TAKE_GAP_WORDS, language=language)
    return score >= CONFIDENT_SCORE and start - cursor <= TAKE_GAP_WORDS


def _jumps_over_a_later_line(
    expected: list[str],
    index: int,
    stream: list[tuple[str, float, float]],
    cursor: int,
    start: int,
    score: float,
    *,
    language: str = "ru",
) -> bool:
    """Не лежит ли между курсором и найденным местом одна из следующих реплик.

    Диктор читает сверху вниз, поэтому место реплики не может стоять за местом
    следующей. 20.10 («Иули», глава 47) однословная «Нет,», которую диктор пропустил,
    нашлась в слове «не» пятнадцатью словами дальше — внутри реплики через одну, — и
    курсор перепрыгнул прочитанную «Я бы вообще убила вас…»: письмо назвало
    пропущенными две прочитанные реплики. Короткую реплику похожее слово найдёт почти
    где угодно; что она не на своём месте, видно только по тому, что она перепрыгнула.

    Два случая, и оба только для короткой реплики и только про длинную соседку
    (длинная реплика случайно далеко не находится, а короткая соседка сама «находится»
    в любом перепрыгнутом куске — замер по 260 сверкам прода: без этих условий правило
    теряло точные совпадения вроде «Что это?» и «Спит дракон под горой»):

    - реплика короче `MIN_TAKE_WORDS` — соседке достаточно подойти перепрыгнутому
      куску не хуже, чем найденное место этой реплике (на мусор между подходами —
      «ой, ещё раз» — правило не срабатывает);
    - реплика до `SHORT_LINE_WORDS` слов, найденная слабо (ниже `WEAK_SCORE`), — если
      соседка звучит в перепрыгнутом почти дословно (`CONFIDENT_SCORE`). 28.10
      («Хубрхю», глава 55): «А… о… т…» встала на 0.8 в «„А“, „о“, „н“ и „т“», хотя
      следующая «Тоа… атом… атом?..» дословно звучала полторы минуты раньше, и сверка
      потеряла весь прочитанный между ними блок — двенадцать реплик.
    """
    if start <= cursor:
        return False
    words = len(normalize_words(expected[index], language=language))
    skipped = stream[cursor:start]
    if words < MIN_TAKE_WORDS:
        return bool(_later_lines_in(expected, index, skipped, max(MATCH_THRESHOLD, score),
                                   min_words=MIN_TAKE_WORDS, language=language))
    if words <= SHORT_LINE_WORDS and score < WEAK_SCORE:
        return bool(_later_lines_in(expected, index, skipped, CONFIDENT_SCORE,
                                   min_words=MIN_TAKE_WORDS, language=language))
    return False


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
    *,
    language: str = "ru",
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
        next_score, next_start, next_end = _best_span(target, stream, cursor, lookahead=TAKE_GAP_WORDS,
                                                   language=language)
        if next_end <= next_start or next_score < TAKE_THRESHOLD:
            break
        if next_start - cursor > TAKE_GAP_WORDS:
            break
        jumped = _later_lines_in(expected, index, stream[cursor:next_start], CONFIDENT_SCORE,
                                min_words=1, language=language)
        if any(not _reread_right_after(normalize_words(expected[r], language=language), stream, next_end,
                                      language=language) for r in jumped):
            # Между подходами дословно звучит следующая реплика, а за «подходом» её
            # повтора нет — значит это не повтор пары, а похожие слова дальше в
            # сценарии. 28.10 («Юмкуба», глава 14): «Ты хочешь мне изменять!» взяла
            # «вторым подходом» «ты хочешь изменять» из длинной реплики через две и
            # перепрыгнула прочитанные «Тогда подпиши!» и «Ты лжец,» — обе в пропуски.
            #
            # Повтор пары («А», «Б», «А», «Б»…) правило не трогает: без этой оговорки
            # замер по проду снял 192 подхода, 161 из них дословные (1.0), — диктор
            # перечитывает реплику вместе с репликой-соседкой. Узнаётся он по тому, что соседка звучит
            # и за подходом. Соседку спрашиваем любой длины: окно — не больше
            # `TAKE_GAP_WORDS` слов, а лишний отказ стоит только подхода.
            break
        if _needed_by_a_later_line(expected, index, stream, next_start, next_end, next_score, language=language):
            break
        takes.append(_take_at(stream, next_start, next_end, next_score))
        cursor = next_end
    return takes, cursor


def align_transcript(expected: list[str], spoken: list[dict], *, language: str = "ru") -> dict:
    """Сопоставить реплики роли с тем, что услышано в файле.

    `expected` — реплики в порядке сценария, `spoken` — сегменты распознавания.
    Язык задаётся книгой: автоопределение по тексту меняло бы правила при каждом дубле.
    """
    normalize_words("", language=language)
    stream = word_stream(spoken, language=language)
    lines: list[dict] = []
    missing: list[int] = []
    cursor = 0

    for index, text in enumerate(expected or []):
        target = normalize_words(text, language=language)
        score, start, end = _best_span(target, stream, cursor, language=language)
        matched = bool(target) and score >= MATCH_THRESHOLD
        if not matched and target:
            # Чтение ушло дальше окна — курсор отстал и сам не догонит: пропуск его
            # нарочно не двигает, иначе пропущенная реплика съела бы поток. Смотрим
            # весь остаток, но спрашиваем строже: на такой длине «похоже» найдётся
            # почти всегда, и по ближнему порогу сюда пролезла бы чужая реплика.
            far_score, far_start, far_end = _best_span(target, stream, cursor, lookahead=len(stream), language=language)
            if far_score >= RESYNC_THRESHOLD:
                score, start, end = far_score, far_start, far_end
                matched = True
        if matched and _claimed_by_a_later_line(expected, index, stream, start, end, score, language=language):
            # Отрезок похож на эту реплику, но на следующую похож заметно больше —
            # значит эту диктор пропустил, а мы едва не отдали ей чужое место и не
            # увели за собой все реплики до конца главы.
            matched = False
        if matched and _jumps_over_a_later_line(expected, index, stream, cursor, start, score, language=language):
            # Найденное место — за следующей репликой: эту диктор пропустил, а мы
            # чуть не увели курсор через прочитанную.
            matched = False
            # Повторного поиска «до отвергнутого места» нет намеренно: он находил
            # реплику внутри той самой соседки, которую она перепрыгнула («Нет,» в
            # «нести» из «Сам нести буду.»), и прочитанная соседка уходила в пропуски —
            # беда «Эины» 20.10. Ограничить его надёжно не удалось; реплика остаётся
            # ненайденной, и это честнее чужого места.
        takes: list[dict] = []
        if matched:
            # Курсор двигает только найденная реплика: пропущенная не должна съесть
            # кусок потока и увести за собой следующие. Двигаем за последний подход —
            # иначе подходы 2 и 3 остаются впереди курсора и сбивают следующие реплики.
            takes, cursor = _collect_takes(target, stream, start, end, score, expected or [], index, language=language)
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
