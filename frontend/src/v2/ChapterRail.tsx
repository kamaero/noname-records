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

export function ChapterRail({ bookTitle, chapters, currentId, open, mobileOpen, onClose }: ChapterRailProps) {
  const marked = chapters.filter((chapter) => chapter.has_v2).length;
  const approved = chapters.filter((chapter) => chapter.approved).length;
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
          {chapters.map((chapter) => {
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
          })}
        </ol>
      </aside>
    </>
  );
}
