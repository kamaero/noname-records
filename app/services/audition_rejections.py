"""Вежливый отказ диктору — когда на роль, на которую он пробовался, утвердили другого.

С 03.10 отказ заводит утверждение роли, а не оценка: оценка 1–10 — только сигнал
владельцу (`audition_scores`), а решение по роли принимается утверждением в касте.
Заводится только событием (`schedule_rejections_for_approval` из ручки каста) и никогда —
по состоянию: иначе выкатка разослала бы письма всем, кто пробовался на роли,
утверждённые месяц назад.

Отказ уходит не сразу, а через `REJECTION_DELAY`: утверждение можно переиграть, а
письмо живому человеку — нет. Срок живёт в базе, рассылку раз в минуту делает
`send_due_rejections` (`scripts/send_due_rejections.py` под таймером systemd).

Отказ — один на пару «книга + роль + актёр», сколько бы проб у актёра ни было.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.models import AudioFile, AuditionRejection, AuditionScore, Character, ScriptBook
from app.services import studio
from app.services.audio_deletion import _find_book_for_audio
from app.services.audio_uploads import AUDITION, derive_book_code
from app.services.audition_scores import pair_has_auditions
from app.services.role_approval import relay_to_owner_and_agents, resolve_actor_candidates
from app.services.telegram import names_match, send_telegram_message
from app.time_utils import utcnow_naive
from app.services.author_profile import normalize_name
from app.v2.cast_ops import parse_actor_name, split_aliases

logger = logging.getLogger(__name__)

REJECTION_DELAY = timedelta(minutes=15)

#: имя, которое загрузка ставит пробе без актёра (`audio_uploads`)
UNKNOWN_ACTOR = "Unknown"

PENDING = "pending"
SENT = "sent"
CANCELLED = "cancelled"
NO_ACCOUNT = "no_account"
AMBIGUOUS = "ambiguous"
FAILED = "failed"
#: письмо вот-вот уйдёт: строка занята до отправки, чтобы упавший после неё процесс
#: не отправил письмо второй раз — следующий проход превратит её в `failed`
SENDING = "sending"
#: решение по паре прожито — строка больше не оживает
FINAL = {SENT, NO_ACCOUNT, AMBIGUOUS, FAILED}


def iso_utc(moment: datetime | None) -> str:
    """UTC без зоны из базы → ISO с `Z`: без неё браузер прочтёт время как местное."""
    return f"{moment.replace(microsecond=0).isoformat()}Z" if moment else ""


def rejection_view(rej: AuditionRejection | None) -> dict | None:
    if rej is None or rej.status == CANCELLED:
        return None
    # «отправляется» длится секунды — для экрана это ещё «уйдёт»
    status = PENDING if rej.status == SENDING else rej.status
    return {"status": status, "due_at": iso_utc(rej.due_at), "sent_at": iso_utc(rej.sent_at)}


#: Фраза о причине — после «звучать немного иначе». Без указания, кто решил: 👎 ставят
#: и автор, и студия, и письмо не должно приписывать отказ автору (решение владельца 05.10).
REASON_LINES = {
    "voice": "This character needs a different voice.",
    "manner": "This character needs a different delivery.",
    "all": "This character needs a different voice and delivery.",
}


def rejection_notice(*, role: str, book_title: str, reason: str = "") -> str:
    """Что диктор прочтёт в телеграме. Без единого слова, выдающего род, и без склонения
    имени роли — «вы с Фэдывнютд» было бы хуже, чем «с персонажем «Фэдывнютд»».
    `reason` — что не подошло; без неё письмо без строки о причине.
    Нейтрально: ни автор, ни студия не названы — решение общее."""
    role = str(role or "").strip()
    reason_line = REASON_LINES.get(str(reason or ""), "")
    return "\n".join([
        f"🎙 {studio.name()} — about your audition",
        f"Role: «{role}»",
        f"Book: {str(book_title or '').strip()}",
        "",
        f"Thank you for auditioning. This time, a different voice was selected for «{role}». "
        "This does not reflect the recording quality; the character needs a different sound."
        + (f"\n{reason_line}" if reason_line else ""),
        "",
        "We hope to find another character whose voice is a good match for yours 🙂",
    ])


def rejection_relay_notice(*, role: str, book_title: str, actor_name: str, candidates: list[str]) -> str:
    """Владельцу и агентам — когда написать актёру лично система не может."""
    why = (
        f"ambiguous name; matches: {', '.join(candidates)}"
        if candidates else "no account was found"
    )
    return "\n".join([
        f"🎙 {studio.name()} — audition result was not delivered",
        f"Role: «{str(role or '').strip()}»",
        f"Book: {str(book_title or '').strip()}",
        f"Actor: {str(actor_name or '').strip()} — {why}. Please contact the actor directly.",
    ])


def role_keys(name: str, aliases: str = "") -> set[str]:
    """Все написания роли, свёрнутые как в профиле автора: регистр и ударения не в счёт,
    псевдонимы — тоже имя роли. Роль пробы приходит из имени файла («тик-так»), а в
    касте она «Тик-Так» или вовсе псевдоним («Отец Чудовищ» у Убкнэляба) — точное
    сравнение строк не находило персонажа, и утвердить пробу было нельзя."""
    keys = {normalize_name(item) for item in [str(name or ""), *split_aliases(aliases)] if item and item.strip()}
    keys.discard("")
    return keys


def character_for_role(characters, role: str):
    """Персонаж каста, которому принадлежит роль пробы, или `None`."""
    key = normalize_name(str(role or ""))
    if not key:
        return None
    for character in characters:
        if key in role_keys(character.name, character.aliases):
            return character
    return None


def _loose(key: str) -> str:
    return key.replace("й", "и").replace("ё", "е")


def _one_edit_apart(a: str, b: str) -> bool:
    """Одна замена, вставка или пропуск буквы."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i + 1:] if len(a) == len(b) else a[i:] == b[i + 1:]


