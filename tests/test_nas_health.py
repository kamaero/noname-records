import os
import time

from app.services import nas_health


def test_without_a_probe_we_do_not_claim_to_know():
    """Ни разу не спрашивали — «не знаем». Ответ «жив» соврал бы, «мёртв» —
    без причины отложил бы зеркалирование на только что поднятом процессе."""
    assert nas_health.nas_online() is None


def test_a_fresh_successful_probe_means_online():
    now = time.monotonic()
    nas_health.record_probe(True, now=now)
    assert nas_health.nas_online(now=now + 5) is True


def test_a_fresh_failed_probe_means_offline():
    now = time.monotonic()
    nas_health.record_probe(False, now=now)
    assert nas_health.nas_online(now=now + 5) is False


def test_a_stale_probe_stops_being_an_answer():
    """Поток сторожа мог умереть. Старый ответ «жив» тогда опаснее незнания:
    на нём мы уйдём писать на мёртвое монтирование и повиснем."""
    now = time.monotonic()
    nas_health.record_probe(True, now=now)
    assert nas_health.nas_online(now=now + 61, stale_seconds=60) is None


def test_a_stale_failed_probe_also_stops_being_an_answer():
    """Протухшая проба — не ответ независимо от последнего вердикта. Если бы старое
    «мёртв» продолжало значить «мёртв», умерший поток сторожа было бы не отличить от
    NAS, который правда молчит: и то и то не даёт свежих данных."""
    now = time.monotonic()
    nas_health.record_probe(False, now=now)
    assert nas_health.nas_online(now=now + 61, stale_seconds=60) is None


def test_probe_writes_into_the_mount_and_reports_success(tmp_path):
    assert nas_health.probe_once(str(tmp_path)) is True
    assert (tmp_path / ".nas_probe").exists()


def test_probe_reports_failure_when_the_mount_is_not_writable(tmp_path):
    """Корень на месте, а записать в него нельзя — это тоже «мертво», и отдельно от
    случая «корня нет вовсе».

    Непригодность подстроена тем, что имя сторожевого файла занято каталогом: тесты
    идут от рута, где права ничего не запрещают, и отказ надо получить по существу,
    а не по разрешениям."""
    root = tmp_path / "rec"
    (root / nas_health.PROBE_FILENAME).mkdir(parents=True)

    assert nas_health.probe_once(str(root)) is False


def test_probe_flushes_and_fsyncs_so_success_is_proven_on_the_server(tmp_path, monkeypatch):
    """Без flush()+fsync() запись оседает в буфере Python/ОС — а на NFS ещё и в
    локальном кеше клиента, — и «успех» пробы ничего не доказывает про сам сервер."""
    calls: list[int] = []
    real_fsync = os.fsync

    def fake_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(nas_health.os, "fsync", fake_fsync)
    assert nas_health.probe_once(str(tmp_path)) is True
    assert calls, "probe_once не вызвал os.fsync — успех пробы не доказан"


def test_playback_of_a_local_file_no_longer_depends_on_the_nas():
    """Файл на локальном диске читается всегда: молчащий NAS ему не помеха,
    и отказывать в прослушивании из-за домашнего канала владельца больше незачем."""
    from app.services import nas_health
    from app.v2.api import storage_unavailable_for

    nas_health.record_probe(False)
    assert storage_unavailable_for("local") is False


def test_playback_of_a_file_still_on_the_nas_refuses_fast_instead_of_hanging():
    """Сторож снят с локальных файлов, но не со всех: шестьдесят записей до
    переезда физически лежат на NAS, и у них `location` так и говорит.

    Обращение к мёртвому `hard`-монтированию не падает, а виснет навсегда, и
    каждый такой запрос съедает поток — ровно то, ради чего сторож и заведён.
    Отдать «всегда доступно» для файла, которого на сервере нет, значит вернуть
    зависание тем записям, которые ещё не переехали. Ветка умрёт сама, когда
    переезд проставит им `location = local`."""
    from app.services import nas_health
    from app.v2.api import storage_unavailable_for

    nas_health.record_probe(False)
    assert storage_unavailable_for("nas") is True


