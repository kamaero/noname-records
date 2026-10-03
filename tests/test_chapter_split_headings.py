from app.services.book_import import _score_heading_line, split_into_chapters, _build_lines, _extract_toc, _collect_chapter_candidates


def _heading_detected(line: str) -> bool:
    # standalone heading line (blank context), as in a real chapter break
    return _score_heading_line(line, prev_blank=True, next_blank=True) is not None


def test_heading_with_en_dash_is_detected():
    assert _heading_detected("Глава 3. Самое трудное – не заблудиться самому")


def test_heading_with_em_dash_is_detected():
    assert _heading_detected("Глава 7. Давайте — разрешим эту ситуацию")


def test_heading_with_colon_is_detected():
    assert _heading_detected("Глава 5: Я магнит для уродов")


def test_heading_with_comma_and_quotes_is_detected():
    assert _heading_detected('Глава 12. Ао, «зверушки» и прочие')


def test_plain_heading_still_detected():
    assert _heading_detected("Глава 2. Дгарнинам вход воспрещен")


def test_word_form_chapter_still_detected():
    assert _heading_detected("Глава Третья")


def test_bare_glava_word_without_designator_not_detected():
    # "Глава" with nothing after it is not a chapter heading
    assert not _heading_detected("Глава ")


def test_english_word_form_chapter_detected():
    assert _heading_detected("Chapter Three")


def test_dash_colon_headings_collected_by_primary_detector():
    # Primary heading detector must collect dash/colon-titled "Глава N" headings as
    # candidates — independent of the legacy fallback in split_into_chapters.
    text = "\n\n".join(
        f"Глава {i}. Заголовок {'–' if i % 2 else ':'} часть {i}\n\n" + ("Тело главы. " * 50)
        for i in range(1, 7)
    )
    lines = _build_lines(text)
    toc = _extract_toc(lines)
    cands = _collect_chapter_candidates(lines, toc_end=toc.get("end", -1))
    titles = [c.title for c in cands]
    assert len(cands) >= 6
    assert any(("–" in t) or (":" in t) for t in titles)