def similar_character(characters, role: str):
    """Персонаж, которого роль пробы, скорее всего, и имела в виду, или `None`.

    Роль пробы приходит из имени файла, а диктор пишет имя на слух: «Ийли» вместо
    «Иули». `character_for_role` такую роль не узнаёт намеренно — «й» и «и» разные
    буквы («Тайн» не «таин»), — и 11.10 окно утверждения завело вторую роль «Ийли»
    без единой реплики: диктор, утверждённая на неё, открыла пустую Читалку, а срок
    пробы на настоящей роли остался висеть и прислал ей «просрочено». Поэтому здесь
    не совпадение, а подсказка человеку: «й/и», «ё/е», одна опечатка («Пыфбу» —
    «Пыдбу») или первое слово полного имени («Хлод» — «Хлод Рулыт»), и только если
    похожий персонаж ровно один. Короткие имена (меньше четырёх букв) — только по
    «й/ё»: «Ао» и «Ай» за одну букву — разные люди.
    """
    key = _loose(normalize_name(str(role or "")))
    if not key:
        return None
    found = []
    for character in characters:
        for other in map(_loose, role_keys(character.name, character.aliases)):
            if other == key or (min(len(key), len(other)) >= 4 and _one_edit_apart(other, key)) or (
                    len(key) >= 3 and " " in other and other.split(" ", 1)[0] == key):
                found.append(character)
                break
    return found[0] if len(found) == 1 else None


def approved_actor_on_role(db, *, book_id: str, role: str) -> str:
    """Кто утверждён на роль. Имя со «?» — предложение, не утверждение: пусто."""
    character = character_for_role(db.query(Character).filter_by(book_id=book_id).all(), role)
    if character is None:
        return ""
    name, tentative = parse_actor_name(str(character.actor_name or ""))
    return name if name and not tentative else ""


