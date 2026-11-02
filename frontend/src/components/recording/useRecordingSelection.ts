/**
 * Что выбрано для работы диктора: книга, глава, роль, за кого грузим.
 *
 * Общее для «Сценария» (`/recording`) и «Загрузки» (`/takes`): до 29.10 это была одна
 * страница с текстом слева и формой справа. Владелец разнёс их, чтобы освободить
 * страницу, где читают текст, и дать загрузке место. Выбор живёт в адресе
 * (`book_id`, `chapter_id`, `role`), поэтому переход между разделами сохраняет его.
 *
 * Endpoints:
 *   GET /api/recording/workspace?book_id=&chapter_id=  → published chapters, cast, my_roles, recent_files
 *   GET /api/v2/books/:id/chapters                      → only for `?preview=1` on an unpublished book
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { apiGet } from "../../api/client";
import { isAgentRoles } from "../../layout/AppShell";
import type { ReaderBookChapters, ReaderCastEntry, ReaderChapterPayload } from "../../v2/types";
import type { BooksResponse, DictorWorkspaceResponse, MeResponse } from "../../types";
import type { PanelBook, PanelChapter, PanelRole } from "./RecordingPanel";

/** book statuses that mean «дикторам открыто» */
const PUBLISHED_BOOK = new Set(["published", "published_to_dictor", "ready_for_mix"]);
const NO_CAST: ReaderCastEntry[] = [];
const SEP = "";

export function sameActor(a: string, b: string): boolean {
  const left = a.trim().toLocaleLowerCase("ru-RU");
  return left !== "" && left === b.trim().toLocaleLowerCase("ru-RU");
}

export function chapterLabel(chapter: PanelChapter | undefined): string {
  if (!chapter) return "";
  return chapter.title.trim() || `Глава ${chapter.index}`;
}

export function useRecordingSelection() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedBookId = searchParams.get("book_id") || "";
  const requestedChapterId = searchParams.get("chapter_id") || "";
  const requestedRole = searchParams.get("role") || "";
  const preview = searchParams.get("preview") === "1";

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
  const isAdmin = (meQuery.data?.roles ?? []).includes("admin");
  /* Озвученная книга дикторам не нужна: без этого «Сценарий» без book_id открыл бы её
     первой. Владелец её видит — ему нужно вернуть книгу в работу. */
  const publishedBooks = useMemo<PanelBook[]>(
    () =>
      books
        .filter((book) => PUBLISHED_BOOK.has(book.status) && (isAdmin || !book.voiced_at))
        .map((book) => ({ id: book.id, title: book.display_title || book.title })),
    [books, isAdmin],
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
      released: Boolean(chapter.released_at),
    }));
    if (published.length || !preview) return published;
    return (previewChaptersQuery.data?.chapters ?? [])
      .filter((chapter) => chapter.has_v2)
      .map((chapter) => ({ id: chapter.id, index: chapter.index, title: chapter.title, released: Boolean(chapter.released_at) }));
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

  // The role: the actor's first own role until they pick another; a typed role survives chapter changes.
  const [role, setRole] = useState(requestedRole);
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

  const listedBook = books.find((book) => book.id === bookId);
  const bookTitle =
    workspace?.selected_book?.display_title ||
    listedBook?.display_title ||
    listedBook?.title ||
    previewChaptersQuery.data?.book.title ||
    "";
  const bookLink = meQuery.data?.full_access && bookId ? `/books/${encodeURIComponent(bookId)}` : "/";
  const panelBooks: PanelBook[] =
    publishedBooks.length > 1 ? publishedBooks : bookId ? [{ id: bookId, title: bookTitle || "Книга" }] : [];

  return {
    searchParams,
    setSearchParams,
    preview,
    isAgent,
    canUploadForOthers,
    canDeleteAny,
    actorName,
    setUploadAs,
    bookId,
    bookTitle,
    bookLink,
    panelBooks,
    workspace,
    workspaceQuery,
    previewChaptersQuery,
    chapters,
    allowedChapterIds,
    chapterId,
    chapter,
    goToChapter,
    goToBook,
    onReaderLoaded,
    cast,
    myRoles,
    myRoleNames,
    castActorNames,
    role,
    chooseRole,
    roleOwner,
  };
}
