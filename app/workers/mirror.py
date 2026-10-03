import logging
import time
from collections.abc import Callable
from typing import Any

from app.services import audio_storage, nas_health
from app.services.audio_mirror import mirror_once, purge_deleted_once
from app.services.chapter_delivery import archive_pending_chapter_sessions
from app.workers.launcher import heartbeat_background_run, start_tracked_thread

logger = logging.getLogger(__name__)


def nas_probe_loop(*, settings, session_factory: Callable[[], Any], background_run_cls) -> None:
    """Единственное место, которое трогает мёртвое монтирование.

    Пробовать NAS прямо в обработчике запроса нельзя: при флаге `hard` операция не
    падает, а виснет навсегда, и на десятой загрузке кончатся потоки. Здесь виснет
    один поток, а запросы читают готовый флаг.
    """
    interval = max(1, int(settings.nas_probe_interval_seconds))
    while True:
        try:
            heartbeat_background_run(session_factory, background_run_cls, run_key="nas-probe-loop",
                                     meta={"interval_seconds": interval})
            # Отметка ставится ДО вызова: при пропаже NAS `probe_once` не возвращается —
            # `hard`-монтирование не отдаёт ошибку, а виснет. Без отметки повисшая проба
            # выглядела бы как умерший сторож («не знаем») дольше, чем есть на самом
            # деле. Приём файла на NAS не идёт ни при каком ответе этого флага — выбор
            # места приёма по нему упразднён; «не знаем» лишь держит `.sesx` на паузе
            # (см. `chapter_delivery.archive_chapter_session`) так же, как честное
            # «недоступен», а не отпускает его раньше времени.
            nas_health.begin_probe()
            nas_health.record_probe(nas_health.probe_once(audio_storage.nas_root()))
        except Exception:
            logger.exception("Ошибка в nas_probe_loop")
        time.sleep(interval)


def mirror_loop(*, settings, session_factory: Callable[[], Any], background_run_cls) -> None:
    """Копирование на NAS. Молчит, когда копировать нечего.

    Файл проекта дописанной главы кладётся здесь же, следом за самим зеркалированием
    (а не в обработчике загрузки — NFS не может быть в пути приёма): это единственное
    место в системе, где запись на NAS вне запроса уже законна — цикл уже спрашивает
    сторожа и уже пишет сюда. Задержка в несколько минут ничего не стоит: файлы главы
    к этому моменту давно зеркалированы, а `.sesx` открывают руками, не в ту же минуту.
    """
    interval = max(1, int(settings.mirror_interval_minutes)) * 60
    while True:
        try:
            heartbeat_background_run(session_factory, background_run_cls, run_key="mirror-loop",
                                     meta={"interval_seconds": interval})
            with session_factory() as db:
                stats = mirror_once(db)
                db.commit()
            if stats["mirrored"] or stats["failed"]:
                logger.info("Зеркало: скопировано %s, не вышло %s, осталось %s",
                            stats["mirrored"], stats["failed"], stats["left"])
            # Стирание — после копирования: порядок между ними формально не важен,
            # но так только что удалённый файл не успеет скопироваться заново в
            # этом же круге (запись на локальном диске уже исчезла раньше, чем
            # появилось поручение — см. audio_deletion.delete_audio_file, — но
            # мало ли что ещё лежит в очереди на копирование к этому моменту).
            with session_factory() as db:
                purge_stats = purge_deleted_once(db)
            if purge_stats["purged"] or purge_stats["missing"] or purge_stats["reused"]:
                # `reused` — поручения, чей ключ снова занят живой записью: файл
                # перезалили после удаления. Это не сбой, а самый частый способ
                # пользоваться кнопкой удаления, и в логе он назван отдельно,
                # чтобы «снято 0, стёрто 0» не выглядело простоем цикла.
                logger.info(
                    "Зеркало: поручений снято %s, копии не было у %s, перезалито %s, осталось %s",
                    purge_stats["purged"], purge_stats["missing"], purge_stats["reused"],
                    purge_stats["left"],
                )
            with session_factory() as db:
                sessions = archive_pending_chapter_sessions(db)
            if sessions["archived"]:
                logger.info("Файл проекта: положено %s, пропущено %s, пересобрано %s",
                            sessions["archived"], sessions["skipped"], sessions["rebuilt"])
        except Exception:
            logger.exception("Ошибка в mirror_loop")
        time.sleep(interval)


def start_nas_probe_worker(*, settings, session_factory, background_run_cls, loop_factory) -> bool:
    return start_tracked_thread(
        "nas-probe-worker", loop_factory,
        session_factory=session_factory, background_run_cls=background_run_cls,
        job_kind="nas_probe_loop", entity_type="system", entity_id="nas-probe",
        run_key="nas-probe-loop", meta={},
    )


def start_mirror_worker(*, settings, session_factory, background_run_cls, loop_factory) -> bool:
    return start_tracked_thread(
        "mirror-worker", loop_factory,
        session_factory=session_factory, background_run_cls=background_run_cls,
        job_kind="mirror_loop", entity_type="system", entity_id="mirror",
        run_key="mirror-loop", meta={},
    )