#: Ниже этой оценки проба — «не подходит». Решение владельца 04.10: 7 и выше — сигнал
#: на утверждение, ниже 7 у обоих — вежливый отказ.
APPROVE_SCORE = 7

#: повод письма — см. `AuditionRejection.cause`
CAUSE_APPROVAL = "approval"
CAUSE_SCORES = "scores"
#: владелец или автор нажали «Отказать» в ленте проб (13.10): ждать утверждения соперника
#: или двух низких оценок незачем — решение уже принято
CAUSE_MANUAL = "manual"


class RejectError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def scores_reject(db, *, book_id: str, role: str, actor_name: str) -> bool:
    """Оценили оба — автор и студия — и оба ниже 7. Одной оценки мало, как бы низко
    её ни поставили: решение владельца 04.10 — роль оценивают двое, и письмо уходит
    только после того, как высказались оба."""
    rows = db.query(AuditionScore).filter_by(book_id=book_id, role=role, actor_name=actor_name).all()
    lanes = {str(row.lane): int(row.score) for row in rows}
    return set(lanes) >= {"author", "studio"} and all(score < APPROVE_SCORE for score in lanes.values())


def sync_rejection_with_scores(db, *, book_id: str, role: str, actor_name: str, now: datetime | None = None):
    """Привести отказ пары в соответствие с оценками после нажатия на шкалу. Не коммитит.

    Обе ниже 7 — отказ через 15 минут (столько же, сколько после утверждения: оценку
    можно поправить, письмо — нет). Оценку подняли или сняли — ждущий отказ отменяется
    сразу, чтобы под строкой не висело «отказ уйдёт», если только он не держится на
    утверждении другого актёра. Заводится только событием нажатия — уже стоящие
    оценки при выкатке писем не шлют.
    """
    now = now or utcnow_naive()
    book = db.get(ScriptBook, str(book_id or ""))
    actor = str(actor_name or "")
    if book is None or not actor.strip() or actor == UNKNOWN_ACTOR:
        return None
    approved = approved_actor_on_role(db, book_id=book.id, role=role)
    if approved and names_match(approved, actor):
        reject = False
    else:
        reject = scores_reject(db, book_id=book.id, role=role, actor_name=actor)
    rej = db.query(AuditionRejection).filter_by(book_id=book.id, role=role, actor_name=actor).one_or_none()
    if reject:
        if rej is None:
            rej = AuditionRejection(book_id=book.id, book_code=derive_book_code(str(book.title or "")), role=role,
                                    actor_name=actor, due_at=now + REJECTION_DELAY, status=PENDING,
                                    cause=CAUSE_SCORES)
            db.add(rej)
        elif rej.status == CANCELLED or (rej.status == PENDING and rej.cause != CAUSE_MANUAL):
            rej.status = PENDING
            rej.due_at = now + REJECTION_DELAY
            rej.cause = CAUSE_SCORES
    # Отказ, нажатый рукой, оценки не отменяют: его снимает только «Не отказывать»
    # или утверждение этого же актёра.
    elif rej is not None and rej.status == PENDING and rej.cause != CAUSE_MANUAL \
            and (not approved or names_match(approved, actor)):
        rej.status = CANCELLED
    db.flush()
    return rej


