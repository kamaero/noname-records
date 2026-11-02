/**
 * Сценарий `/app/recording?book_id=&chapter_id=&role=`
 *
 * The v2 reader embedded in «моя роль» mode (the actor's own lines with context), the
 * whole width of the page. Above it — one quiet line: book (only when there is a
 * choice), role, «все реплики роли», and the way to «Загрузка» with the same pick.
 * The upload form lived in a sticky panel on the right until 29.10; the owner moved it
 * to its own section so the page dictors read from carries only the text.
 *
 * `?preview=1` is a client-only flag for checking the layout on a book that is
 * not published yet: the chapter list falls back to the book's v2-marked chapters
 * and the cast to the reader's payload.
 */
import { useCallback, useMemo } from "react";
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
  const sel = useRecordingSelection();
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

  if (sel.workspaceQuery.isLoading || (sel.preview && sel.previewChaptersQuery.isLoading)) {
    return <SkeletonPanel label="Открываю сценарий…" lines={6} />;
  }
  if (sel.workspaceQuery.isError) {
    return (
      <EmptyState
        icon="doc"
        text={describeApiError(sel.workspaceQuery.error, "Не удалось открыть сценарий.")}
        action={<LinkButton to="/">Открыть библиотеку</LinkButton>}
      />
    );
  }

  // Nothing to read anywhere: no book asked for and none published.
  if (!sel.bookId && !sel.workspace?.selected_book) {
    return (
      <EmptyState
        icon="doc"
        text="Ни у одной книги ещё нет опубликованных глав. Сценарий откроется, когда режиссёр его опубликует."
        action={<LinkButton to="/">Открыть библиотеку</LinkButton>}
      />
    );
  }

  if (!sel.chapters.length) {
    return (
      <EmptyState
        icon="doc"
        text={
          sel.bookTitle
            ? `У книги «${sel.bookTitle}» ещё нет опубликованных глав. Сценарий появится здесь после публикации.`
            : "У книги ещё нет опубликованных глав. Сценарий появится здесь после публикации."
        }
        action={<LinkButton to={sel.bookLink}>{sel.bookLink === "/" ? "Открыть библиотеку" : "Открыть книгу"}</LinkButton>}
      />
    );
  }

  // Свои роли — первыми: диктор открывает сценарий ради них, а чужую выбирает реже.
  const mine = new Set(sel.myRoleNames);
  const others = sel.cast.filter((item) => !mine.has(item.name));

  return (
    <div className="script-page">
      <div className="script-bar" role="toolbar" aria-label="Что читать">
        {sel.panelBooks.length > 1 ? (
          <label className="script-bar-field">
            <span className="rec-label">Книга</span>
            <select className="ui-input rec-select" value={sel.bookId} onChange={(event) => sel.goToBook(event.target.value)}>
              {sel.panelBooks.map((book) => (
                <option key={book.id} value={book.id}>
                  {book.title}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="script-bar-field">
          <span className="rec-label">Роль</span>
          <select className="ui-input rec-select" value={sel.role} onChange={(event) => sel.chooseRole(event.target.value)}>
            <option value="">{sel.myRoles.length ? "— все мои роли —" : "— выберите роль —"}</option>
            {sel.myRoles.length ? (
              <optgroup label="Мои роли">
                {sel.myRoles.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name} · {item.lines}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {others.length ? (
              <optgroup label={sel.myRoles.length ? "Другие роли" : "Роли книги"}>
                {others.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name} · {item.lines}
                  </option>
                ))}
              </optgroup>
            ) : null}
          </select>
        </label>
        {sel.role && sel.bookId ? (
          <Link
            className="script-bar-link"
            to={roleScriptHref(sel.bookId, sel.role, "/recording/role", searchParams.toString())}
            title="Все реплики этой роли по всей книге, с контекстом"
          >
            Все реплики роли в книге →
          </Link>
        ) : null}
        <LinkButton className="script-bar-upload" variant="primary" size="sm" to={takesHref} icon={<Icon name="upload" />}>
          Загрузить дубль
        </LinkButton>
      </div>
      <ScriptReader
        key={sel.bookId}
        embedded
        chapterId={sel.chapterId}
        onChapterChange={sel.goToChapter}
        initialRoles={readerRoles}
        allowedChapterIds={sel.allowedChapterIds}
        onLoaded={sel.onReaderLoaded}
        openStressQueue={openStressQueue}
        onStressQueueOpened={clearStressRequest}
        roleScriptBase="/recording/role"
        roleScriptSearch={searchParams.toString()}
      />
    </div>
  );
}
