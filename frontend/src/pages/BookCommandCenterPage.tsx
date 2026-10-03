import "./BookCommandCenterPage.css";

import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { BookSettingsCard } from "../components/book/v2/BookSettingsCard";
import { CastCard } from "../components/book/v2/CastCard";
import { HubMenu, type HubMenuItem } from "../components/book/v2/HubMenu";
import { PipelineStepper } from "../components/book/v2/PipelineStepper";
import { RecordingCard } from "../components/book/v2/RecordingCard";
import { ServiceCard } from "../components/book/v2/ServiceCard";
import { hubKeys, useHubActions, useHubProgress, type HubActions } from "../components/book/v2/hubApi";
import { Button, EmptyState, LinkButton, PageHeader, StatusChip, describeStatus } from "../ui";
import { useBookCast, useBookProfile } from "../v2/editorApi";
import type { ReaderBookChapters } from "../v2/types";
import type { BooksResponse, MeResponse, V2ProgressResponse } from "../types";
import { normalizeBookStatus } from "../viewModels/bookWorkflow";
import {
  choosePrimaryAction,
  hubStatusCode,
  hubSubtitle,
  isRunActive,
  normalizeProgress,
  type HubStepKey,
  type PrimaryKind,
} from "../viewModels/bookHub";
import { describeNextStep } from "./CommandCenterPage";

// Publishing messages the actors, so it takes two clicks: the first arms, the second sends.
const PUBLISH_ARM_MS = 6000;
const WAIT_HINT = "Сначала остановите прогон";

