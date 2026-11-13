"""Синтетическая книга: отдельный приём, оплаченный кэш и монтаж без потери дублей."""
from contextlib import contextmanager
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import uuid
import wave

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AsrJob, AudioFile, BotUpload, ScriptBook, ScriptChapter, TelegramAuthAccount, User
from app.services import asr_run, audio_storage, chapter_title_intake as intake
from app.services.chapter_titles import TITLE_ROLE, chapter_targets, configure
from app.services.audio_uploads import derive_book_code
from app.services.shared_runtime import NARRATOR_DISPLAY_NAME
from app.v2.models import V2Attribution, V2Segment
from tests.asr_studio import heard

NOW = datetime(2026, 1, 1)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_storage.settings, 'audio_storage_path', str(tmp_path / 'audio'))
    monkeypatch.setattr(audio_storage.settings, 'local_reserve_gb', 0)
    engine = create_engine(f'sqlite:///{tmp_path / "titles.db"}', connect_args={'check_same_thread': False, 'timeout': 5})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        book = ScriptBook(id='book', title='Forest Signals', language='en', source_filename='sample.txt',
            source_format='txt', chapter_title_actor_name='Test Voice', role_identity_mode='typed_v1')
        db.add(book)
        db.add(User(id='actor', login='actor', password_hash='test', display_name='Test Voice'))
        db.add(TelegramAuthAccount(telegram_user_id='123', role='dictor', user_id='actor', display_name='Test Voice'))
        for index, heading in [(1, 'Chapter 1 Red Harbor'), (2, 'Chapter 2 Blue Field')]:
            cid = f'chapter{index}'
            db.add(ScriptChapter(id=cid, book_id='book', chapter_index=index, chapter_title=heading, status='published'))
            for ordinal, text in enumerate([heading, 'The silent river glows.']):
                sid = f'{cid}:{ordinal}'
                db.add(V2Segment(id=sid, book_id='book', chapter_id=cid, ordinal=ordinal,
                                 kind='heading' if ordinal == 0 else 'paragraph', text=text))
                db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=sid, speaker=NARRATOR_DISPLAY_NAME,
                    role_ref='narrator:system', span_start=0, span_end=len(text), source='operator'))
        db.commit()
        yield db, book


def info(book, *, filename=None, unique='unique'):
    return {'file_id': 'file', 'file_unique_id': unique, 'kind': 'document', 'mime_type': 'audio/wav',
            'file_name': filename or f'{derive_book_code(book.title)}_Главы_TestVoice.wav', 'file_size': 100, 'duration': 30}


def register(db, book, **kw):
    result = intake.receive(db, tid='123', chat_id='123', user=None, info=info(book, **kw), now=NOW)
    db.commit()
    enqueue = [item for item in result if item.method == 'enqueue_bot_take']
    return enqueue[0].payload['id'] if enqueue else None


def wav(path):
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b'\x00\x00' * 30 * 8000)
    return path


def run(db, rowid, tmp_path, transcript, *, transcribe=None):
    source = wav(tmp_path / 'input.wav')
    @contextmanager
    def fetch(_):
        with source.open('rb') as handle:
            yield handle
    return intake.process(db, rowid, fetch=fetch, transcribe_part=transcribe or (lambda _: transcript),
        send_text=lambda *args: True, notify_owner=lambda *args: True,
        workdir=str(tmp_path / 'spool'), now=NOW)


def test_headings_project_to_separate_role_without_mutating_attribution(studio):
    from app.v2.cast_ops import chapter_role_counts
    from app.v2.role_view import role_script
    db, book = studio
    assert asr_run.expected_lines(db, 'chapter1', NARRATOR_DISPLAY_NAME) == ['The silent river glows.']
    assert asr_run.expected_lines(db, 'chapter1', TITLE_ROLE) == ['Chapter 1 Red Harbor']
    assert chapter_role_counts(db, 'chapter1') == {TITLE_ROLE: 1, NARRATOR_DISPLAY_NAME: 1}
    assert role_script(db, book.id, NARRATOR_DISPLAY_NAME)['counts']['lines'] == 2
    assert role_script(db, book.id, TITLE_ROLE)['counts']['lines'] == 2
    assert all(row.speaker == NARRATOR_DISPLAY_NAME for row in db.query(V2Attribution))
    book.chapter_title_actor_name = ''
    db.flush()
    assert len(asr_run.expected_lines(db, 'chapter1', NARRATOR_DISPLAY_NAME)) == 2


