import { useState } from "react";
import { Link } from "react-router-dom";
import type { ReaderChapterRef } from "./types";

type ChapterRailProps = {
  bookTitle: string;
  chapters: ReaderChapterRef[];
  currentId: string;
  /** open on wide screens (persisted pref) */
  open: boolean;
  /** open as an overlay on narrow screens */
  mobileOpen: boolean;
  onClose: () => void;
};

const OPEN_KEY = "v2r.releasedOpen";

function readOpen(): boolean {
  try {
    return window.localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function ChapterRail({ bookTitle, chapters, currentId, open, mobileOpen, onClose }: ChapterRailProps) {
  const marked = chapters.filter((chapter) => chapter.has_v2).length;
  const approved = chapters.filter((chapter) => chapter.approved).length;
  const active = chapters.filter((chapter) => !chapter.released_at);
  const released = chapters.filter((chapter) => chapter.released_at);
  /* Сданные главы не мешают работать, но текущая сданная глава должна быть на виду,
     иначе человек открыл её и не видит её в списке. */
  const [releasedPref, setReleasedPref] = useState(readOpen);
  const currentReleased = released.some((chapter) => chapter.id === currentId);
  const releasedOpen = releasedPref || currentReleased;
  const toggleReleased = () => {
    const next = !releasedOpen;
    setReleasedPref(next);
    try {
      window.localStorage.setItem(OPEN_KEY, next ? "1" : "0");
    } catch {
      /* хранилище недоступно — состояние живёт до перезагрузки */
    }
  };
  const renderItem = (chapter: ReaderChapterRef) => {
    const itemClass = [
      "v2r-rail-item",
      chapter.id === currentId ? "is-active" : "",
      chapter.has_v2 ? "" : "is-empty",
    ]
      .filter(Boolean)
      .join(" ");
    return (
      <li key={chapter.id}>
        <Link
          to={`/reader/${encodeURIComponent(chapter.id)}`}
          className={itemClass}
          onClick={onClose}
          aria-current={chapter.id === currentId ? "page" : undefined}
          title={chapter.has_v2 ? undefined : "Глава ещё не размечена в v2"}
        >
          <span className="v2r-rail-index num">{chapter.index}</span>
          <span className="v2r-rail-title">{chapter.title || "Без названия"}</span>
          {chapter.approved ? (
            <span className="v2r-rail-approved" title="Проверена автором" aria-label="Проверена">
              ✓
            </span>
          ) : null}
        </Link>
      </li>
    );
  };
  const className = ["v2r-rail", open ? "is-open" : "", mobileOpen ? "is-mobile-open" : ""].filter(Boolean).join(" ");
  return (
    <>
      {mobileOpen ? <div className="v2r-backdrop" onClick={onClose} aria-hidden="true" /> : null}
      <aside className={className} aria-label="Главы книги">
        <div className="v2r-rail-head">
          <div className="v2r-rail-book">
            <strong>{bookTitle || "Книга"}</strong>
            <span className="faint num">
              {marked} / {chapters.length} глав
              {approved ? ` · проверено ${approved}` : ""}
            </span>
          </div>
          {mobileOpen ? (
            // only the mobile sheet closes; on wide screens the toolbar's «Главы» toggles the rail
            <button type="button" className="btn btn-sm btn-ghost v2r-btn v2r-rail-close" onClick={onClose} aria-label="Закрыть список глав">
              ✕
            </button>
          ) : null}
        </div>
        <ol className="v2r-rail-list">
          {active.map(renderItem)}
        </ol>
        {released.length ? (
          <div className="v2r-rail-released">
            <button
              type="button"
              className="v2r-rail-released-head"
              onClick={toggleReleased}
              aria-expanded={releasedOpen}
              disabled={currentReleased}
            >
              <span className="v2r-rail-chevron" aria-hidden="true">
                {releasedOpen ? "▾" : "▸"}
              </span>
              Сданные ({released.length})
            </button>
            {releasedOpen ? <ol className="v2r-rail-list">{released.map(renderItem)}</ol> : null}
          </div>
        ) : null}
      </aside>
    </>
  );
}
