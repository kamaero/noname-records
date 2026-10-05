"""Распознать дубль и сверить его со сценарием.

Три шага, каждый из которых умеет проверяться отдельно: что диктор должен был сказать,
что он сказал на самом деле, и что из этого совпало. Здесь они сходятся.

Реплика в v2 — не строка текста, а отрезок внутри абзаца: `text[span_start:span_end]`.
Один абзац несёт и слова рассказчика, и реплику героя, поэтому «реплики роли» — это
выборка отрезков, а не выборка абзацев. Порядок не украшение: на нём стоит всё
выравнивание.
"""
from __future__ import annotations

import json
import logging

from app.services.asr_azure import transcribe_file_azure
from app.services.asr_transcribe import OPENAI_BASE_URL, compress_for_asr, transcribe_file
from app.services.author_profile import normalize_name
from app.services.shared_runtime import is_narrator_name
from app.services.speech_parts import speech_and_narration
from app.v2.reader import effective_attributions
from app.v2.store import load_chapter_segments

logger = logging.getLogger(__name__)


def chapter_replicas(db, chapter_id: str) -> list[dict]:
    """Все реплики главы в порядке текста — то есть в порядке звучания.

    `expected_lines` отвечает на вопрос диктора «что мне читать»; этот — на вопрос
    монтажёра «что за чем идёт». Без него сессию не собрать черновиком: каждый актёр
    писал отдельно и начинал с нуля, и только сценарий знает, чья очередь.

    `role_index` — номер реплики внутри своей роли. Выравнивание считает реплики
    именно так, и это единственное, чем связать место в главе с местом в файле.
    """
    segments = load_chapter_segments(db, chapter_id=str(chapter_id or "").strip())
    if not segments:
        return []
    rows_by_segment = effective_attributions(db, [segment.id for segment in segments])

    out: list[dict] = []
    seen: dict[str, int] = {}
    for segment in segments:
        for row in rows_by_segment.get(segment.id, []):
            speaker = str(row.speaker or "").strip()
            text = str(segment.text or "")[int(row.span_start or 0):int(row.span_end or 0)].strip()
            if not speaker or not text:
                continue
            key = normalize_name(speaker)
            out.append({
                "order": len(out),
                "role": speaker,
                "role_index": seen.get(key, 0),
                "text": text,
                "segment_id": str(segment.id),
                "span_start": int(row.span_start or 0),
                "span_end": int(row.span_end or 0),
            })
            seen[key] = seen.get(key, 0) + 1
    return out


def _role_pieces(db, chapter_id: str, role: str) -> "tuple[bool, list[str]]":
    """Куски роли в этой главе, в порядке сценария, вместе с признаком рассказчика.

    Общий обход для `expected_lines` и `narration_only_lines`: обе функции смотрят
    на одни и те же куски, различаясь только тем, что делают с разбором каждого.
    """
    wanted = normalize_name(str(role or "").strip())
    if not wanted:
        return False, []
    narrator = is_narrator_name(role)
    segments = load_chapter_segments(db, chapter_id=str(chapter_id or "").strip())
    if not segments:
        return narrator, []
    rows_by_segment = effective_attributions(db, [segment.id for segment in segments])

    pieces: list[str] = []
    for segment in segments:
        for row in rows_by_segment.get(segment.id, []):
            speaker = str(row.speaker or "").strip()
            same = is_narrator_name(speaker) if narrator else normalize_name(speaker) == wanted
            if not same:
                continue
            text = str(segment.text or "")[int(row.span_start or 0):int(row.span_end or 0)].strip()
            if text:
                pieces.append(text)
    return narrator, pieces


def expected_lines(db, chapter_id: str, role: str) -> list[str]:
    """Речь роли в этой главе, по одной строке на кусок, в порядке сценария.

    От актёра ожидается только речь: авторские слова внутри куска читает рассказчик,
    а кусок без речи вовсе — ошибка разметки, и его место в отчёте владельцу (см.
    `narration_only_lines`), а не в письме диктору о пропусках.

    Рассказчику разбор не применяется: авторские слова и есть его работа, поэтому
    в список идёт кусок целиком.
    """
    narrator, pieces = _role_pieces(db, chapter_id, role)
    if narrator:
        return pieces
    lines: list[str] = []
    for text in pieces:
        speech, _ = speech_and_narration(text)
        if speech:
            lines.append(speech)
    return lines