def schedule_rejections_for_approval(
    db, *, book, role: str, approved_actor: str, aliases: str = "", now: datetime | None = None,
) -> list[AuditionRejection]:
    """Утвердили `approved_actor` — остальным пробовавшимся на роль отказ через 15 минут.
    Не коммитит: ручка каста коммитит голос, назначение и отказы одним коммитом.

    Прожитый отказ (`FINAL`, `sending`) не оживает: письмо уже ушло или могло уйти.
    Пробы на роль ищутся по всем её написаниям (`role_keys`), а отказ заводится на то
    написание, под которым проба лежит, — по нему его найдут лента и `_settle`.
    """
    now = now or utcnow_naive()
    code = derive_book_code(str(book.title or ""))
    keys = role_keys(role, aliases)
    pairs = {
        (str(audio_role or ""), str(name or ""))
        for (audio_role, name) in db.query(AudioFile.role, AudioFile.actor_name).filter(
            AudioFile.kind == AUDITION, AudioFile.book_code == code,
        ).distinct()
        if normalize_name(str(audio_role or "")) in keys
    }
    touched: list[AuditionRejection] = []
    for role, actor in sorted(pairs):
        # «Unknown» — подстановка загрузки без имени, а не человек: писать некому.
        # Порядок аргументов — как в `_settle`: `names_match` несимметричен.
        if not actor.strip() or actor == UNKNOWN_ACTOR:
            continue
        rej = db.query(AuditionRejection).filter_by(book_id=book.id, role=role, actor_name=actor).one_or_none()
        if names_match(approved_actor, actor):
            # Утвердили его самого — его ждущий отказ (по оценкам) гаснет сейчас, а не к
            # сроку: снятое через пять минут утверждение иначе отправило бы письмо.
            if rej is not None and rej.status == PENDING:
                rej.status = CANCELLED
            continue
        if rej is None:
            rej = AuditionRejection(book_id=book.id, book_code=code, role=role, actor_name=actor,
                                    due_at=now + REJECTION_DELAY, status=PENDING, cause=CAUSE_APPROVAL)
            db.add(rej)
        elif rej.status == CANCELLED:
            rej.status = PENDING
            rej.due_at = now + REJECTION_DELAY
            rej.cause = CAUSE_APPROVAL
        elif rej.status == PENDING:
            # отказ по оценкам остаётся отказом по оценкам: повод сильнее утверждения,
            # которое могут снять
            rej.due_at = now + REJECTION_DELAY
        else:
            continue
        touched.append(rej)
    db.flush()
    return touched


def reject_by_hand(db, *, book_id: str, role: str, actor_name: str, now: datetime | None = None):
    """«Отказать» в ленте проб — письмо через 15 минут, как после утверждения: за это время
    можно передумать («Не отказывать»), а письмо живому человеку не отзовёшь. Не коммитит.

    Отказ, который уже ушёл или уходит, не трогаем — второго письма не будет.
    """
    now = now or utcnow_naive()
    book = db.get(ScriptBook, str(book_id or ""))
    actor = str(actor_name or "")
    if book is None or not pair_has_auditions(
        db, book_code=derive_book_code(str(book.title or "")), role=role, actor_name=actor,
    ):
        raise RejectError("not_found")
    if not actor.strip() or actor == UNKNOWN_ACTOR:
        raise RejectError("no_actor")
    approved = approved_actor_on_role(db, book_id=book.id, role=role)
    if approved and names_match(approved, actor):
        raise RejectError("approved")
    rej = db.query(AuditionRejection).filter_by(book_id=book.id, role=role, actor_name=actor).one_or_none()
    if rej is None:
        rej = AuditionRejection(book_id=book.id, book_code=derive_book_code(str(book.title or "")), role=role,
                                actor_name=actor, due_at=now + REJECTION_DELAY, status=PENDING, cause=CAUSE_MANUAL)
        db.add(rej)
    elif rej.status in (PENDING, CANCELLED):
        rej.status = PENDING
        rej.due_at = now + REJECTION_DELAY
        rej.cause = CAUSE_MANUAL
    db.flush()
    return rej


def cancel_pending_rejection(db, *, book_id: str, role: str, actor_name: str):
    """«Не отказывать» — ждущий отказ гаснет, по какому бы поводу он ни был заведён:
    человек передумал, и это сильнее и оценок, и чужого утверждения. Не коммитит."""
    rej = db.query(AuditionRejection).filter_by(
        book_id=str(book_id or ""), role=role, actor_name=str(actor_name or ""),
    ).one_or_none()
    if rej is None or rej.status != PENDING:
        raise RejectError("not_pending")
    rej.status = CANCELLED
    db.flush()
    return rej


