"""Разбор сообщения, которое владелец переслал боту (проект Б)."""
from app.services.bot_demos import parse_forward

USER = {"type": "user", "date": 1, "sender_user": {"id": 555, "is_bot": False, "first_name": "Ольга",
                                                    "last_name": "Ветрова", "username": "olga"}}


def _msg(**extra):
    message = {"message_id": 70, "from": {"id": 900000100}, "chat": {"id": 900000100, "type": "private"},
               "forward_origin": USER}
    message.update(extra)
    return message


def _file(name="demo.mp3", mime="audio/mpeg"):
    return {"file_id": "F" * 80, "file_unique_id": "U", "file_name": name, "mime_type": mime, "file_size": 3_000}


def test_user_origin():
    got = parse_forward(_msg(audio=_file()))
    assert (got.sender_key, got.telegram_user_id, got.tg_name, got.tg_username, got.kind) == (
        "tg:555", "555", "Ольга Ветрова", "olga", "user")
    assert got.message_id == 70
    assert got.files == [{"file_id": "F" * 80, "file_name": "demo.mp3", "mime_type": "audio/mpeg",
                          "file_size": 3_000}]


def test_hidden_origin():
    got = parse_forward(_msg(forward_origin={"type": "hidden_user", "date": 1, "sender_user_name": "Ольга В."},
                             voice={"file_id": "V", "mime_type": "audio/ogg", "file_size": 10}))
    assert (got.sender_key, got.telegram_user_id, got.tg_name, got.kind) == ("hidden:Ольга В.", "", "Ольга В.",
                                                                             "hidden")
    assert got.files[0]["file_name"] == "voice.ogg"


def test_channel_origin_is_chat():
    for origin in ({"type": "channel", "date": 1, "chat": {"id": -100, "title": "Канал"}, "message_id": 3},
                   {"type": "chat", "date": 1, "sender_chat": {"id": -5, "title": "Группа"}}):
        assert parse_forward(_msg(forward_origin=origin, audio=_file())).kind == "chat"


def test_media_kinds():
    taken = [
        {"voice": {"file_id": "V", "mime_type": "audio/ogg", "file_size": 1}},
        {"video": {"file_id": "W", "mime_type": "video/mp4", "file_size": 1}},
        {"video_note": {"file_id": "N", "file_size": 1}},
        {"document": _file("reel.mp4", "video/mp4")},
        {"document": _file("demo.WAV", "application/octet-stream")},
    ]
    for extra in taken:
        assert len(parse_forward(_msg(**extra)).files) == 1, extra
    for extra in ({"document": _file("cv.pdf", "application/pdf")},
                  {"photo": [{"file_id": "P", "file_size": 1}]}):
        assert parse_forward(_msg(**extra)).files == [], extra


def test_links_and_rest_text():
    caption = "Привет, баритон https://disk.yandex.ru/d/abc и тут"
    start = caption.index("https")
    got = parse_forward(_msg(audio=_file(), caption=caption, caption_entities=[
        {"type": "url", "offset": start, "length": len("https://disk.yandex.ru/d/abc")},
        {"type": "text_link", "offset": caption.index("тут"), "length": 3,
         "url": "https://drive.google.com/file/d/X/view"},
    ]))
    assert got.links == ["https://disk.yandex.ru/d/abc", "https://drive.google.com/file/d/X/view"]
    assert got.text == "Привет, баритон и тут"


def test_entity_offsets_are_utf16():
    # эмодзи — две единицы UTF-16: срез по символам Python съехал бы на один
    text = "🎙 демо https://disk.yandex.ru/d/q"
    got = parse_forward(_msg(text=text, entities=[{"type": "url", "offset": 8, "length": 26}]))
    assert got.links == ["https://disk.yandex.ru/d/q"]
    assert got.text == "🎙 демо"


def test_not_forwarded_is_none():
    message = _msg(audio=_file())
    del message["forward_origin"]
    assert parse_forward(message) is None
