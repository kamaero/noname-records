import { roleLabel } from "./types";
import { useState } from "react";
import { Icon } from "../components/Icon";
import { StressToggle } from "./StressToggle";
import type { FontSize, ReaderPrefs } from "./useReaderPrefs";

type ReaderToolbarProps = {
  bookTitle: string;
  chapterIndex: number;
  chapterTitle: string;
  /** «слова автора к проверке: N» — 0 (or omitted) hides it; author/operator only, never for a plain dictor */
  remarkCount?: number;
  onPrev: (() => void) | null;
  onNext: (() => void) | null;
  prefs: ReaderPrefs;
  onPrefs: (patch: Partial<ReaderPrefs>) => void;
  query: string;
  onQuery: (value: string) => void;
  onPrint: () => void;
  onToggleRail: () => void;
  railOpen: boolean;
  /** the chapter's cast: the sheet that holds it, and what it is filtering by now */
  cast: {
    count: number;
    selected: string[];
    open: boolean;
    onToggle: () => void;
    onClear: () => void;
    /** where the arrow keys are among the selected role's lines */
    cursor?: { position: number; total: number; onStep: (step: -1 | 1) => void };
  };
  /** the author's «проверено» for this chapter; absent for dictors and empty chapters */
  approval?: {
    approved: boolean;
    pending: boolean;
    onToggle: () => void;
  };
  /** звуковой слой на полях: сцены, переходы, звуки; только редактор — у диктора нет */
  sound?: { on: boolean; onToggle: () => void };
  /** «Лор» — шпаргалка о мире книги; есть только у книг автора, чью энциклопедию загрузили */
  lore?: { onOpen: () => void };
  /** split view controls; absent on phones, where two columns do not fit */
  compare?: {
    on: boolean;
    onToggle: () => void;
    /** panes scroll together */
    linked: boolean;
    onToggleLinked: () => void;
  };
  /**
   * «Мои реплики»: the book-wide way to a role's own lines. It sits next to the
   * chapter's cast but is deliberately not part of it — the cast lists who speaks
   * *here*, and the roles this is for are the ones who mostly do not.
   */
  onFindRole: () => void;
  /** the reader's own roles in this book, when the cast names him: the button says so */
  myRoleCount?: number;
  /**
   * A phone: the toolbar is folded down to what a dictor needs while reading —
   * the chapter, its cast, the neighbours — and everything else waits behind «Ещё».
   * On a 390px screen the full toolbar was 428px tall: half the screen was chrome
   * before the first line of the book.
   */
  compact?: boolean;
  /**
   * The stress queue is a dictor's tool (`can_voice`); the disputed lines and the
   * book profile are the author's (`can_edit`). They used to arrive together, so a
   * dictor got neither.
   */
  editor?: {
    queueLabel: string;
    queueOpen: boolean;
    onQueue: () => void;
    /** author only: lines the model would not settle, and the book profile */
    markup?: {
      disputedLabel: string;
      disputedOpen: boolean;
      onDisputed: () => void;
      profileOpen: boolean;
      onProfile: () => void;
      /** находки консилиума; нет у книги находок — нет и кнопки */
      consilium?: { label: string; open: boolean; onToggle: () => void };
      /** места книги звукового слоя */
      places?: { open: boolean; onToggle: () => void };
    };
  };
};

const RADII = [0, 1, 2, 3, 5];
const FONTS: Array<[FontSize, string]> = [
  ["s", "S"],
  ["m", "M"],
  ["l", "L"],
];

