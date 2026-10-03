"""`.env` дополняет окружение, но не спорит с ним.

28.09 NAS лежал, и сайт попробовали отвязать от него, выставив службе
`AUDIO_NAS_PATH=` (пусто — «зеркала нет»). Загрузчик `.env` счёл пустое значение
отсутствующим и вписал путь к мёртвому монтированию обратно — выключатель молча
не сработал.
"""
from app import config


def _load(monkeypatch, tmp_path, text):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    config._load_local_env_file()


def test_an_explicitly_empty_variable_stays_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("NONAME_TEST_SWITCH", "")
    _load(monkeypatch, tmp_path, "NONAME_TEST_SWITCH=/mnt/somewhere\n")
    assert config.os.environ["NONAME_TEST_SWITCH"] == ""


def test_a_missing_variable_is_filled_from_the_file(monkeypatch, tmp_path):
    monkeypatch.delenv("NONAME_TEST_SWITCH", raising=False)
    _load(monkeypatch, tmp_path, "NONAME_TEST_SWITCH=/mnt/somewhere\n")
    assert config.os.environ["NONAME_TEST_SWITCH"] == "/mnt/somewhere"
    monkeypatch.delenv("NONAME_TEST_SWITCH")


def test_a_set_variable_wins_over_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv("NONAME_TEST_SWITCH", "/from/systemd")
    _load(monkeypatch, tmp_path, "NONAME_TEST_SWITCH=/mnt/somewhere\n")
    assert config.os.environ["NONAME_TEST_SWITCH"] == "/from/systemd"
