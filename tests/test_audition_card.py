"""Карточка пробы в боте (пакет C1, 05.10)."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, AuditionScore, Character, ScriptBook, TelegramAuthAccount, User, UserRole
from app.services import audition_card
from app.services.audio_uploads import derive_book_code

KP = "Карта Приозерья"


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(audition_card.settings, "owner_telegram_id", "11")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title=KP, display_title=KP, source_filename="x", source_format="docx",
                               total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true",
                               status="processing"))
        session.add(Character(id="c1", book_id="b1", name="Дракон", actor_name="Цертан Царели?", appears_in="1"))
        session.add(User(id="u80", login="tg_80", password_hash="x", display_name="Варени Вискон", is_active="true"))
        session.add(UserRole(user_id="u80", role="author"))
        session.add(TelegramAuthAccount(telegram_user_id="80", role="author", access_scope="full",
                                        display_name="Варени Вискон", is_active="true", user_id="u80"))
        session.add(AudioFile(id="a1", book_code=derive_book_code(KP), original_filename="voice.ogg",
                              stored_key="k/a1", mime_type="audio/ogg", size_bytes=2048, chapter="", role="дракон",
                              actor_name="Цертан Царели", kind="audition", codec="opus"))
        session.add(AuditionScore(book_id="b1", role="дракон", actor_name="Цертан Царели", lane="studio", score=7,
                                  voter_uid="u11", voter_name="Фернос Фарита", reason=""))
        session.commit()
        yield session


def test_caption_names_role_book_actor_voice_and_scores(db):
    text = audition_card.caption(db, db.get(AudioFile, "a1"))
    assert text.startswith("🎙 Проба «Дракон» («Карта Приозерья») — Цертан Царели · голосовое")
    assert "Автор: —   Студия: 7" in text


def test_keyboard_has_ten_scores_and_approve():
    data = [b["callback_data"] for row in audition_card.keyboard("a1")["inline_keyboard"] for b in row]
    assert data == [f"ac:s{n}:a1" for n in range(1, 11)] + ["ac:a:a1"]
    assert "ac:a:a1" not in [b["callback_data"] for row in audition_card.keyboard("a1", approve=False)["inline_keyboard"]
                             for b in row]


def test_card_goes_to_owner_and_author(db, tmp_path, monkeypatch):
    monkeypatch.setattr(audition_card, "resolve_path", lambda key, location="local": str(tmp_path / "a.ogg"))
    sent = []
    n = audition_card.send_card(db, "a1", send_audio=lambda chat, **kw: sent.append((chat, kw)) or True,
                                send_text=lambda chat, text: pytest.fail("текст вместо аудио"))
    assert n == 2 and sorted(chat for chat, _ in sent) == ["11", "80"]
    assert all(kw["voice"] and kw["path"].endswith("a.ogg") and kw["reply_markup"] for _, kw in sent)


def test_a_bot_upload_is_resent_by_file_id(db):
    sent = []
    audition_card.send_card(db, "a1", file_id="TGFILE", send_audio=lambda chat, **kw: sent.append(kw) or True,
                            send_text=lambda chat, text: True)
    assert all(kw["file_id"] == "TGFILE" and not kw["path"] for kw in sent)


def test_a_big_file_goes_as_a_link(db):
    db.get(AudioFile, "a1").size_bytes = 60 * 1024 * 1024
    db.commit()
    texts = []
    audition_card.send_card(db, "a1", send_audio=lambda chat, **kw: pytest.fail("аудио больше 50 МБ"),
                            send_text=lambda chat, text: texts.append(text) or True)
    assert len(texts) == 2 and "/app/auditions" in texts[0] and "Дракон" in texts[0]


def test_no_owner_still_reaches_the_author(db, monkeypatch):
    monkeypatch.setattr(audition_card.settings, "owner_telegram_id", "")
    assert audition_card.recipients(db) == [("80", "author")]


from app.models import AuditLog, AuditionRejection, RoleDeadline  # noqa: E402
from app.services import onboarding  # noqa: E402
from app.services.bot_dialog import handle_update  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, 0)


@pytest.fixture()
def staff(db):
    db.add(User(id="u11", login="admin", password_hash="x", display_name="Фернос Фарита", is_active="true"))
    db.add(UserRole(user_id="u11", role="admin"))
    db.add(TelegramAuthAccount(telegram_user_id="11", role="admin", access_scope="full", display_name="Фернос Фарита",
                               is_active="true", user_id="u11"))
    db.add(User(id="u41", login="tg_41", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
    db.add(UserRole(user_id="u41", role="dictor"))
    db.add(TelegramAuthAccount(telegram_user_id="41", role="dictor", access_scope="full", display_name="Ветрова Ольга",
                               is_active="true", user_id="u41"))
    db.commit()
    onboarding._cache.clear()
    return db


def _press(data, tid, message_id=5):
    return {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": message_id,
                                                           "chat": {"id": int(tid), "type": "private"}}}}


def _texts(out, chat):
    return [o.payload.get("text", "") for o in out if o.method == "sendMessage" and str(o.payload["chat_id"]) == chat]


def _score(db, lane):
    row = db.query(AuditionScore).filter_by(role="дракон", actor_name="Цертан Царели", lane=lane).one_or_none()
    return row.score if row else None


def test_the_author_scores_the_author_lane_and_is_asked_why(staff):
    out = handle_update(staff, _press("ac:s4:a1", "80"), now=NOW)
    assert _score(staff, "author") == 4 and _score(staff, "studio") == 7
    edit = next(o for o in out if o.method == "editMessageCaption")
    assert "Автор: 4   Студия: 7" in edit.payload["caption"] and edit.payload["message_id"] == 5
    why = next(o for o in out if o.method == "sendMessage")
    assert "Чем не этот герой" in why.payload["text"]
    buttons = [b for row in why.payload["reply_markup"]["inline_keyboard"] for b in row]
    assert [b["callback_data"] for b in buttons] == ["ac:rvoice:a1", "ac:rmanner:a1", "ac:rall:a1", "ac:c:a1"]
    assert [b["text"] for b in buttons][0] == "Тембр не этого героя"
    out = handle_update(staff, _press("ac:rmanner:a1", "80"), now=NOW)
    assert staff.query(AuditionScore).filter_by(lane="author").one().reason == "manner"
    # владелец узнаёт о реакции сразу — отдельным сообщением
    assert any("Подача не этого героя" in t and "4/10" in t for t in _texts(out, "11"))


def test_a_six_also_asks_why(staff):
    """С 11.10 — любая оценка ниже 7: шестёрка ведёт к отказу так же, как пятёрка."""
    out = handle_update(staff, _press("ac:s6:a1", "80"), now=NOW)
    assert any("Чем не этот герой" in t for t in _texts(out, "80"))


def test_a_high_score_offers_only_own_words(staff):
    out = handle_update(staff, _press("ac:s8:a1", "80"), now=NOW)
    ask = next(o for o in out if o.method == "sendMessage")
    assert [b["callback_data"] for row in ask.payload["reply_markup"]["inline_keyboard"] for b in row] == ["ac:c:a1"]


def _say(text, tid):
    return {"update_id": 3, "message": {"message_id": 9, "from": {"id": int(tid)}, "text": text,
                                        "chat": {"id": int(tid), "type": "private"}}}


def test_own_words_are_saved_for_the_studio_and_sent_to_the_owner(staff):
    handle_update(staff, _press("ac:s4:a1", "80"), now=NOW)
    ask = handle_update(staff, _press("ac:c:a1", "80"), now=NOW)
    assert "Увидит только студия" in _texts(ask, "80")[0]
    out = handle_update(staff, _say("Голос должен быть звонким и девчачьим", "80"), now=NOW)
    row = staff.query(AuditionScore).filter_by(lane="author").one()
    assert row.comment == "Голос должен быть звонким и девчачьим" and row.score == 4
    assert "Записал" in _texts(out, "80")[0]
    assert any("«Голос должен быть звонким и девчачьим»" in t for t in _texts(out, "11"))
    # ожидание снято: следующее сообщение — обычный разговор, не комментарий
    handle_update(staff, _say("спасибо", "80"), now=NOW)
    assert staff.query(AuditionScore).filter_by(lane="author").one().comment == "Голос должен быть звонким и девчачьим"


def test_own_words_wait_expires(staff):
    from datetime import timedelta

    handle_update(staff, _press("ac:s4:a1", "80"), now=NOW)
    handle_update(staff, _press("ac:c:a1", "80"), now=NOW)
    handle_update(staff, _say("поздно", "80"), now=NOW + timedelta(hours=2))
    assert staff.query(AuditionScore).filter_by(lane="author").one().comment == ""


def test_own_words_need_a_score_first(staff):
    out = handle_update(staff, _press("ac:c:a1", "80"), now=NOW)
    assert "Сначала поставьте оценку" in _texts(out, "80")[0]


def test_the_owner_scores_the_studio_lane_and_both_low_schedule_a_rejection(staff):
    handle_update(staff, _press("ac:s3:a1", "80"), now=NOW)
    handle_update(staff, _press("ac:s5:a1", "11"), now=NOW)
    assert _score(staff, "studio") == 5
    assert staff.query(AuditionRejection).filter_by(actor_name="Цертан Царели", status="pending").count() == 1


def test_a_dictor_cannot_press(staff):
    assert [o.method for o in handle_update(staff, _press("ac:s9:a1", "41"), now=NOW)] == ["answerCallbackQuery"]
    assert _score(staff, "author") is None


def test_approve_goes_through_the_shared_chain(staff, monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.cast_assign.notify_role_approved", lambda db, **kw: calls.append(kw) or {})
    out = handle_update(staff, _press("ac:a:a1", "11"), now=NOW)
    assert staff.get(Character, "c1").actor_name == "Цертан Царели"
    assert "Утверждено: Цертан Царели — «Дракон»" in _texts(out, "11")[0] and calls
    edit = next(o for o in out if o.method == "editMessageCaption")   # подпись и кнопки — одним вызовом
    assert "ac:a:a1" not in [b["callback_data"] for row in edit.payload["reply_markup"]["inline_keyboard"] for b in row]


def test_approving_a_busy_actor_asks_first(staff, monkeypatch):
    monkeypatch.setattr("app.services.cast_assign.notify_role_approved", lambda db, **kw: {})
    staff.add(Character(id="c2", book_id="b1", name="Убув", actor_name="Цертан Царели", appears_in="1"))
    staff.commit()
    out = handle_update(staff, _press("ac:a:a1", "11"), now=NOW)
    ask = next(o for o in out if o.method == "sendMessage")
    assert "На «Убув» уже стоит Цертан Царели" in ask.payload["text"]
    assert staff.get(Character, "c1").actor_name == "Цертан Царели?"
    handle_update(staff, _press("ac:A:a1", "11"), now=NOW)
    assert staff.get(Character, "c1").actor_name == "Цертан Царели"


def test_the_same_actor_already_on_the_role(staff):
    staff.get(Character, "c1").actor_name = "Цертан Царели"
    staff.commit()
    out = handle_update(staff, _press("ac:a:a1", "11"), now=NOW)
    assert "Роль уже утверждена: Цертан Царели" in _texts(out, "11")[0]


def test_stale_cards(staff):
    staff.get(Character, "c1").name = "Не Дракон"
    staff.commit()
    out = handle_update(staff, _press("ac:a:a1", "11"), now=NOW)
    assert "Роль не найдена в касте" in _texts(out, "11")[0]
    out = handle_update(staff, _press("ac:s8:a1", "11"), now=NOW)
    assert _score(staff, "studio") == 8                      # оценка — по паре, каст не нужен
    staff.delete(staff.get(AudioFile, "a1"))
    staff.commit()
    out = handle_update(staff, _press("ac:s8:a1", "11"), now=NOW)
    assert "Проба удалена" in _texts(out, "11")[0]


def test_a_file_on_the_nas_is_read_from_there_or_linked_when_the_nas_is_down(db, monkeypatch):
    """Прогон на копии прода: старые записи лежат на NAS (`location = nas`) — путь берётся с
    тем же `location`, что у плеера сайта, а при лежащем NAS — ссылка, а не падение задачи."""
    db.get(AudioFile, "a1").location = "nas"
    db.commit()
    asked = []
    monkeypatch.setattr(audition_card, "resolve_path", lambda key, location="local": asked.append(location) or "/x")
    monkeypatch.setattr(audition_card, "storage_unavailable_for", lambda location: False)
    audition_card.send_card(db, "a1", send_audio=lambda chat, **kw: True, send_text=lambda chat, text: True)
    assert asked and set(asked) == {"nas"}
    monkeypatch.setattr(audition_card, "storage_unavailable_for", lambda location: location == "nas")
    texts = []
    audition_card.send_card(db, "a1", send_audio=lambda chat, **kw: pytest.fail("NAS лежит"),
                            send_text=lambda chat, text: texts.append(text) or True)
    assert texts and "/app/auditions" in texts[0]


def test_a_wav_is_sent_as_one_mp3_listening_copy(db, tmp_path, monkeypatch):
    """Прогон на копии прода: пробы — WAV до 37 МБ, а плеер Telegram ждёт MP3/M4A. Копия для
    прослушивания — одна на всех адресатов и удаляется после отправки; оригинал не трогаем."""
    audio = db.get(AudioFile, "a1")
    audio.codec, audio.original_filename, audio.size_bytes = "pcm_s24le", "proba.wav", 60 * 1024 * 1024
    db.commit()
    monkeypatch.setattr(audition_card, "resolve_path", lambda key, location="local": str(tmp_path / "proba.wav"))
    made = []

    def fake_mp3(source):
        target = tmp_path / "listen.mp3"
        target.write_bytes(b"mp3")
        made.append(source)
        return str(target)

    sent = []
    audition_card.send_card(db, "a1", send_audio=lambda chat, **kw: sent.append(kw) or True,
                            send_text=lambda chat, text: pytest.fail("большой WAV после сжатия — аудио"),
                            transcode=fake_mp3)
    assert made == [str(tmp_path / "proba.wav")] and len(sent) == 2
    assert all(kw["path"].endswith("listen.mp3") and not kw["voice"] for kw in sent)
    assert not (tmp_path / "listen.mp3").exists()



def test_the_worker_asks_the_nas_itself(monkeypatch):
    """Ревью: флаг NAS живёт в памяти веб-процесса, в воркере он всегда «не знаем» — и
    каждая запись на NAS уходила ссылкой. Воркер спрашивает NAS разовой пробой."""
    from app.services import nas_health
    monkeypatch.setattr(nas_health, "reachable_now", lambda timeout=None: True)
    assert audition_card.storage_unavailable_for("nas") is False
    monkeypatch.setattr(nas_health, "reachable_now", lambda timeout=None: False)
    assert audition_card.storage_unavailable_for("nas") is True
    assert audition_card.storage_unavailable_for("local") is False


def test_a_failed_conversion_or_upload_falls_back_to_a_link(db, tmp_path, monkeypatch):
    """Ревью: карточка, упавшая в воркере, пропадала молча — о пробе не узнавал никто.
    Не сжалась, не прочиталась, Telegram отказал одному адресату — ему текст со ссылкой."""
    audio = db.get(AudioFile, "a1")
    audio.codec, audio.original_filename = "pcm_s24le", "proba.wav"
    db.commit()
    monkeypatch.setattr(audition_card, "resolve_path", lambda key, location="local": str(tmp_path / "proba.wav"))

    def broken(source):
        raise RuntimeError("ffmpeg")

    texts = []
    n = audition_card.send_card(db, "a1", send_audio=lambda chat, **kw: pytest.fail("сжатие упало"),
                                send_text=lambda chat, text: texts.append(chat) or True, transcode=broken)
    assert n == 2 and sorted(texts) == ["11", "80"]
    audio.codec = "mp3"
    db.commit()
    texts.clear()
    n = audition_card.send_card(db, "a1", send_audio=lambda chat, **kw: chat == "11",
                                send_text=lambda chat, text: texts.append((chat, text)) or True)
    assert n == 2 and texts[0][0] == "80" and "/app/auditions" in texts[0][1]


def test_the_mp3_copy_is_removed_when_ffmpeg_fails(monkeypatch, tmp_path):
    import subprocess
    made = []

    def boom(*args, **kwargs):
        made.append(args[0][-1])
        raise subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(subprocess.CalledProcessError):
        audition_card.to_mp3(str(tmp_path / "x.wav"))
    import os
    assert made and not os.path.exists(made[0])


def test_the_track_is_named_after_the_role_and_actor(db, tmp_path, monkeypatch):
    """Ревью: файл уходил под временным именем, и плеер Telegram показывал «tmpab12.mp3»."""
    monkeypatch.setattr(audition_card, "resolve_path", lambda key, location="local": str(tmp_path / "a.ogg"))
    sent = []
    audition_card.send_card(db, "a1", send_audio=lambda chat, **kw: sent.append(kw) or True,
                            send_text=lambda chat, text: True)
    assert sent[0]["filename"] == "Дракон — Цертан Царели.ogg"


def test_a_score_on_an_approved_card_does_not_bring_back_approve(staff, monkeypatch):
    staff.get(Character, "c1").actor_name = "Цертан Царели"
    staff.commit()
    out = handle_update(staff, _press("ac:s9:a1", "11"), now=NOW)
    edit = next(o for o in out if o.method == "editMessageCaption")
    assert "ac:a:a1" not in [b["callback_data"] for row in edit.payload["reply_markup"]["inline_keyboard"] for b in row]