def narration_only_lines(db, chapter_id: str, role: str) -> list[str]:
    """Куски роли, в которых нет ни слова прямой речи.

    Это не пропуск диктора, а сигнал о разметке: повествование приписано
    персонажу. Показывается владельцу, чинится в сценарии.

    Для рассказчика всегда пуст: авторские слова — его работа, и здесь нечему
    быть сигналом.
    """
    narrator, pieces = _role_pieces(db, chapter_id, role)
    if narrator:
        return []
    lines: list[str] = []
    for text in pieces:
        speech, _ = speech_and_narration(text)
        if not speech:
            lines.append(text)
    return lines


class AsrRunError(RuntimeError):
    """Распознавать нечего или не за чем. Текст — код для лога, не фраза для человека."""


def find_chapter_for_take(db, audio) -> "ScriptChapter | None":
    """Глава, к которой диктор привязал этот дубль.

    Связь идёт через название главы — то самое, что диктор выбрал в форме, — и через
    код книги, выведенный из её заголовка. Другого ключа у файла нет: `audio_files`
    хранит книгу кодом, а не идентификатором.
    """
    from app.models import ScriptBook, ScriptChapter
    from app.services.audio_naming import book_token
    from app.services.audio_uploads import derive_book_code

    label = str(getattr(audio, "chapter", "") or "").strip()
    wanted = book_token(str(getattr(audio, "book_code", "") or ""))
    if not label or not wanted:
        return None
    for book in db.query(ScriptBook).all():
        if book_token(derive_book_code(str(book.title or ""))) != wanted:
            continue
        chapter = (
            db.query(ScriptChapter)
            .filter(ScriptChapter.book_id == book.id, ScriptChapter.chapter_title == label)
            .one_or_none()
        )
        if chapter is not None:
            return chapter
    return None


def book_phrases(db, book_id: str) -> list[str]:
    """Имена и прозвища персонажей книги — подсказка распознаванию, каких слов ждать.

    «Бимькмолепус», «Бдеукс», «Полумрак» не встречаются нигде, кроме этой книги, и
    услышать их без подсказки почти нельзя. А неуслышанное имя — это не косметика:
    выравнивание объявит реплику пропущенной, и диктора зря отправят её перезаписывать.
    """
    from app.models import Character
    from app.v2.cast_ops import split_aliases

    out: list[str] = []
    for name, aliases in db.query(Character.name, Character.aliases).filter(Character.book_id == str(book_id or "")).all():
        for item in [str(name or ""), *split_aliases(aliases)]:
            text = " ".join(str(item or "").split())
            if text and text not in out:
                out.append(text)
    return out


def _transcribe_with_service(path: str, *, phrases: list[str] | None = None) -> dict:
    """Настоящий путь: сжать и отправить тому, кто выбран в настройках.

    Сжатие нужно обоим: у whisper это условие приёма (25 МБ), у Azure — экономия
    времени на отправке. Модели всё равно слушают шестнадцать килогерц.
    """
    import os
    import tempfile

    from app.config import settings
    from app.services.provider_keys import provider_key

    compressed = os.path.join(tempfile.gettempdir(), f"asr-{os.path.basename(path)}.mp3")
    try:
        compress_for_asr(path, compressed)
        provider = str(settings.asr_provider or "").strip().lower()
        if provider == "azure":
            return transcribe_file_azure(
                compressed,
                key=provider_key("azure"),
                endpoint=settings.azure_speech_endpoint,
                locale=settings.azure_speech_locale,
                phrases=phrases or [],
            )
        # RouterAI совместим с OpenAI по формату — отличаются адрес, ключ и имена моделей
        routerai = provider == "routerai"
        return transcribe_file(
            compressed,
            api_key=provider_key("routerai") if routerai else provider_key("openai"),
            base_url=settings.routerai_base_url if routerai else OPENAI_BASE_URL,
            model=settings.asr_model,
            language=settings.asr_language,
            # подсказка одной строкой: у OpenAI-формата список фраз называется `prompt`
            prompt=settings.asr_prompt or ", ".join((phrases or [])[:80]),
        )
    finally:
        if os.path.exists(compressed):
            os.remove(compressed)


