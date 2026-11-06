"""Оценка проб 1–10 (с 03.10 вместо 👍/👎).

Оценивают актёра на роль, а не файл: решение — про голос для роли, и у актёра бывает
по четыре пробы. Дорожек две — автор и студия — и они не сводятся в одно число:
расхождение «Пяволов 8 · Сыкыдов 5» и есть то, что владелец хочет видеть. Оценка — только
сигнал: письма она не шлёт, отказ заводит утверждение роли (`audition_rejections`).
"""
from __future__ import annotations

from app.config import settings
from app.models import AudioFile, Author, AuditionScore, ScriptBook, TelegramAuthAccount, User
from app.services.audio_uploads import AUDITION, derive_book_code
from app.time_utils import utcnow_naive

LANES = ("author", "studio")
#: Что не понравилось автору (решение владельца 04.10): спрашивается только у автора и
#: одной фразой уходит диктору в вежливом отказе. С 11.10 — при любой оценке ниже 7, то есть
#: там же, где начинается отказ: при шестёрке отказ уходил, а причину не спрашивали.
REASONS = ("voice", "manner", "all")
REASON_MAX_SCORE = 6
#: Подписи кнопок — про героя, а не про голос (11.10). «Голос» Пяволов читал как «голос
#: плохой» и не отвечал вовсе: «голос отличный, просто совершенно не подходящий персонажу».
#: Смысл ключей прежний — письмо диктору («нужен другой тембр») не меняется.
REASON_LABELS = {
    "voice": "Тембр не этого героя",
    "manner": "Подача не этого героя",
    "all": "И тембр, и подача",
}
#: Комментарий — живой ответ, а не сочинение; длиннее телеграм-сообщения не нужен.
COMMENT_MAX_CHARS = 1000
_FALLBACK_LABELS = {"author": "Автор", "studio": "Студия"}


