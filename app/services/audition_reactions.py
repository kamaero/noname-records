"""Реакция автора на пробу — и вежливый отказ, который следует за 👎.

Отказ уходит не сразу, а через `REJECTION_DELAY`: промах пальцем мимо 👍 не должен
стать письмом живому человеку, которое нельзя отозвать. Срок живёт в базе, а не в
очереди: основной воркер часами занят финалами глав, и задание «через 15 минут»
простояло бы там до вечера. Рассылку раз в минуту делает `send_due_rejections`
(`scripts/send_due_rejections.py` под таймером systemd).

Отказ — один на пару «книга + роль + актёр», сколько бы проб ни получили 👎.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.models import AudioFile, AuditionReaction, AuditionRejection, Character, ScriptBook
from app.services.audio_deletion import _find_book_for_audio
from app.services.audio_uploads import AUDITION
from app.services.auditions import find_audition
from app.services.role_approval import relay_to_owner_and_agents, resolve_actor_candidates
from app.services.telegram import names_match, send_telegram_message
from app.time_utils import utcnow_naive
from app.v2.cast_ops import parse_actor_name
from app.services import studio

logger = logging.getLogger(__name__)

REJECTION_DELAY = timedelta(minutes=15)

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


class ReactionError(ValueError):
    """`code` — машинный код отказа для ручки, не фраза для человека."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def iso_utc(moment: datetime | None) -> str:
    """UTC без зоны из базы → ISO с `Z`: без неё браузер прочтёт время как местное."""
    return f"{moment.replace(microsecond=0).isoformat()}Z" if moment else ""


def rejection_view(rej: AuditionRejection | None) -> dict | None:
    if rej is None or rej.status == CANCELLED:
        return None
    # «отправляется» длится секунды — для экрана это ещё «уйдёт»
    status = PENDING if rej.status == SENDING else rej.status
    return {"status": status, "due_at": iso_utc(rej.due_at), "sent_at": iso_utc(rej.sent_at)}


def pair_has_thumbs_down(db, *, book_code: str, role: str, actor_name: str) -> bool:
    """Стоит ли 👎 хотя бы на одной пробе этого актёра на эту роль."""
    ids = [
        row.id
        for row in db.query(AudioFile.id).filter(
            AudioFile.kind == AUDITION,
            AudioFile.book_code == book_code,
            AudioFile.role == role,
            AudioFile.actor_name == actor_name,
        )
    ]
    if not ids:
        return False
    return (
        db.query(AuditionReaction.id)
        .filter(AuditionReaction.audio_file_id.in_(ids), AuditionReaction.value == -1)
        .first()
        is not None
    )


def sync_rejection(
    db, *, book_id: str, book_code: str, role: str, actor_name: str, now: datetime, fresh_down: bool,
) -> AuditionRejection | None:
    """Привести отказ пары в соответствие с реакциями. Не коммитит.

    `fresh_down` — только что поставлен 👎: отказ заводится или его срок сдвигается.
    Иначе — 👎 могли снять: если на паре не осталось ни одного, ждущий отказ отменяется.
    """
    rej = (
        db.query(AuditionRejection)
        .filter_by(book_id=book_id, role=role, actor_name=actor_name)
        .one_or_none()
    )
    if fresh_down:
        if rej is None:
            rej = AuditionRejection(
                book_id=book_id, book_code=book_code, role=role, actor_name=actor_name,
                due_at=now + REJECTION_DELAY, status=PENDING,
            )
            db.add(rej)
        elif rej.status in (PENDING, CANCELLED):
            rej.status = PENDING
            rej.due_at = now + REJECTION_DELAY
        db.flush()
        return rej
    if rej is not None and rej.status == PENDING and not pair_has_thumbs_down(
        db, book_code=book_code, role=role, actor_name=actor_name,
    ):
        rej.status = CANCELLED
        db.flush()
    return rej


def set_reaction(db, audio_id: str, *, voter_uid: str, voter_name: str, value: int, now: datetime | None = None) -> dict:
    """Поставить (1, -1) или снять (0) реакцию на пробу. Коммитит сам."""
    if value not in (1, -1, 0):
        raise ReactionError("bad_value")
    audio = find_audition(db, audio_id)
    if audio is None:
        raise ReactionError("not_found")
    now = now or utcnow_naive()

    row = db.query(AuditionReaction).filter_by(audio_file_id=audio.id, voter_uid=voter_uid).one_or_none()
    if value == 0:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(AuditionReaction(audio_file_id=audio.id, voter_uid=voter_uid, voter_name=voter_name, value=value))
    else:
        row.value = value
        row.voter_name = voter_name
    db.flush()

    rej = None
    book = _find_book_for_audio(db, audio)
    if book is not None:
        rej = sync_rejection(
            db, book_id=book.id, book_code=str(audio.book_code or ""), role=str(audio.role or ""),
            actor_name=str(audio.actor_name or ""), now=now, fresh_down=value == -1,
        )
    db.commit()
    return {"author_reaction": value or None, "rejection": rejection_view(rej)}


def rejection_notice(*, role: str, book_title: str) -> str:
    """Что диктор прочтёт в телеграме. Без единого слова, выдающего род, и без склонения
    имени роли — «вы с Дазиптовд» было бы хуже, чем «с персонажем «Дазиптовд»»."""
    role = str(role or "").strip()
    return "\n".join([
        f"🎙 {studio.name()} — про вашу пробу",
        f"Роль: «{role}»",
        f"Книга: {str(book_title or '').strip()}",
        "",
        f"Спасибо, что попробовались! В этот раз вы с персонажем «{role}» не сошлись "
        "характерами — точнее, тембрами. Запись тут ни при чём: просто у автора в голове "
        "этот герой говорит чуть иначе.",
        "",
        "Не прощаемся: героев много, и среди них наверняка есть тот, с кем ваш голос "
        "сойдётся с первого слова 🙂",
    ])


