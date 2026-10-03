/**
 * Запись `/app/recording?book_id=&chapter_id=`
 *
 * Left: the v2 reader embedded in «моя роль» mode (the actor's own lines with
 * context). Right: the upload panel — book (only when there is a choice), role,
 * chapter, the batch drop zone, the chapter's last files.
 *
 * Endpoints are the same as before this page was rebuilt:
 *   GET  /api/recording/workspace?book_id=&chapter_id=  → published chapters, cast, my_roles, recent_files
 *   POST /dictor-pro/batch-validate, POST /api/recording/batch  (useRecordingUploads)
 *
 * `?preview=1` is a client-only flag for checking the layout on a book that is
 * not published yet: the chapter list falls back to the book's v2-marked chapters
 * and the cast to the reader's payload. Uploads stay disabled in preview — the
 * workspace gives no book code for an unpublished book, and a wrong code files
 * audio where the DAW export will not look.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { apiGet, apiPostJson, describeApiError } from "../api/client";
import { isAgentRoles } from "../layout/AppShell";
import { SkeletonPanel } from "../components/Skeleton";
import { RecordingPanel, type PanelBook, type PanelChapter, type PanelRole } from "../components/recording/RecordingPanel";
import { useExpectedFilename, useRecordingUploads } from "../components/recording/useRecordingUploads";
import { useToast } from "../components/ToastProvider";
import { EmptyState, LinkButton } from "../ui";
import { ScriptReader } from "../v2/ScriptReader";
import type { ReaderBookChapters, ReaderCastEntry, ReaderChapterPayload } from "../v2/types";
import type { BooksResponse, DictorRecentFile, DictorWorkspaceResponse, MeResponse } from "../types";
import "../components/recording/recording.css";

/** book statuses that mean «дикторам открыто» */
const PUBLISHED_BOOK = new Set(["published", "published_to_dictor", "ready_for_mix"]);
const NO_CAST: ReaderCastEntry[] = [];
const SEP = "";

function sameActor(a: string, b: string): boolean {
  const left = a.trim().toLocaleLowerCase("ru-RU");
  return left !== "" && left === b.trim().toLocaleLowerCase("ru-RU");
}

function chapterLabel(chapter: PanelChapter | undefined): string {
  if (!chapter) return "";
  return chapter.title.trim() || `Глава ${chapter.index}`;
}