def notify_take_missing(db, job) -> dict | None:
    """Письмо диктору о пропусках одной джобы — по её сверке в эту минуту.

    Отдельно от распознавания, чтобы прогон главы мог сначала поискать реплики у соседних
    ролей того же актёра (`asr_borrow`) и только потом писать: иначе диктору ушло бы письмо
    о реплике, которая лежит в его же соседнем файле.
    """
    from app.models import AudioFile, ScriptBook, ScriptChapter
    from app.services import asr_notice

    alignment = json.loads(job.alignment_json or "{}")
    if not alignment.get("missing"):
        return None
    audio = db.get(AudioFile, str(job.audio_file_id))
    chapter = db.get(ScriptChapter, str(job.chapter_id))
    if audio is None or chapter is None:
        return None
    book = db.get(ScriptBook, chapter.book_id)
    return asr_notice.notify_missing_lines(
        db,
        actor_name=str(audio.actor_name or ""),
        role=str(audio.role or ""),
        chapter=str(chapter.chapter_title or ""),
        book_title=str(getattr(book, "title", "") or ""),
        book_id=str(chapter.book_id),
        lines=alignment["lines"],
    )


def run_asr_for_take(db, audio_file_id: str, *, transcribe=None, notify: bool = True) -> "AsrJob":
    """Распознать дубль, сверить со сценарием и записать, что вышло.

    Неудача — тоже результат: она ложится в ту же строку со своей причиной. Молча
    потерянное задание выглядит как «ещё не считали» и ждёт вечно.

    `notify=False` — для прогонов по нашей инициативе (перераспознать архив, когда
    поменялись правила). Диктору о пропусках в таком прогоне не пишут: он прислал этот
    дубль недели назад и письмо получил тогда же, а второе сказало бы ему неправду о
    его работе и отправило перезаписывать уже сданное. Умолчание — сообщать: обычный
    приём дубля обязан успеть предупредить, пока диктор ещё у микрофона.
    """
    from app.models import AsrJob, AudioFile
    from app.config import settings
    from app.services import audio_storage
    from app.services.asr_align import align_transcript
    from app.time_utils import utcnow_naive

    audio = db.get(AudioFile, str(audio_file_id or "").strip())
    if audio is None:
        raise AsrRunError("audio_not_found")
    if str(audio.kind or "take") != "take":
        # У пробы нет главы, с которой её сверять: она заявка на роль, а не запись.
        raise AsrRunError("not_a_take")
    chapter = find_chapter_for_take(db, audio)
    if chapter is None:
        raise AsrRunError("chapter_not_found")

    job = (
        db.query(AsrJob).filter(AsrJob.audio_file_id == audio.id).one_or_none()
        or AsrJob(audio_file_id=audio.id)
    )
    job.chapter_id = str(chapter.id)
    job.provider = settings.asr_provider
    job.model = settings.asr_model
    job.expected_role = str(audio.role or "")
    job.expected_chapter_index = int(chapter.chapter_index or 0)
    job.status = "running"
    job.error_message = ""
    job.updated_at = utcnow_naive()
    db.add(job)
    db.flush()

    try:
        location = str(audio.location or "local")
        if location == "nas":
            # Старая запись на NAS: воркер не знает, жив ли он, — спрашиваем разово и
            # с таймаутом. Иначе мёртвое монтирование держит очередь распознавания.
            from app.services import nas_health

            if not nas_health.reachable_now():
                raise AsrRunError("storage_unavailable: запись на NAS, а он не отвечает")
        source = audio_storage.resolve_path(str(audio.stored_key or ""), location=location)
        heard = (
            transcribe(source) if transcribe
            else _transcribe_with_service(source, phrases=book_phrases(db, str(chapter.book_id)))
        )
    except Exception as exc:  # noqa: BLE001 — причина уходит в строку задания, а не в небо
        job.status = "failed"
        job.error_message = str(exc)[:255]
        job.coverage = 0.0
        db.flush()
        return job

    expected = expected_lines(db, chapter.id, str(audio.role or ""))
    alignment = align_transcript(expected, heard.get("segments") or [])
    job.status = "done"
    job.transcript_text = str(heard.get("text") or "")
    # Услышанное целиком, а не только его расшифровка текстом: сверку пересчитывают из
    # сегментов со словными таймингами, и без них переиграть её нечем (см. `heard_json`
    # в `app/models.py`).
    job.heard_json = json.dumps(heard, ensure_ascii=False)
    store_alignment(job, alignment)
    db.flush()
    if notify:
        # Диктору сообщают сразу: перезаписывать надо, пока он ещё у микрофона.
        notify_take_missing(db, job)
    return job


