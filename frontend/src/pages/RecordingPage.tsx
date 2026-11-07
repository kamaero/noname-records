import { roleLabel } from "../v2/types";
/**
 * Сценарий `/app/recording?book_id=&chapter_id=&role=`
 *
 * Выбор роли и переход к своим репликам живут над Читалкой: они остаются доступны,
 * даже если текст текущей главы не загрузился. Загрузка сохраняет тот же выбор.
 * The upload form lived in a sticky panel on the right until 29.10; the owner moved it
 * to its own section so the page dictors read from carries only the text.
 *
 * `?preview=1` is a client-only flag for checking the layout on a book that is
 * not published yet: the chapter list falls back to the book's v2-marked chapters
 * and the cast to the reader's payload.
 */
import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { SkeletonPanel } from "../components/Skeleton";
import { useRecordingSelection } from "../components/recording/useRecordingSelection";
import { EmptyState, LinkButton } from "../ui";
import { ScriptReader } from "../v2/ScriptReader";
import { roleScriptHref } from "../v2/roleRoutes";
import "../components/recording/recording.css";

export function RecordingPage() {
  const sel = useRecordingSelection({ rememberPosition: true });
  const [roleActionHost, setRoleActionHost] = useState<HTMLDivElement | null>(null);
  const { searchParams, setSearchParams } = sel;
  const openStressQueue = searchParams.get("stress") === "1";

  const clearStressRequest = useCallback(() => {
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("stress");
        return next;
      },
      { replace: true },
    );
  }, [setSearchParams]);

  // The reader folds to the role picked above; before a pick, to all of the actor's own roles.
  const readerRoles = useMemo(() => (sel.role.trim() ? [sel.role.trim()] : sel.myRoleNames), [sel.role, sel.myRoleNames]);

  // «Загрузка» opens on the same book, role and chapter the dictor is reading.
  const takesHref = useMemo(() => {
    const params = new URLSearchParams(searchParams);
    if (sel.bookId) params.set("book_id", sel.bookId);
    if (sel.chapterId) params.set("chapter_id", sel.chapterId);
    if (sel.role.trim()) params.set("role", sel.role.trim());
    params.delete("stress");
    params.delete("preview");
    const query = params.toString();
    return `/takes${query ? `?${query}` : ""}`;
  }, [searchParams, sel.bookId, sel.chapterId, sel.role]);

  if (sel.workspaceQuery.isLoading || sel.workspaceQuery.isPlaceholderData || (sel.preview && sel.previewChaptersQuery.isLoading)) {
    return <SkeletonPanel label="Opening script…" lines={6} />;
  }
  if (sel.workspaceQuery.isError) {
    return (
      <EmptyState
        icon="doc"
        text={describeApiError(sel.workspaceQuery.error, "Could not open the script.")}
        action={<LinkButton to="/">Open library</LinkButton>}
      />
    );
  }

  // Nothing to read anywhere: no book asked for and none published.
  if (!sel.bookId && !sel.workspace?.selected_book) {
    return (
      <EmptyState
        icon="doc"
        text="No published chapters yet. The script will be available when the director publishes it."
        action={<LinkButton to="/">Open library</LinkButton>}
      />
    );
  }

  if (!sel.chapters.length) {
    return (
      <EmptyState
        icon="doc"
        text={
          sel.bookTitle
            ? `Book «${sel.bookTitle}» has no published chapters yet. Its script will appear here once published.`
            : "This book has no published chapters yet. Its script will appear here once published."
        }
        action={<LinkButton to={sel.bookLink}>{sel.bookLink === "/" ? "Open library" : "Open book"}</LinkButton>}
      />
    );
  }

  // Свои роли — первыми: диктор открывает сценарий ради них, а чужую выбирает реже.
  const mine = new Set(sel.myRoleNames);
  const others = sel.cast.filter((item) => !mine.has(item.name));

  return (
    <div className="script-page">
      <div className="script-bar" role="toolbar" aria-label="Reading selection">
        <div className="script-bar-selection">
          {sel.panelBooks.length > 1 ? (
            <label className="script-bar-field">
              <span className="rec-label">Book</span>
              <select className="ui-input rec-select" value={sel.bookId} onChange={(event) => sel.goToBook(event.target.value)}>
                {sel.panelBooks.map((book) => (
                  <option key={book.id} value={book.id}>
                    {book.title}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="script-bar-field">
            <label className="rec-label" htmlFor="script-role">Role</label>
            <select id="script-role" className="ui-input rec-select" value={sel.role} onChange={(event) => sel.chooseRole(event.target.value)}>
              <option value="">{sel.myRoles.length ? "— all my roles —" : "— choose a role —"}</option>
              {sel.myRoles.length ? (
                <optgroup label="My roles">
                  {sel.myRoles.map((item) => (
                    <option key={item.name} value={item.name}>
                      {roleLabel(item.name)} · {item.lines}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              {others.length ? (
                <optgroup label={sel.myRoles.length ? "Other roles" : "Book roles"}>
                  {others.map((item) => (
                    <option key={item.name} value={item.name}>
                      {roleLabel(item.name)} · {item.lines}
                    </option>
                  ))}
                </optgroup>
              ) : null}
            </select>
            {sel.role && sel.bookId ? (
              <Link
                className="script-bar-role-link"
                to={roleScriptHref(sel.bookId, sel.role, "/recording/role", searchParams.toString())}
                title="All lines for the selected role throughout the book, with context"
              >
                Selected role script →
              </Link>
            ) : null}
          </div>
        </div>
        <div className="script-bar-role-action" ref={setRoleActionHost} />
        <LinkButton className="script-bar-upload" variant="secondary" size="sm" to={takesHref} icon={<Icon name="upload" />}>
          Upload take
        </LinkButton>
      </div>
      <ScriptReader
        key={sel.bookId}
        embedded
        roleActionHost={roleActionHost}
        roleActionBookId={sel.bookId}
        chapterId={sel.chapterId}
        onChapterChange={sel.goToChapter}
        initialRoles={readerRoles}
        allowedChapterIds={sel.allowedChapterIds}
        onLoaded={sel.onReaderLoaded}
        readingPosition={sel.readerPosition}
        openStressQueue={openStressQueue}
        onStressQueueOpened={clearStressRequest}
        roleScriptBase="/recording/role"
        roleScriptSearch={searchParams.toString()}
      />
    </div>
  );
}
