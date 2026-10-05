import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("PRODUCTION", "false")
os.environ.setdefault("AUDIO_STORAGE_PATH", "/tmp/noname_test_audio")

# Hard-disable outbound Telegram for the entire suite. Some tests exercise the real
# failure -> notify_book_status_change -> send_telegram_message path; if a production
# .env is loaded into the environment (deploy host, CI secret), an unmocked call would
# fire a live message to the owner. Force the values BEFORE app.config is imported so
# the settings singleton is built inert, and blank the token as a second line of defence.
os.environ["TELEGRAM_NOTIFY_ENABLED"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""

import pytest

from app.config import settings
from app.services import nas_health

# Belt and suspenders: the singleton may have been constructed earlier via another import
# chain, so pin the flag off here too. The autouse fixture re-pins it before every test in
# case a test toggles it.
settings.telegram_notify_enabled = False


@pytest.fixture(autouse=True)
def _no_outbound_telegram():
    settings.telegram_notify_enabled = False
    yield


@pytest.fixture(autouse=True)
def _no_outbound_elevenlabs(request, monkeypatch):
    """Ни один тест не ходит в ElevenLabs: транспорт по умолчанию (`_urllib_transport`, сеть
    настоящая, каждый вызов — оплаченный трек) подменён падением теста. Тесты, что проверяют
    сам транспорт с подменённым `urlopen`, просят его явно: `@pytest.mark.elevenlabs_transport`.

    `pytest.fail` — не `Exception`, поэтому его не проглотит ни клиент, ни движок: тест,
    забывший подменить `compose`, упадёт громко, а не «трек не вышел».
    """
    if request.node.get_closest_marker("elevenlabs_transport") is not None:
        yield
        return
    from app.services import elevenlabs_client

    def blocked(url, headers, body, timeout):
        pytest.fail("тест полез в настоящий ElevenLabs: подмените transport/compose")

    monkeypatch.setattr(elevenlabs_client, "_urllib_transport", blocked)
    yield


@pytest.fixture(autouse=True)
def _no_outbound_bot_reach(monkeypatch):
    """Ни один тест не ходит в Telegram через `check_bot_reach`/`requests.get`: он читает
    чужой аккаунт (`getChat`), а не только шлёт — тому же самому сторожу, что и
    `_no_outbound_telegram`, эта дорога не видна. Заглушка стоит на `requests.get` самого
    модуля `requests`, то есть в тестах блокирует ЛЮБОЙ `requests.get`, не только этот.
    Тесты, что проверяют сам запрос, просят его явно через свой
    `monkeypatch.setattr(...requests.get, ...)`, который в пределах теста её переопределяет.

    Кэш достижимости и фоновые перепроверки `onboarding` — состояние процесса: чистятся до
    и после каждого теста, а перепроверка выполняется сразу, а не в потоке, — иначе падение
    заглушки случилось бы в чужом потоке и прошло бы незамеченным.
    """
    from app.services import bot_reach, onboarding

    def blocked(*args, **kwargs):
        pytest.fail("тест полез в сеть: requests.get в тестах заблокирован целиком — подмените requests.get или check")

    monkeypatch.setattr(bot_reach.requests, "get", blocked)
    monkeypatch.setattr(onboarding, "_spawn", lambda job: job())
    onboarding.reset_state()
    yield
    onboarding.reset_state()


@pytest.fixture(autouse=True)
def _clean_nas_state():
    """Флаг NAS — состояние модуля, общее на весь процесс, а тесты его расставляют.

    Раньше каждый тест сбрасывал его сам в конце тела — то есть не сбрасывал вовсе,
    если падал раньше: `online=False` утекал в остальную сессию, и следующие тесты
    видели «NAS молчит» без всякой на то причины. Сброс до и после — не забота теста.
    """
    nas_health.reset_state()
    yield
    nas_health.reset_state()


@pytest.fixture(autouse=True)
def _fresh_key_cache():
    """Кэш ключей живёт 30 секунд — в проде это нарочно, а в тестах ключ из соседнего теста
    подменил бы «ключа нет». Чистим до и после каждого теста."""
    from app.services import provider_keys

    provider_keys.clear_cache()
    yield
    provider_keys.clear_cache()


@pytest.fixture(autouse=True)
def _fresh_step_cache():
    from app.services import step_models

    step_models.clear_cache()
    yield
    step_models.clear_cache()
