"""Одинаковая подпись главы не разрешает брать или оплачивать чужую запись."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import AsrJob, AudioFile, BotUpload, Character, ScriptBook, ScriptChapter
from app.services import asr_audit, asr_borrow, asr_replay, asr_run, bot_takes, chapter_delivery
from app.services.role_identity import NARRATOR_REF, RoleIdentityError, book_roles
from app.v2.models import V2Attribution, V2Segment
from tests.asr_studio import heard


@pytest.fixture()
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for bid, title, mode, language in [('legacy', 'Silver Window', 'legacy', 'ru'),
                                           ('typed', 'Silver River', 'typed_v1', 'en')]:
            db.add(ScriptBook(id=bid, title=title, source_filename='sample.txt', source_format='txt',
                              role_identity_mode=mode, language=language))
            db.add(ScriptChapter(id=bid+'-chapter', book_id=bid, chapter_index=1,
                                 chapter_title='Chapter 1', status='published'))
            db.add(Character(id=bid+'-narrator', book_id=bid, name='Рассказчик', actor_name='Synthetic Actor'))
            text = 'Медленно закрылась дверь' if language == 'ru' else 'The window closed silently'
            db.add(V2Segment(id=bid+'-segment', book_id=bid, chapter_id=bid+'-chapter', ordinal=0, text=text))
            db.add(V2Attribution(id=bid+'-attr', segment_id=bid+'-segment', version=1, speaker='Рассказчик',
                                 role_ref=NARRATOR_REF if mode=='typed_v1' else '', span_start=0, span_end=len(text)))
        db.commit()
        yield db
    engine.dispose()


def audio(db, id='foreign', *, owner='typed', code='SW', ref=NARRATOR_REF):
    row = AudioFile(id=id, book_code=code, chapter='Chapter 1', role='Рассказчик', role_ref=ref,
                    role_book_id=owner, actor_name='Synthetic Actor', kind='take', original_filename=id+'.wav',
                    canonical_filename=id+'.wav', stored_key=id+'.wav', mime_type='audio/wav', size_bytes=20,
                    uploaded_at=datetime(2026, 1, 1), duration_seconds=3)
    db.add(row); db.commit()
    return row


def no_paid(*a, **kw):
    pytest.fail('a foreign or ambiguous input reached a paid service')


def test_legacy_chapter_excludes_explicitly_foreign_audio_before_any_payment(db, monkeypatch):
    foreign = audio(db)
    monkeypatch.setattr(asr_run, '_transcribe_with_service', no_paid)
    result = asr_run.run_asr_for_chapter(db, 'legacy-chapter', transcribe=no_paid)
    assert result['takes'] == result['done'] == result['failed'] == 0
    assert db.query(AsrJob).count() == 0
    assert book_roles(db, 'legacy').owns_audio(foreign) is False
    assert book_roles(db, 'typed').owns_audio(foreign) is True
    assert asr_run.find_chapter_for_take(db, foreign).id == 'typed-chapter'
    status = chapter_delivery.chapter_recording_status(db, 'legacy-chapter')
    assert all(not role['files'] for role in status['roles'])
    progress = chapter_delivery.book_recording_status(db, 'legacy')
    assert progress['chapters'][0]['recorded_roles'] == 0
    assert asr_audit.latest_narrator_take(db, db.get(ScriptChapter, 'legacy-chapter')) is None
    audit = asr_audit.run_asr_audit(db, 'legacy-chapter', transcribe=no_paid, judge=no_paid)
    assert audit.status != 'done' and audit.stt_cost_rub == 0


def test_legacy_unbound_audio_requires_unique_matching_book_code(db):
    local = audio(db, 'local', owner='', ref='')
    assert book_roles(db, 'legacy').owns_audio(local)
    assert not book_roles(db, 'typed').owns_audio(local)
    assert asr_run.find_chapter_for_take(db, local).id == 'legacy-chapter'
    db.get(ScriptBook, 'typed').title = 'Silver Water'
    db.commit()
    assert not book_roles(db, 'legacy').owns_audio(local)
    with pytest.raises(RoleIdentityError, match='ambiguous_book_code'):
        asr_run.find_chapter_for_take(db, local)
    result = asr_run.run_asr_for_chapter(db, 'legacy-chapter', transcribe=no_paid)
    assert result['takes'] == 0 and db.query(AsrJob).count() == 0


def test_explicit_owner_is_authoritative_even_when_book_code_collides(db):
    row = audio(db, 'owned', owner='legacy', code='OLD', ref='')
    db.get(ScriptBook, 'typed').title = 'Silver Water'
    db.commit()
    assert book_roles(db, 'legacy').owns_audio(row)
    assert asr_run.find_chapter_for_take(db, row).id == 'legacy-chapter'


def test_legacy_own_recording_still_pays_once_then_realigns_for_free(db):
    local = audio(db, 'local', owner='', ref='')
    calls=[]
    def paid(path):
        calls.append(path)
        return heard('Медленно закрылась дверь')(path)
    first = asr_run.run_asr_for_chapter(db, 'legacy-chapter', transcribe=paid)
    second = asr_run.run_asr_for_chapter(db, 'legacy-chapter', transcribe=no_paid)
    assert first['done'] == second['realigned'] == len(calls) == 1
    assert db.query(AsrJob).one().audio_file_id == local.id


def test_old_misbound_job_is_excluded_from_replay_borrow_and_paid_reuse(db):
    foreign = audio(db)
    job = AsrJob(id='misbound', audio_file_id=foreign.id, chapter_id='legacy-chapter', status='done', language='en',
                 expected_role='Рассказчик', heard_json=json.dumps(heard('The window closed silently')('unused')),
                 alignment_json='{"lines": [], "language": "en", "old_paid_evidence": true}')
    db.add(job);db.commit()
    before = (job.chapter_id, job.language, job.heard_json, job.alignment_json, job.status)
    assert asr_borrow.chapter_done_jobs(db, 'legacy-chapter') == []
    assert asr_replay.realign_job(db, job.id) is None
    result = asr_run.run_asr_for_chapter(db, 'typed-chapter', transcribe=no_paid)
    assert result['done'] == result['realigned'] == 0 and result['failed'] == 1
    with pytest.raises(asr_run.AsrRunError, match='cached_asr_scope_mismatch'):
        asr_run.run_asr_for_take(db, foreign.id, transcribe=no_paid)
    assert (job.chapter_id, job.language, job.heard_json, job.alignment_json, job.status) == before


@pytest.mark.parametrize('problem', ['alias_collision', 'foreign_character', 'ambiguous_book_code'])
def test_bot_preflights_authoritative_role_and_book_before_fetch_or_paid_stt(db, tmp_path, monkeypatch, problem):
    ch = Character(id='visitor', book_id='typed', name='Visitor', actor_name='Synthetic Actor')
    db.add(ch)
    if problem == 'alias_collision':
        db.add(Character(id='other', book_id='typed', name='Other', aliases='Visitor', actor_name='Other Actor'))
    elif problem == 'ambiguous_book_code':
        db.get(ScriptBook, 'legacy').title = 'Silver Road'
    selected_book = 'legacy' if problem == 'foreign_character' else 'typed'
    row = BotUpload(id='upload', telegram_user_id='41', purpose='take', state='queued', book_id=selected_book,
                    character_id='visitor', role_name='Visitor', actor_name='Synthetic Actor', file_id='synthetic',
                    file_unique_id='unique', file_name='take.wav', kind='document', mime_type='audio/wav',
                    file_size=20, created_at=datetime(2026,1,1), updated_at=datetime(2026,1,1))
    db.add(row);db.commit()
    monkeypatch.setattr('app.services.take_split.transcribe_whole', no_paid)
    received=[];owners=[];workdir=tmp_path/'no-writes'
    assert bot_takes.process(db, row.id, fetch=no_paid, transcribe_part=no_paid,
        send_text=lambda chat,text:received.append((chat,text)) or True,
        notify_owner=lambda text:owners.append(text) or True,
        workdir=str(workdir), now=datetime(2026,1,1)) == 'failed'
    assert not workdir.exists() and db.query(AudioFile).count() == db.query(AsrJob).count() == 0
    assert row.state == 'failed' and len(received)==len(owners)==1
    assert any(code in owners[0] for code in ['ambiguous_role_identity','audio_book_scope_mismatch','ambiguous_book_code'])


@pytest.mark.parametrize('duplicate_name', [False, True])
def test_typed_recorded_status_and_deadlines_survive_rename_by_immutable_ref(db, duplicate_name):
    from app.models import RoleDeadline
    from app.services import casting, role_deadlines
    ch = Character(id='stable-character', book_id='typed', name='Before Rename', actor_name='Synthetic Actor')
    db.add(ch)
    db.add(V2Segment(id='dialogue', book_id='typed', chapter_id='typed-chapter', ordinal=1,
                     text='The visitor spoke quietly.'))
    db.add(V2Attribution(id='dialogue-attr', segment_id='dialogue', version=1, speaker='Before Rename',
                         role_ref='character:stable-character', span_start=0, span_end=25))
    take = audio(db, 'renamed-take', owner='typed', code='SR', ref='character:stable-character')
    take.role = 'Before Rename'
    audition = audio(db, 'renamed-audition', owner='typed', code='SR', ref='character:stable-character')
    audition.role, audition.kind = 'Before Rename', 'audition'
    ch.name = 'After Rename'
    if duplicate_name:
        db.add(Character(id='same-label', book_id='typed', name='After Rename', actor_name='Synthetic Actor'))
    deadline = RoleDeadline(character_id=ch.id, book_id='typed', actor_name='Synthetic Actor', kind='audition',
                            created_at=datetime(2025,1,1), due_at=datetime(2026,1,2))
    db.add(deadline);db.commit()
    assert role_deadlines.role_progress(db, ch, 'Synthetic Actor') == (1, 1)
    assert casting.role_recorded(db, ch) is True
    assert role_deadlines._done(db, deadline, ch) is True
    # Даже совпавшие имя, актёр и ref не заменяют принадлежность книге.
    take.role_book_id = audition.role_book_id = 'legacy'
    db.commit()
    assert role_deadlines.role_progress(db, ch, 'Synthetic Actor') == (0, 1)
    assert casting.role_recorded(db, ch) is False
    assert role_deadlines._done(db, deadline, ch) is False
    take.role_book_id = audition.role_book_id = 'typed'
    take.role_ref = audition.role_ref = 'character:another-role'
    db.commit()
    assert casting.role_recorded(db, ch) is False
    assert role_deadlines._done(db, deadline, ch) is False


def test_real_single_take_unknown_book_code_fails_before_paid_call_or_job(db):
    row = audio(db, 'unknown-book', owner='', code='UNKNOWN', ref='')
    with pytest.raises(asr_run.AsrRunError, match='chapter_not_found'):
        asr_run.run_asr_for_take(db, row.id, transcribe=no_paid)
    assert db.query(AsrJob).count() == 0


def test_chapter_run_skips_orphan_book_audio_without_paid_call_or_job(db):
    db.add(ScriptChapter(id='orphan-chapter', book_id='missing-book', chapter_index=1,
                         chapter_title='Chapter 1', status='published'))
    audio(db, 'orphan-audio', owner='', code='UNKNOWN', ref='')
    result = asr_run.run_asr_for_chapter(db, 'orphan-chapter', transcribe=no_paid)
    assert result == {'chapter_id':'orphan-chapter', 'takes':0, 'done':0, 'realigned':0, 'failed':0}
    assert db.query(AsrJob).count() == 0
    assert db.get(AudioFile, 'orphan-audio') is not None
    assert not book_roles(db, 'missing-book').owns_audio(db.get(AudioFile, 'orphan-audio'))


def test_legacy_alignment_with_empty_job_chapter_keeps_coverage_and_layout_when_book_scope_is_unique(db):
    row = audio(db, 'old-cache', owner='', code='SW', ref='')
    payload = {'total':1, 'missing':[], 'lines':[{'index':0, 'text':'Медленно закрылась дверь',
                'matched':True, 'start':0., 'end':2., 'takes':[{'start':0., 'end':2.}]}]}
    job = AsrJob(audio_file_id=row.id, chapter_id='', status='done', language='ru',
                 alignment_json=json.dumps(payload))
    db.add(job);db.commit()
    assert chapter_delivery.chapter_recording_status(db, 'legacy-chapter')['matched_lines'] == 1
    assert chapter_delivery.book_recording_status(db, 'legacy')['chapters'][0]['matched_lines'] == 1
    layout = chapter_delivery._script_layout(db, 'legacy-chapter')
    assert layout[3] and {clip.audio_file_id for _role,clip in layout[3]} == {row.id}
    assert job.chapter_id == ''
    # Совместимость не разрешает отдать ту же старую сверку явно чужой книге.
    row.role_book_id = 'typed'
    db.commit()
    assert chapter_delivery.chapter_recording_status(db, 'legacy-chapter')['matched_lines'] == 0
    assert chapter_delivery.book_recording_status(db, 'legacy')['chapters'][0]['matched_lines'] == 0
    assert job.chapter_id == '' and job.alignment_json == json.dumps(payload)