def _chat_ids(candidates: list[tuple[str, str]]) -> set[str]:
    return {str(chat_id) for chat_id, _ in candidates if str(chat_id or "").strip()}


def _why_not_send(db, rej: AuditionRejection) -> tuple[str, list[tuple[str, str]]]:
    """Причина не слать письмо (пусто — слать) и адресаты актёра.

    Сравнение имён ловит «Мелвис Моркен» против «Моркен Мелвис», но не учётку под двумя
    именами: проба залита под телеграм-именем («сценический псевдоним»), а утверждён человек
    под именем из каста («настоящее имя»). Поэтому сверяются ещё и чаты — иначе победитель
    получил бы отказ за выигранную роль.
    """
    approved = approved_actor_on_role(db, book_id=rej.book_id, role=rej.role)
    if approved and names_match(approved, rej.actor_name):
        return "утвердили этого актёра", []
    # Повода для письма три: на роль утвердили другого, оба (автор и студия) оценили
    # пробу ниже 7, или отказали рукой. Ни одного к сроку не осталось — письма нет.
    # Оценки — повод, только если отказ заведён ими (нажатием на шкалу): иначе снятое
    # утверждение соперника при двух старых оценках ниже 7 отправило бы письмо, которого
    # никто не заводил (ревью 04.10).
    by_scores = str(rej.cause or "") == CAUSE_SCORES and scores_reject(
        db, book_id=rej.book_id, role=rej.role, actor_name=rej.actor_name,
    )
    by_hand = str(rej.cause or "") == CAUSE_MANUAL
    if not approved and not by_scores and not by_hand:
        return "утверждения нет, а оценки не отклоняют", []
    if not pair_has_auditions(db, book_code=rej.book_code, role=rej.role, actor_name=rej.actor_name):
        return "пробы больше нет", []
    candidates = resolve_actor_candidates(db, rej.actor_name)
    mine = _chat_ids(candidates)
    if not mine:
        return "", candidates
    if approved and mine & _chat_ids(resolve_actor_candidates(db, approved)):
        return "утверждён тот же человек под другим именем", candidates
    # Один человек — одно письмо на роль: две пробы под разными именами одной учётки
    # дают две строки отказа, а письмо должно уйти одно.
    others = db.query(AuditionRejection).filter(
        AuditionRejection.book_id == rej.book_id,
        AuditionRejection.role == rej.role,
        AuditionRejection.actor_name != rej.actor_name,
        AuditionRejection.status.in_((SENT, SENDING)),
    ).all()
    for other in others:
        if mine & _chat_ids(resolve_actor_candidates(db, other.actor_name)):
            return "этому человеку отказ по роли уже ушёл", candidates
    return "", candidates


def _author_reason(db, rej: AuditionRejection) -> str:
    """Что не понравилось автору в пробах этой пары — если он сказал."""
    row = db.query(AuditionScore).filter_by(book_id=rej.book_id, role=rej.role, actor_name=rej.actor_name,
                                            lane="author").one_or_none()
    return str(row.reason or "") if row is not None else ""


def _cancel(db, rej: AuditionRejection, why: str) -> str:
    logger.info("Отказ по пробе %s отменён: %s", rej.id, why)
    rej.status = CANCELLED
    rej.sent_at = None
    db.commit()
    return rej.status


