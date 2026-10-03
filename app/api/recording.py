from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

from fastapi import File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse

from types import SimpleNamespace

from app.services import audio_mirror
from app.services.asr_run import enqueue_asr_for_chapter, find_chapter_for_take
from app.services.audio_deletion import AudioDeleteError, delete_audio_file
from app.services.audio_integrity import chapter_needs_verification, enqueue_verify_for_chapter
from app.services.audio_uploads import chapter_label_number
from app.services.recording import notify_author_about_auditions, upload_notice
from app.services.upload_kind import classify_upload_kind

#: Диск переполнен — это не баг, а исчерпание места: диктору нужен внятный отказ,
#: а не 500. 507 (Insufficient Storage) точнее прочих кодов называет причину.
DISK_FULL_MESSAGE = "Не могу принять файл: на диске сервера кончилось место. Попробуйте позже."

#: `delete_audio_file` (`app/services/audio_deletion.py`) отказывает машинным кодом —
#: экрану он не годится, диктор, увидевший `too_old`, не поймёт, что делать. Словарь
#: рядом с ручкой — тот же обычай, что у `RENAME_ERRORS` во фронтовом `CastPage.tsx`.
AUDIO_DELETE_ERRORS: dict[str, tuple[int, str]] = {
    "not_found": (
        status.HTTP_404_NOT_FOUND,
        "Такой записи уже нет — возможно, её удалили раньше. Обновите страницу.",
    ),
    "forbidden": (
        status.HTTP_403_FORBIDDEN,
        "Это чужая запись — удалить её может только тот, кто её записал, либо автор или владелец.",
    ),
    "too_old": (
        status.HTTP_403_FORBIDDEN,
        "Запись сделана больше суток назад — самостоятельно удалить её уже нельзя. "
        "Обратитесь к автору или владельцу студии.",
    ),
    "forbidden_agent": (
        status.HTTP_403_FORBIDDEN,
        "Агент загружает записи, но не удаляет их. "
        "Если файл попал сюда по ошибке — напишите владельцу студии, он уберёт.",
    ),
}

#: Отказ пачки по одному файлу — словами, а не кодом: «Не удалось загрузить:
#: validation_failed» диктору ничего не говорит. Тексты — те же, что под файлом в
#: предпросмотре (`ERROR_TEXT` во фронтовом `BatchUpload.tsx`).
BATCH_ERROR_TEXT: dict[str, str] = {
    "chapter_required": "не указана глава",
    "role_required": "не указана роль",
    "duplicate_in_batch": "в пачке два одинаковых файла",
    "duplicate_in_storage": "этот же файл уже загружен в эту роль и главу",
}


def _validation_item_text(item: dict) -> str:
    error = str(item.get("error") or "")
    if error == "chapter_mismatch":
        return (
            f"в имени файла глава {item.get('filename_chapter')}, "
            f"а выбрана глава {item.get('chosen_chapter')}"
        )
    return BATCH_ERROR_TEXT.get(error, error)


def batch_validation_message(items: list[dict]) -> str:
    """«KP_Ch11_Gamuk.wav: этот же файл уже загружен в эту роль и главу; …»."""
    parts = []
    for item in items:
        text = _validation_item_text(item)
        name = str(item.get("file") or "").strip()
        parts.append(f"{name}: {text}" if name else text)
    return "; ".join(parts)


logger = logging.getLogger(__name__)


async def _md5_of(upload: UploadFile) -> str:
    """Сумма присланного файла: Starlette уже держит его во временном файле, читаем кусками."""
    digest = hashlib.md5()
    await upload.seek(0)
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    await upload.seek(0)
    return digest.hexdigest()


