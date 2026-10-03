"""The Telegram line the owner reads when a dictor sends takes.

It said «Пользователь: tg_900000102». That is the synthetic login Telegram sign-in
invents, not a person: the owner got six of those in a row and could not tell who was
working. The session carries the display name in the same payload the login came from.

While the message is being rewritten it may as well say what arrived. «Файлов: 1» six
times over answers neither «кто» nor «что» — the chapter and the role are what turn a
notification into news.
"""
from app.services.recording import upload_notice


def _file(chapter: str, role: str, name: str = "take.wav") -> dict:
    return {"chapter": chapter, "role": role, "original_filename": name, "actor_name": ""}


def test_the_person_is_named_not_his_login():
    text = upload_notice("Сергей Иванович", [_file("12", "Гамук")])

    assert "Сергей Иванович" in text
    assert "tg_" not in text


def test_a_login_is_still_shown_when_there_is_no_name():
    """A service account or an old session: better a login than «unknown»."""
    text = upload_notice("", [_file("12", "Гамук")], login="tg_900000102")

    assert "tg_900000102" in text


def test_the_chapter_and_the_role_are_the_news():
    text = upload_notice("Сергей Иванович", [_file("12", "Гамук")])

    assert "глава 12" in text
    assert "Гамук" in text


def test_one_file_and_many_read_differently():
    one = upload_notice("Сергей Иванович", [_file("12", "Гамук")])
    many = upload_notice("Сергей Иванович", [_file("12", "Гамук"), _file("12", "Гамук")])

    assert "Файлов" not in one, "про один файл незачем писать «Файлов: 1»"
    assert "Файлов: 2" in many


def test_takes_from_several_places_are_listed_once_each():
    text = upload_notice("Сергей Иванович", [
        _file("12", "Гамук"), _file("12", "Гамук"), _file("13", "Дгарнин"),
    ])

    assert text.count("Гамук") == 1
    assert "глава 13 · Дгарнин" in text


def test_a_long_batch_names_a_few_places_and_counts_the_rest():
    files = [_file(str(n), f"Роль {n}") for n in range(6)]

    text = upload_notice("Сергей Иванович", files)

    assert "Файлов: 6" in text
    assert "и ещё" in text


def test_an_upload_with_nothing_in_it_still_says_who():
    text = upload_notice("Сергей Иванович", [])

    assert "Сергей Иванович" in text


def _audition(role: str, name: str = "проба.wav") -> dict:
    return {"chapter": "", "role": role, "original_filename": name, "actor_name": "", "kind": "audition"}


def test_an_audition_is_announced_as_one_not_as_takes():
    """Пробу владелец слушает и решает; дубль утверждённой роли — просто работа."""
    text = upload_notice("София Ершова", [_audition("Эней")])

    assert "Проб" in text
    assert "Дубли" not in text


def test_an_audition_names_the_role_and_no_chapter():
    text = upload_notice("София Ершова", [_audition("Эней")])

    assert "Эней" in text
    assert "глава" not in text