def _settle(db, rej: AuditionRejection, now: datetime) -> str:
    """Решить судьбу созревшего отказа и, если надо, отправить. Коммитит сам.

    Перед любой отправкой — владельцу ли, актёру ли — строка занимается (`sending`) и
    фиксируется: письмо нельзя отозвать, и лучше не отправить вовсе, чем отправить дважды.
    """
    # Утверждение сняли или переиграли в его пользу, либо пробы больше нет —
    # за 15 минут передумать можно, а письмо не отзовёшь.
    why, _ = _why_not_send(db, rej)
    if why:
        return _cancel(db, rej, why)

    rej.status = SENDING
    rej.sent_at = now
    db.commit()

    # Первые чтения шли вне транзакции, а захват мог ждать сохранения каста, которое
    # как раз утвердило этого актёра. Захват уже стоит — проверяем всё заново по свежим
    # данным: `expire_all`, чтобы карта сущностей не подсунула прежнее состояние.
    # Отмена тут безопасна: письмо ещё не уходило.
    db.expire_all()
    rej = db.get(AuditionRejection, rej.id)
    why, candidates = _why_not_send(db, rej)
    if why:
        return _cancel(db, rej, why)

    book = db.get(ScriptBook, rej.book_id)
    title = str(getattr(book, "title", "") or "")
    if len(candidates) != 1:
        relay_to_owner_and_agents(db, rejection_relay_notice(
            role=rej.role, book_title=title, actor_name=rej.actor_name,
            candidates=[label for _, label in candidates],
        ))
        rej.status = AMBIGUOUS if candidates else NO_ACCOUNT
    else:
        # `direct=True` обязателен: без него OWNER_TELEGRAM_ID подменит адресата владельцем.
        sent = send_telegram_message(
            db, rejection_notice(role=rej.role, book_title=title, reason=_author_reason(db, rej)),
            chat_ids=[candidates[0][0]], direct=True,
        )
        rej.status = SENT if sent else FAILED
    db.commit()
    return rej.status


def send_due_rejections(db, *, now: datetime | None = None) -> list[tuple[str, str]]:
    """Разослать созревшие отказы. Каждая строка — своя транзакция: сбой на одной не
    должен ни отправить уже отправленные второй раз, ни держать очередь за собой.

    `failed` не повторяется — письмо могло уйти, а ответ потеряться; владелец видит
    статус в листе проб. По той же причине `sending`, оставшийся от упавшего прохода,
    становится `failed`, а не отправляется снова.
    """
    now = now or utcnow_naive()
    done: list[tuple[str, str]] = []
    for stale in db.query(AuditionRejection).filter(AuditionRejection.status == SENDING).all():
        stale.status = FAILED
        db.commit()
        done.append((stale.id, FAILED))
    due_ids = [
        rej.id
        for rej in db.query(AuditionRejection)
        .filter(AuditionRejection.status == PENDING, AuditionRejection.due_at <= now)
        .order_by(AuditionRejection.due_at)
    ]
    for rej_id in due_ids:
        try:
            status = _settle(db, db.get(AuditionRejection, rej_id), now)
        except Exception:
            logger.exception("Отказ по пробе %s не разослан — помечен как failed", rej_id)
            db.rollback()
            rej = db.get(AuditionRejection, rej_id)
            if rej.status in (PENDING, SENDING):
                rej.status = FAILED
                rej.sent_at = rej.sent_at or now
                db.commit()
            status = rej.status
        done.append((rej_id, status))
    return done


def forget_audition(db, audio) -> None:
    """Проба уходит насовсем. Если она у пары последняя — уходят и оценки пары, а ждущий
    отказ отменяется: письмо про пробу, которой нет, было бы бессмыслицей.
    Не коммитит — удаление коммитит всё одним коммитом. Зовётся до `db.delete(audio)`,
    поэтому сама проба исключается из подсчёта."""
    book_code, role, actor = str(audio.book_code or ""), str(audio.role or ""), str(audio.actor_name or "")
    if pair_has_auditions(db, book_code=book_code, role=role, actor_name=actor, exclude_id=str(audio.id)):
        return
    book = _find_book_for_audio(db, audio)
    if book is None:
        return
    db.query(AuditionScore).filter_by(book_id=book.id, role=role, actor_name=actor).delete()
    rej = db.query(AuditionRejection).filter_by(book_id=book.id, role=role, actor_name=actor).one_or_none()
    if rej is not None and rej.status == PENDING:
        rej.status = CANCELLED
    db.flush()
