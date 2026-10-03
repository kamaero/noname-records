"""Что браузер имеет право положить в кеш, а что обязан перепроверять.

Точка входа и файлы с хешем в имени живут по разным правилам, и до сих пор жили по
одному — никакому. `index.html` уходил без `Cache-Control`, браузер кешировал его по
своему усмотрению, и после каждого выката часть людей продолжала работать со вчерашней
сборкой, пока не нажимала Ctrl+Shift+R. Поймано 2026-09-10: владелец завёл учётку и не
нашёл в списке роль, которая на сервере уже была.

Второе правило — про отсутствующий файл. SPA-заглушка ловила и его тоже: запрос
`/assets/index-СТАРЫЙ.js` получал `index.html` с кодом 200 и `content-type: text/html`.
Браузер такой ответ выполнить откажется (`nosniff`), и вместо честной ошибки человек
видит белый экран. 404 честнее: по нему браузер перезагружает страницу начисто.
"""
import pytest

from app.services.main_helpers import build_main_helpers


@pytest.fixture()
def dist(tmp_path):
    """Каталог сборки: точка входа, файл с хешем в имени и картинка."""
    (tmp_path / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-DEADBEEF.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def spa(dist):
    import os

    helpers = build_main_helpers({
        "User": object,
        "UserRole": object,
        "settings": object(),
        "SessionLocal": object,
        "needs_password_setup": lambda: True,
        "frontend_dist_dir": str(dist),
        "frontend_dist_file": lambda *parts: os.path.join(str(dist), *parts),
    })
    return helpers["spa_response"]


class TestTheEntryPoint:
    """`index.html` обязан ходить на сервер за свежей версией — всегда."""

    def test_it_is_never_taken_from_cache_without_asking(self, spa):
        response = spa("")

        assert "no-cache" in response.headers.get("cache-control", "")

    def test_the_same_holds_for_any_spa_path(self, spa):
        """`/app/users` — не файл, а маршрут; отдаётся та же точка входа."""
        response = spa("users")

        assert "no-cache" in response.headers.get("cache-control", "")


class TestFilesWithAHashInTheirName:
    """Их содержимое не меняется никогда: меняется — меняется и имя."""

    def test_they_may_be_kept_for_a_year(self, spa):
        response = spa("assets/index-DEADBEEF.js")

        cache = response.headers.get("cache-control", "")
        assert "immutable" in cache and "max-age=31536000" in cache

    def test_a_file_outside_assets_is_not_promised_to_be_immutable(self, spa):
        """У `favicon.svg` имя без хеша: пообещать вечность значит запереть старую иконку."""
        response = spa("favicon.svg")

        assert "immutable" not in response.headers.get("cache-control", "")


class TestAMissingAsset:
    """Заглушка SPA не должна выдавать HTML за скрипт."""

    def test_it_is_a_plain_404(self, spa):
        response = spa("assets/index-СТАРЫЙ.js")

        assert response.status_code == 404

    def test_and_not_the_entry_point_pretending_to_be_one(self, spa):
        response = spa("assets/index-СТАРЫЙ.js")

        assert "text/html" not in response.headers.get("content-type", "")

    def test_a_missing_route_is_still_the_entry_point(self, spa):
        """Отсутствующий *маршрут* — не отсутствующий файл: его по-прежнему ловит SPA."""
        response = spa("books/42/cast")

        assert response.status_code == 200


class TestNothingUnhashedMayLandInAssets:
    """Мина, которую `immutable` взводит на год вперёд.

    Правило «файл в `assets/` неизменен» — это контракт Vite: он кладёт туда только то,
    что сам собрал, а собранному даёт имя с хешем содержимого. Отличить хеш от обычного
    слова по виду нельзя: настоящие имена сборки — `index-DRrXxguD.js` и
    `index-B6ZC8CTn.css`, и во втором нет ни одной цифры. Любая эвристика вида «дефис и
    восемь символов» примет и `hero-BannerLarge.png`. Поэтому проверка по каталогу, а
    сторож — здесь.

    Единственный способ положить в `dist/assets/` файл без хеша — создать
    `frontend/public/assets/`: оттуда Vite копирует файлы байт в байт, не трогая имён.
    Такой файл получил бы `max-age` на год под стабильным адресом, и отозвать его было бы
    нечем — он остался бы в браузерах до следующей осени.
    """

    def test_there_is_no_public_directory_to_copy_from(self):
        from pathlib import Path

        public = Path(__file__).resolve().parent.parent / "frontend" / "public"

        assert not public.exists(), (
            "Появился frontend/public/. Vite копирует его содержимое в dist/ без хеша в "
            "имени. Если что-то попадёт в frontend/public/assets/, spa_response выдаст "
            "этому файлу immutable на год — исправить его потом будет нечем. "
            "Либо уберите каталог, либо научите spa_response отличать хеш от имени."
        )