def rejection_relay_notice(*, role: str, book_title: str, actor_name: str, candidates: list[str]) -> str:
    """Владельцу и агентам — когда написать актёру лично система не может."""
    why = (
        f"имя неоднозначное, подходят: {', '.join(candidates)}"
        if candidates else "учётки в системе нет"
    )
    return "\n".join([
        f"🎙 {studio.name()} — отказ по пробе не доставлен",
        f"Роль: «{str(role or '').strip()}»",
        f"Книга: {str(book_title or '').strip()}",
        f"Актёр: {str(actor_name or '').strip()} — {why}. Сообщите, пожалуйста, сами.",
    ])


def actor_approved_on_role(db, *, book_id: str, role: str, actor_name: str) -> bool:
    """Утверждён ли актёр на роль. Имя со «?» — предложение, не утверждение."""
    for character in db.query(Character).filter_by(book_id=book_id, name=role):
        name, tentative = parse_actor_name(str(character.actor_name or ""))
        if name and not tentative and names_match(name, actor_name):
            return True
    return False


def _settle(db, rej: AuditionRejection, now: datetime) -> str:
    """Решить судьбу созревшего отказа и, если надо, отправить. Коммитит сам.

    Перед любой отправкой — владельцу ли, актёру ли — строка занимается (`sending`) и
    фиксируется: письмо нельзя отозвать, и лучше не отправить вовсе, чем отправить дважды.
    """
    if not pair_has_thumbs_down(db, book_code=rej.book_code, role=rej.role, actor_name=rej.actor_name):
        rej.status = CANCELLED
        db.commit()
        return rej.status
    if actor_approved_on_role(db, book_id=rej.book_id, role=rej.role, actor_name=rej.actor_name):
        rej.status = CANCELLED
        db.commit()
        return rej.status
    book = db.get(ScriptBook, rej.book_id)
    title = str(getattr(book, "title", "") or "")
    candidates = resolve_actor_candidates(db, rej.actor_name)

    rej.status = SENDING
    rej.sent_at = now
    db.commit()

    if len(candidates) != 1:
        relay_to_owner_and_agents(db, rejection_relay_notice(
            role=rej.role, book_title=title, actor_name=rej.actor_name,
            candidates=[label for _, label in candidates],
        ))
        rej.status = AMBIGUOUS if candidates else NO_ACCOUNT
    else:
        # `direct=True` обязателен: без него OWNER_TELEGRAM_ID подменит адресата владельцем.
        sent = send_telegram_message(
            db, rejection_notice(role=rej.role, book_title=title), chat_ids=[candidates[0][0]], direct=True,
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


def attach_reactions(db, rows: list[dict], *, book_id: str, viewer_name: str, sees_all: bool) -> list[dict]:
    """Добавить к пробам реакцию автора и судьбу отказа — по правам смотрящего.

    Ручка отдаёт все пробы книги любому вошедшему, поэтому резать здесь, а не на экране.
    Владелец и автор (`sees_all`) видят всё. Диктор — только свою пробу («своя» — тем же
    `names_match`, каким решается право удалить), и 👎 на ней — лишь когда отказ уже
    прожит: иначе промах пальцем он увидит раньше, чем автор успеет его снять.
    """
    if not rows:
        return rows
    ids = [str(row.get("id") or "") for row in rows]
    reactions: dict[str, int] = {}
    for reaction in (
        db.query(AuditionReaction)
        .filter(AuditionReaction.audio_file_id.in_(ids))
        .order_by(AuditionReaction.updated_at)
    ):
        reactions[reaction.audio_file_id] = reaction.value
    rejections = {
        (rej.role, rej.actor_name): rej
        for rej in db.query(AuditionRejection).filter_by(book_id=book_id)
    }
    for row in rows:
        value = reactions.get(str(row.get("id") or ""))
        rej = rejections.get((str(row.get("role") or ""), str(row.get("actor_name") or "")))
        if not sees_all:
            own = names_match(str(row.get("actor_name") or ""), viewer_name)
            settled = rej is not None and rej.status in FINAL
            if not own:
                value, rej = None, None
            elif not settled:
                # Отказ — по паре, а не по файлу: несозревший не видно ни под 👎, ни под
                # соседней пробой с 👍 — иначе промах пальцем диктор увидит сразу.
                rej = None
                if value == -1:
                    value = None
            elif value != -1:
                # итог отказа — под той пробой, что получила 👎, а не под соседней с 👍
                rej = None
        row["author_reaction"] = value
        row["rejection"] = rejection_view(rej)
    return rows


def forget_audition(db, audio) -> None:
    """Проба уходит насовсем: её реакции — вместе с ней, а отказ пары пересматривается.
    Не коммитит — удаление коммитит всё одним коммитом."""
    db.query(AuditionReaction).filter(AuditionReaction.audio_file_id == audio.id).delete()
    db.flush()
    book = _find_book_for_audio(db, audio)
    if book is not None:
        sync_rejection(
            db, book_id=book.id, book_code=str(audio.book_code or ""), role=str(audio.role or ""),
            actor_name=str(audio.actor_name or ""), now=utcnow_naive(), fresh_down=False,
        )
