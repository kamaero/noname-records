import asyncio
import json
from types import SimpleNamespace

from app.config import settings
from app.main import RequestBodyLimitMiddleware


def _request(path: str, length: int):
    return SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path=path),
        headers={"content-length": str(length)},
    )


def _middleware():
    return RequestBodyLimitMiddleware(app=None, max_body_bytes=100 * 1024 * 1024)


def _dispatch(middleware, request):
    """Прогоняет dispatch() без реального ASGI-стека — над отказом вызывать
    call_next не нужно, и он не должен быть вызван."""

    async def _call_next(_request):
        raise AssertionError("call_next не должен вызываться при превышении предела")

    return asyncio.run(middleware.dispatch(request, _call_next))


def test_a_normal_take_passes():
    """Час моно 48 кГц/24 бит — около 520 МБ. Потолок не должен мешать работе."""
    assert _middleware().over_limit(_request("/api/recording/batch", 900 * 1024 * 1024)) is False


def test_an_absurd_upload_is_refused():
    """Безлимит означал, что один кривой запрос может забить системный диск,
    пока тело переливается во временный файл."""
    assert _middleware().over_limit(_request("/api/recording/batch", 5 * 1024**3)) is True


def test_ordinary_endpoints_keep_their_own_smaller_limit():
    assert _middleware().over_limit(_request("/api/books", 200 * 1024 * 1024)) is True


def test_upload_refusal_reports_the_upload_ceiling_not_the_general_one():
    """dispatch() раньше всегда клал в тело self.max_body_bytes (100 МБ общего
    лимита), даже когда отказ пришёл по потолку загрузки (2 ГБ) — диктор видел
    в ответе заведомо неверное число."""
    response = _dispatch(_middleware(), _request("/api/recording/batch", 5 * 1024**3))
    body = json.loads(response.body)
    assert body["max_body_bytes"] == settings.upload_max_bytes


def test_ordinary_refusal_still_reports_the_general_ceiling():
    middleware = _middleware()
    response = _dispatch(middleware, _request("/api/books", 200 * 1024 * 1024))
    body = json.loads(response.body)
    assert body["max_body_bytes"] == middleware.max_body_bytes


def test_refusal_message_is_russian_and_names_the_ceiling():
    """Спека обещает, что отказ формулирует приложение понятным диктору текстом,
    а не голым кодом — фронт должен получить готовую фразу."""
    response = _dispatch(_middleware(), _request("/api/recording/batch", 5 * 1024**3))
    body = json.loads(response.body)
    assert "2 ГБ" in body["message"]
