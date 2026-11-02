"""«Ты утверждён на роль» — письмо диктору в тот момент, когда решение принято.

Утверждение — это назначение актёра на роль в касте. До сих пор оно происходило молча:
диктор узнавал о нём, когда сам заходил и замечал роль среди своих. Теперь узнаёт сразу
и понимает, что делать дальше — записывать роль целиком, по главам.

Имя актёра в касте и имя учётки в телеграме — разные строки. У Зотова каст говорит
«Pavel Petrovich», телеграм — «Сергей Зотов», и связывает их учётка, к которой
привязаны обе. Поэтому ищем по обоим именам, а не по одному.

Хвостовой «?» в имени актёра — предложение, а не решение
(`parse_actor_name`, `app/v2/cast_ops.py`), и раньше на нём просто молчали: агент
писал «Натали Ким?», и никто никуда не уходил. Теперь на нём зовут на пробу — тем же
механизмом поиска учётки и той же схемой «нашли — написали адресно, не нашли —
переслали владельцу и агентам».

`names_match` — нечёткое совпадение (пересечение слов), и на короткое или частое имя
может отозваться не одна учётка. Писать лично в такой ситуации нельзя — можно попасть
не в того человека, — поэтому неоднозначное имя обрабатывается так же, как отсутствие
учётки: релеем владельцу и агентам, но с отдельной причиной и списком кандидатов, а не
тишиной, которая выглядела бы как «учётки нет».
"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)
from app.models import Character, TelegramAuthAccount, User, UserRole
from app.services import bot_out, studio
from app.services.telegram import max_download_bytes, names_match, send_telegram_message, size_text
from app.time_utils import utcnow_naive
from app.v2.cast_ops import parse_actor_name


def _terms_lines(due_text: str, cost_rub: int) -> list[str]:
    lines = []
    if due_text:
        lines.append(f"Срок: до {due_text}")
    if int(cost_rub or 0) > 0:
        lines.append(f"Стоимость роли: {int(cost_rub):,} ₽".replace(",", " "))
    return lines


def _help_lines() -> list[str]:
    """Ответ на «а как загрузить?» и «где мои реплики?» — прямо в письме (решение 01.10)."""
    base = (settings.app_base_url or "").strip().rstrip("/")
    lines = [""]
    if base:  # адрес не настроен — ссылок не выдумываем
        lines += [f"Как записывать и загружать — {base}/app/help", f"Первые шаги — {base}/app/?onboarding=1"]
    return lines + [f"Нужно больше времени — {studio.write_to()}."]


def naming_block(*, book_title: str, book_code: str, chapter_index: int, chapter_label: str, role: str,
                 actor_name: str, audition: bool) -> list[str]:
    """Как диктору называть файлы — с примером для него самого и его роли.

    Беда: дикторы присылали файлы как придётся, а бот сопоставляет запись с главой и
    ролью по имени. Пример собирает та же функция, что даёт имя при сохранении, иначе
    транслитерация разъехалась бы: код книги — через `book_token` («КП» кириллицей, в файле — `KP`).
    """
    from app.services.audio_naming import book_token, chapter_token, file_extension, latin_word

    # Токены считаем теми же помощниками, что собирают имя при сохранении, а не разбираем
    # готовое имя обратно по «_»: у главы без номера («Пролог») токена главы в имени нет,
    # и разбор по позициям падал бы, а письмо диктору не должно зависеть от подсказки.
    book_token_ = book_token(book_code) or "BOOK"
    chapter_token_ = chapter_token(chapter_label) or "Ch01"
    role_token = latin_word(role) or "Role"
    actor_token = latin_word(actor_name) or "Unknown"
    example = f"{book_token_}_{chapter_token_}_{role_token}_{actor_token}.{file_extension('x.wav')}"
    chapter_number = int(chapter_token_[2:])
    first = ("Пробу назовите так же, а роль, когда вас утвердят, записывайте по главам: один файл — одна глава."
             if audition else "Записывайте роль по главам: один файл — одна глава.")
    return [
        first,
        f"Как называть файлы: {example}",
        f"{book_token_} — книга («{str(book_title or '').strip()}»)",
        f"{chapter_token_} — глава {chapter_number} (Chapter)",
        f"{role_token} — роль латиницей",
        f"{actor_token} — фамилия и имя диктора латиницей, без пробела",
    ]


def _naming_for(db, *, book_id: str, book_title: str, role: str, actor_name: str, audition: bool) -> list[str]:
    """Блок для письма: первая по номеру глава роли; нет глав — `Ch01`."""
    from app.models import Character
    from app.services.audio_naming import chapter_token
    from app.services.audio_uploads import derive_book_code
    from app.services.role_deadlines import role_chapters

    character = db.query(Character).filter(Character.book_id == str(book_id or ""),
                                           Character.name == str(role or "")).first()
    chapters = role_chapters(db, character) if character is not None else {}
    # Первая глава, из названия которой выходит номер: «Пролог» или «Эпилог» токена главы не
    # дают, а пример в письме должен выглядеть как настоящее имя файла.
    index, label = 1, "Глава 1"
    for number in sorted(chapters):
        if chapter_token(chapters[number]):
            index, label = number, chapters[number]
            break
    try:
        return naming_block(book_title=book_title, book_code=derive_book_code(book_title), chapter_index=index,
                            chapter_label=label, role=role, actor_name=actor_name, audition=audition)
    except Exception:  # noqa: BLE001 — подсказка об именах не вправе сорвать письмо и смену диктора
        logger.exception("не собрался блок об именах файлов для роли %r", role)
        return []


def approval_notice(*, role: str, book_title: str, link: str = "", also_in=(), due_text: str = "",
                    cost_rub: int = 0, naming=()) -> str:
    """Что диктор прочтёт в телеграме."""
    lines = [
        f"🎬 Вы утверждены на роль «{str(role or '').strip()}»!",
        f"Книга: {str(book_title or '').strip()}",
    ]
    others = _other_books(also_in)
    if others:
        # Роль персонажа цикла — одна на все книги (профиль автора): одно письмо, не пачка.
        lines.append(f"Также в книгах: {others}")
    lines += _terms_lines(due_text, cost_rub)
    lines += list(naming) or ["Записывайте всю роль по главам."]
    if str(link or "").strip():
        lines.append(f"Все реплики роли: {link.strip()}")
    return "\n".join(lines + _help_lines())


def audition_notice(*, role: str, book_title: str, link: str = "", due_text: str = "", cost_rub: int = 0,
                    naming=()) -> str:
    """Что диктор прочтёт в телеграме, когда его лишь предлагают на роль.

    Отдельная фраза от `approval_notice` нарочно: «вы утверждены» и «вас зовут на
    пробу» — разные новости, и диктор должен сразу понять, какая из двух.
    """
    lines = [
        f"🎙 {studio.name()} — вас зовут на пробу",
        f"Роль: «{str(role or '').strip()}»",
        f"Книга: {str(book_title or '').strip()}",
    ]
    lines += _terms_lines(due_text, cost_rub)
    # Пробу принимает и бот, и сайт. Предел зависит от выбранного узла Bot API.
    lines.append("Запишите пробу и пришлите её сюда, в бот, — аудиофайлом или голосовым "
                 f"(до {size_text(max_download_bytes())}). Можно и на "
                 "сайте: раздел «Загрузка», выберите книгу и роль. Пробу послушает автор.")
    lines += list(naming)
    if str(link or "").strip():
        lines.append(f"Все реплики роли: {link.strip()}")
    return "\n".join(lines + _help_lines())


def role_script_link(book_id: str, role: str) -> str:
    """Ссылка на все реплики роли по книге — пусто, если базовый адрес не настроен."""
    from urllib.parse import quote

    base = (settings.app_base_url or "").strip().rstrip("/")
    if not base or not str(book_id or "").strip():
        return ""
    return f"{base}/app/reader/role/{quote(str(book_id))}?role={quote(str(role or ''))}"


def _matching_accounts(db, actor_name: str) -> list[TelegramAuthAccount]:
    """Все включённые учётки, чьё имя (своё или имя связанного пользователя) подходит
    `actor_name` по `names_match`. Общая часть для `resolve_actor_chat_id` (нужен один
    адресат) и `resolve_actor_candidates` (нужно знать, не подошло ли их несколько).
    """
    wanted = str(actor_name or "").strip()
    if not wanted:
        return []
    accounts = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.is_active == "true").all()
    if not accounts:
        return []
    linked_names: dict[str, str] = {}
    user_ids = [str(account.user_id or "") for account in accounts if str(account.user_id or "").strip()]
    if user_ids:
        linked_names = {
            str(row.id): str(row.display_name or "")
            for row in db.query(User).filter(User.id.in_(user_ids)).all()
        }
    matched = []
    for account in accounts:
        names = [str(account.display_name or ""), linked_names.get(str(account.user_id or ""), "")]
        if any(name and names_match(wanted, name) for name in names):
            matched.append(account)
    return matched


def resolve_actor_chat_id(db, actor_name: str) -> str:
    """Телеграм того, кого в касте зовут `actor_name`. Пусто — если такого нет.

    Сверяет `names_match` — тот же, что и в касте: «Роман Сомов» против «Сомов Роман»
    — один человек. Выключенная учётка не адресат: её выключили, чтобы не писать.

    Первое совпадение, не все. Письма актёрам (утверждение, проба, АСР-уведомления)
    идут через `resolve_actor_candidates`: им нужно отличать одного адресата от
    нескольких. Этот помощник остаётся для тех, кому достаточно «есть ли вообще».
    """
    matched = _matching_accounts(db, actor_name)
    return str(matched[0].telegram_user_id or "") if matched else ""


def resolve_actor_candidates(db, actor_name: str) -> list[tuple[str, str]]:
    """Различные адресаты, которых `actor_name` находит по нечёткому совпадению.

    Список из `(chat_id, подпись)`, без повторов по `chat_id` — учётка, найденная и по
    своему имени, и по имени связанного пользователя, это одна найденная, а не две.
    Больше одного элемента — имя неоднозначно, и писать напрямую нельзя: можно попасть
    не в того человека.
    """
    seen: dict[str, str] = {}
    for account in _matching_accounts(db, actor_name):
        chat_id = str(account.telegram_user_id or "").strip()
        if chat_id and chat_id not in seen:
            seen[chat_id] = str(account.display_name or "").strip() or chat_id
    return list(seen.items())


def agent_chat_ids(db) -> list[str]:
    """Телеграмы агентов — тех, кто ведёт актёров и должен узнать о таком назначении.

    Роль ищется в двух местах, потому что живёт в двух: в `user_roles` у учётки и в
    самой записи телеграм-доступа. Выключенная учётка не адресат — её выключили,
    чтобы не писать.
    """
    accounts = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.is_active == "true").all()
    if not accounts:
        return []
    agent_users = {
        str(row.user_id)
        for row in db.query(UserRole).filter(UserRole.role == "agent").all()
    }
    found: list[str] = []
    for account in accounts:
        chat_id = str(account.telegram_user_id or "").strip()
        if not chat_id or chat_id in found:
            continue
        if str(account.role or "") == "agent" or str(account.user_id or "") in agent_users:
            found.append(chat_id)
    return found


AUDITION_RELAY_HEADER = f"📣 {studio.name()} — актёра надо позвать на пробу самим"
APPROVAL_RELAY_HEADER = f"📣 {studio.name()} — актёра надо предупредить самим"


def _other_books(also_in) -> str:
    return ", ".join(str(title).strip() for title in (also_in or ()) if str(title).strip())


def relay_notice(*, role: str, book_title: str, actor_name: str, link: str = "", header: str = APPROVAL_RELAY_HEADER,
                 also_in=()) -> str:
    """Что прочтут владелец и агент, когда у актёра нет учётки.

    `header` различает два случая: назначение и предложение на пробу — тело письма
    (роль, книга, «учётки нет») у них одинаковое, разная только первая строка.
    """
    lines = [
        header,
        f"Роль: «{str(role or '').strip()}»",
        f"Книга: {str(book_title or '').strip()}",
    ]
    others = _other_books(also_in)
    if others:
        lines.append(f"Также в книгах: {others}")
    lines += [
        f"Актёр: {str(actor_name or '').strip()} — учётки в системе нет, письмо ему не ушло.",
    ]
    if str(link or "").strip():
        lines.append(f"Все реплики роли: {link.strip()}")
    return "\n".join(lines)


def ambiguous_relay_notice(*, role: str, book_title: str, actor_name: str, candidates: list[str], link: str = "", header: str) -> str:
    """Что прочтут владелец и агент, когда имени в касте подходит несколько учёток.

    Список кандидатов — именно для того, чтобы владелец сам разобрался, кто из них
    имелся в виду: адресно этого не решить, `names_match` нечёткий.
    """
    lines = [
        header,
        f"Роль: «{str(role or '').strip()}»",
        f"Книга: {str(book_title or '').strip()}",
        f"Актёр: {str(actor_name or '').strip()} — имя неоднозначное, подходят: {', '.join(candidates)}. Письмо никому не ушло.",
    ]
    if str(link or "").strip():
        lines.append(f"Все реплики роли: {link.strip()}")
    return "\n".join(lines)


def relay_to_owner_and_agents(db, text: str) -> bool:
    """Владельцу и агентам — тем, кто способен передать актёру сам, когда система не может.

    Двумя вызовами, а не одним: при заданном OWNER_TELEGRAM_ID вызов без `direct=True`
    подменяет адресата владельцем и **выбрасывает** `chat_ids` целиком
    (`app/services/telegram.py:118-122`). Одним вызовом агент не получил бы ничего, и
    промах был бы незаметен — письмо-то ушло.

    Публичная — механика общая для всех писем «системе некому написать напрямую»:
    здесь для утверждения и пробы, и в `app/services/asr_notice.py` для неоднозначного
    имени при уведомлении о репликах.
    """
    relayed = bool(send_telegram_message(db, text))
    agents = agent_chat_ids(db)
    if agents:
        relayed = bool(send_telegram_message(db, text, chat_ids=agents, direct=True)) or relayed
    return relayed


def _deliver(db, *, chat_id: str, actor_name: str, notice_text: str, relay_text: str, kind: str,
             character_id: str = "") -> dict:
    """Общая механика отправки: адресно, если учётка нашлась однозначно, иначе — в релей.

    Одна и та же схема для утверждения и для приглашения на пробу — разные только
    тексты писем, которые получились до вызова. Для приглашения на пробу добавляем
    кнопки «Беру / Не смогу / Нужно больше времени».
    """
    if not chat_id:
        return {"notified": False, "reason": "no_telegram", "actor_name": actor_name, "relayed": relay_to_owner_and_agents(db, relay_text), "kind": kind}

    if not character_id:
        markup = None
    elif kind == "audition":
        markup = bot_out.invite_markup(character_id)
    else:
        markup = bot_out.time_markup(character_id)
    sent = send_telegram_message(db, notice_text, chat_ids=[chat_id], direct=True, reply_markup=markup)
    if not sent:
        return {"notified": False, "reason": "not_sent", "actor_name": actor_name, "kind": kind}
    return {"notified": True, "reason": "sent", "actor_name": actor_name, "kind": kind}


def _terms(db, *, book_id: str, role: str, kind: str) -> tuple[str, int]:
    """Срок тем же правилом, что у сверки сроков, и стоимость роли из сметы каста."""
    from app.models import Character, CharacterBudgetSnapshot
    from app.services.role_deadlines import due_for, format_due
    from app.services.shared_runtime import is_narrator_name

    if is_narrator_name(str(role or "")):
        return "", 0  # рассказчик — актёр книги, без срока (спека); смета у него своя
    due_text = format_due(due_for(db, kind, now=utcnow_naive()), kind=kind)
    character = db.query(Character).filter(Character.book_id == str(book_id or ""),
                                           Character.name == str(role or "")).first()
    cost = 0
    if character is not None:
        snapshot = db.query(CharacterBudgetSnapshot).filter(
            CharacterBudgetSnapshot.character_id == character.id).first()
        cost = int(getattr(snapshot, "total_rub", 0) or 0)
    return due_text, cost


def notify_role_approved(db, *, book_id: str, book_title: str, role: str, actor_name: str, also_in=()) -> dict:
    """Сказать актёру, что роль его — или что его на неё зовут. Возвращает, дошло ли,
    и почему нет.

    Молчаливого исхода нет: либо письмо самому актёру, либо релей владельцу и агентам.

    Имя со знаком вопроса — предложение агента, а не решение о назначении, и письмо
    для такого имени — не «вы утверждены», а «вас зовут на пробу» (`kind: "audition"`).
    Разбирать это обязательно через `parse_actor_name`, а не искать «?» на месте:
    `names_match` знака не видит — `_normalize_person_name` вычищает всё, кроме букв,
    цифр и пробелов, — и без разбора «Натали Ким?» нашла бы учётку Натали и получила
    бы настоящее «вы утверждены» вместо приглашения на пробу. По той же причине
    «???» без имени перед знаками разбирается в пустое имя — назначать/приглашать
    некого, и это тоже отдельный, ничей не молчаливый исход (`reason: "no_actor"`).

    Неоднозначное имя (`resolve_actor_candidates` нашёл больше одной учётки) не
    пишется никому напрямую — слишком велик риск попасть не в того человека, —
    а идёт в тот же релей, что и «учётки нет», но со своей причиной и списком
    кандидатов в тексте.

    Учётки у актёра нет — письмо идёт владельцу и агентам: часть актёров агента в
    системе есть, часть нет, и так будет всегда. Молчание тут хуже ошибки: владелец
    решит, что человек предупреждён, а тот будет ждать.
    """
    actor = str(actor_name or "").strip()
    if not actor:
        return {"notified": False, "reason": "no_actor", "actor_name": "", "kind": "approval"}

    name, tentative = parse_actor_name(actor)
    kind = "audition" if tentative else "approval"
    if not name:
        # «???» — «ещё не решил», а не имя: разбирать некого, извещать некого.
        return {"notified": False, "reason": "no_actor", "actor_name": "", "kind": kind}

    link = role_script_link(book_id, role)
    header = AUDITION_RELAY_HEADER if tentative else APPROVAL_RELAY_HEADER
    # Учётку ищем по имени БЕЗ «?» — иначе предложение никого не найдёт: `names_match`
    # чистит строку так же, и адресат тот же самый, что если бы «?» и не было.
    candidates = resolve_actor_candidates(db, name)

    if len(candidates) > 1:
        relay = ambiguous_relay_notice(
            role=role, book_title=book_title, actor_name=name,
            candidates=[label for _, label in candidates], link=link, header=header,
        )
        return {"notified": False, "reason": "ambiguous", "actor_name": name, "relayed": relay_to_owner_and_agents(db, relay), "kind": kind}

    chat_id = candidates[0][0] if candidates else ""
    character = db.query(Character).filter(Character.book_id == str(book_id or ""),
                                           Character.name == str(role or "")).first()
    character_id = str(character.id or "") if character is not None else ""

    due_text, cost_rub = _terms(db, book_id=book_id, role=role, kind="audition" if tentative else "role")
    if tentative:
        naming = _naming_for(db, book_id=book_id, book_title=book_title, role=role, actor_name=name, audition=True)
        notice = audition_notice(role=role, book_title=book_title, link=link, due_text=due_text, cost_rub=cost_rub,
                                 naming=naming)
        relay = relay_notice(role=role, book_title=book_title, actor_name=name, link=link, header=header)
        return _deliver(db, chat_id=chat_id, actor_name=name, notice_text=notice, relay_text=relay, kind=kind,
                        character_id=character_id)

    naming = _naming_for(db, book_id=book_id, book_title=book_title, role=role, actor_name=name, audition=False)
    notice = approval_notice(role=role, book_title=book_title, link=link, also_in=also_in, due_text=due_text,
                             cost_rub=cost_rub, naming=naming)
    relay = relay_notice(role=role, book_title=book_title, actor_name=name, link=link, header=header, also_in=also_in)
    return _deliver(db, chat_id=chat_id, actor_name=name, notice_text=notice, relay_text=relay, kind=kind,
                    character_id=character_id)


def should_notify_actor_change(previous_actor_name: str, next_actor_name: str) -> bool:
    """Решить на месте вызова, стоит ли вообще звать `notify_role_approved`.

    Сравнение — по разобранному имени (`parse_actor_name`: имя без «?» + флаг
    предварительности), не по сырой строке. Сырая строка ловит косметику, которая
    актёра не меняет: «Имя?» → «Имя??» или «Имя ?» — тот же человек в том же
    статусе, а сырое сравнение звало бы `notify_role_approved` заново на каждую
    такую правку, включая круговой возврат голосования к прежнему предложению.

    Пустое разобранное имя (нет актёра или «???») — извещать некого, `False` сразу:
    `notify_role_approved` сам вернул бы `no_actor`, но нет смысла даже вызывать его
    и трогать релей ради ничего.

    Отдельное владельческое решение — утверждённого актёра переписали в то же имя
    со знаком вопроса («Иван» → «Иван?»): формально разобранная пара меняется
    (флаг предварительности), но письма быть не должно — предложить того же
    человека самому себе не новость. Обратное — «Иван?» → «Иван» — новость есть:
    это и означает «утверждён», а не предложен, и старый approval-путь его посылает.
    """
    next_name, next_tentative = parse_actor_name(str(next_actor_name or "").strip())
    if not next_name:
        return False
    prev_name, prev_tentative = parse_actor_name(str(previous_actor_name or "").strip())
    if (prev_name, prev_tentative) == (next_name, next_tentative):
        return False
    if prev_name == next_name and not prev_tentative and next_tentative:
        return False
    return True
