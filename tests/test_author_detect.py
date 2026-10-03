"""Auto-detect the author of an uploaded book from its title/filename.

Stem-aware surname match so 'Белозёровы Сказки волшебников' binds to author
'Александр Белозёров'. Conservative: tokens >=5 chars, prefix-stem match — avoids
false binds on common words. No match -> "" (book stays unbound)."""
from app.services.author_detect import match_author_id

AUTHORS = [("rud", "Александр Белозёров"), ("tol", "Лев Толстой")]


def test_matches_surname_in_declension():
    assert match_author_id("Белозёровы Сказки волшебников. Книга 2", AUTHORS) == "rud"


def test_matches_plain_surname():
    assert match_author_id("Белозёров Криабал 1", AUTHORS) == "rud"


def test_no_match_returns_empty():
    assert match_author_id("Война и мир", AUTHORS) == ""


def test_does_not_match_on_short_or_common_tokens():
    # 'Книга'/'Семья' must not bind anything; only surname-like tokens count
    assert match_author_id("Книга 2. Семья и магия", AUTHORS) == ""


def test_picks_the_stronger_author_match():
    assert match_author_id("Толстой. Детство", AUTHORS) == "tol"


def test_detect_author_id_against_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import Author
    from app.services.author_detect import detect_author_id

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(Author(id="rud-1", name="Александр Белозёров", slug="belozerov"))
        db.commit()
        assert detect_author_id(db, "Белозёровы Сказки волшебников. Книга 2", "file.fb2") == "rud-1"
        assert detect_author_id(db, "Война и мир", "tolstoy.fb2") == ""


def test_extract_fb2_author_from_title_info():
    from app.pipeline.book_parser import _extract_fb2_author
    fb2 = ('<?xml version="1.0" encoding="utf-8"?>'
           '<FictionBook><description><title-info>'
           '<genre>fantasy</genre>'
           '<author><first-name>Александр</first-name><last-name>Белозёров</last-name>'
           '<home-page>x</home-page></author>'
           '<book-title>Тест</book-title></title-info></description>'
           '<body><section><p>текст</p></section></body></FictionBook>').encode('utf-8')
    assert _extract_fb2_author(fb2) == "Александр Белозёров"


def test_extract_fb2_author_missing_returns_empty():
    from app.pipeline.book_parser import _extract_fb2_author
    fb2 = b'<?xml version="1.0"?><FictionBook><body><p>x</p></body></FictionBook>'
    assert _extract_fb2_author(fb2) == ""
