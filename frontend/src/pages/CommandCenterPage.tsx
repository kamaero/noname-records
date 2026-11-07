import "./CommandCenterPage.css";

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiGet, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { UploadPanel } from "../components/upload/UploadPanel";
import { FirstStepsCard } from "../components/FirstStepsCard";
import { Button, DataTable, EmptyState, PageHeader, StatusChip, StepDots, describeStatus, type Column, type StepDot } from "../ui";
import { normalizeBookStatus } from "../viewModels/bookWorkflow";
import { formatServerDateTime } from "../utils/serverTime";
import type { BookListItem, BooksResponse, V2ProgressResponse } from "../types";

/* ---------- v2 progress, one lazy query per visible row ---------- */

const ACTIVE_RUN = new Set(["running", "processing", "queued", "stopping", "starting"]);

async function fetchProgress(bookId: string): Promise<V2ProgressResponse | null> {
  try {
    return await apiGet<V2ProgressResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/progress`);
  } catch (error) {
    // no v2 data for this book yet (or the endpoint is not deployed) — dots stay pending
    if (error instanceof ApiError && (error.status === 404 || error.status === 405)) return null;
    throw error;
  }
}

function useV2Progress(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["v2-progress", bookId],
    queryFn: () => fetchProgress(bookId),
    enabled,
    retry: false,
    staleTime: 10_000,
    refetchInterval: (query) => {
      const run = query.state.data?.run;
      return run && ACTIVE_RUN.has(run.status) ? 5_000 : 30_000;
    },
  });
}

/** True once the element has been on screen (stays true afterwards). */
function useSeen<T extends Element>(): [RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node || seen) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setSeen(true);
        observer.disconnect();
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [seen]);
  return [ref, seen];
}

/* ---------- words ---------- */

const STEP_LABEL: Record<string, string> = {
  segment: "segmentation",
  cast: "characters",
  attribute: "roles",
  stress: "word stress",
  review: "review",
  publish: "publication",
};

function shortError(text: string): string {
  const line = text.split("\n")[0].trim();
  return line.length > 72 ? `${line.slice(0, 70)}…` : line;
}

type NextStep = { text: string; tone?: "danger" | "amber" | "blue" };

/** What happens next for a book, from its status plus the v2 run when we have it. */
export function describeNextStep(book: BookListItem, progress: V2ProgressResponse | null | undefined): NextStep {
  const status = normalizeBookStatus(book);
  const run = progress?.run ?? null;

  if (run && (run.status === "running" || run.status === "processing" || run.status === "starting")) {
    const step = progress?.steps.find((s) => s.key === run.step)?.label || STEP_LABEL[run.step] || run.step;
    const chapters = run.chapters_total ? ` · ${run.chapters_done}/${run.chapters_total} chapters` : "";
    return { text: `Running: ${step.toLowerCase()}${chapters}`, tone: "blue" };
  }
  if (run?.status === "queued") return { text: "Queued for annotation", tone: "blue" };
  // The flag only means anything while something is running: left raised by a finished
  // run it used to make the book say «останавливается» for ever.
  const stopping = run?.status === "stopping" || status === "stopping" || (book.stop_requested && run?.status === "running");
  if (stopping) return { text: "Stopping", tone: "amber" };
  if (run?.status === "failed" || status === "failed" || status.includes("error")) {
    return { text: run?.error ? `Error: ${shortError(run.error)}` : "Review error", tone: "danger" };
  }
  if (status === "stalled") return { text: "Restart annotation", tone: "amber" };
  if (status === "stopped") return { text: "Resume annotation", tone: "amber" };

  switch (status) {
    case "uploaded":
    case "queued":
      return { text: "Start annotation" };
    case "processing":
    case "char_extracting":
    case "waiting":
      return { text: book.progress?.action_hint || "Preparing", tone: "blue" };
    case "author_review":
    case "needs_review":
    case "pending_review":
      return { text: "Review roles and word stress" };
    case "partially_approved":
      return { text: "Approve remaining chapters", tone: "amber" };
    case "approved":
    case "done":
      return { text: "Publish to voice actors" };
    case "published":
    case "published_to_dictor":
      return { text: "Record" };
    case "ready_for_mix":
      return { text: "Mix" };
  }
  return { text: book.progress?.action_hint || "—" };
}

function stepDots(progress: V2ProgressResponse | null | undefined): StepDot[] {
  if (!progress?.steps?.length) return [];
  return progress.steps.map((s) => ({ key: s.key, label: s.label, state: s.state }));
}

function relativeTime(iso: string, now: number): { text: string; title: string } {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return { text: iso || "—", title: iso || "" };
  const diff = Math.max(0, now - date.getTime());
  const min = Math.floor(diff / 60_000);
  const hours = Math.floor(min / 60);
  const days = Math.floor(hours / 24);
  let text: string;
  if (min < 1) text = "just now";
  else if (min < 60) text = `${min} min ago`;
  else if (hours < 24) text = `${hours} hr ago`;
  else if (days === 1) text = "yesterday";
  else if (days < 7) text = `${days} days ago`;
  else text = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", year: days > 300 ? "numeric" : undefined }).format(date);
  return { text, title: formatServerDateTime(iso) };
}

function bookMeta(book: BookListItem): string {
  const parts: string[] = [];
  if (book.chapter_count) parts.push(`${book.chapter_count} ch.`);
  if (book.total_chars) parts.push(`${Math.round(book.total_chars / 1000)} k characters`);
  return parts.join(" · ");
}

/* ---------- cells ---------- */

function NextStepCell({ book }: { book: BookListItem }) {
  const [ref, seen] = useSeen<HTMLSpanElement>();
  const progress = useV2Progress(book.id, seen);
  const next = describeNextStep(book, progress.data);
  return (
    <span ref={ref} className={["lib-next", next.tone ? `lib-next--${next.tone}` : ""].filter(Boolean).join(" ")}>
      {next.text}
    </span>
  );
}

function StepsCell({ book }: { book: BookListItem }) {
  const [ref, seen] = useSeen<HTMLSpanElement>();
  const progress = useV2Progress(book.id, seen);
  return (
    <span ref={ref} className="lib-steps">
      <StepDots steps={stepDots(progress.data)} />
    </span>
  );
}

function StatusCell({ book }: { book: BookListItem }) {
  const code = normalizeBookStatus(book);
  const meta = describeStatus(code);
  const serverLabel = book.progress?.effective_status_label || book.status_label || "";
  return <StatusChip status={code} label={meta.known ? undefined : serverLabel || undefined} title={code} />;
}

/* ---------- page ---------- */

export function CommandCenterPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") || "").trim().toLowerCase();
  const [uploadOpen, setUploadOpen] = useState(params.get("upload") === "1");
  const [now, setNow] = useState(() => Date.now());

  const booksQuery = useQuery({
    queryKey: ["books"],
    queryFn: () => apiGet<BooksResponse>("/api/books"),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const books = booksQuery.data?.items ?? [];
  const visible = useMemo(() => {
    if (!q) return books;
    return books.filter((b) => [b.display_title, b.title, b.author || "", b.source_filename].some((s) => String(s || "").toLowerCase().includes(q)));
  }, [books, q]);

  const clearSearch = () => {
    const next = new URLSearchParams(params);
    next.delete("q");
    setParams(next, { replace: true });
  };

  const columns: Column<BookListItem>[] = useMemo(
    () => [
      {
        key: "book",
        header: "Book",
        render: (b) => (
          <span className="lib-book">
            <strong className="lib-title">{b.display_title || b.title}</strong>
            {/* автор уже стоит в витрине «Автор - "Название"» — второй строкой идёт объём,
                а не он же второй раз */}
            <span className="lib-author">{bookMeta(b) || b.author || "—"}</span>
          </span>
        ),
      },
      { key: "status", header: "Status", nowrap: true, render: (b) => <StatusCell book={b} /> },
      { key: "next", header: "Next step", render: (b) => <NextStepCell book={b} />, className: "lib-col-next" },
      { key: "steps", header: "Steps", nowrap: true, render: (b) => <StepsCell book={b} />, className: "lib-col-steps" },
      {
        key: "updated",
        header: "Updated",
        align: "right",
        nowrap: true,
        className: "lib-col-updated",
        render: (b) => {
          const t = relativeTime(b.updated_at || b.created_at, now);
          return (
            <time className="num lib-updated" dateTime={b.updated_at || b.created_at} title={t.title}>
              {t.text}
            </time>
          );
        },
      },
    ],
    [now],
  );

  const isEmpty = !booksQuery.isLoading && !booksQuery.isError && books.length === 0;

  return (
    <div className="lib">
      <PageHeader
        title="Library"
        subtitle={books.length ? `${books.length} ${plural(books.length, "book", "books", "books")}` : undefined}
        actions={
          !isEmpty ? (
            <Button
              variant={uploadOpen ? "secondary" : "primary"}
              icon={<Icon name={uploadOpen ? "close" : "upload"} />}
              aria-expanded={uploadOpen}
              aria-controls="lib-upload"
              onClick={() => setUploadOpen((v) => !v)}
            >
              {uploadOpen ? "Close form" : "Upload book"}
            </Button>
          ) : null
        }
      />

      <FirstStepsCard />

      {uploadOpen ? (
        <section id="lib-upload" className="ui-card ui-card--accent ui-card-pad lib-upload" aria-label="Loading book">
          <h2 className="lib-upload-title">New book</h2>
          <UploadPanel autoFocus onCancel={() => setUploadOpen(false)} />
        </section>
      ) : null}

      {q ? (
        <div className="lib-filter">
          <span>
            Search: <strong>{params.get("q")}</strong> · {visible.length} {plural(visible.length, "book", "books", "books")}
          </span>
          <Button size="sm" variant="ghost" onClick={clearSearch}>
            Clear
          </Button>
        </div>
      ) : null}

      {booksQuery.isError ? (
        <div className="ui-note ui-note--error lib-error" role="alert">
          <span>Could not load books: {describeApiError(booksQuery.error, "the server did not respond")}.</span>
          <Button size="sm" variant="secondary" onClick={() => booksQuery.refetch()} loading={booksQuery.isFetching}>
            Retry
          </Button>
        </div>
      ) : (
        <DataTable
          aria-label="Books"
          columns={columns}
          rows={visible}
          rowKey={(b) => b.id}
          loading={booksQuery.isLoading}
          skeletonRows={4}
          onRowClick={(b) => navigate(`/books/${encodeURIComponent(b.id)}`)}
          rowLabel={(b) => `Open book «${b.display_title || b.title}»`}
          empty={
            q ? (
              <EmptyState icon="search" text="No books match your search." action={<Button variant="secondary" onClick={clearSearch}>Show all books</Button>} />
            ) : (
              <EmptyState
                icon="books"
                text="No books yet. Upload a text to start preparing it."
                action={
                  !uploadOpen ? (
                    <Button variant="primary" icon={<Icon name="upload" />} onClick={() => setUploadOpen(true)}>
                      Upload book
                    </Button>
                  ) : null
                }
              />
            )
          }
        />
      )}
    </div>
  );
}

function plural(n: number, one: string, _few: string, many: string): string {
  return n === 1 ? one : many;
}