export function BookCommandCenterPage() {
  const { bookId = "" } = useParams();
  const navigate = useNavigate();

  const booksQuery = useQuery({
    queryKey: ["books"],
    queryFn: () => apiGet<BooksResponse>("/api/books"),
    refetchInterval: 15_000,
  });
  const book = useMemo(() => booksQuery.data?.items.find((b) => b.id === bookId) ?? null, [booksQuery.data, bookId]);
  const hasBook = Boolean(book);

  const progressQuery = useHubProgress(bookId, hasBook);
  const progress = useMemo(() => normalizeProgress(progressQuery.data), [progressQuery.data]);
  // Layout already owns this query. Reusing its key reads the authenticated user
  // from React Query's cache without introducing a second identity contract.
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => apiGet<MeResponse>("/api/me"),
  });
  const chaptersQuery = useQuery({
    queryKey: hubKeys.chapters(bookId),
    queryFn: () => apiGet<ReaderBookChapters>(`/api/v2/books/${encodeURIComponent(bookId)}/chapters`),
    enabled: hasBook,
    retry: false,
    staleTime: 30_000,
  });
  const cast = useBookCast(bookId, hasBook);
  const profile = useBookProfile(bookId, hasBook);
  const actions = useHubActions(bookId, { onDeleted: () => navigate("/", { replace: true }) });
  const [publishArmed, setPublishArmed] = useState(false);
  useEffect(() => {
    if (!publishArmed) return;
    const timer = window.setTimeout(() => setPublishArmed(false), PUBLISH_ARM_MS);
    return () => window.clearTimeout(timer);
  }, [publishArmed]);
  const publishClick = () => {
    if (!publishArmed) { setPublishArmed(true); return; }
    setPublishArmed(false);
    actions.publish.mutate();
  };

  if (booksQuery.isLoading) return <HubSkeleton />;
  if (booksQuery.isError) {
    return (
      <div className="hub">
        <div className="ui-note ui-note--error hub-error" role="alert">
          <span>Не удалось загрузить книгу: {describeApiError(booksQuery.error, "сервер не ответил")}.</span>
          <Button size="sm" variant="secondary" onClick={() => booksQuery.refetch()} loading={booksQuery.isFetching}>Повторить</Button>
        </div>
      </div>
    );
  }
  if (!book) {
    return (
      <div className="hub">
        <PageHeader title="Книга не найдена" back={{ to: "/", label: "Библиотека" }} />
        <div className="panel">
          <EmptyState icon="books" text="Такой книги нет: её удалили или ссылка устарела." action={<LinkButton to="/" variant="secondary">Открыть библиотеку</LinkButton>} />
        </div>
      </div>
    );
  }

  const title = book.display_title || book.title;
  const status = normalizeBookStatus(book);
  const chipCode = hubStatusCode(status, progress);
  const chipMeta = describeStatus(chipCode);
  const firstReadable = chaptersQuery.data?.chapters.find((c) => c.has_v2);
  const hasReader = Boolean(firstReadable);
  const readerTo = firstReadable
    ? `/reader/${encodeURIComponent(firstReadable.id)}?book_id=${encodeURIComponent(bookId)}`
    : `/reader?book_id=${encodeURIComponent(bookId)}`;
  const compareTo = firstReadable ? `${readerTo}&compare=1` : readerTo;
  const stressTo = firstReadable ? `${readerTo}&stress=1` : readerTo;
  const stressAvailable = Boolean(
    meQuery.data?.full_access
      || (meQuery.data?.roles ?? []).some((role) => role === "admin" || role === "author" || role === "dictor"),
  );
  // Консилиум стоит денег: блок видит только тот, кто правит разметку (как запуск прогона).
  const canRunConsilium = Boolean(
    meQuery.data?.full_access || (meQuery.data?.roles ?? []).some((role) => role === "admin" || role === "author"),
  );
  const consiliumTo = firstReadable && canRunConsilium ? `${readerTo}&consilium=1` : undefined;
  // звуковая разметка — тоже платный прогон редактора; итог ведёт в Читалку к «Местам книги»
  const soundTo = firstReadable && canRunConsilium ? `${readerTo}&sound=1` : undefined;
  const primary = choosePrimaryAction(status, progress, hasReader);
  const runActive = isRunActive(progress.run);
  const author = profile.data?.author?.name || book.author || "";
  const roles = cast.data?.characters.length || progress.counts.cast || book.character_count || 0;
  // the library's next-step words; its type still says `tokens: number`, the contract sends an object
  const next = describeNextStep(book, progress as unknown as V2ProgressResponse);

  const rerun = (step: HubStepKey) => {
    actions.run.mutate({ steps: [step], force: true });
  };

  const menu: HubMenuItem[] = [
    ...(primary.kind !== "run" && primary.kind !== "resume"
      ? [{ key: "run", label: "Запустить разметку заново", onSelect: () => actions.run.mutate({ force: true }), disabled: runActive, hint: WAIT_HINT }]
      : []),
    ...(book?.status === "published_to_dictor"
      ? [{ key: "unpublish", label: "Вернуть на проверку", onSelect: () => actions.unpublish.mutate(), disabled: actions.unpublish.isPending, hint: "" }]
      : []),
    // Раньше это была кнопка «Собрать персонажей заново» на отдельном экране
    // приёмки. Извлечение ничего не удаляет и не переписывает правки человека:
    // совпавшую по имени роль оно только дополняет пустыми полями, а роль, чьё
    // имя не совпало, добавляет ВТОРОЙ строкой — то есть кнопка наращивает
    // дубли, которые потом разбирают в карте персонажей.
    {
      key: "cast",
      label: "Собрать персонажей заново",
      onSelect: () => rerun("cast"),
      disabled: runActive,
      hint: WAIT_HINT,
      note: "Не удаляет и не перезаписывает правки: совпавшую роль дополняет, а несовпавшую добавляет второй строкой — дубли потом разбирают в карте персонажей.",
    },
    { key: "attribute", label: "Повторить роли", onSelect: () => rerun("attribute"), disabled: runActive, hint: WAIT_HINT },
    { key: "stress", label: "Повторить ударения", onSelect: () => rerun("stress"), disabled: runActive, hint: WAIT_HINT },
  ];

  return (
    <div className="hub">
      <PageHeader
        back={{ to: "/", label: "Библиотека" }}
        title={
          <span className="hub-title">
            {title}
            <StatusChip status={chipCode} label={chipMeta.known ? undefined : book.progress?.effective_status_label || book.status_label || undefined} />
          </span>
        }
        subtitle={hubSubtitle(author, book.chapter_count, roles) || book.source_filename}
        actions={
          <>
            <PrimaryButton kind={primary.kind} label={primary.label} readerTo={readerTo} actions={actions} waiting={chaptersQuery.isLoading} publishArmed={publishArmed} onPublish={publishClick} />
            <HubMenu items={menu} bookTitle={title} onDelete={() => actions.remove.mutate()} deletePending={actions.remove.isPending} />
          </>
        }
      />

      {progressQuery.isError ? (
        <p className="ui-note ui-note--error" role="alert">
          Ход работы недоступен: {describeApiError(progressQuery.error, "сервер не ответил")}. Шаги показаны без состояния.
        </p>
      ) : null}

      <PipelineStepper
        bookId={bookId}
        progress={progress}
        next={next.text && next.text !== "—" ? next : undefined}
        readerTo={readerTo}
        compareTo={compareTo}
        stressTo={stressTo}
        review={progress.review ?? { approved: 0, attributed: 0, total: 0, published: 0 }}
        onApproveAll={() => actions.approveAll.mutate()}
        approvePending={actions.approveAll.isPending}
        // while the switch is saving, show what was asked for: a controlled checkbox
        // that snaps back until the server answers reads as a click that did nothing
        autoPublish={actions.autoPublish.isPending ? Boolean(actions.autoPublish.variables) : Boolean(progress.auto_publish)}
        onAutoPublish={(enabled) => actions.autoPublish.mutate(enabled)}
        autoPublishPending={actions.autoPublish.isPending}
        hasReader={hasReader}
        stressAvailable={stressAvailable}
        onPublish={publishClick}
        publishPending={actions.publish.isPending}
        publishArmed={publishArmed}
        consiliumTo={consiliumTo}
        soundTo={soundTo}
      />

      <div className="hub-columns">
        <div className="hub-col">
          {/* Своей кнопки запуска у карточки больше нет: тот же «Запустить разметку»
              уже стоит в `PrimaryButton` над ней — дублировать незачем. */}
          <CastCard bookId={bookId} />
        </div>
        <div className="hub-col">
          <RecordingCard bookId={bookId} />
        </div>
      </div>

      {/* Mounted only once progress has settled: the auto-open default reads
          `progress.steps`/`progress.model`, and mounting on the empty
          before-fetch progress would lock in the wrong default (see the
          card's own note on `useState`'s lazy initializer). */}
      {progressQuery.isLoading ? null : (
        <BookSettingsCard
          key={bookId}
          bookId={bookId}
          progress={progress}
          title={book.title}
          author={book.author || ""}
          authorName={profile.data?.author?.name || ""}
          locked={runActive}
        />
      )}

      <ServiceCard
        bookTitle={title}
        bookStatus={book.status}
        stopRequested={Boolean(book.stop_requested || progress.stop_requested)}
        progress={progress}
        actions={actions}
      />
    </div>
  );
}

