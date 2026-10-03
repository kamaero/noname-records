"""Чем доказывается приписка реплики персонажу.

Разметка мелкая: авторские слова («— сказал Гамук») лежат отдельным отрезком
рассказчика ВПЛОТНУЮ к реплике. Поэтому доказательство чаще всего не надо искать по
главе — оно рядом, и его достаточно узнать.

Здесь только чистые функции над текстом: ни базы, ни сети. Так их можно проверять на
выдуманных примерах, а опись собирать отдельно.

Везде, где правило можно понять и строго, и мягко, оно понято строго. Ложное
доказательство стоит чужого голоса в записи: реплику, объявленную доказанной, больше
никто не проверит. Недобор доказанного стоит лишь денег на платного арбитра по
остатку — это дешевле в разы.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.pipeline.attribution_triage import normalize_label
from app.services.speech_parts import speech_and_narration

#: Основы глаголов, которыми текст вводит прямую речь. Сравнение — вхождением в
#: слово, а не началом: русский лепит приставки («про-говорил», «за-говорил»), и
#: перечислять их все бессмысленно. От существительных с той же основой
#: («разговор», «сказка») спасает требование глагольного окончания ниже.
#:
#: Второй десяток — не речь буквально, а реакции, которыми русская проза ВВОДИТ
#: реплику («— Славно! — обрадовалась Погтда»). Без них класс терял бы настоящие
#: ремарки; опора на них не опаснее прочих, потому что имя рядом обязано стоять в
#: именительном падеже, а сама ремарка — быть ремаркой (см. `_neighbour_evidence`).
SPEECH_VERB_STEMS = frozenset({
    "сказ", "скаж", "говор", "ответ", "отвеч", "спрос", "спраш", "произнес",
    "шепт", "шепн", "шепч", "крик", "крич", "клик", "буркн", "ворч", "бормот",
    "молв", "добав", "заяв", "возраз", "возраж", "уточн", "интересова", "переби",
    "отозва", "шип", "рявк", "проси", "признал", "подтверд", "поправ", "звучал",
    "обрадова", "огорчи", "удиви", "возмути", "смея", "смеял", "усмехн", "хмыкн",
    "фыркн", "простон", "стон", "вздыхн", "выдохн",
})

#: Глагольное окончание: прошедшее время, настоящее время или возвратная частица.
#: «с» в конце — для «произнёс»; «ё» перед сравнением сводится к «е».
_VERB_TAIL = re.compile(r"(?:лся|лась|лось|лись|ла|ло|ли|л|ится|ется|ит|ет|ют|ут|ат|ят|с)$")

_WORD = re.compile(r"[^\W\d_]+", flags=re.UNICODE)

#: Сколько знаков допускается между глаголом речи и именем. В настоящей ремарке они
#: стоят рядом («— сказал, оглядываясь, Дгарнин»); на разных концах абзаца это уже
#: два независимых факта, а не одна авторская пометка.
CUE_GAP = 40

#: Длина, после которой «ремарка» перестаёт быть ремаркой и становится абзацем
#: прозы. Именно на длинных кусках ломалась первая версия слоя: имя просто
#: встречалось в повествовании, и это засчитывалось за авторские слова.
REMARK_MAX_CHARS = 150

#: Знаки, отделяющие обращение от остальной речи. Обращение стоит в начале реплики,
#: в конце или между запятыми — иначе имя названо в третьем лице, а не окликнуто.
_ADDRESS_LEFT = frozenset(",;:!?…—–-«\"(")
_ADDRESS_RIGHT = frozenset(",;!?…—–-")


def _fold(word: str) -> str:
    return normalize_label(word).replace("ё", "е")


def _verb_spans(text: str) -> list[tuple[int, int]]:
    """Где в тексте стоят слова с глагольным окончанием — годится и причастие."""
    return [match.span() for match in _WORD.finditer(str(text or ""))
            if _VERB_TAIL.search(_fold(match.group(0)))]


def _speech_verb_spans(text: str) -> list[tuple[int, int]]:
    """Где в тексте стоят глаголы речи. Пусто — авторских слов здесь нет."""
    spans = []
    for match in _WORD.finditer(str(text or "")):
        word = _fold(match.group(0))
        if not word or not _VERB_TAIL.search(word):
            continue
        if any(stem in word for stem in SPEECH_VERB_STEMS):
            spans.append(match.span())
    return spans


@dataclass(frozen=True)
class NameHit:
    """Имя из каста, названное в тексте: кто, где и в словарной ли форме."""

    canonical: str
    start: int
    end: int
    exact: bool


def _stem_matches(known_norm: str, word_norm: str) -> bool:
    """Склонённая форма имени. Основа — имя без двух последних букв, но не короче
    четырёх: короткие имена вроде «Агг» склонением почти не меняются, а обрезание их
    до двух букв начало бы ловить чужие слова."""
    stem = known_norm[:-2] if len(known_norm) > 5 else known_norm
    return len(stem) >= 4 and word_norm.startswith(stem)


def find_name_hits(text: str, names: dict[str, str]) -> list[NameHit]:
    """Все имена каста, названные в тексте, с позицией и признаком словарной формы.

    `exact` значит, что слово совпало с именем или алиасом БЕЗ склонения, то есть
    стоит в именительном падеже. Для говорящего в авторских словах это
    единственная допустимая форма: «сказал Дгарнин» — говорит, «сказал Дгарнину» —
    слушает. Склонённое имя тоже возвращается, но подпирать им приписку нельзя.

    Совпадение по основе, указывающее на ДВУХ разных персонажей («Гамук» и
    «Гамукар» для формы «Гамука»), не возвращается вовсе: гадать тут не о чем.
    """
    hits: list[NameHit] = []
    for match in _WORD.finditer(str(text or "")):
        norm = normalize_label(match.group(0))
        if not norm:
            continue
        canonical = names.get(norm)
        if canonical and canonical != "<AMBIGUOUS>":
            hits.append(NameHit(canonical, match.start(), match.end(), True))
            continue
        matched = {value for known, value in names.items()
                   if value != "<AMBIGUOUS>" and _stem_matches(known, norm)}
        if len(matched) == 1:
            hits.append(NameHit(matched.pop(), match.start(), match.end(), False))
    return hits


def find_name_in_narration(narration_parts: list[str], names: dict[str, str]) -> str:
    """Каноничное имя, названное в авторских словах, — и только если оно там одно.

    Ремарка, называющая двоих («— ответил Дгарнину Гамук»), не доказывает ничего:
    кто из них говорит, из неё не видно. Раньше здесь возвращалось первое
    попавшееся имя, и такая ремарка подпирала обоих.
    """
    found = {hit.canonical for part in narration_parts or []
             for hit in find_name_hits(str(part or ""), names)}
    return found.pop() if len(found) == 1 else ""


def canonical_speaker(speaker: str, names: dict[str, str]) -> str:
    """Ярлык отрезка, приведённый к имени каста: в разметке он может быть алиасом."""
    return names.get(normalize_label(speaker), "") or str(speaker or "").strip()


def _names_this_speaker(hit: NameHit, speaker: str, names: dict[str, str]) -> bool:
    return normalize_label(hit.canonical) == normalize_label(canonical_speaker(speaker, names))


def speech_cue(text: str, speaker: str, names: dict[str, str], *,
               remark: bool = False) -> str:
    """Цитата авторских слов, называющих ЭТОГО говорящего, или `""`.

    Спека требует двух вещей сразу: глагол речи и имя. Ни того, ни другого по
    отдельности мало — имя без глагола встречается в любой прозе, а глагол без
    имени («сказал он») не называет никого. Имя обязано стоять в именительном
    падеже и рядом с глаголом: так говорящего отличают от того, к кому обращаются.

    `remark=True` говорит, что кусок — авторская ремарка ТОГО ЖЕ абзаца, отделённая
    тире от речи. У такой ремарки принадлежность известна из вёрстки абзаца, и
    глагол в ней может быть любым: русская проза вводит реплику чем угодно —
    «— покачал головой Дгарнин», «— кивнул Гамук», «— парировал Пупип». Замер по
    книге: строгий список глаголов речи отверг бы примерно половину настоящих
    ремарок. Для соседнего абзаца поблажка не действует — там принадлежность
    ремарки как раз и неизвестна, и держит её только глагол речи.
    """
    body = str(text or "")
    verbs = _speech_verb_spans(body)
    if not verbs and remark and len(body) <= REMARK_MAX_CHARS:
        verbs = _verb_spans(body)
    if not verbs:
        return ""
    for hit in find_name_hits(body, names):
        if not hit.exact or not _names_this_speaker(hit, speaker, names):
            continue
        for start, end in verbs:
            gap = hit.start - end if hit.start >= end else start - hit.end
            if 0 <= gap <= CUE_GAP:
                return body.strip()[:200]
    return ""


def _window(text: str, start: int, end: int, width: int = 200) -> str:
    """Кусок текста вокруг найденного слова, обрезанный по границам слов.

    Цитата обязана быть ДОСЛОВНОЙ частью абзаца: слой 3 ищет её в тексте поиском,
    и склеенная строка такой проверки не переживёт.
    """
    body = str(text or "")
    pad = max(0, (width - (end - start)) // 2)
    left, right = max(0, start - pad), min(len(body), end + pad)
    if left > 0:
        space = body.find(" ", left, start)
        left = space + 1 if space != -1 else left
    if right < len(body):
        space = body.rfind(" ", end, right)
        right = space if space != -1 else right
    return body[left:right].strip()


def address_quote(said: str, speaker: str, names: dict[str, str]) -> str:
    """Цитата реплики, в которой этого персонажа ОКЛИКАЮТ по имени, или `""`.

    Обращение — это имя, отделённое от остальной речи знаками: в начале реплики,
    в конце или между запятыми. Имя посреди фразы («а Дгарнин вчера был в порту»)
    сказано о человеке, а не человеку, и ответной репликой не является. Падеж —
    именительный, как и положено звательной форме русского языка.

    Ищется обращение в РЕЧИ отрезка (ремарка внутри него — слова автора, а не
    собеседника), а цитата вырезается из самого отрезка: речь бывает склеена из
    кусков через ремарку, и такой склейки в тексте абзаца нет.
    """
    body = str(said or "")
    speech, _ = speech_and_narration(body)
    for hit in find_name_hits(speech, names):
        if not hit.exact or not _names_this_speaker(hit, speaker, names):
            continue
        left = speech[:hit.start].rstrip()
        right = speech[hit.end:].lstrip()
        if left and left[-1] not in _ADDRESS_LEFT:
            continue
        if right and right[0] not in _ADDRESS_RIGHT and right.strip(".") != "":
            continue
        word = speech[hit.start:hit.end]
        found = re.search(rf"(?<![^\W\d_]){re.escape(word)}(?![^\W\d_])", body)
        if found is None:
            continue
        return _window(body, found.start(), found.end())
    return ""


#: Слова, которыми текст обозначает говорящего, НЕ называя его. Разметка в таких местах
#: опознаёт персонажа сверх текста — иногда верно (модель свела концы по всей книге),
#: иногда выдумкой. Для озвучки это отдельный вопрос даже когда опознание верно:
#: узнаваемый голос выдаёт тайну раньше автора.
UNNAMED_MARKERS = frozenset({
    "незнакомец", "незнакомка", "голос", "кто-то", "некто", "мужчина", "женщина",
    "старик", "старуха", "тень", "силуэт", "прохожий", "спутник", "спутница",
})


@dataclass
class Evidence:
    """Чем держится приписка: класс, кем подтверждена, цитата и номер абзаца."""

    kind: str
    speaker: str = ""
    quote: str = ""
    paragraph: int = 0


def classify_span(*, speaker: str, span_text: str, narration: list[str], paragraph: int,
                  names: dict[str, str]) -> Evidence:
    """Доказательство в пределах одного абзаца. Соседи — забота `classify_chapter`.

    `narration` — авторские слова этого абзаца, лежащие ОТДЕЛЬНЫМИ отрезками рассказчика:
    в действующей разметке реплика и ремарка разделены, и доказательство почти всегда
    снаружи отрезка-реплики. Авторские части внутри самого отрезка тоже учитываются — у
    абзацев, разделения не переживших.
    """
    _, own_narration = speech_and_narration(span_text)
    # Ремаркой считается то, что отделено от речи тире: куски внутри самого отрезка
    # (там тире съедено разбором) и отрезки рассказчика, которые с тире начинаются.
    # Отрезок рассказчика без тире — проза в одном абзаце с репликой, и поблажки на
    # «любой глагол» он не заслуживает.
    pieces = [(part, _starts_with_dash(part)) for part in (narration or [])]
    pieces += [(part, True) for part in own_narration]
    narration = [part for part, _ in pieces]
    for part, is_remark in pieces:
        quote = speech_cue(part, speaker, names, remark=is_remark)
        if quote:
            return Evidence(kind="cue_named", speaker=canonical_speaker(speaker, names),
                            quote=quote, paragraph=paragraph)
    for part in narration:
        for word in _WORD.findall(str(part or "").lower()):
            if word in UNNAMED_MARKERS:
                return Evidence(kind="unnamed_speaker", speaker=str(speaker or ""),
                                quote=str(part).strip()[:200], paragraph=paragraph)
    return Evidence(kind="unsupported", speaker=str(speaker or ""), paragraph=paragraph)


NARRATOR = "Рассказчик"

#: Классы, которые считаются доказанными текстом и арбитру не отправляются. Список
#: живёт здесь, рядом с самим разбором: опись и гейт честности обязаны понимать
#: «доказано» одинаково, а вторая копия перечня однажды разъедется с первой.
PROVEN_KINDS = ("cue_named", "cue_adjacent", "address_by_name", "alternation")


def _replica_spans(paragraph: dict) -> list[dict]:
    """Отрезки-реплики абзаца: всё, что не рассказчик."""
    return [s for s in paragraph.get("spans") or []
            if str(s.get("speaker") or "").strip() != NARRATOR]


def _paragraph_speakers(paragraph: dict) -> set[str]:
    return {str(s.get("speaker") or "").strip() for s in _replica_spans(paragraph)}


def _narration_parts(paragraph: dict) -> list[str]:
    """Куски абзаца, приписанные рассказчику, — там живут авторские слова.

    Именно КУСКАМИ, а не одной склеенной строкой: склейка двух отрезков через
    пробел даёт текст, которого в абзаце нет, а цитата обязана быть дословной.
    """
    text = str(paragraph.get("text") or "")
    return [part for part in (text[int(s["start"]):int(s["end"])].strip()
                              for s in paragraph.get("spans") or []
                              if str(s.get("speaker") or "").strip() == NARRATOR) if part]


def _chains(paragraphs: list[dict]) -> list[list[int]]:
    """Диалоговые цепочки: подряд идущие абзацы, в каждом есть реплика."""
    chains, current = [], []
    for index, paragraph in enumerate(paragraphs):
        if _replica_spans(paragraph):
            current.append(index)
            continue
        if len(current) > 1:
            chains.append(current)
        current = []
    if len(current) > 1:
        chains.append(current)
    return chains


def classify_chapter(paragraphs: list[dict], *, names: dict[str, str]) -> dict:
    """Доказательство по каждому отрезку-реплике главы.

    Порядок разбора — от сильного к слабому: своё доказательство в абзаце, потом
    соседнее, потом обращение по имени, потом чередование. Слабое не переписывает
    сильное: первое найденное остаётся.
    """
    out: dict[tuple[int, int, int], Evidence] = {}
    for index, paragraph in enumerate(paragraphs):
        number = int(paragraph.get("paragraph") or 0)
        text = str(paragraph.get("text") or "")
        # Авторские слова абзаца — отдельными отрезками рассказчика; именно там в
        # действующей разметке лежит «— сказал Дгарнин», а не внутри реплики.
        narration = _narration_parts(paragraph)
        for span in _replica_spans(paragraph):
            key = (number, int(span["start"]), int(span["end"]))
            speaker = str(span.get("speaker") or "")
            found = classify_span(
                speaker=speaker,
                span_text=text[int(span["start"]):int(span["end"])],
                narration=narration,
                paragraph=number,
                names=names,
            )
            if found.kind == "unsupported":
                found = _neighbour_evidence(paragraphs, index, speaker, names) or found
            out[key] = found
    _apply_alternation(paragraphs, out, names)
    # Доказательство без цитаты проверить нечем, а значит, оно и не доказательство:
    # спека стоит на том, что закрывает реплику только проверяемая строка текста.
    for key, found in out.items():
        if found.kind in PROVEN_KINDS and not str(found.quote or "").strip():
            out[key] = Evidence(kind="unsupported", speaker=found.speaker, paragraph=key[0])
    return out


def _starts_with_dash(text: str) -> bool:
    """Отрезок начинается с тире, то есть это произнесённая реплика, а не проза."""
    return bool(re.match(r"^\s*[—–-]\s", str(text or "")))


def _neighbour_evidence(paragraphs, index, speaker, names):
    """Доказательство в соседнем абзаце: ремарка с именем или обращение по имени.

    Спека допускает соседа только при условии «между репликой и ремаркой нет чужой
    реплики». Отсюда две отсечки, которых в первой версии не было:

    * в нашем собственном абзаце не должно быть реплик другого персонажа — иначе
      неизвестно, чью из них подпирает соседняя ремарка;
    * если у соседа есть своя реплика, его ремарка принадлежит ЕЙ. Такой сосед
      годится только на обращение по имени в ответной реплике.

    И отдельно: наш собственный отрезок обязан начинаться с тире. Соседняя ремарка
    доказывает чью-то РЕЧЬ; кусок без тире — либо проза, ошибочно приписанная
    персонажу (доказывать нечего), либо продолжение реплики, опора которого должна
    найтись в её собственном абзаце.
    """
    own = paragraphs[index]
    if _paragraph_speakers(own) - {str(speaker or "").strip()}:
        return None
    text = str(own.get("text") or "")
    if not any(_starts_with_dash(text[int(s["start"]):int(s["end"])])
               for s in _replica_spans(own)):
        return None
    canonical = canonical_speaker(speaker, names)
    for offset in (1, -1):
        neighbour_index = index + offset
        if not 0 <= neighbour_index < len(paragraphs):
            continue
        neighbour = paragraphs[neighbour_index]
        number = int(neighbour.get("paragraph") or 0)
        replicas = _replica_spans(neighbour)
        if replicas:
            for span in replicas:
                if str(span.get("speaker") or "").strip() == str(speaker or "").strip():
                    continue        # своя же реплика обращением к себе не бывает
                said = str(neighbour.get("text") or "")[int(span["start"]):int(span["end"])]
                if not _starts_with_dash(said):
                    continue        # без тире это проза, ошибочно приписанная персонажу
                quote = address_quote(said, speaker, names)
                if quote:
                    return Evidence(kind="address_by_name", speaker=canonical,
                                    quote=quote, paragraph=number)
            continue
        narration = _narration_parts(neighbour)
        if not narration or len(" ".join(narration)) > REMARK_MAX_CHARS:
            continue                # целый абзац прозы — не авторская ремарка
        if find_name_in_narration(narration, names) != canonical:
            continue                # ремарка называет кого-то ещё или сразу двоих
        for part in narration:
            quote = speech_cue(part, speaker, names)
            if quote:
                return Evidence(kind="cue_adjacent", speaker=canonical,
                                quote=quote, paragraph=number)
    return None


def _turns(paragraphs: list[dict], chain: list[int]) -> list[tuple[str, list, int]] | None:
    """Ходы цепочки: по абзацу на ход. `None`, если абзац делят двое.

    Абзац с репликами двух персонажей — это не один ход, и чередование по такой
    цепочке считать нельзя: неизвестно, чья очередь была чьей.
    """
    turns = []
    for index in chain:
        paragraph = paragraphs[index]
        speakers = _paragraph_speakers(paragraph)
        if len(speakers) != 1:
            return None
        number = int(paragraph.get("paragraph") or 0)
        keys = [(number, int(s["start"]), int(s["end"]))
                for s in _replica_spans(paragraph)]
        turns.append((speakers.pop(), keys, number))
    return turns


def _apply_alternation(paragraphs, out, names):
    """Середину цепочки на двоих доказывают её закреплённые концы.

    Спека держит эту опору на трёх условиях сразу, и слабину даёт любое
    пропущенное: участников ровно двое, ходы ДЕЙСТВИТЕЛЬНО чередуются, и оба конца
    закреплены `cue_named` — самым сильным классом. Концы на `cue_adjacent` или
    `address_by_name` наследовали бы их ошибки всей серединой цепочки.

    Плюс условие, которого спека не пишет прямо, потому что считает его очевидным:
    участники цепочки — персонажи каста. Чередование — единственный класс, который
    доказывает ярлык, не найдя его в тексте, и «UNSURE» им доказывать нечестно:
    это как раз то место, где разметка сама призналась, что не знает говорящего.
    """
    for chain in _chains(paragraphs):
        turns = _turns(paragraphs, chain)
        if not turns or len(turns) < 3:
            continue
        if len({turn[0] for turn in turns}) != 2:
            continue
        if any(names.get(normalize_label(turn[0]), "<AMBIGUOUS>") == "<AMBIGUOUS>"
               for turn in turns):
            continue
        if any(turns[i][0] == turns[i + 1][0] for i in range(len(turns) - 1)):
            continue
        head = next((out[key] for key in turns[0][1] if out[key].kind == "cue_named"), None)
        tail = next((out[key] for key in turns[-1][1] if out[key].kind == "cue_named"), None)
        if head is None or tail is None:
            continue
        for speaker, keys, number in turns[1:-1]:
            # Цитата и номер абзаца берутся с одного конца: слой 3 проверяет, что
            # цитата лежит в тексте названного абзаца, и разошедшаяся пара не
            # пережила бы собственной проверки.
            anchor = head if abs(number - head.paragraph) <= abs(number - tail.paragraph) else tail
            for key in keys:
                if out[key].kind != "unsupported":
                    continue
                out[key] = Evidence(kind="alternation", speaker=out[key].speaker,
                                    quote=anchor.quote, paragraph=anchor.paragraph)


def consecutive_same_speaker(paragraphs: list[dict]) -> list[dict]:
    """Две подряд идущие реплики одного персонажа через границу хода.

    Четвёртая перекрёстная проверка спеки. В диалоге ход переходит собеседнику;
    два хода подряд за одним лицом означают либо потерянную реплику второго, либо
    неверный ярлык — и в обоих случаях чередованию в этой цепочке верить нельзя.
    """
    found: list[dict] = []
    for chain in _chains(paragraphs):
        previous: tuple[str, int] | None = None
        for index in chain:
            paragraph = paragraphs[index]
            speakers = _paragraph_speakers(paragraph)
            number = int(paragraph.get("paragraph") or 0)
            if len(speakers) != 1:
                previous = None
                continue
            speaker = speakers.pop()
            if previous is not None and previous[0] == speaker:
                found.append({"speaker": speaker, "paragraph": number,
                              "previous_paragraph": previous[1]})
            previous = (speaker, number)
    return found