def test_reserved_bad_filename_never_falls_back_to_ordinary_take(studio):
    from app.services.bot_takes import receive
    db, book = studio
    response = receive(db, tid='123', chat_id='123', user=db.get(User, 'actor'),
        info=info(book, filename='FS_Главы_TestVoice.mp3'), now=NOW)
    assert response and not db.query(BotUpload).count()


def test_sender_and_surname_must_be_unambiguous(studio):
    db, book = studio
    filename = f'{derive_book_code(book.title)}_Главы_Voice.wav'
    assert intake.resolve(db, filename, '123')[0].id == book.id
    with pytest.raises(ValueError, match='sender_forbidden'):
        intake.resolve(db, filename, '999')
    db.add(User(id='other', login='other', password_hash='test', display_name='Another Voice'))
    db.commit()
    with pytest.raises(ValueError, match='ambiguous_title_surname'):
        intake.resolve(db, filename, '123')


def test_a_b_a_all_takes_survive_and_body_starts_after_title_lane(studio, tmp_path):
    from app.services.chapter_delivery import _script_layout
    from app.services.audio_integrity import chapter_is_ready
    db, book = studio
    rowid = register(db, book)
    transcript = heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field', 'Chapter 1 Red Harbor')('')
    calls = []
    assert run(db, rowid, tmp_path, transcript, transcribe=lambda _: calls.append(1) or transcript) == 'done'
    assert len(calls) == 1
    assert db.query(AudioFile).count() == 2
    assert [len(json.loads(job.alignment_json)['lines'][0]['takes']) for job in db.query(AsrJob).order_by(AsrJob.chapter_id)] == [2, 1]
    assert not chapter_is_ready(db, 'chapter1')
    layout = _script_layout(db, 'chapter1')
    clips = [clip for role, clip in layout[3] if role == TITLE_ROLE]
    assert len(clips) == 2 and clips[1].start >= clips[0].start + clips[0].length
    body_start = layout[4].get('chapter1:1')
    assert body_start is None or body_start >= clips[-1].start + clips[-1].length
    db.get(BotUpload, rowid).state = 'queued'
    db.commit()
    assert run(db, rowid, tmp_path, transcript, transcribe=lambda _: pytest.fail('paid twice')) == 'done'
    assert db.query(AudioFile).count() == 2
    from app.services.chapter_delivery import build_chapter_session
    xml = build_chapter_session(db, 'chapter1', include_audit=False)
    assert TITLE_ROLE in xml


def test_unknown_payment_blocks_paid_retry_and_keeps_source(studio, tmp_path):
    db, book = studio
    rowid = register(db, book)
    count = []
    def timeout(_):
        count.append(1)
        raise TimeoutError('unknown payment')
    assert run(db, rowid, tmp_path, {}, transcribe=timeout) == 'failed'
    db.get(BotUpload, rowid).state = 'queued'
    db.commit()
    assert run(db, rowid, tmp_path, {}, transcribe=timeout) == 'failed'
    assert len(count) == 1
    assert (tmp_path / 'spool' / 'source.wav').exists()
    assert list((tmp_path / 'spool').glob('*.pending'))