def store_alignment(job, alignment: dict) -> None:
    """Положить итог сверки в джобу — одним местом для прогона и для пересчёта.

    Вынесено затем, чтобы пересчёт из сохранённого (`app/services/asr_replay.py`) не
    считал покрытие и сходство по своей копии формул: разъехавшись, две копии дали бы
    джобы, у которых `coverage` значит разное в зависимости от того, кто его записал.
    """
    lines = alignment.get("lines") or []
    job.alignment_json = json.dumps(alignment, ensure_ascii=False)
    job.coverage = float(alignment["coverage"])
    job.similarity = float(sum(line["score"] for line in lines) / len(lines)) if lines else 0.0


def takes_fingerprint(take_ids) -> str:
    """Отпечаток состава по готовому списку идентификаторов дублей.

    Идентификаторы, а не суммы файлов: сверка смотрит на состав главы, а
    целостность самих файлов стережёт отдельная проверка контрольных сумм.
    Значит перезапись файла под тем же идентификатором нового отчёта не вызовет
    — и это осознанно.

    Отдельная функция, а не тело `chapter_take_fingerprint`, потому что у отчёта
    и у вопроса «каков состав главы сейчас» источники списка разные: прогону
    нужен отпечаток ровно того состава, который он разобрал.
    """
    from hashlib import sha1

    ids = sorted({str(item or "").strip() for item in (take_ids or []) if str(item or "").strip()})
    if not ids:
        return ""
    return sha1("\n".join(ids).encode("utf-8")).hexdigest()


def chapter_take_fingerprint(db, chapter_id: str) -> str:
    """Отпечаток состава дублей главы — того, что лежит в базе сейчас."""
    from app.models import AudioFile, ScriptChapter
    from app.services.audio_uploads import TAKE

    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return ""
    label = str(chapter.chapter_title or "").strip()
    return takes_fingerprint(
        item.id
        for item in db.query(AudioFile).filter(AudioFile.kind == TAKE, AudioFile.chapter == label).all()
    )