class ScoreError(ValueError):
    """`code` — машинный код отказа для ручки, не фраза для человека."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _valid_score(score) -> bool:
    # bool — подкласс int: `True` иначе записался бы как оценка 1
    return isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 10


def pair_has_auditions(db, *, book_code: str, role: str, actor_name: str, exclude_id: str = "") -> bool:
    """Есть ли у пары хоть одна проба. `exclude_id` — проба, которую как раз удаляют."""
    query = db.query(AudioFile.id).filter(
        AudioFile.kind == AUDITION,
        AudioFile.book_code == book_code,
        AudioFile.role == role,
        AudioFile.actor_name == actor_name,
    )
    if exclude_id:
        query = query.filter(AudioFile.id != exclude_id)
    return query.first() is not None


def _view(row: AuditionScore | None) -> dict | None:
    if row is None:
        return None
    return {"score": int(row.score), "name": str(row.voter_name or ""), "reason": str(row.reason or ""),
            "comment": str(row.comment or "")}


def scores_by_pair(db, book_id: str) -> dict[tuple[str, str], dict[str, dict | None]]:
    """Оценки всех пар книги разом — лента и лист читают их одним запросом."""
    result: dict[tuple[str, str], dict[str, dict | None]] = {}
    for row in db.query(AuditionScore).filter(AuditionScore.book_id == book_id):
        pair = result.setdefault((str(row.role), str(row.actor_name)), {lane: None for lane in LANES})
        pair[str(row.lane)] = _view(row)
    return result


def _pair_scores(db, *, book_id: str, role: str, actor_name: str) -> dict[str, dict | None]:
    return scores_by_pair(db, book_id).get((role, actor_name), {lane: None for lane in LANES})


def set_score(db, *, book_id: str, role: str, actor_name: str, lane: str, score, voter_uid: str, voter_name: str,
              reason: str | None = None) -> dict:
    """Поставить оценку дорожке пары или снять её (`score=None`). Коммитит сам.

    `reason` — что не понравилось автору; допустим только у автора при оценке ниже 7. Не
    передан — причина остаётся прежней, пока оценка низкая, и стирается, когда её
    подняли до 7: «подача не этого героя» под семёркой была бы неправдой. Комментарий
    при смене оценки не трогается: «чуть моложе бы» верно и при шестёрке, и при восьмёрке."""
    if lane not in LANES:
        raise ScoreError("bad_lane")
    if score is not None and not _valid_score(score):
        raise ScoreError("bad_score")
    if reason and (reason not in REASONS or lane != "author" or score is None or score > REASON_MAX_SCORE):
        raise ScoreError("bad_reason")
    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        raise ScoreError("not_found")
    if not pair_has_auditions(db, book_code=derive_book_code(str(book.title or "")), role=role, actor_name=actor_name):
        raise ScoreError("not_found")

    row = (
        db.query(AuditionScore)
        .filter_by(book_id=book.id, role=role, actor_name=actor_name, lane=lane)
        .one_or_none()
    )
    if score is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(AuditionScore(book_id=book.id, role=role, actor_name=actor_name, lane=lane, score=score,
                             voter_uid=str(voter_uid or ""), voter_name=str(voter_name or ""),
                             reason=str(reason or "")))
    else:
        row.score = score
        row.voter_uid = str(voter_uid or "")
        row.voter_name = str(voter_name or "")
        if reason:
            row.reason = reason
        elif score > REASON_MAX_SCORE:
            row.reason = ""
        row.updated_at = utcnow_naive()
    db.commit()
    return _pair_scores(db, book_id=book.id, role=role, actor_name=actor_name)


def set_comment(db, *, book_id: str, role: str, actor_name: str, lane: str, comment: str,
                voter_uid: str, voter_name: str) -> dict:
    """Комментарий своими словами к уже поставленной оценке дорожки. Коммитит сам.

    Без оценки комментарий не принимается: он живёт в строке оценки, а строки без оценки не
    бывает — снятая оценка удаляет строку. Пустой текст стирает комментарий."""
    if lane not in LANES:
        raise ScoreError("bad_lane")
    text = str(comment or "").strip()
    if len(text) > COMMENT_MAX_CHARS:
        raise ScoreError("comment_too_long")
    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        raise ScoreError("not_found")
    row = (
        db.query(AuditionScore)
        .filter_by(book_id=book.id, role=role, actor_name=actor_name, lane=lane)
        .one_or_none()
    )
    if row is None:
        raise ScoreError("no_score")
    row.comment = text
    row.voter_uid = str(voter_uid or "")
    row.voter_name = str(voter_name or "")
    row.updated_at = utcnow_naive()
    db.commit()
    return _pair_scores(db, book_id=book.id, role=role, actor_name=actor_name)


def reaction_notice(db, *, book_id: str, role: str, actor_name: str, lane: str = "author", role_title: str = "") -> str:
    """Что владелец получает в Telegram, когда автор назвал причину или написал комментарий
    (решение владельца 11.10: «мне очень важно видеть его реакции»). Пусто — нечего сказать."""
    book = db.get(ScriptBook, str(book_id or "").strip())
    entry = _pair_scores(db, book_id=str(book_id or ""), role=role, actor_name=actor_name).get(lane) if book else None
    if not entry or not (entry["reason"] or entry["comment"]):
        return ""
    who = lane_labels(db, book).get(lane, "Автор")  # из записи автора: в учётке «Фамилия Имя» вперемешку
    title = str(getattr(book, "display_title", "") or book.title or "").strip()
    lines = [f"💬 {who} о пробе «{role_title or role}» ({title}) — {actor_name}, {entry['score']}/10"]
    if entry["reason"] in REASON_LABELS:
        lines.append(REASON_LABELS[entry["reason"]])
    if entry["comment"]:
        lines.append(f"«{entry['comment']}»")
    return "\n".join(lines)


def send_reaction_to_owner(book_id: str, role: str, actor_name: str) -> None:
    """Отправка `reaction_notice` владельцу — из фоновой задачи ответа, не из обработчика:
    uvicorn один, и ожидание Telegram заморозило бы сайт. Сбой отправки — только в журнал:
    комментарий уже сохранён и виден на сайте."""
    import logging

    from app.db import SessionLocal
    from app.services.telegram import send_telegram_message

    owner = str(settings.owner_telegram_id or "").strip()
    if not owner:
        return
    try:
        with SessionLocal() as db:
            text = reaction_notice(db, book_id=book_id, role=role, actor_name=actor_name,
                                   role_title=_role_title(db, book_id, role))
            if text:
                send_telegram_message(db, text, chat_ids=[owner])
    except Exception:
        logging.getLogger(__name__).exception("Не удалось отправить владельцу реакцию автора на пробу")


def _role_title(db, book_id: str, role: str) -> str:
    """Имя роли, как в касте: в пробе роль записана как пришла в имени файла («ао»)."""
    from app.models import Character
    from app.services.audition_rejections import character_for_role

    ch = character_for_role(db.query(Character).filter(Character.book_id == book_id).all(), role)
    return str(ch.name) if ch is not None else role


def _surname(full_name: str) -> str:
    """«Вискон Варени» → «Варени»: подпись дорожки короткая, иначе на телефоне
    имя спорит со шкалой за строку. Порядок «Имя Фамилия» — так записаны оба человека."""
    words = str(full_name or "").split()
    return words[-1] if words else ""


def lane_labels(db, book) -> dict[str, str]:
    """Подписи дорожек — не того, кто нажал, а сторон: шкала подписана, даже пока пуста."""
    labels = dict(_FALLBACK_LABELS)
    author_id = str(getattr(book, "author_id", "") or "")
    author = db.get(Author, author_id) if author_id else None
    if author is not None and _surname(author.name):
        labels["author"] = _surname(author.name)
    owner_tg = str(settings.owner_telegram_id or "").strip()
    if owner_tg:
        account = db.query(TelegramAuthAccount).filter_by(telegram_user_id=owner_tg).one_or_none()
        # Имя учётки важнее Telegram-имени: у владельца в Telegram «Псевдоним», а учётка
        # с 30.09 называется «Фернос Фарита» — подпись «Псевдоним» под шкалой никто не узнаёт.
        user = db.get(User, account.user_id) if account is not None and account.user_id else None
        name = (user.display_name if user is not None else "") or (account.display_name if account is not None else "")
        if _surname(name):
            labels["studio"] = _surname(name)
    return labels