def build_recording_handlers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    is_authenticated = deps["is_authenticated"]
    has_any_role = deps["has_any_role"]
    has_workspace_full_access = deps["has_workspace_full_access"]
    session_payload = deps["session_payload"]
    is_agent = deps["is_agent"]
    session_local = deps["SessionLocal"]
    audio_file_model = deps["AudioFile"]
    build_canonical_audio_filename = deps["build_canonical_audio_filename"]
    store_audio_file = deps["store_audio_file"]
    send_telegram_message = deps["send_telegram_message"]
    recording_identity = deps["recording_identity"]
    recording_workspace_payload = deps["recording_workspace_payload"]
    require_recording_access = deps["require_recording_access"]
    parse_batch_audio_filename = deps["parse_batch_audio_filename"]

    def actor_for(request, wanted: str) -> str:
        """Чьим именем подписать запись.

        Имя приходит от браузера, поэтому брать его у кого угодно нельзя: подписанная
        чужим именем запись тихо портит и покрытие, и уведомления, и смету. Владельцу и
        автору называть чужое имя нужно — к ним попадают записи, присланные мимо системы.
        Агенту нужно тем более: он ведёт актёров и грузит за них по роду занятий, и часть
        его актёров в системе есть, а часть нет и не будет.

        Диктору незачем: ему подставляется его собственное. Это защита не от обмана, а от
        опечатки — актёр записал свою роль и случайно выбрал в форме не себя. За всю
        карьеру владельца студии попыток записаться за другого не было ни разу. Расширять
        её на агента значило бы мешать ему каждый раз.

        Правило уже покрыто тестами: `TestWhoseNameGoesOnTheFile` в
        `tests/test_audition_batch_endpoint.py` гоняет его через настоящую
        `recording_batch` с фейковым `deps`. Второй раз выносить эту функцию на
        уровень модуля не нужно — там уже есть на чём проверить.
        """
        mine = str(session_payload(request).get("display_name") or "").strip()
        if has_any_role(request, {"admin", "author"}) or is_agent(request):
            return str(wanted or "").strip() or mine
        return mine

    async def recording_replica_patch(
        request: Request,
        book_code: str = Form("BOOK"),
        chapter: str = Form(...),
        role: str = Form(...),
        actor_name: str = Form(""),
        line_index: int = Form(...),
        file: UploadFile = File(...),
    ):
        access_error = require_recording_access(request)
        if access_error:
            return access_error
        # Поток, а не await file.read(): двухгигабайтный дубль Рассказчика не должен
        # въезжать в память целиком. seek(0) — Starlette мог уже читать тело при
        # разборе multipart-формы.
        await file.seek(0)
        with session_local() as db:
            try:
                item = store_audio_file(
                    db,
                    source=file.file,
                    size_hint=int(getattr(file, "size", 0) or 0),
                    mime_type=file.content_type or "audio/webm",
                    original_filename=file.filename or "patch.webm",
                    book_code=book_code,
                    chapter=chapter,
                    role=role,
                    actor_name=actor_for(request, actor_name),
                    line_index=int(line_index),
                )
            except audio_mirror.LocalDiskFull:
                return JSONResponse(
                    {"ok": False, "error": "disk_full", "message": DISK_FULL_MESSAGE},
                    status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                )
            db.commit()
            return JSONResponse({
                "ok": True,
                "id": item.id,
                "line_index": item.line_index,
            })

    async def recording_batch(
        request: Request,
        files: list[UploadFile] = File(...),
        meta_json: str = Form(...),
    ):
        """Дубли утверждённых ролей — и, отдельным видом, пробы на роль.

        Что именно пришло, решает сервер по роли и актёру, а не галочка в форме:
        диктор не обязан помнить, на что он утверждён, а система обязана. У пробы нет
        главы, и второй файл на ту же роль — не промах, а вторая попытка: обе проверки,
        которые стерегут дубли, для проб выключены.
        """
        access_error = require_recording_access(request)
        if access_error:
            return access_error
        try:
            meta_items = json.loads(meta_json or "[]")
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_meta_json"}, status_code=status.HTTP_400_BAD_REQUEST)
        if not isinstance(meta_items, list):
            return JSONResponse({"ok": False, "error": "invalid_meta_items"}, status_code=status.HTTP_400_BAD_REQUEST)
        if len(meta_items) != len(files):
            return JSONResponse({"ok": False, "error": "count_mismatch"}, status_code=status.HTTP_400_BAD_REQUEST)

        validation_errors: list[dict] = []
        prepared: list[dict] = []
        with session_local() as db:
            for idx, up in enumerate(files):
                meta = meta_items[idx] if idx < len(meta_items) else {}
                role = str((meta or {}).get("role") or "").strip()
                actor_name = actor_for(request, str((meta or {}).get("actor_name") or ""))
                book_code = str((meta or {}).get("book_code") or "").strip() or "BOOK"
                kind = classify_upload_kind(db, role=role, actor_name=actor_name)
                chapter = str((meta or {}).get("chapter") or "").strip()
                if not chapter and kind != "audition":
                    validation_errors.append({"index": idx, "file": up.filename or "", "error": "chapter_required"})
                if not role:
                    validation_errors.append({"index": idx, "file": up.filename or "", "error": "role_required"})
                # Форма выбирает главу, имя файла — только подсказка. Расхождение молчало,
                # и файл ложился в чужую главу, где роль не говорит ни слова: опись
                # показывала «Ничья роль», а распознавание было оплачено впустую
                # (Тихонов Дмитрий, 2026-09-16). `confirm_chapter` снимает отказ, если
                # диктор знает, что делает.
                filename_chapter = int(parse_batch_audio_filename(
                    up.filename or "", default_book_code=book_code, default_actor_name=actor_name,
                ).get("filename_chapter_index") or 0)
                chosen_chapter = chapter_label_number(chapter)
                mismatch = (
                    kind != "audition" and bool(filename_chapter) and bool(chosen_chapter)
                    and filename_chapter != chosen_chapter
                )
                confirmed = bool((meta or {}).get("confirm_chapter"))
                if mismatch and not confirmed:
                    validation_errors.append({
                        "index": idx, "file": up.filename or "", "error": "chapter_mismatch",
                        "filename_chapter": filename_chapter, "chosen_chapter": chosen_chapter,
                    })
                prepared.append(
                    {
                        "index": idx,
                        "book_code": book_code,
                        "chapter": chapter,
                        "role": role,
                        "actor_name": actor_name,
                        "kind": kind,
                        "file": up,
                        # (глава из имени, выбранная глава) — только у подтверждённых
                        # вручную. След о них оставляют ниже, когда файл уже сохранён:
                        # пачка ещё может отказать по чужой причине — второй файл дубль
                        # или без роли, — и тогда не сохранится ничего, а строка в журнале
                        # говорила бы о записи, которой нет.
                        "chapter_confirmed": (filename_chapter, chosen_chapter) if (mismatch and confirmed) else None,
                    }
                )

        # «Уже загружен» решает содержимое, а не имя — имя больше ничего не запрещает:
        # дозапись всей роли и короткий фикс приходят каждый под своим обычным именем
        # (решение владельца 2026-09-15). У пробы дубль — законная вторая попытка, а не
        # повтор, поэтому эта проверка её не касается.
        for item in prepared:
            if item["kind"] == "take":
                item["md5"] = await _md5_of(item["file"])

        md5_counts: dict[str, int] = {}
        for item in prepared:
            if "md5" in item:
                md5_counts[item["md5"]] = md5_counts.get(item["md5"], 0) + 1
        for item in prepared:
            if "md5" in item and md5_counts.get(item["md5"], 0) > 1:
                validation_errors.append({
                    "index": item["index"],
                    "file": item["file"].filename or "",
                    "error": "duplicate_in_batch",
                })

        with session_local() as db:
            for item in prepared:
                if "md5" not in item:
                    continue
                dup = (
                    db.query(audio_file_model.id)
                    .filter(
                        audio_file_model.kind == "take",
                        audio_file_model.chapter == item["chapter"],
                        audio_file_model.role == item["role"],
                        audio_file_model.md5 == item["md5"],
                    )
                    .first()
                )
                if dup is not None:
                    validation_errors.append({
                        "index": item["index"],
                        "file": item["file"].filename or "",
                        "error": "duplicate_in_storage",
                    })

            if validation_errors:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "validation_failed",
                        "message": batch_validation_message(validation_errors),
                        "items": validation_errors,
                    },
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            saved: list[dict] = []
            # Переполненный диск — не баг, а исчерпание места: он не должен рушить
            # молча всю пачку. Файл, которому не хватило места, откладывается сюда;
            # остальные файлы пачки пишутся как ни в чём не бывало.
            disk_errors: list[dict] = []
            failed_indexes: set[int] = set()
            # Подтверждённые вручную расхождения глав — только по тем файлам, которые
            # действительно сохранились: уведомление говорит о том, что легло на диск.
            confirmed_chapters: list[tuple[int, int]] = []
            try:
                for item in prepared:
                    # Файл уже на диске: Starlette сбрасывает большие тела во временный
                    # файл. Читать его целиком в память, чтобы тут же записать в
                    # хранилище, — это гигабайт на каждый дубль Рассказчика.
                    #
                    # Копирование идёт здесь же, а не в отдельном потоке: вместе с ним
                    # пришлось бы отдать туда и сессию БД, а сессии не потокобезопасны.
                    # Блокировка на время копирования была и раньше — новым был бы
                    # только тихий шанс на гонку.
                    await item["file"].seek(0)
                    try:
                        created = store_audio_file(
                            db,
                            source=item["file"].file,
                            mime_type=item["file"].content_type or "audio/wav",
                            original_filename=item["file"].filename or "audio.wav",
                            book_code=item["book_code"],
                            chapter=item["chapter"],
                            role=item["role"],
                            actor_name=item["actor_name"],
                            kind=item["kind"],
                            size_hint=int(getattr(item["file"], "size", 0) or 0),
                        )
                    except audio_mirror.LocalDiskFull:
                        disk_errors.append({
                            "index": item["index"],
                            "file": item["file"].filename or "",
                            "error": "disk_full",
                        })
                        failed_indexes.add(item["index"])
                        continue
                    saved.append(
                        {
                            "id": created.id,
                            "original_filename": created.original_filename,
                            "canonical_filename": created.canonical_filename,
                            "chapter": created.chapter,
                            "role": created.role,
                            "actor_name": created.actor_name,
                            "kind": created.kind,
                        }
                    )
                    if item.get("chapter_confirmed"):
                        # Снятое подтверждением расхождение — единственный случай, когда
                        # файл ложится в главу, с которой не согласен даже разбор его
                        # собственного имени. Молча этого делать нельзя: именно так и ушли
                        # 82 МБ платного распознавания. Строка — след, по которому разбирают
                        # потом, и пишется она только о том, что действительно сохранено.
                        logger.warning(
                            "Глава подтверждена вручную: %s — в имени глава %s, выбрана глава %s (%s)",
                            item["file"].filename or "", item["chapter_confirmed"][0],
                            item["chapter_confirmed"][1], item["chapter"],
                        )
                        if item["chapter_confirmed"] not in confirmed_chapters:
                            confirmed_chapters.append(item["chapter_confirmed"])
                if saved:
                    db.commit()
                else:
                    db.rollback()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)

            # Часть пачки могла упереться в переполненный диск — но то, что уже
            # сохранилось и закоммичено, не должно тонуть в тишине вместе с ней:
            # `enqueue_asr_for_chapter` зовётся только отсюда во всём проекте
            # (ни планировщика, ни повтора нет), так что пропущенный вызов здесь
            # значит — распознавание для этих файлов не запустится никогда.
            if saved:
                payload = session_payload(request)
                send_telegram_message(
                    db,
                    upload_notice(
                        str(payload.get("display_name") or ""),
                        saved,
                        login=str(payload.get("sub") or ""),
                        confirmed_chapters=confirmed_chapters,
                    ),
                )
                # Автору — отдельным письмом и только о пробах: чей голос подходит его
                # героям, решает он. Ход записи его не касается и уходит владельцу выше.
                # Своим `try` — как и отчёт по главе ниже: файлы уже приняты и
                # закоммичены, и неудача уведомления не должна превращать успешную
                # загрузку в ошибку для диктора.
                try:
                    notify_author_about_auditions(
                        db,
                        saved,
                        user_name=str(payload.get("display_name") or ""),
                        login=str(payload.get("sub") or ""),
                    )
                except Exception:
                    logger.exception("Не удалось сообщить автору о пробах")
            # Главы, которых коснулась эта пачка: распознавание идёт следом за загрузкой,
            # чтобы диктор узнал о пропусках, пока он ещё у микрофона. Только по тому,
            # что реально сохранилось — файлу, упёршемуся в переполненный диск, ставить
            # распознавание не на что.
            chapters = {
                chapter.id
                for item in prepared
                if item["kind"] == "take" and item["index"] not in failed_indexes
                for chapter in [find_chapter_for_take(db, SimpleNamespace(
                    chapter=item["chapter"], book_code=item["book_code"]
                ))]
                if chapter is not None
            }
            db.commit()

        for chapter_id in chapters:
            # Постановка в очередь идёт последней, уже за пределами транзакции: файлы
            # сохранены и закоммичены. Упавший Redis не должен превращать этот ответ в
            # 500 — диктор увидел бы «файлы не загружены» про файлы, которые лежат на
            # месте, и переслал бы гигабайт заново. Распознавание же перезапускается
            # дёшево и по кнопке.
            try:
                enqueue_asr_for_chapter(chapter_id)
                # Событие «глава собралась» в системе не хранится — статус считается на
                # чтении. Значит спрашиваем здесь же, где уже спрашивали про ASR.
                with session_local() as check:
                    if chapter_needs_verification(check, chapter_id):
                        enqueue_verify_for_chapter(chapter_id)
                # Файл проекта на NAS сюда НЕ ставится: §4 запрещает NFS в пути приёма —
                # мёртвое `hard`-монтирование не отдаёт ошибку, а виснет навсегда, и
                # `except Exception` ниже от зависания не спасает (это не исключение).
                # Кроме фонового цикла `mirror_loop` (`app.workers.mirror`), который уже
                # спрашивает сторожа NAS и уже пишет туда вне запроса, класть `.sesx`
                # умеет и синхронная ручка `POST /api/v2/chapters/{id}/session-archive`
                # (`chapter_delivery.archive_chapter_session`) — но не путь приёма файла.
            except Exception:
                logger.exception("Не удалось поставить задания по главе %s после загрузки", chapter_id)

        if disk_errors:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "disk_full",
                    "message": DISK_FULL_MESSAGE,
                    "items": disk_errors,
                    "saved_count": len(saved),
                    "saved": saved,
                },
                status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            )
        return JSONResponse({"ok": True, "saved_count": len(saved), "saved": saved})

    def recording_filename_hint(request: Request):
        """Точное имя, которым диктору назвать этот файл.

        Не образец, а именно то имя, которого ждут от файла при нынешнем выборе: книга,
        глава, роль и актёр форме уже известны. Без роли не показывается ничего —
        половина имени хуже, чем ничего: её скопируют и решат, что так и надо.
        """
        access_error = require_recording_access(request)
        if access_error:
            return access_error
        params = request.query_params
        role = str(params.get("role") or "").strip()
        if not role:
            return JSONResponse({"ok": True, "filename": "", "kind": "take"})
        actor_name = str(params.get("actor_name") or "").strip()
        with session_local() as db:
            kind = classify_upload_kind(db, role=role, actor_name=actor_name)
        return JSONResponse({
            "ok": True,
            "kind": kind,
            "filename": build_canonical_audio_filename(
                str(params.get("book_code") or ""),
                str(params.get("chapter") or ""),
                role,
                actor_name,
                str(params.get("ext") or "имя.wav"),
                kind=kind,
            ),
        })

    async def recording_workspace(request: Request):
        access_error = require_recording_access(request)
        if access_error:
            return access_error
        payload = session_payload(request)
        uid = str(payload.get("uid") or "")
        user_id, user_name = recording_identity(
            request,
            str(payload.get("display_name") or payload.get("sub") or ""),
        )
        with session_local() as db:
            return JSONResponse(recording_workspace_payload(
                db,
                book_id=(request.query_params.get("book_id") or "").strip(),
                chapter_id=(request.query_params.get("chapter_id") or "").strip(),
                user_id=user_id or uid,
                user_name=user_name,
            ))

    async def recording_delete_audio(request: Request, audio_id: str):
        """Стереть загруженную запись — кнопка «удалить» в разделе записи.

        «Можно всё» — НЕ тот же признак, каким `actor_for` выше решает, кому можно
        подписать чужим именем, и разница намеренная: `actor_for` пускает ещё и агента
        (`is_agent(request)`), а `privileged` здесь — только `has_any_role(request,
        {"admin", "author"})`. Агент подписывает чужим именем то, что грузит за актёра,
        но ничего не удаляет: удаление чужой записи — решение владельца или автора, не
        кастинга. Диктору — только своя запись и только в первые сутки, это уже решает
        `delete_audio_file`.

        Ручка не коммитит вовсе: оба коммита (журнал; строка + задания + поручение
        зеркалу) — внутри `delete_audio_file`, и только после них она стирает файл.
        К моменту возврата всё уже согласовано и в базе, и на диске — здесь остаётся
        только собрать ответ по готовому результату.
        """
        access_error = require_recording_access(request)
        if access_error:
            return access_error
        privileged = has_any_role(request, {"admin", "author"})
        payload = session_payload(request)
        actor_name = str(payload.get("display_name") or "").strip()
        actor_uid = str(payload.get("uid") or "").strip()
        with session_local() as db:
            try:
                result = delete_audio_file(
                    db, audio_id, actor_uid=actor_uid, actor_name=actor_name,
                    privileged=privileged, is_agent=is_agent(request),
                )
            except AudioDeleteError as exc:
                error_status, message = AUDIO_DELETE_ERRORS.get(
                    exc.code, (status.HTTP_400_BAD_REQUEST, exc.code)
                )
                return JSONResponse(
                    {"ok": False, "error": exc.code, "message": message}, status_code=error_status,
                )
        return JSONResponse({"ok": True, **result})

    return {
        "recording_replica_patch": recording_replica_patch,
        "recording_batch": recording_batch,
        "recording_workspace": recording_workspace,
        "recording_filename_hint": recording_filename_hint,
        "recording_delete_audio": recording_delete_audio,
    }
