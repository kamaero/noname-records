"""Именованная запись заголовков хранит исходник и оплаченные ответы до разбора."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path

from app.models import AsrJob, AudioFile, BotUpload, ScriptBook, TelegramAuthAccount, User
from app.services.audio_naming import book_token, latin_word
from app.services.audio_uploads import derive_book_code
from app.services.chapter_titles import TITLE_ROLE, chapter_targets, title_actor


def _words(value):
    return tuple(sorted(latin_word(word).lower() for word in re.findall(r'[^\W_]+', str(value or '')) if latin_word(word)))


def reserved(filename):
    fields = str(filename or '').split('_')
    return any(latin_word(field).lower().startswith('glavy') for field in fields[1:])


def resolve(db, filename, tid):
    """Код и фамилия лишь выбирают назначение; право даёт активная учётка отправителя."""
    if (not isinstance(filename, str) or len(filename) > 255 or any(c in filename for c in '/\\\x00')
            or any(ord(c) < 32 for c in filename)):
        raise ValueError('invalid_title_filename')
    match = re.fullmatch(r'([^_]+)_(?:Главы|Glavy)_([^_]+)\.(?:wav|flac)', filename, re.IGNORECASE)
    if not match or any(v != v.strip() or not v for v in (match[1], match[2])):
        raise ValueError('Expected BookCode_Главы_Actor.wav or .flac')
    books = [b for b in db.query(ScriptBook).all() if book_token(match[1]) in {
        book_token(b.title), book_token(derive_book_code(b.title))}]
    if len(books) != 1:
        raise ValueError('ambiguous_title_book')
    book = books[0]
    actor = title_actor(book)
    if not actor or book.voiced_at is not None:
        raise ValueError('title_assignment_unavailable')
    users = db.query(User).filter(User.is_active == 'true').all()
    approved = [u for u in users if _words(u.display_name) == _words(actor)]
    if len(approved) != 1:
        raise ValueError('ambiguous_title_actor')
    account = db.query(TelegramAuthAccount).filter(
        TelegramAuthAccount.telegram_user_id == str(tid), TelegramAuthAccount.is_active == 'true').one_or_none()
    sender = db.get(User, account.user_id) if account is not None else None
    if (sender is None or sender.is_active != 'true'
            or (str(account.user_id) != str(approved[0].id) and account.role not in {'admin', 'agent'})):
        raise ValueError('title_sender_forbidden')
    wanted = _words(match[2])
    if wanted != _words(actor) and latin_word(match[2]).lower() != latin_word(actor).lower():
        if len(wanted) != 1 or not set(wanted).issubset(_words(actor)):
            raise ValueError('title_actor_not_assigned')
        if [u.id for u in users if set(wanted).issubset(_words(u.display_name))] != [approved[0].id]:
            raise ValueError('ambiguous_title_surname')
    targets = chapter_targets(db, book.id)
    if not targets:
        raise ValueError('title_targets_missing')
    return book, actor, targets


def receive(db, *, tid, chat_id, user, info, now):
    from app.services.bot_out import Outgoing, send
    from app.services.telegram import max_download_bytes

    if not reserved(info.get('file_name')):
        return None
    try:
        book, actor, targets = resolve(db, info.get('file_name'), tid)
        if int(info.get('file_size') or 0) > max_download_bytes():
            raise ValueError('title_file_too_large')
    except ValueError as exc:
        return [send(chat_id, f'Chapter title recording was not accepted: {exc}.')]
    unique = str(info.get('file_unique_id') or '')
    earlier = db.query(BotUpload).filter(BotUpload.purpose == 'titles', BotUpload.book_id == book.id,
        BotUpload.telegram_user_id == str(tid), BotUpload.file_unique_id == unique).first() if unique else None
    from app.services.step_models import step_model
    provider, model = step_model('asr')
    if earlier is not None:
        from app.services.bot_takes import STALE_AFTER
        stale = earlier.state in {'queued', 'processing'} and earlier.updated_at <= now - STALE_AFTER
        if earlier.state in {'failed', 'unsorted'} or stale:
            try:
                snapshot = json.loads(earlier.result_json or '{}')
            except (TypeError, ValueError):
                snapshot = {}
            if (not isinstance(snapshot, dict) or earlier.actor_name != actor
                    or snapshot.get('targets') != targets or snapshot.get('language') != book.language
                    or snapshot.get('provider') != provider or snapshot.get('model') != model):
                return [send(chat_id, 'This title recording needs studio review because its assignment or recognition settings changed.')]
            # Сравниваем и время, и снимок: heartbeat или другой повтор
            # отменяет захват. Новый token запрещает старому воркеру запись.
            original = earlier.result_json
            previous_at, previous_state = earlier.updated_at, earlier.state
            snapshot['lease_token'] = uuid.uuid4().hex
            resumed = db.query(BotUpload).filter(BotUpload.id == earlier.id,
                BotUpload.state == previous_state, BotUpload.updated_at == previous_at,
                BotUpload.result_json == original).update(
                    {'state': 'queued', 'updated_at': now,
                     'result_json': json.dumps(snapshot, ensure_ascii=False)}, synchronize_session=False)
            if resumed == 1:
                db.expire(earlier)
                return [send(chat_id, 'Title recording processing resumed using the saved source and recognition cache.'),
                        Outgoing('enqueue_bot_take', {'id': earlier.id})]
        return [send(chat_id, 'This title recording is already registered.')]
    row = BotUpload(telegram_user_id=str(tid), purpose='titles', book_id=book.id, actor_name=actor,
        role_name=TITLE_ROLE, state='queued', created_at=now, updated_at=now,
        result_json=json.dumps({'targets': targets, 'language': book.language, 'provider': provider, 'model': model}, ensure_ascii=False),
        **{key: info.get(key, '' if key not in {'file_size', 'duration'} else 0) for key in (
            'file_id', 'file_unique_id', 'kind', 'mime_type', 'file_name', 'file_size', 'duration')})
    db.add(row)
    db.flush()
    return [send(chat_id, 'Title recording received. All recognized takes will be kept for the studio.'),
            Outgoing('enqueue_bot_take', {'id': row.id})]


def _sync_directory(path):
    """Переименование и отметка оплаты должны пережить также сбой всей машины."""
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _save(path, value):
    """Атомарный ответ и fsync не дают сбою превратить уже оплаченную часть в новую."""
    path = Path(path)
    with path.with_suffix(path.suffix + '.tmp').open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(path.with_suffix(path.suffix + '.tmp'), path)
    _sync_directory(path.parent)


def paid_part(path, *, transcribe, workdir, guard=None):
    if guard:
        guard()
    with open(path, 'rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    cached = Path(workdir) / f'paid-{digest}.json'
    pending = cached.with_suffix('.pending')
    if cached.exists():
        return json.loads(cached.read_text(encoding='utf-8'))
    if pending.exists():
        raise ValueError('asr_payment_unknown_manual_review_required')
    # До сети: после таймаута или остановки повтор должен решать оператор.
    with pending.open('x') as handle:
        handle.write('started')
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(pending.parent)
    started = False
    try:
        if guard:
            guard()
        started = True
        result = transcribe(path)
    except Exception as exc:
        from app.services.asr_run import RETRY_ERRORS
        if not started or str(exc) in RETRY_ERRORS:
            pending.unlink(missing_ok=True)
            _sync_directory(pending.parent)
        raise
    _save(cached, result)
    pending.unlink(missing_ok=True)
    _sync_directory(pending.parent)
    return result


def alignment(db, chapter_id, heard, *, language='ru'):
    from app.models import ScriptChapter
    from app.services.chapter_title_match import match_title_candidates

    chapter = db.get(ScriptChapter, chapter_id)
    targets = chapter_targets(db, chapter.book_id)
    target = next(item for item in targets if item['chapter_id'] == chapter_id)
    report = match_title_candidates(targets, heard.get('segments') or [], language=language)
    from app.services.asr_align import normalize_words, word_stream
    stream = word_stream(heard.get('segments') or [], language=language)
    takes_by_line = {index: [] for index in range(len(target['lines']))}
    for number, interval in enumerate(report['intervals']):
        if interval['chapter_id'] != chapter_id:
            continue
        words = [word for word in stream if word[1] >= interval['start'] and word[2] <= interval['end']]
        counts = [len(normalize_words(text, language=language)) for text in target['lines']]
        if sum(counts) != len(words):
            raise ValueError('title_word_binding_ambiguous')
        cursor = 0
        for index, count in enumerate(counts):
            selected = words[cursor:cursor + count]
            cursor += count
            if selected:
                takes_by_line[index].append({'start': selected[0][1], 'end': selected[-1][2],
                    'score': 1.0, 'title_group': number})
    lines = []
    for index, text in enumerate(target['lines']):
        takes = takes_by_line[index]
        lines.append({'text': text, 'matched': bool(takes), 'score': 1.0 if takes else 0.0,
            'start': takes[0]['start'] if takes else None, 'end': takes[-1]['end'] if takes else None,
            'takes': takes})
    matched = sum(bool(row['matched']) for row in lines)
    return {'lines': lines, 'total': len(lines), 'matched': matched,
            'coverage': matched / len(lines) if lines else 0,
            'missing': [row['text'] for row in lines if not row['matched']], 'language': language,
            'normalizer_version': 'chapter-title-exact-v1'}


class _LostLease(RuntimeError):
    """Возобновлённая задача уже принадлежит другому обработчику."""


class _TitleLease:
    def __init__(self, db, upload_id, token, clock, session_factory=None, interval=30):
        import threading
        from sqlalchemy.orm import sessionmaker

        self.db, self.upload_id, self.token, self.clock = db, upload_id, token, clock
        self.stop = threading.Event()
        self.lost = threading.Event()
        engine = db.get_bind()
        self.factory = session_factory or sessionmaker(bind=engine)
        # Обычная SQLite :memory: существует только в одном соединении.
        # Для конкурентных тестов передаётся отдельная file-backed фабрика.
        self.background = session_factory is not None or getattr(engine.url, 'database', None) not in {None, '', ':memory:'}
        self.interval, self.thread = interval, None

    def condition(self):
        from sqlalchemy import func
        return (BotUpload.id == self.upload_id, BotUpload.purpose == 'titles',
                BotUpload.state == 'processing', func.json_extract(BotUpload.result_json, '$.lease_token') == self.token)

    def touch(self, db=None, **values):
        if self.lost.is_set():
            raise _LostLease()
        session = db if db is not None else self.db
        values['updated_at'] = self.clock()
        count = session.query(BotUpload).filter(*self.condition()).update(values, synchronize_session=False)
        if count != 1:
            self.lost.set()
            raise _LostLease()

    def guard(self):
        self.touch()
        self.db.commit()

    def checkpoint(self, meta, **values):
        meta['lease_token'] = self.token
        self.touch(result_json=json.dumps(meta, ensure_ascii=False), **values)
        self.db.commit()

    def __enter__(self):
        import logging
        import threading

        def beat():
            while not self.stop.wait(self.interval):
                try:
                    with self.factory() as session:
                        self.touch(session)
                        session.commit()
                except _LostLease:
                    return
                except Exception:
                    # Короткая блокировка SQLite не убивает отметку навсегда.
                    # Перед сетью и записью основной поток проверит token сам.
                    logging.getLogger(__name__).warning('Title heartbeat could not update upload %s', self.upload_id)
        if self.background:
            self.thread = threading.Thread(target=beat, name='title-upload-heartbeat', daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)


def process(db, upload_id, *, fetch, transcribe_part, send_text, notify_owner, workdir, now=None,
            clock=None, language=None, session_factory=None, heartbeat_interval=30):
    from app.time_utils import utcnow_naive
    from app.services import asr_run, take_format
    from app.services.audio_probe import probe_audio_file
    from app.services.audio_uploads import store_audio_file
    from app.services.chapter_title_match import match_title_candidates
    from app.services.chapter_delivery import mark_session_outdated
    from app.services.shared_runtime import safe_name
    from app.services.take_split import transcribe_whole

    clock = clock or utcnow_naive
    now = now or clock()
    row = db.get(BotUpload, upload_id)
    if row is None or row.purpose != 'titles' or row.state != 'queued':
        return 'skip'
    original = row.result_json
    meta = json.loads(original or '{}')
    token = uuid.uuid4().hex
    meta['lease_token'] = token
    claimed = db.query(BotUpload).filter(BotUpload.id == upload_id, BotUpload.purpose == 'titles',
        BotUpload.state == 'queued', BotUpload.result_json == original).update(
            {'state': 'processing', 'updated_at': now, 'result_json': json.dumps(meta, ensure_ascii=False)},
            synchronize_session=False)
    db.commit()
    if claimed != 1:
        return 'skip'
    with _TitleLease(db, upload_id, token, clock, session_factory, heartbeat_interval) as lease:
        try:
            db.expire(row)
            directory = Path(workdir)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            source = directory / ('source' + Path(row.file_name).suffix.lower())
            book, actor, targets = resolve(db, row.file_name, row.telegram_user_id)
            if book.id != row.book_id or actor != row.actor_name or targets != meta.get('targets'):
                raise ValueError('title_assignment_changed_manual_review_required')
            language = asr_run.asr_language(meta.get('language') or language or book.language)
            lease.guard()
            if not source.exists():
                temporary = directory / f'source-{token}.download'
                size = 0
                from app.services.telegram import max_download_bytes
                with fetch(row.file_id) as handle, temporary.open('wb') as target:
                    while chunk := handle.read(1024 * 1024):
                        size += len(chunk)
                        if size > max_download_bytes():
                            raise ValueError('title_file_too_large')
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                # UPDATE держит право записи до commit: повтор не может вклиниться
                # между проверкой token и публикацией исходника на диске.
                lease.touch()
                if not source.exists():
                    os.replace(temporary, source)
                    _sync_directory(directory)
                db.commit()
            with source.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'md5').hexdigest()
            if row.md5 and row.md5 != digest:
                raise ValueError('title_source_changed')
            duplicate = db.query(BotUpload).filter(BotUpload.id != row.id, BotUpload.purpose == 'titles',
                BotUpload.book_id == book.id, BotUpload.telegram_user_id == row.telegram_user_id,
                BotUpload.md5 == digest).first()
            if duplicate:
                raise ValueError('title_content_already_registered')
            lease.touch(md5=digest)
            db.commit()
            probe = probe_audio_file(str(source))
            duration = float(probe.get('duration_seconds') or 0)
            if not math.isfinite(duration) or duration <= 0 or not take_format.allowed_audio(str(source), probe):
                raise ValueError('title_lossless_audio_required')
            lease.guard()
            heard_file = directory / 'heard.json'
            if heard_file.exists():
                heard = json.loads(heard_file.read_text(encoding='utf-8'))
            else:
                heard = transcribe_whole(str(source), workdir=str(directory), duration=duration,
                    transcribe_part=lambda path: paid_part(path, transcribe=transcribe_part,
                                                          workdir=directory, guard=lease.guard))
                # Ответ уже оплачен и сохранён paid_part даже после потери lease.
                # Общий план и главы публикует только действующий обработчик.
                lease.guard()
                _save(heard_file, heard)
            for segment in heard.get('segments') or []:
                if str(segment.get('text') or '').strip() and not segment.get('words'):
                    raise ValueError('title_word_times_required')
                for word in segment.get('words') or []:
                    start, end = float(word['start']), float(word['end'])
                    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
                        raise ValueError('title_timing_out_of_source')
            report = match_title_candidates(targets, heard.get('segments') or [], language=language)
            db.refresh(book)
            if title_actor(book) != row.actor_name or chapter_targets(db, book.id) != targets:
                raise ValueError('title_assignment_changed_manual_review_required')
            meta['recognition'] = report
            stored = meta.setdefault('stored', {})
            for target in targets:
                cid = target['chapter_id']
                if not any(item['chapter_id'] == cid for item in report['intervals']):
                    continue
                # SQLite сериализует этот UPDATE с отзывом lease в receive.
                # Сохранение файла, строки и checkpoint входят в ту же транзакцию.
                lease.touch()
                audio = db.get(AudioFile, stored[cid]) if cid in stored else None
                if cid in stored and (audio is None or audio.role_book_id != book.id or audio.role != TITLE_ROLE):
                    raise ValueError('title_checkpoint_scope_mismatch')
                if audio is None:
                    with source.open('rb') as handle:
                        audio = store_audio_file(db, source=handle, mime_type=row.mime_type, original_filename=row.file_name,
                            book_code=derive_book_code(book.title), chapter=target['chapter_title'] or f"Глава {target['chapter_index']}",
                            role=TITLE_ROLE, actor_name=actor, kind='take', safe_name=safe_name, size_hint=source.stat().st_size)
                    audio.role_book_id = book.id
                    from app.services.role_identity import TITLE_REF
                    audio.role_ref = TITLE_REF
                    stored[cid] = audio.id
                    lease.checkpoint(meta)
                lease.touch()
                job = db.query(AsrJob).filter(AsrJob.audio_file_id == audio.id).one_or_none() or AsrJob(audio_file_id=audio.id)
                job.chapter_id, job.expected_role, job.role_ref = cid, TITLE_ROLE, audio.role_ref
                job.expected_chapter_index, job.language, job.status = target['chapter_index'], language, 'done'
                job.provider, job.model = str(meta.get('provider') or ''), str(meta.get('model') or '')
                job.heard_json, job.transcript_text = json.dumps(heard, ensure_ascii=False), str(heard.get('text') or '')
                asr_run.store_alignment(job, alignment(db, cid, heard, language=language))
                db.add(job)
                mark_session_outdated(db, cid)
                lease.checkpoint(meta)
            state = 'done' if not report['unresolved'] and not report['missing'] else 'unsorted'
            lease.checkpoint(meta, state=state, audio_id=next(iter(stored.values()), ''))
            send_text(row.telegram_user_id, f"Chapter titles: {len(report['intervals'])} takes kept; {len(report['missing'])} headings missing; {len(report['unresolved'])} intervals need studio review.")
            notify_owner(f"Chapter title recording {row.id}: {state}.")
            return state
        except _LostLease:
            db.rollback()
            return 'skip'
        except BaseException as exc:
            db.rollback()
            meta['error'] = type(exc).__name__ + ': ' + str(exc)[:180]
            try:
                lease.checkpoint(meta, state='failed')
            except _LostLease:
                db.rollback()
                return 'skip'
            if isinstance(exc, Exception):
                notify_owner(f'Chapter title recording {upload_id} needs manual review; source and paid cache retained.')
                return 'failed'
            raise
