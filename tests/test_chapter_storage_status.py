"""NAS учитывает подтверждённые дубли своей книги, а не готовность ролей."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services.chapter_delivery import book_recording_status
from app.services.chapter_storage_status import nas_delivery
from app.services.mirror_evidence import make_mirror_proof
from app.v2.models import V2Attribution, V2Segment


STAMP = datetime(2026, 1, 2, 3, 4, 5)


@pytest.fixture
def studio(monkeypatch):
    monkeypatch.setattr(settings, 'audio_nas_path', '/synthetic/nas')
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ScriptBook(id='book', title='Sample Novel', source_filename='sample.txt',
                          source_format='txt', language='en', role_identity_mode='typed_v1'))
        db.add(ScriptBook(id='other-book', title='Another Novel', source_filename='other.txt', source_format='txt'))
        chapter = ScriptChapter(id='chapter', book_id='book', chapter_index=1, chapter_title='Chapter 1', status='published')
        db.add(chapter)
        db.add(Character(id='speaker', book_id='book', name='Speaker', actor_name='Sample Actor'))
        db.add(Character(id='silent', book_id='book', name='Silent', actor_name='Other Actor'))
        db.add(V2Segment(id='segment', book_id='book', chapter_id='chapter', ordinal=0, text='Sample dialogue.'))
        db.add(V2Attribution(id='attribution', segment_id='segment', version=1,
                             speaker='Speaker', role_ref='character:speaker', span_start=0, span_end=16))
        db.commit()
        yield db, chapter
    engine.dispose()


def take(db, id, **changes):
    fields = dict(id=id, role_book_id='book', book_code='SN', chapter='Chapter 1',
                  role='Speaker', role_ref='character:speaker', kind='take',
                  original_filename='same.wav', canonical_filename='', stored_key='private/not-returned.wav',
                  mime_type='audio/wav', size_bytes=10, md5='a' * 32, mirrored_at=STAMP, mirror_state='ok')
    fields.update(changes)
    row = AudioFile(**fields)
    db.add(row)
    db.flush()
    if "mirror_proof_json" not in changes:
        row.mirror_proof_json = make_mirror_proof(row, checksum=row.md5, size=row.size_bytes, root=settings.audio_nas_path)
    db.commit()
    return row


def status(db):
    return book_recording_status(db, 'book')['chapters'][0]


def test_all_takes_and_fixes_are_distinct_even_with_duplicate_names(studio):
    db, _ = studio
    take(db, 'first')
    take(db, 'second')
    take(db, 'fix', canonical_filename='speaker_fix1.wav', mirrored_at=None, mirror_state='')
    result = status(db)['nas_delivery']
    assert (result['state'], result['total_files'], result['delivered_files'], result['percent']) == ('known', 3, 2, 66)
    assert {entry['audio_id'] for entry in result['files']} == {'first', 'second', 'fix'}
    assert result['checked_at'] == '2026-01-02T03:04:05.000+00:00'


def test_scope_excludes_other_book_silent_orphan_role_and_auditions(studio):
    db, _ = studio
    take(db, 'owned')
    take(db, 'foreign', role_book_id='other-book')
    take(db, 'orphan', role_ref='character:missing')
    take(db, 'silent', role_ref='character:silent', role='Silent')
    take(db, 'audition', kind='audition')
    take(db, 'other-chapter', chapter='Chapter 2')
    result = status(db)['nas_delivery']
    assert result['total_files'] == result['delivered_files'] == 1
    assert result['files'][0]['audio_id'] == 'owned'


@pytest.mark.parametrize('changes, expected', [
    ({'mirror_state': 'mismatch'}, 'error'),
    ({'mirror_state': 'missing'}, 'error'),
    ({'verify_state': 'missing'}, 'error'),
    ({'verify_state': 'mismatch'}, 'error'),
    ({'mirror_state': ''}, 'pending'),
    ({'mirrored_at': None}, 'pending'),
])
def test_missing_or_conflicting_proof_never_reaches_100(studio, changes, expected):
    db, _ = studio
    take(db, 'confirmed')
    take(db, 'problem', **changes)
    result = status(db)['nas_delivery']
    assert result['percent'] == 50
    entry = next(entry for entry in result['files'] if entry['audio_id'] == 'problem')
    assert entry['state'] == expected
    assert bool(entry['error']) == (expected == 'error')


def test_no_files_and_missing_configuration_remain_distinct(studio, monkeypatch):
    db, _ = studio
    assert status(db)['nas_delivery'] == {
        'state': 'empty', 'total_files': 0, 'delivered_files': 0, 'percent': None,
        'checked_at': None, 'reason': 'no_files', 'files': [],
    }
    take(db, 'confirmed')
    monkeypatch.setattr(settings, 'audio_nas_path', '')
    row = status(db)
    assert row['nas_delivery']['state'] == 'unknown'
    assert row['nas_delivery']['percent'] is None
    assert row['nas_delivery']['files'][0]['state'] == 'unknown'
    assert row['assembly']['state'] == 'unknown'
    assert row['assembly']['assembled'] is False


def test_assembly_requires_verified_current_archive_not_download_or_readiness(studio):
    db, chapter = studio
    take(db, 'confirmed')
    chapter.delivered_at = STAMP
    db.commit()
    assert status(db)['ready'] is True
    assert status(db)['assembly']['state'] == 'missing'
    chapter.session_archived_at = STAMP
    db.commit()
    assert status(db)['assembly']['state'] == 'unknown'
    chapter.session_sha256 = 'a' * 64
    db.commit()
    assert status(db)['assembly'] == {'assembled': True, 'state': 'current',
                                      'completed_at': '2026-01-02T03:04:05.000+00:00', 'reason': ''}
    chapter.session_outdated_at = STAMP
    db.commit()
    assert status(db)['assembly']['assembled'] is False
    assert status(db)['assembly']['state'] == 'outdated'


def test_safe_filename_and_floor_prevent_false_completion():
    files = [dict(audio_id=str(index), role='Speaker', filename=r'C:\private\take.wav',
                  mirror_state='ok', mirrored_at=STAMP) for index in range(1000)]
    for file in files:
        file.update(stored_key="sample.wav", md5="a" * 32, size_bytes=10)
        file["mirror_proof_json"] = make_mirror_proof(file, checksum=file["md5"], size=10, root=settings.audio_nas_path)
    files[-1]['mirror_state'] = ''
    result = nas_delivery(files, configured=True)
    assert result['percent'] == 99
    assert result['delivered_files'] == 999
    assert {entry['filename'] for entry in result['files']} == {'take.wav'}


@pytest.mark.parametrize("change", ["stored_key", "md5", "size_bytes", "proof", "root", "legacy"])
def test_delivery_receipt_matches_current_source_and_destination_without_io(studio, monkeypatch, change):
    db, _ = studio
    row = take(db, "receipt")
    assert status(db)["nas_delivery"]["delivered_files"] == 1
    if change == "stored_key":
        row.stored_key = "new/source.wav"
    elif change == "md5":
        row.md5 = "b" * 32
    elif change == "size_bytes":
        row.size_bytes += 1
    elif change == "proof":
        row.mirror_proof_json = row.mirror_proof_json.replace('"copied_size": 10', '"copied_size": 11')
    elif change == "root":
        monkeypatch.setattr(settings, "audio_nas_path", "/different/nas")
    else:
        row.mirror_proof_json = ""
    db.commit()
    monkeypatch.setattr("app.services.audio_storage.file_exists", lambda *a, **kw: pytest.fail("GET read NAS"))
    monkeypatch.setattr("app.services.nas_health.nas_online", lambda: pytest.fail("GET probed NAS"))
    result = status(db)["nas_delivery"]
    assert result["delivered_files"] == 0 and result["files"][0]["state"] == "unknown"


def test_service_metadata_does_not_invalidate_verified_delivery(studio):
    db, _ = studio
    row = take(db, "metadata")
    row.status, row.actor_name = "accepted", "Updated Actor"
    row.uploaded_at = STAMP
    db.commit()
    assert status(db)["nas_delivery"]["delivered_files"] == 1
