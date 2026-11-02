"""Файл, присланный боту, — потоком (21.10, свой узел Telegram Bot API).

Свой узел отдаёт файлы до 2 ГБ и не по ссылке, а путём на своём диске. Держать гигабайт в
памяти воркера нельзя — файл открывается на время сохранения. Убирает скачанное сама
служба узла (таймер в infra/telegram-bot-api), воркер его только читает.
"""
import pytest

from app.config import settings
from app.services import telegram


class _Answer:
    def __init__(self, payload=None, chunks=()):
        self._payload, self._chunks = payload, chunks
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=None):
        return iter(self._chunks)


@pytest.fixture()
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "telegram_api_base", "http://127.0.0.1:8081")
    monkeypatch.setattr(settings, "telegram_bot_token", "T")
    monkeypatch.setattr(telegram, "LOCAL_FILES_ROOT", str(tmp_path))
    return tmp_path


def _get_file_answers(monkeypatch, file_path):
    monkeypatch.setattr(telegram.requests, "get",
                        lambda url, **kw: _Answer({"ok": True, "result": {"file_path": file_path}}))


def test_local_file_is_read_and_left_for_the_node_cleanup(local, monkeypatch):
    """Узел создаёт каталоги 0750 и файлы 0640 (tdlib FileLoaderUtils) — удалить файл воркер
    не может, сколько бы ни дали прав группе. Убирает сама служба узла таймером
    (infra/telegram-bot-api), воркер только читает."""
    path = local / "T" / "documents" / "a.wav"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"RIFF")
    _get_file_answers(monkeypatch, str(path))

    with telegram.fetched_file("F") as fh:
        assert fh.read() == b"RIFF"

    assert path.exists()


def test_local_getfile_waits_for_the_whole_download(local, monkeypatch):
    """Свой узел отвечает на getFile, только скачав файл целиком: 15 секунд оборвали бы
    как раз большие пробы, ради которых узел и ставили."""
    path = local / "a.wav"
    path.write_bytes(b"RIFF")
    timeouts = []

    def get(url, **kw):
        timeouts.append(kw.get("timeout"))
        return _Answer({"ok": True, "result": {"file_path": str(path)}})

    monkeypatch.setattr(telegram.requests, "get", get)

    with telegram.fetched_file("F"):
        pass

    read_timeout = timeouts[0][1] if isinstance(timeouts[0], tuple) else timeouts[0]
    assert read_timeout >= 1800


def test_local_file_stays_when_saving_failed(local, monkeypatch):
    path = local / "a.wav"
    path.write_bytes(b"RIFF")
    _get_file_answers(monkeypatch, str(path))

    with pytest.raises(RuntimeError):
        with telegram.fetched_file("F"):
            raise RuntimeError("disk full")

    assert path.exists()


def test_local_path_outside_root_is_refused(local, monkeypatch):
    _get_file_answers(monkeypatch, "/etc/passwd")

    with pytest.raises(ValueError):
        with telegram.fetched_file("F"):
            pytest.fail("чужой файл открываться не должен")


def test_local_path_escaping_root_through_dots_is_refused(local, monkeypatch):
    _get_file_answers(monkeypatch, str(local / ".." / "secret.txt"))

    with pytest.raises(ValueError):
        with telegram.fetched_file("F"):
            pytest.fail("выход из каталога узла открываться не должен")


def test_cloud_streams_to_a_temp_file(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "")
    monkeypatch.setattr(settings, "telegram_bot_token", "T")
    asked = []

    def get(url, **kw):
        asked.append(url)
        if url.endswith("/getFile"):
            return _Answer({"ok": True, "result": {"file_path": "voice/x.ogg"}})
        return _Answer(chunks=[b"Og", b"gS"])

    monkeypatch.setattr(telegram.requests, "get", get)

    with telegram.fetched_file("F") as fh:
        assert fh.read() == b"OggS"

    assert asked[1] == "https://api.telegram.org/file/botT/voice/x.ogg"


def test_limit_by_mode(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "")
    assert telegram.max_download_bytes() == 20 * 1024 * 1024
    monkeypatch.setattr(settings, "telegram_api_base", "http://127.0.0.1:8081")
    assert telegram.max_download_bytes() == 2000 * 1024 * 1024