export function RecordingPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedBookId = searchParams.get("book_id") || "";
  const requestedChapterId = searchParams.get("chapter_id") || "";
  const requestedRole = searchParams.get("role") || "";
  const preview = searchParams.get("preview") === "1";
  const openStressQueue = searchParams.get("stress") === "1";

  const meQuery = useQuery({ queryKey: ["me"], queryFn: () => apiGet<MeResponse>("/api/me") });
  const booksQuery = useQuery({ queryKey: ["books"], queryFn: () => apiGet<BooksResponse>("/api/books") });

  const isAgent = isAgentRoles(meQuery.data?.roles ?? []);

  /* Владелец и автор грузят за других: к ним попадают записи, присланные мимо системы —
     почтой, в мессенджере, на флешке. Агент — потому что ведёт актёров и грузит за них
     по роду занятий. Дикторам сервер всё равно подставит их собственное имя. */
  const canUploadForOthers =
    (meQuery.data?.roles ?? []).some((role) => role === "admin" || role === "author") || isAgent;

  /* А вот удаление с этим признаком больше не совпадает. Они совпадали, пока «грузит за
     других» носили только владелец и автор; агент грузит за других и не удаляет ничего. */
  const canDeleteAny = (meQuery.data?.roles ?? []).some((role) => role === "admin" || role === "author");
  const [uploadAs, setUploadAs] = useState("");
  const actorName = (canUploadForOthers && uploadAs.trim()) || meQuery.data?.display_name || "";
  const books = booksQuery.data?.items ?? [];
  const publishedBooks = useMemo<PanelBook[]>(
    () =>
      books
        .filter((book) => PUBLISHED_BOOK.has(book.status))
        .map((book) => ({ id: book.id, title: book.display_title || book.title })),
    [books],
  );

  // One published book is taken silently; the picker only appears for two or more.
  const bookId = requestedBookId || publishedBooks[0]?.id || "";

  const workspaceQuery = useQuery({
    queryKey: ["recording-workspace", bookId, requestedChapterId],
    queryFn: () => {
      const params = new URLSearchParams();
      if (bookId) params.set("book_id", bookId);
      if (requestedChapterId) params.set("chapter_id", requestedChapterId);
      const query = params.toString();
      return apiGet<DictorWorkspaceResponse>(`/api/recording/workspace${query ? `?${query}` : ""}`);
    },
    enabled: Boolean(requestedBookId) || booksQuery.isFetched,
    placeholderData: keepPreviousData,
  });
  const workspace = workspaceQuery.data;

  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  /* Кнопка живёт в самой таблице (см. `RecordingPanel`) — она уже спросила «точно?»
     словами про роль, главу и длительность; сюда долетает только решённое «да».
     Ручка отвечает человеческим текстом на отказ («Отказы приходят с человеческим
     текстом, не кодом»), поэтому `describeApiError` берёт его как есть. */
  const deleteFileMutation = useMutation({
    mutationFn: (fileId: string) =>
      apiPostJson<{ ok: boolean }>(`/api/recording/files/${encodeURIComponent(fileId)}/delete`, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["recording-workspace"] });
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Не удалено", detail: describeApiError(error, "Не удалось удалить запись.") });
    },
  });

  // Preview only: an unpublished book has no workspace chapters, so take the v2-marked ones.
  const previewChaptersQuery = useQuery({
    queryKey: ["v2", "book-chapters", bookId],
    queryFn: () => apiGet<ReaderBookChapters>(`/api/v2/books/${encodeURIComponent(bookId)}/chapters`),
    enabled: preview && Boolean(bookId) && workspaceQuery.isSuccess && (workspace?.chapters.length ?? 0) === 0,
  });

  const chapters = useMemo<PanelChapter[]>(() => {
    const published = (workspace?.chapters ?? []).map((chapter) => ({
      id: chapter.id,
      index: chapter.chapter_index,
      title: chapter.chapter_title,
    }));
    if (published.length || !preview) return published;
    return (previewChaptersQuery.data?.chapters ?? [])
      .filter((chapter) => chapter.has_v2)
      .map((chapter) => ({ id: chapter.id, index: chapter.index, title: chapter.title }));
  }, [workspace?.chapters, preview, previewChaptersQuery.data]);
  const allowedChapterIds = useMemo(() => new Set(chapters.map((chapter) => chapter.id)), [chapters]);

  // The chapter in the address wins when it is one of ours; otherwise the workspace's pick, else the first.
  const workspaceChapterId = workspace?.selected_chapter?.id ?? "";
  const chapterId = chapters.some((chapter) => chapter.id === requestedChapterId)
    ? requestedChapterId
    : chapters.some((chapter) => chapter.id === workspaceChapterId)
      ? workspaceChapterId
      : chapters[0]?.id ?? "";
  const chapter = chapters.find((item) => item.id === chapterId);

  const goToChapter = useCallback(
    (id: string) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("chapter_id", id);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );
  const goToBook = useCallback(
    (id: string) => {
      setRole("");
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("book_id", id);
          next.delete("chapter_id");
          next.delete("role");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // The reader's cast is the preview fallback for roles (the workspace has none for an unpublished book).
  const [readerCast, setReaderCast] = useState<ReaderCastEntry[]>(NO_CAST);
  const onReaderLoaded = useCallback((payload: ReaderChapterPayload) => setReaderCast(payload.cast), []);

  const cast = useMemo<PanelRole[]>(() => {
    const fromWorkspace = (workspace?.cast ?? []).map((item) => ({ name: item.name, lines: item.lines, actor: item.actor_name }));
    if (fromWorkspace.length || !preview) return fromWorkspace;
    return readerCast.map((item) => ({ name: item.name, lines: item.lines, actor: item.actor }));
  }, [workspace?.cast, preview, readerCast]);
  const myRoles = useMemo<PanelRole[]>(() => {
    const fromWorkspace = (workspace?.my_roles ?? []).map((item) => ({ name: item.name, lines: item.lines, actor: item.actor_name }));
    if (fromWorkspace.length || !preview) return fromWorkspace;
    return cast.filter((item) => sameActor(item.actor || "", actorName));
  }, [workspace?.my_roles, preview, cast, actorName]);
  const myRoleNames = useMemo(() => myRoles.map((item) => item.name), [myRoles]);
  /** Имена актёров из каста — подсказка, чтобы имя совпало с тем, что знает система. */
  const castActorNames = useMemo(
    () => [...new Set((cast.map((item) => item.actor || "").filter(Boolean)))].sort((a, b) => a.localeCompare(b, "ru")),
    [cast],
  );

  // The role: the actor's first own role until they pick another; a typed role survives chapter changes.
  const [role, setRole] = useState(requestedRole);
  const myRolesKey = myRoleNames.join(SEP);
  useEffect(() => {
    if (requestedRole) setRole(requestedRole);
  }, [requestedRole]);
  useEffect(() => {
    if (!role && myRolesKey) setRole(myRolesKey.split(SEP)[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [myRolesKey]);
  const chooseRole = useCallback(
    (nextRole: string) => {
      setRole(nextRole);
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (nextRole) next.set("role", nextRole);
          else next.delete("role");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );
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

  /* Роль выбрана — значит выбран и тот, за кого грузим: редактор, берущийся за чужую
     роль, почти всегда грузит за её актёра. Раньше поле молча оставалось со своим
     именем, и запись уходила в собственные пробы того, кто её загружал. */
  /* Хвостовой «?» — пометка агента «предложен, не утверждён» (`approved_actor`
     на сервере). В имя на файле он попасть не может: `actor_name` записи — настоящий
     человек, которого потом ищут по имени и в касте, и в уведомлениях. */
  const roleOwner = useMemo(
    () => (cast.find((item) => item.name === role.trim())?.actor?.trim() || "").replace(/\?+$/, "").trim(),
    [cast, role],
  );
  useEffect(() => {
    if (canUploadForOthers) setUploadAs(roleOwner);
  }, [roleOwner, canUploadForOthers]);

  // The reader folds to the role the panel names; before a pick, to all of the actor's own roles.
  const readerRoles = useMemo(() => (role.trim() ? [role.trim()] : myRoleNames), [role, myRoleNames]);

  const bookCode = workspace?.selected_book?.code || "";
  /* «Глава не указана» — отдельный флажок, а не пустой `chapterId`: глава в адресе
     ведёт читалку слева, и проба, которую читали неизвестно откуда, не должна её
     закрывать. Флажок независим от вида загрузки, иначе подсказка имени считалась бы
     по главе, которая от неё же и зависит. */
  const [noChapter, setNoChapter] = useState(false);
  const uploadChapter = noChapter ? "" : chapterLabel(chapter);
  // Проба это или дубль, решает сервер по роли и актёру; здесь только показываем.
  const expected = useExpectedFilename({ bookCode, chapter: uploadChapter, role, actorName });
  const audition = expected.kind === "audition";
  const upload = useRecordingUploads({ bookCode, chapter: uploadChapter, role, actorName, kind: expected.kind });

  const listedBook = books.find((book) => book.id === bookId);
  const bookTitle =
    workspace?.selected_book?.display_title ||
    listedBook?.display_title ||
    listedBook?.title ||
    previewChaptersQuery.data?.book.title ||
    "";
  const bookLink = meQuery.data?.full_access && bookId ? `/books/${encodeURIComponent(bookId)}` : "/";

  if (workspaceQuery.isLoading || (preview && previewChaptersQuery.isLoading)) {
    return <SkeletonPanel label="Открываю запись…" lines={6} />;
  }
  if (workspaceQuery.isError) {
    return (
      <EmptyState
        icon="mic"
        text={describeApiError(workspaceQuery.error, "Не удалось открыть рабочее место записи.")}
        action={<LinkButton to="/">Открыть библиотеку</LinkButton>}
      />
    );
  }

  // Nothing to record anywhere: no book asked for and none published.
  if (!bookId && !workspace?.selected_book) {
    return (
      <EmptyState
        icon="mic"
        text="Ни у одной книги ещё нет опубликованных глав. Запись откроется, когда режиссёр опубликует сценарий."
        action={<LinkButton to="/">Открыть библиотеку</LinkButton>}
      />
    );
  }

  const uploadDisabledReason = !chapters.length
    ? "Загрузка откроется после публикации глав."
    : preview && !bookCode
      ? "Предпросмотр: книга не опубликована, загрузка отключена."
      : undefined;

  const stage = chapters.length ? (
    <ScriptReader
      key={bookId}
      embedded
      chapterId={chapterId}
      onChapterChange={goToChapter}
      initialRoles={readerRoles}
      allowedChapterIds={allowedChapterIds}
      onLoaded={onReaderLoaded}
      openStressQueue={openStressQueue}
      onStressQueueOpened={clearStressRequest}
      roleScriptBase="/recording/role"
      roleScriptSearch={searchParams.toString()}
    />
  ) : (
    <EmptyState
      icon="mic"
      text={
        bookTitle
          ? `У книги «${bookTitle}» ещё нет опубликованных глав. Сценарий появится здесь после публикации.`
          : "У книги ещё нет опубликованных глав. Сценарий появится здесь после публикации."
      }
      action={<LinkButton to={bookLink}>{bookLink === "/" ? "Открыть библиотеку" : "Открыть книгу"}</LinkButton>}
    />
  );

  const panelBooks: PanelBook[] =
    publishedBooks.length > 1 ? publishedBooks : bookId ? [{ id: bookId, title: bookTitle || "Книга" }] : [];

  return (
    <div className="rec-layout">
      <div className="rec-stage">{stage}</div>
      <RecordingPanel
        books={panelBooks}
        bookId={bookId}
        onBook={goToBook}
        actorName={actorName}
        canUploadForOthers={canUploadForOthers}
        onActorName={setUploadAs}
        actorNames={castActorNames}
        roleOwner={roleOwner}
        myRoles={myRoles}
        cast={cast}
        role={role}
        onRole={chooseRole}
        roleScriptSearch={searchParams.toString()}
        chapters={chapters}
        chapterId={noChapter ? "" : chapterId}
        onChapter={(id) => {
          setNoChapter(!id);
          if (id) goToChapter(id);
        }}
        kind={expected.kind}
        chapterOptional={audition}
        expectedFilename={expected.filename}
        notice={preview ? "Предпросмотр: книга не опубликована, показаны размеченные главы." : undefined}
        upload={{
          files: upload.files,
          preview: upload.preview,
          status: upload.status,
          isUploading: upload.isUploading,
          progress: upload.progress,
          blocked: upload.blocked,
          disabledReason: uploadDisabledReason,
          confirmChapter: upload.confirmChapter,
          chapterMismatchRefused: upload.chapterMismatchRefused,
          onSelect: upload.select,
          onClear: upload.clear,
          onUpload: () => void upload.uploadBatch(),
          onConfirmChapter: upload.onConfirmChapter,
        }}
        recentFiles={workspace?.recent_files ?? []}
        loading={workspaceQuery.isFetching && !workspace}
        canDeleteAny={canDeleteAny}
        canDeleteOwn={!isAgent}
        deletingFileId={deleteFileMutation.isPending ? deleteFileMutation.variables : undefined}
        /* `mutate`, а не `void mutateAsync`: отказ уже разобран в `onError`, а
           отброшенный промис `mutateAsync` вдобавок всплывает как
           unhandled rejection — в консоли и в любом её сборщике. */
        onDeleteFile={(row: DictorRecentFile) => deleteFileMutation.mutate(row.id)}
      />
    </div>
  );
}