def test_failed_checkpoint_replay_uses_paid_cache(studio, tmp_path, monkeypatch):
    from app.services import audio_uploads
    db, book = studio
    rowid = register(db, book)
    transcript = heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field')('')
    original = audio_uploads.store_audio_file
    calls = []
    def store(*args, **kwargs):
        if len(calls) == 1:
            raise OSError('simulated disk failure')
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(audio_uploads, 'store_audio_file', store)
    assert run(db, rowid, tmp_path, transcript) == 'failed'
    assert db.query(AudioFile).count() == 1
    monkeypatch.setattr(audio_uploads, 'store_audio_file', original)
    db.get(BotUpload, rowid).state = 'queued'
    db.commit()
    assert run(db, rowid, tmp_path, transcript, transcribe=lambda _: pytest.fail('paid twice')) == 'done'
    assert db.query(AudioFile).count() == 2


def test_missing_title_keeps_distinct_missing_status(studio, tmp_path):
    from app.services.chapter_delivery import chapter_recording_status
    db, book = studio
    rowid = register(db, book)
    assert run(db, rowid, tmp_path, heard('Chapter 1 Red Harbor')('')) == 'unsorted'
    assert chapter_recording_status(db, 'chapter2')['title_missing']
    assert not chapter_recording_status(db, 'chapter2')['ready']