def run_asr_for_chapter(db, chapter_id: str, *, transcribe=None, force: bool = False) -> dict:
    """Распознать дубли главы. Один упавший файл не отменяет остальные.

    Распознавание платное и невоспроизводимое (см. память `project_asr_heard_replay`):
    дубль, у которого уже есть готовая джоба с сохранённым `heard_json`, не отправляется
    в него заново, а пересчитывается бесплатно из уже услышанного (`asr_replay.realign_job`).
    Иначе фикс в одну реплику заново оплачивал бы распознавание всего хора уже сданной
    главы. Платит только тот, кого ещё не слышали, — новый или присланный впервые файл.

    `force=True` — кнопка «Распознать» в редакторе: владелец сознательно платит за то,
    чтобы все дубли главы услышали заново, и пересчёт из старого услышанного его не заменяет.
    В итоге `done` — распознано в этом прогоне, `realigned` — пересчитано бесплатно.
    """
    from app.models import AsrJob, AudioFile, ScriptChapter
    from app.services.asr_replay import realign_job
    from app.services.audio_uploads import TAKE

    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        raise AsrRunError("chapter_not_found")
    label = str(chapter.chapter_title or "").strip()
    takes = (
        db.query(AudioFile)
        .filter(AudioFile.kind == TAKE, AudioFile.chapter == label)
        .order_by(AudioFile.uploaded_at.asc())
        .all()
    )
    # Уже распознанные дубли главы — с готовой джобой и непустым `heard_json`.
    recognised_jobs: dict[str, AsrJob] = {}
    if takes and not force:
        recognised_jobs = {
            str(job.audio_file_id): job
            for job in db.query(AsrJob).filter(
                AsrJob.audio_file_id.in_([take.id for take in takes]), AsrJob.status == "done",
            ).all()
            if str(job.heard_json or "").strip()
        }
    done, realigned, failed = 0, 0, 0
    # Дубли, упавшие до того, как появилась строка `AsrJob`: по базе они
    # неотличимы от нераспознанных вовсе, и отчёт, который читает только
    # `AsrJob`, промолчал бы о них. Причину прогон знает здесь — и только здесь.
    lost_takes: list[dict] = []
    # Только джобы, распознанные ИМЕННО в этом прогоне: письма о пропусках идут по ним
    # ниже, а пересчитанным заново старым джобам письмо уже ушло, когда их приняли
    # впервые — второе сказало бы диктору неправду о его работе (см. `asr_replay.py`).
    done_jobs: list = []
    for take in takes:
        recognised = recognised_jobs.get(str(take.id))
        if recognised is not None:
            # Своим `try`: испорченный сохранённый JSON одной старой джобы не должен
            # обрывать прогон всей главы. Платить за такой дубль заново не идём —
            # только в журнал; услышать его заново можно кнопкой «Распознать».
            try:
                realign_job(db, recognised.id)
            except Exception:
                logger.exception("Не удалось пересчитать сверку джобы %s, дубль %s", recognised.id, take.id)
                continue
            realigned += 1
            continue
        try:
            job = run_asr_for_take(db, take.id, transcribe=transcribe, notify=False)
        except AsrRunError as exc:
            failed += 1
            lost_takes.append({"role": str(take.role or "").strip(), "error": str(exc)[:255]})
            continue
        if job.status == "done":
            done += 1
            done_jobs.append(job)
        else:
            failed += 1
    # Поиск у соседних ролей — до писем: реплику, перенесённую к другой роли того же
    # актёра, он найдёт в соседнем файле, и письма о ней не будет.
    #
    # Собственный `try`: распознавание уже сделано и не должно откатиться, если сам
    # поиск бросит исключение — тем же приёмом, что отчёт владельцу ниже.
    try:
        from app.services.asr_borrow import borrow_across_roles

        borrow_across_roles(db, str(chapter.id))
    except Exception:
        logger.exception("Не удалось поискать реплики у соседних ролей, глава %s", chapter.id)
    for job in done_jobs:
        try:
            notify_take_missing(db, job)
        except Exception:
            logger.exception("Не удалось написать диктору о пропусках, джоба %s", job.id)
    # Новый дубль в архивной главе — сессия могла поменяться; решит отпечаток.
    if done_jobs:
        try:
            from app.services.chapter_delivery import mark_session_outdated

            mark_session_outdated(db, str(chapter.id))
        except Exception:
            logger.exception("Не удалось пометить сессию главы устаревшей, глава %s", chapter.id)
    # Отчёт владельцу — здесь, а не в ручке приёма: там распознавание ещё не
    # начиналось, и отчитываться было бы не о чем. Здесь дубли главы уже
    # разобраны, и всё, что нужно, лежит в `AsrJob`.
    #
    # Собственный `try`, не завязанный на успех цикла выше: распознавание уже
    # сделано и должно уцелеть при любом исходе отчёта. Единственный `db.commit()`
    # на весь прогон по главе живёт выше, в `perform_asr_chapter_task`
    # (`app/worker_tasks.py`) — упавший здесь `build_chapter_report`,
    # `render_chapter_report` или `send_telegram_message` долетел бы туда и откатил
    # его, стерев все уже распознанные дубли главы; задание при этом ушло бы в
    # упавшие с ложной причиной — как будто не распозналось, хотя распозналось.
    # Тот же приём — в ручке приёма файлов (`app/api/recording.py`) вокруг
    # постановки заданий в очередь: неудача второстепенного шага — запись в
    # журнал, а не потеря уже сделанной работы.
    try:
        _report_chapter_if_assembled(
            db, chapter, take_ids=[str(take.id) for take in takes], lost_takes=lost_takes,
        )
    except Exception:
        logger.exception("Не удалось отправить отчёт по главе %s", chapter.id)
    return {
        "chapter_id": str(chapter.id), "takes": len(takes),
        "done": done, "realigned": realigned, "failed": failed,
    }