type PrimaryButtonProps = { kind: PrimaryKind; label: string; readerTo: string; actions: HubActions; waiting: boolean; publishArmed: boolean; onPublish: () => void };

/** `waiting` = the chapter list is still loading, so the reader/run choice is not final yet. */
function PrimaryButton({ kind, label, readerTo, actions, waiting, publishArmed, onPublish }: PrimaryButtonProps) {
  if (waiting && (kind === "run" || kind === "reader")) return <Button variant="primary" loading>{label}</Button>;
  if (kind === "reader") return <LinkButton to={readerTo} variant="primary" icon={<Icon name="doc" />}>{label}</LinkButton>;
  if (kind === "publish") {
    return (
      <Button variant="primary" loading={actions.publish.isPending} icon={<Icon name="mic" />} onClick={onPublish}
        title={publishArmed ? "Дикторы получат уведомление" : "Первый клик — проверить, второй — отправить"}>
        {publishArmed ? "Подтвердить: уведомить дикторов" : label}
      </Button>
    );
  }
  if (kind === "stop") return <Button variant="primary" loading={actions.stop.isPending} onClick={() => actions.stop.mutate()}>{label}</Button>;
  return (
    <Button variant="primary" icon={<Icon name="play" />} loading={actions.run.isPending} onClick={() => actions.run.mutate({})}>
      {label}
    </Button>
  );
}

function HubSkeleton() {
  return (
    <div className="hub" aria-busy="true" aria-live="polite">
      <div className="hub-skel hub-skel--head"><span /><span /></div>
      <div className="panel hub-card"><div className="hub-skel"><span /><span /><span /></div></div>
      <div className="hub-columns">
        <div className="panel hub-card hub-col"><div className="hub-skel"><span /><span /><span /><span /></div></div>
        <div className="panel hub-card hub-col"><div className="hub-skel"><span /><span /><span /></div></div>
      </div>
      <span className="hub-sr">Загрузка книги</span>
    </div>
  );
}