def test_free_configuration_replays_without_notices(studio, tmp_path, monkeypatch):
    db, book = studio
    rowid = register(db, book)
    assert run(db, rowid, tmp_path, heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field')('')) == 'done'
    monkeypatch.setattr(asr_run, 'notify_take_missing', lambda *args: pytest.fail('replay notice'))
    assert not configure(db, book.id, 'Test Voice', apply=False)['apply']
    assert configure(db, book.id, 'Test Voice', apply=True)['apply']


def test_migration_is_idempotent_and_keeps_default_off():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    engine = create_engine('sqlite:///:memory:')
    path = Path(__file__).parents[1] / 'alembic/versions/0068_chapter_titles.py'
    spec = importlib.util.spec_from_file_location('title_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE script_books (id TEXT PRIMARY KEY)')
        conn.exec_driver_sql("INSERT INTO script_books VALUES ('old')")
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        migration.upgrade()
        assert conn.exec_driver_sql('SELECT chapter_title_actor_name FROM script_books').scalar() == ''


def test_title_coverage_is_recognized_and_all_headings_share_complete_takes(studio, tmp_path):
    from app.services.chapter_delivery import chapter_recording_status, _script_layout
    from app.v2.cast_ops import book_cast
    from app.v2.reader import build_chapter_payload
    db, book = studio
    heading = db.get(V2Segment, 'chapter1:0')
    heading.text = 'Chapter 1'
    db.get(V2Segment, 'chapter1:1').ordinal = 2
    db.flush()
    db.add(V2Segment(id='chapter1:title', book_id=book.id, chapter_id='chapter1', ordinal=1, kind='heading', text='Red Harbor'))
    # Все заголовки входят в одну ожидаемую фразу даже при нескольких сегментах.
    db.commit()
    rowid = register(db, book)
    transcript = heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field', 'Chapter 1 Red Harbor')('')
    assert run(db, rowid, tmp_path, transcript) == 'done'
    status = chapter_recording_status(db, 'chapter1')
    title = next(row for row in status['roles'] if row['role'] == TITLE_ROLE)
    assert title['matched_lines'] == title['lines'] == 2
    assert title['asr_coverage'] == 1 and not status['title_missing']
    clips = [clip for role, clip in _script_layout(db, 'chapter1')[3] if role == TITLE_ROLE]
    assert len(clips) == 2
    cast = book_cast(db, book.id)
    assert next(row for row in cast if row['name'] == TITLE_ROLE)['actor_name'] == 'Test Voice'
    payload = build_chapter_payload(db, 'chapter1', can_edit=False, can_voice=True)
    assert any(row['name'] == TITLE_ROLE for row in payload['cast'])


def test_ordinal_numbers_do_not_confuse_neighboring_russian_chapters():
    from app.services.chapter_title_match import match_title_candidates
    candidates = [{'chapter_id': str(n), 'chapter_index': n, 'lines': [f'Глава {n}']} for n in [2, 20, 21]]
    transcript = heard('Глава вторая', 'Глава двадцать первая', 'Глава двадцатая')('')
    result = match_title_candidates(candidates, transcript['segments'])
    assert [item['chapter_id'] for item in result['intervals']] == ['2', '21', '20']
    assert not result['unresolved'] and not result['missing']


def test_title_timestamp_outside_source_is_not_accepted(studio, tmp_path):
    db, book = studio
    rowid = register(db, book)
    transcript = heard('Chapter 1 Red Harbor')('')
    transcript['segments'][0]['words'][0]['end'] = 31
    assert run(db, rowid, tmp_path, transcript) == 'failed'
    assert not db.query(AudioFile).count()


def test_later_heading_stays_after_the_body_in_session(studio, tmp_path):
    from app.services.chapter_delivery import _place_by_script
    db, book = studio
    db.get(V2Segment, 'chapter1:0').text = 'Chapter 1'
    db.add(V2Segment(id='chapter1:2', book_id=book.id, chapter_id='chapter1', ordinal=2, kind='heading', text='Red Harbor'))
    db.commit()
    rowid = register(db, book)
    assert run(db, rowid, tmp_path, heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field')('')) == 'done'
    title_audio = db.query(AudioFile).filter(AudioFile.chapter.like('Chapter 1%')).one()
    title_lines = json.loads(db.query(AsrJob).filter_by(audio_file_id=title_audio.id).one().alignment_json)['lines']
    body_audio = AudioFile(id='body', stored_key='body.wav', location='local', duration_seconds=3,
                           canonical_filename='body.wav')
    replicas = asr_run.chapter_replicas(db, 'chapter1')
    placed = _place_by_script(replicas, {TITLE_ROLE: [title_audio], NARRATOR_DISPLAY_NAME: [body_audio]},
        {title_audio.id: title_lines, 'body': [{'matched': True, 'start': 0, 'end': 2, 'takes': [{'start': 0, 'end': 2}]}]}, relative=False)
    assert [role for role, clip in placed] == [TITLE_ROLE, NARRATOR_DISPLAY_NAME, TITLE_ROLE]
    assert placed[2][1].start >= placed[1][1].start + placed[1][1].length


def test_resubmit_resumes_failed_checkpoint_without_new_row_or_payment(studio, tmp_path, monkeypatch):
    from app.services import audio_uploads
    db, book = studio
    rowid = register(db, book)
    transcript = heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field')('')
    original = audio_uploads.store_audio_file
    stored = []
    def interrupted_store(*args, **kwargs):
        if stored:
            raise OSError('simulated disk failure')
        stored.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(audio_uploads, 'store_audio_file', interrupted_store)
    assert run(db, rowid, tmp_path, transcript) == 'failed'
    before = json.loads(db.get(BotUpload, rowid).result_json)
    assert len(before['stored']) == 1
    assert register(db, book) == rowid
    assert register(db, book) is None
    assert db.query(BotUpload).count() == 1
    assert json.loads(db.get(BotUpload, rowid).result_json)['stored'] == before['stored']
    monkeypatch.setattr(audio_uploads, 'store_audio_file', original)
    assert run(db, rowid, tmp_path, transcript, transcribe=lambda _: pytest.fail('paid twice')) == 'done'
    assert db.query(AudioFile).count() == 2


def test_resubmit_unsorted_file_reuses_checkpoint_without_payment(studio, tmp_path):
    db, book = studio
    rowid = register(db, book)
    transcript = heard('Chapter 1 Red Harbor')('')
    assert run(db, rowid, tmp_path, transcript) == 'unsorted'
    assert register(db, book) == rowid
    assert run(db, rowid, tmp_path, transcript, transcribe=lambda _: pytest.fail('paid twice')) == 'unsorted'
    assert db.query(BotUpload).count() == 1
    assert db.query(AudioFile).count() == 1


def test_resubmit_unknown_payment_still_blocks_recharge(studio, tmp_path):
    db, book = studio
    rowid = register(db, book)
    calls = []
    def timeout(_):
        calls.append(True)
        raise TimeoutError('unknown payment')
    assert run(db, rowid, tmp_path, {}, transcribe=timeout) == 'failed'
    assert register(db, book) == rowid
    assert run(db, rowid, tmp_path, {}, transcribe=timeout) == 'failed'
    assert len(calls) == 1
    assert db.query(BotUpload).count() == 1


@pytest.mark.parametrize('change', ['actor', 'targets', 'provider', 'language'])
def test_resubmit_rejects_stale_identity_or_recognition_snapshot(studio, tmp_path, monkeypatch, change):
    db, book = studio
    rowid = register(db, book)
    def failure(_):
        raise TimeoutError('unknown payment')
    assert run(db, rowid, tmp_path, {}, transcribe=failure) == 'failed'
    filename = None
    if change == 'actor':
        book.chapter_title_actor_name = 'Other Voice'
        db.add(User(id='new-actor', login='new-actor', password_hash='test', display_name='Other Voice'))
        db.query(TelegramAuthAccount).filter_by(telegram_user_id='123').one().user_id = 'new-actor'
        filename = f'{derive_book_code(book.title)}_Главы_OtherVoice.wav'
    elif change == 'targets':
        db.get(V2Segment, 'chapter1:0').text = 'Chapter 1 Changed Heading'
    elif change == 'provider':
        monkeypatch.setattr('app.services.step_models.step_model', lambda _: ('different-provider', 'different-model'))
    else:
        book.language = 'ru'
    db.commit()
    response = intake.receive(db, tid='123', chat_id='123', user=None, info=info(book, filename=filename), now=NOW)
    db.commit()
    assert not any(item.method == 'enqueue_bot_take' for item in response)
    assert db.get(BotUpload, rowid).state == 'failed'
    assert db.query(BotUpload).count() == 1


@pytest.mark.parametrize('state', ['queued', 'processing', 'done'])
def test_resubmit_active_or_completed_upload_does_not_enqueue(studio, state):
    db, book = studio
    rowid = register(db, book)
    db.get(BotUpload, rowid).state = state
    db.commit()
    assert register(db, book) is None
    assert db.query(BotUpload).count() == 1
    assert db.get(BotUpload, rowid).state == state


@pytest.mark.parametrize('state', ['queued', 'processing'])
def test_stale_queue_or_killed_processing_is_reclaimed(studio, tmp_path, state):
    from datetime import timedelta
    from app.services.bot_takes import STALE_AFTER
    db, book = studio
    rowid = register(db, book)
    row = db.get(BotUpload, rowid)
    before = json.loads(row.result_json)
    db.query(BotUpload).filter_by(id=rowid).update({'state': state, 'updated_at': NOW}, synchronize_session=False)
    db.commit()
    response = intake.receive(db, tid='123', chat_id='123', user=None, info=info(book), now=NOW + STALE_AFTER + timedelta(seconds=1))
    db.commit()
    assert [item.payload['id'] for item in response if item.method == 'enqueue_bot_take'] == [rowid]
    after = json.loads(db.get(BotUpload, rowid).result_json)
    assert all(after[key] == value for key, value in before.items())
    assert after['lease_token']
    assert run(db, rowid, tmp_path, heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field')('')) == 'done'


def _start_title_worker(db, rowid, tmp_path, transcribe, *, clock, interval=30):
    import threading
    factory = sessionmaker(bind=db.get_bind(), autoflush=False)
    source = wav(tmp_path / 'worker-input.wav')
    @contextmanager
    def fetch(_):
        with source.open('rb') as handle:
            yield handle
    results, errors = [], []
    def worker():
        try:
            with factory() as session:
                results.append(intake.process(session, rowid, fetch=fetch, transcribe_part=transcribe,
                    send_text=lambda *args: True, notify_owner=lambda *args: True,
                    workdir=str(tmp_path / 'spool'), clock=clock, session_factory=factory,
                    heartbeat_interval=interval))
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, results, errors, factory


def test_heartbeat_keeps_long_paid_request_active(studio, tmp_path):
    import threading
    import time
    from datetime import timedelta
    from app.services.bot_takes import STALE_AFTER
    db, book = studio
    rowid = register(db, book)
    started, release = threading.Event(), threading.Event()
    current = [NOW]
    transcript = heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field')('')
    def transcribe(_):
        started.set()
        assert release.wait(5)
        return transcript
    thread, results, errors, factory = _start_title_worker(db, rowid, tmp_path, transcribe,
                                                          clock=lambda: current[0], interval=.01)
    try:
        assert started.wait(5)
        current[0] = NOW + STALE_AFTER * 2
        deadline = time.monotonic() + 3
        observed = None
        while time.monotonic() < deadline:
            with factory() as session:
                observed = session.get(BotUpload, rowid).updated_at
            if observed == current[0]:
                break
            time.sleep(.01)
        assert observed == current[0]
        with factory() as session:
            response = intake.receive(session, tid='123', chat_id='123', user=None, info=info(book),
                                      now=current[0] + timedelta(seconds=1))
            session.commit()
            assert not any(item.method == 'enqueue_bot_take' for item in response)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors and results == ['done']


def test_two_stale_resends_have_one_atomic_winner(studio):
    import threading
    from datetime import timedelta
    from app.services.bot_takes import STALE_AFTER
    db, book = studio
    rowid = register(db, book)
    payload = info(book)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False)
    gate = threading.Barrier(2)
    outcomes, errors = [], []
    def resend():
        try:
            with factory() as session:
                gate.wait(3)
                result = intake.receive(session, tid='123', chat_id='123', user=None, info=payload,
                                        now=NOW + STALE_AFTER + timedelta(seconds=1))
                session.commit()
                outcomes.extend(item.payload['id'] for item in result if item.method == 'enqueue_bot_take')
        except BaseException as exc:
            errors.append(exc)
    threads = [threading.Thread(target=resend) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not errors and not any(thread.is_alive() for thread in threads)
    assert outcomes == [rowid]
    db.expire_all()
    assert db.query(BotUpload).count() == 1 and db.get(BotUpload, rowid).state == 'queued'


def test_old_worker_cannot_store_after_stale_lease_reclaimed(studio, tmp_path):
    import threading
    from datetime import timedelta
    from app.services.bot_takes import STALE_AFTER
    db, book = studio
    rowid = register(db, book)
    started, release = threading.Event(), threading.Event()
    transcript = heard('Chapter 1 Red Harbor', 'Chapter 2 Blue Field')('')
    calls = []
    def transcribe(_):
        calls.append(True)
        started.set()
        assert release.wait(5)
        return transcript
    thread, results, errors, factory = _start_title_worker(db, rowid, tmp_path, transcribe,
                                                          clock=lambda: NOW, interval=3600)
    try:
        assert started.wait(5)
        with factory() as session:
            old_meta = json.loads(session.get(BotUpload, rowid).result_json)
            response = intake.receive(session, tid='123', chat_id='123', user=None, info=info(book),
                                      now=NOW + STALE_AFTER + timedelta(seconds=1))
            session.commit()
            assert any(item.method == 'enqueue_bot_take' for item in response)
            new_meta = json.loads(session.get(BotUpload, rowid).result_json)
            assert new_meta['lease_token'] != old_meta['lease_token']
    finally:
        release.set()
        thread.join(5)
    assert not errors and results == ['skip'] and not thread.is_alive()
    db.expire_all()
    assert db.get(BotUpload, rowid).state == 'queued'
    assert not db.query(AudioFile).count()
    assert (tmp_path / 'spool' / 'source.wav').exists()
    assert list((tmp_path / 'spool').glob('paid-*.json'))
    assert run(db, rowid, tmp_path, transcript, transcribe=lambda _: pytest.fail('paid twice')) == 'done'
    assert len(calls) == 1 and db.query(AudioFile).count() == 2


def test_lost_lease_before_paid_request_stops_without_charge(studio, tmp_path, monkeypatch):
    import threading
    from datetime import timedelta
    from app.services.bot_takes import STALE_AFTER
    from app.services import audio_probe
    db, book = studio
    rowid = register(db, book)
    started, release = threading.Event(), threading.Event()
    original_probe = audio_probe.probe_audio_file
    def paused_probe(path):
        started.set()
        assert release.wait(5)
        return original_probe(path)
    monkeypatch.setattr(audio_probe, 'probe_audio_file', paused_probe)
    thread, results, errors, factory = _start_title_worker(db, rowid, tmp_path,
        lambda _: pytest.fail('lost lease paid for ASR'), clock=lambda: NOW, interval=3600)
    try:
        assert started.wait(5)
        with factory() as session:
            response = intake.receive(session, tid='123', chat_id='123', user=None, info=info(book),
                                      now=NOW + STALE_AFTER + timedelta(seconds=1))
            session.commit()
            assert any(item.method == 'enqueue_bot_take' for item in response)
    finally:
        release.set()
        thread.join(5)
    assert not errors and results == ['skip'] and not thread.is_alive()
    assert not list((tmp_path / 'spool').glob('*.pending'))
    db.expire_all()
    assert not db.query(AudioFile).count() and db.get(BotUpload, rowid).state == 'queued'


def test_abrupt_worker_exit_is_reclaimed_without_repeating_unknown_payment(studio, tmp_path):
    import subprocess
    import sys
    from datetime import timedelta
    from app.services.bot_takes import STALE_AFTER
    db, book = studio
    rowid = register(db, book)
    source = wav(tmp_path / 'abrupt-input.wav')
    script = '''
import os, sys
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.services.chapter_title_intake import process
@contextmanager
def fetch(_):
    with open(sys.argv[3], 'rb') as handle:
        yield handle
with sessionmaker(bind=create_engine(sys.argv[1]))() as db:
    process(db, sys.argv[2], fetch=fetch, transcribe_part=lambda _: os._exit(23),
            send_text=lambda *args: True, notify_owner=lambda *args: True, workdir=sys.argv[4])
'''
    child = subprocess.run([sys.executable, '-c', script, str(db.get_bind().url), rowid,
                            str(source), str(tmp_path / 'spool')], capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr.decode()[-500:]
    db.expire_all()
    row = db.get(BotUpload, rowid)
    assert row.state == 'processing'
    assert list((tmp_path / 'spool').glob('*.pending'))
    response = intake.receive(db, tid='123', chat_id='123', user=None, info=info(book),
                              now=row.updated_at + STALE_AFTER + timedelta(seconds=1))
    db.commit()
    assert any(item.method == 'enqueue_bot_take' for item in response)
    assert run(db, rowid, tmp_path, {}, transcribe=lambda _: pytest.fail('paid after abrupt exit')) == 'failed'
    assert (tmp_path / 'spool' / 'source.wav').exists()
    assert list((tmp_path / 'spool').glob('*.pending'))
    assert not db.query(AudioFile).count()


@pytest.mark.parametrize('state', ['queued', 'processing'])
def test_stale_assignment_change_does_not_reclaim_old_upload(studio, state):
    from datetime import timedelta
    from app.services.bot_takes import STALE_AFTER
    db, book = studio
    rowid = register(db, book)
    db.query(BotUpload).filter_by(id=rowid).update({'state': state, 'updated_at': NOW}, synchronize_session=False)
    db.get(V2Segment, 'chapter1:0').text = 'Chapter 1 Changed Heading'
    db.commit()
    response = intake.receive(db, tid='123', chat_id='123', user=None, info=info(book),
                              now=NOW + STALE_AFTER + timedelta(seconds=1))
    db.commit()
    assert not any(item.method == 'enqueue_bot_take' for item in response)
    assert db.get(BotUpload, rowid).state == state
