"""Заставка главы с отдельной записью названия в рабочем таймлайне."""
from itertools import takewhile

from app.services.chapter_titles import TITLE_ROLE

AMBIENT_START = 3.0
TITLE_START = 10.0
AFTER_TITLE = 2.5
AMBIENT_GAIN_DB = -12.0


def place_opening(replicas, clips, starts, gaps, *, chars_per_second, gap_seconds):
    """Поставить заставку и перенести остальную главу вместе с её якорями."""
    title_rows = list(takewhile(lambda row: row["role"] == TITLE_ROLE, replicas))
    if not title_rows:
        return None
    title_ids = {str(row["segment_id"]) for row in title_rows}

    def is_title(role, segment_id):
        # Подход, сохранённый для проверки, может не иметь якоря.
        return role == TITLE_ROLE and (not segment_id or segment_id in title_ids)

    title_starts, title_windows, body_starts = [], [], []
    for role, clip in clips:
        if is_title(role, clip.segment_id):
            title_starts.append(clip.start)
            title_windows.append((clip.start, clip.length))
        else:
            body_starts.append(clip.start)
    for row in gaps:
        if is_title(row["role"], row["segment_id"]):
            title_windows.append((row["start"], row["duration"]))
        else:
            body_starts.append(row["start"])
    title_origin = min(title_starts, default=0.0)
    if title_windows:
        title_duration = max(max(0.0, start - title_origin) + length for start, length in title_windows)
    else:
        # В черновике оставляем место под ещё не записанное объявление.
        title_duration = (sum(max(1.0, len(row["text"]) / chars_per_second) for row in title_rows)
                          + (len(title_rows) - 1) * gap_seconds)
    body_start = TITLE_START + title_duration + AFTER_TITLE
    body_origin = min(body_starts, default=0.0)

    def shift(start, title):
        if title:
            return TITLE_START + max(0.0, start - title_origin)
        return body_start + (start - body_origin)

    for role, clip in clips:
        clip.start = shift(clip.start, is_title(role, clip.segment_id))
    for row in gaps:
        row["start"] = shift(row["start"], is_title(row["role"], row["segment_id"]))
    for segment_id in starts:
        starts[segment_id] = shift(starts[segment_id], segment_id in title_ids)
    return body_start


def opening_cue(start, seconds, *, first, body_start):
    """Первая сцена может начать звучать раньше своих реплик."""
    if not first or body_start is None or start > body_start:
        return start, seconds, 0.0, 0.0
    return AMBIENT_START, seconds + start - AMBIENT_START, TITLE_START - AMBIENT_START, AMBIENT_GAIN_DB