def test_playback_of_a_nas_file_needs_a_nas_confirmed_alive():
    """Читать с NAS — только на подтверждённом «жив». Раньше пускали и на «не знаем»,
    чтобы рестарт не гасил прослушивание до первой пробы, — и 27.09 это стоило сайта:
    каждый запрос к мёртвому монтированию съедал поток, пока их не кончилось сорок.
    Первая проба после старта приходит за секунды, а «не знаем» дольше этого —
    поломка сторожа, и тем более не повод идти на NFS вслепую."""
    from app.services import nas_health
    from app.v2.api import storage_unavailable_for

    nas_health.record_probe(True)
    assert storage_unavailable_for("nas") is False

    nas_health.reset_state()
    assert nas_health.nas_online() is None
    assert storage_unavailable_for("nas") is True


def test_a_probe_that_hangs_longer_than_the_stale_limit_means_offline():
    """Главный отказ, ради которого всё затевалось: на `hard`-монтировании open()
    не падает, а виснет. Проба не возвращается, `record_probe` не зовётся — и раньше
    флаг просто протухал в «не знаем», а «не знаем» ведёт запись на NAS, где она и
    повисала вместе с загрузкой. Висящая дольше срока проба — это офлайн."""
    now = time.monotonic()
    nas_health.begin_probe(now=now)
    assert nas_health.nas_online(now=now + 61, stale_seconds=60) is False


def test_a_probe_in_flight_does_not_shadow_a_fresh_result():
    """Проба висит десять секунд из шестидесяти — это ещё не отказ, а обычный круг
    сторожа. Пока прошлый ответ свеж, отвечаем им."""
    now = time.monotonic()
    nas_health.record_probe(True, now=now)
    nas_health.begin_probe(now=now + 1)
    assert nas_health.nas_online(now=now + 11, stale_seconds=60) is True


def test_without_a_probe_in_flight_a_stale_result_is_still_not_knowing():
    """Пробы в полёте нет, а последняя протухла — сторож умер. Это «не знаем», а не
    «мёртв»: откладывать зеркалирование из-за нашей же поломки нельзя."""
    now = time.monotonic()
    nas_health.record_probe(True, now=now)
    assert nas_health.nas_online(now=now + 61, stale_seconds=60) is None


def test_an_unconfigured_nas_is_not_alive(tmp_path, monkeypatch):
    """Пустой корень плюс имя файла — это путь от рабочего каталога процесса.
    Метка легла бы рядом с кодом, запись бы удалась, и сторож отчитался бы, что
    зеркало живо, — а зеркала нет вовсе. Врущий сторож хуже молчащего: на его
    ответе держится решение «можно копировать»."""
    monkeypatch.chdir(tmp_path)

    assert nas_health.probe_once("") is False
    assert not (tmp_path / nas_health.PROBE_FILENAME).exists()


def test_a_missing_mirror_root_is_dead_but_says_so_out_loud(tmp_path, caplog):
    """Отсутствующий корень зеркала — «мертво», и создавать его проба НЕ должна.

    Корень живёт на стороне NFS, и его отсутствие — единственный дешёвый признак
    отвалившегося монтирования: точка монтирования после `umount` остаётся пустым
    локальным каталогом, внутри неё каталог создался бы прекрасно, и мы начали бы
    писать «на NAS» прямо на системный диск, отчитываясь, что зеркало живо.

    Но и молчать нельзя: при первой выкатке каталога ещё нет, и зеркалирование
    встало бы без единого слова в журнале — вторая копия не появилась бы никогда,
    и понять это можно было бы только по тому, что ничего не происходит."""
    import logging

    root = tmp_path / "rec"
    assert not root.exists()

    with caplog.at_level(logging.WARNING):
        assert nas_health.probe_once(str(root)) is False

    assert not root.exists(), "проба создала корень зеркала — так она замаскирует umount"
    assert str(root) in caplog.text


def test_the_stale_window_is_short_but_never_flags_a_healthy_probe():
    """Окно устаревания — сколько после пропажи NAS мы ещё верим последнему «жив» и
    пускаем запросы на NFS. 60 с — это минута запросов в мёртвое монтирование;
    `soft` отдаёт ошибку за ~30 с, так что дольше верить незачем. Но и короче двух
    кругов сторожа нельзя: здоровая проба раз в 15 с начала бы выглядеть протухшей."""
    from app.config import Settings

    defaults = Settings()
    assert defaults.nas_probe_stale_seconds <= 30
    assert defaults.nas_probe_stale_seconds >= 2 * defaults.nas_probe_interval_seconds