export function ReaderToolbar(props: ReaderToolbarProps) {
  const { bookTitle, chapterIndex, chapterTitle, remarkCount = 0, onPrev, onNext, prefs, onPrefs, query, onQuery, onPrint, onToggleRail, railOpen, cast, approval, sound, lore, compare, editor, compact = false, onFindRole, myRoleCount = 0 } = props;
  const filtering = cast.selected.length > 0;
  const [more, setMore] = useState(false);
  // on a wide screen there is nothing to unfold: everything is already on the row
  const secondaryHidden = compact && !more;
  return (
    <header className="v2r-toolbar">
      <div className="v2r-toolbar-row v2r-toolbar-row--title">
        <button
          type="button"
          className="btn btn-sm v2r-btn v2r-rail-toggle"
          onClick={onToggleRail}
          aria-pressed={railOpen}
          aria-label="Chapter list"
        >
          <Icon name="prep" />
          Chapters
        </button>
        <div className="v2r-title">
          <span className="v2r-title-book">{bookTitle || "Book"}</span>
          <h1 className="v2r-title-chapter">
            <span className="v2r-title-index num">{chapterIndex}</span> {chapterTitle || "Untitled"}
          </h1>
          {remarkCount > 0 ? (
            <span className="v2r-title-remarks" title="Paragraphs where automated processing may have missed narration inside dialogue">
              narration passages to review: <span className="num">{remarkCount}</span>
            </span>
          ) : null}
        </div>
        {approval ? (
          <button
            type="button"
            className={approval.approved ? "btn btn-sm v2r-btn v2r-approve is-done" : "btn btn-sm v2r-btn v2r-approve"}
            onClick={approval.onToggle}
            disabled={approval.pending}
            aria-pressed={approval.approved}
            title={
              approval.approved
                ? "Chapter reviewed by author. Click to remove mark"
                : "Mark this chapter as read and accurately annotated. Only reviewed chapters can be published"
            }
          >
            {approval.approved ? "✓ Reviewed" : "Mark as reviewed"}
          </button>
        ) : null}
        <nav className="v2r-nav v2r-nav--top" aria-label="Adjacent chapters">
          <button type="button" className="btn btn-sm v2r-btn" onClick={onPrev ?? undefined} disabled={!onPrev}>
            <Icon name="back" /> <span className="v2r-nav-label">Prev.</span>
          </button>
          <button type="button" className="btn btn-sm v2r-btn" onClick={onNext ?? undefined} disabled={!onNext}>
            <span className="v2r-nav-label">Next</span> <Icon name="arrow" />
          </button>
        </nav>
      </div>

      <div className="v2r-toolbar-row v2r-toolbar-row--tools">
        <button
          type="button"
          className={cast.open || filtering ? "btn btn-sm v2r-btn v2r-cast-btn is-on" : "btn btn-sm v2r-btn v2r-cast-btn"}
          onClick={cast.onToggle}
          aria-pressed={cast.open}
          aria-expanded={cast.open}
          title="Chapter cast: role colors and My role filter"
        >
          <Icon name="users" />
          {filtering ? `My role: ${cast.selected.map(roleLabel).join(", ")}` : `Chapter cast`}
          <span className="v2r-legend-count num">{filtering ? cast.selected.length : cast.count}</span>
        </button>
        <button
          type="button"
          className="btn btn-sm v2r-btn v2r-mine"
          onClick={onFindRole}
          title="All lines for this role throughout the book, in reading order, with context"
        >
          <Icon name="doc" />
          {myRoleCount > 0 ? "My lines" : "All lines for role"}
        </button>
        {filtering && cast.cursor ? (
          <span className="v2r-cursor" role="group" aria-label="Role line navigation">
            <button
              type="button"
              className="btn btn-sm v2r-btn"
              onClick={() => cast.cursor?.onStep(-1)}
              title="Previous role line (up arrow)"
              aria-label="Previous role line"
            >
              ↑
            </button>
            <button
              type="button"
              className="btn btn-sm v2r-btn"
              onClick={() => cast.cursor?.onStep(1)}
              title="Next role line (down arrow)"
              aria-label="Next role line"
            >
              ↓
            </button>
            <span className="v2r-cursor-count num" title="Role lines in this chapter">
              {cast.cursor.position || "—"} / {cast.cursor.total}
            </span>
          </span>
        ) : null}
        {filtering ? (
          <button type="button" className="btn btn-sm btn-ghost v2r-btn" onClick={cast.onClear} title="Clear role filter">
            Show all
          </button>
        ) : null}
        {compact ? (
          <button
            type="button"
            className={more ? "btn btn-sm v2r-btn v2r-more is-on" : "btn btn-sm v2r-btn v2r-more"}
            onClick={() => setMore((open) => !open)}
            aria-expanded={more}
            title="Search, word stress, font size, print"
          >
            {more ? "Collapse" : "More"}
          </button>
        ) : null}

        <div className="v2r-toolbar-more" hidden={secondaryHidden}>
          <label className="v2r-search">
            <Icon name="search" />
            <input
              className="v2r-search-input"
              type="search"
              value={query}
              onChange={(event) => onQuery(event.target.value)}
              placeholder="Find in chapter…"
              aria-label="Search paragraphs"
            />
          </label>

          <StressToggle prefs={prefs} onPrefs={onPrefs} />

          {sound ? (
            <button
              type="button"
              className={sound.on ? "btn btn-sm v2r-btn is-on" : "btn btn-sm v2r-btn"}
              onClick={sound.onToggle}
              aria-pressed={sound.on}
              title={sound.on ? "Hide sound annotations: scenes, transitions and sounds" : "Show sound annotations: scenes, transitions and sounds"}
            >
              Sound
            </button>
          ) : null}

          {lore ? (
            <button
              type="button"
              className="btn btn-sm v2r-btn"
              onClick={lore.onOpen}
              title="Story world: species, titles and characters; the author's reference guide"
            >
              Lore
            </button>
          ) : null}

          {compare ? (
            <div className="v2r-compare-btns" role="group" aria-label="Compare with original">
              <button
                type="button"
                className={compare.on ? "btn btn-sm v2r-btn is-on" : "btn btn-sm v2r-btn"}
                onClick={compare.onToggle}
                aria-pressed={compare.on}
                title="Show the original text beside the script"
              >
                <Icon name="doc" /> Original
              </button>
              {compare.on ? (
                <button
                  type="button"
                  className={compare.linked ? "btn btn-sm v2r-btn is-on" : "btn btn-sm v2r-btn"}
                  onClick={compare.onToggleLinked}
                  aria-pressed={compare.linked}
                  title={compare.linked ? "Panels scroll together; unlink" : "Panels scroll separately; link"}
                >
                  {compare.linked ? "Linked" : "Unlinked"}
                </button>
              ) : null}
            </div>
          ) : null}

          <label className="v2r-radius" title="Paragraphs of context around your lines in My role mode">
            <span className="v2r-radius-label">± paragraphs</span>
            <select
              className="v2r-select"
              value={prefs.radius}
              onChange={(event) => onPrefs({ radius: Number(event.target.value) })}
              aria-label="Context around lines"
            >
              {RADII.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          <div className="v2r-seg" role="group" aria-label="Font size">
            {FONTS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={prefs.font === key ? "v2r-seg-btn is-on" : "v2r-seg-btn"}
                onClick={() => onPrefs({ font: key })}
                aria-pressed={prefs.font === key}
              >
                {label}
              </button>
            ))}
          </div>

          {editor ? (
            <div className="v2r-editor-btns" role="group" aria-label="Editor tools">
              <button
                type="button"
                className={editor.queueOpen ? "btn btn-sm v2r-btn v2r-editor-btn is-on" : "btn btn-sm v2r-btn v2r-editor-btn"}
                onClick={editor.onQueue}
                aria-pressed={editor.queueOpen}
                title="Word stress queue: unresolved words and homographs"
              >
                {editor.queueLabel}
              </button>
              {editor.markup ? (
                <>
                  <button
                    type="button"
                    className={editor.markup.disputedOpen ? "btn btn-sm v2r-btn v2r-editor-btn is-on" : "btn btn-sm v2r-btn v2r-editor-btn"}
                    onClick={editor.markup.onDisputed}
                    aria-pressed={editor.markup.disputedOpen}
                    title="Lines where the model could not assign a role or was uncertain"
                  >
                    {editor.markup.disputedLabel}
                  </button>
                  {editor.markup.consilium ? (
                    <button
                      type="button"
                      className={editor.markup.consilium.open ? "btn btn-sm v2r-btn v2r-editor-btn is-on" : "btn btn-sm v2r-btn v2r-editor-btn"}
                      onClick={editor.markup.consilium.onToggle}
                      aria-pressed={editor.markup.consilium.open}
                      title="AI review findings: passages where independent reviewers disagreed with the script"
                    >
                      {editor.markup.consilium.label}
                    </button>
                  ) : null}
                  {editor.markup.places ? (
                    <button
                      type="button"
                      className={editor.markup.places.open ? "btn btn-sm v2r-btn v2r-editor-btn is-on" : "btn btn-sm v2r-btn v2r-editor-btn"}
                      onClick={editor.markup.places.onToggle}
                      aria-pressed={editor.markup.places.open}
                      title="Sound locations: appearances, potential duplicates and ambience queries"
                    >
                      Locations
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className={editor.markup.profileOpen ? "btn btn-sm v2r-btn v2r-editor-btn is-on" : "btn btn-sm v2r-btn v2r-editor-btn"}
                    onClick={editor.markup.onProfile}
                    aria-pressed={editor.markup.profileOpen}
                    title="Book and author profile"
                  >
                    <Icon name="users" /> Profile
                  </button>
                </>
              ) : null}
            </div>
          ) : null}

          <button type="button" className="btn btn-sm v2r-btn v2r-print" onClick={onPrint}>
            <Icon name="doc" /> PDF / print
          </button>
        </div>
      </div>
    </header>
  );
}