def _report_chapter_if_assembled(db, chapter, *, take_ids, lost_takes=None) -> bool:
    """Отправить владельцу отчёт, если глава собралась и по этому составу его не было.

    `take_ids` — идентификаторы тех дублей, которые прогон действительно разобрал,
    а не свежий запрос «что в главе сейчас». Разница принципиальная: состав
    снимается в начале прогона, а прогон перераспознаёт все дубли главы заново,
    то есть идёт минуты. Дубль, загруженный в это окно, свежим запросом попал бы и
    в «глава собралась», и в отпечаток — при том что распознан он не был и в отчёт
    не попал. Отметка по такому составу закрывала бы тему навсегда: следующий
    прогон увидел бы совпавший отпечаток и промолчал. Считая отпечаток по
    разобранному списку, мы оставляем пришедший в середине дубль новым составом —
    и следующий прогон отчитается по нему честно.
    """
    from app.config import settings
    from app.services.audio_integrity import chapter_is_ready
    from app.services.chapter_coverage_report import build_chapter_report, render_chapter_report
    from app.services.telegram import send_telegram_message
    from app.time_utils import utcnow_naive

    if not chapter_is_ready(db, str(chapter.id)):
        return False
    fingerprint = takes_fingerprint(take_ids)
    if not fingerprint or fingerprint == str(chapter.coverage_reported_takes or ""):
        return False
    text = render_chapter_report(build_chapter_report(db, str(chapter.id), lost_takes=lost_takes))
    if not text:
        return False
    # Без `direct`: в этом режиме `send_telegram_message` нарочно подменяет
    # адресата владельцем — ровно то, что здесь нужно.
    delivered = send_telegram_message(db, text)
    # Отметка о составе — обещание «отчёт по нему уже ушёл», и второго шанса у
    # состава нет. Поэтому её ставит только факт доставки: сетевые отказы
    # `send_telegram_message` гасит внутри и возвращает ноль отправленных чатов,
    # а разовая ошибка сети в паре с однократностью превратилась бы в вечное
    # молчание по этой главе.
    #
    # Выключенный настройкой канал — не отказ доставки, а сознательно закрытая
    # дверь: адресата нет вовсе, и ждать его появления бессмысленно. Отчёт при
    # этом собирается заново на каждую загрузку, а «есть что сказать» решается
    # только после сборки — поэтому такой состав отмечается отправленным, чтобы
    # выключенные уведомления не оборачивались бесконечной работой впустую.
    if not delivered and settings.telegram_notify_enabled:
        return False
    chapter.coverage_reported_takes = fingerprint
    chapter.coverage_reported_at = utcnow_naive()
    db.flush()
    return True


def enqueue_asr_for_chapter(chapter_id: str) -> str | None:
    """Поставить главу в очередь на распознавание. Повторный вызов не плодит заданий.

    Зовётся сразу после загрузки: к тому времени, как владелец откроет экран, всё уже
    посчитано, а диктор уже знает про свои пропуски.
    """
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    chapter_id = str(chapter_id or "").strip()
    if not chapter_id:
        return None
    return enqueue_tracked_task(
        queue_name="high",
        func_ref="app.worker_tasks.perform_asr_chapter_task",
        session_factory=SessionLocal,
        background_run_cls=BackgroundRun,
        job_kind="asr_chapter",
        entity_type="script_chapter",
        entity_id=chapter_id,
        run_key=f"asr:{chapter_id}",
        meta={"chapter_id": chapter_id},
        chapter_id=chapter_id,
    )
