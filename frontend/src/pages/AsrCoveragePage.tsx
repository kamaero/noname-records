/**
 * ASR `/asr` — покрытие книги по главам.
 *
 * Таблица устроена как каст, только в строках главы: сколько ролей записано из
 * скольких, и можно ли забирать главу в сведение. Пока считается по файлам — у роли
 * есть дубль или нет; когда заработает распознавание, в ту же строку встанет второй,
 * точный счёт: сколько реплик сценария и вправду произнесено.
 *
 * Разница между этими двумя числами и есть то, ради чего экран существует: «роль
 * записана» и «роль записана целиком» — не одно и то же, и сегодня мы знаем только
 * первое.
 */
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiPostJson, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { Button, DataTable, EmptyState, LinkButton, PageHeader, type Column } from "../ui";
import { Icon } from "../components/Icon";
import { ChapterFilesRow } from "../components/asr/ChapterFilesRow";
import type { BookRecordingChapter, BookRecordingResponse, BooksResponse, MeResponse } from "../types";
import { plural } from "../viewModels/bookHub";
import { ambientPlan, describeAmbientError, startChapterAmbient, stopChapterAmbient } from "../v2/soundApi";
import "./AsrCoveragePage.css";

/** книги, у которых есть что записывать */
const PUBLISHED_BOOK = new Set(["published", "published_to_dictor", "ready_for_mix"]);

function duration(seconds: number): string {
  const value = Math.round(seconds);
  return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60]
    .map((part) => String(part).padStart(2, "0")).join(":");
}

/** ISO → «дд.мм»; пустое или кривое значение не должно ломать строку. */
function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** Нажатие на кнопку внутри строки — только её собственное дело.
 *
 * Вся строка раскрывает опись главы, и без этого «сверить» запускало бы сверку и
 * заодно разворачивало список файлов, а «архив» — начинал скачивание и разворачивал.
 * Всплытие гасится у каждого действия в строке, включая ссылки: у них своё поведение,
 * и складывать его с чужим нельзя.
 */
function stopRow<T extends { stopPropagation: () => void }>(run?: () => void) {
  return (event: T) => {
    event.stopPropagation();
    run?.();
  };
}

export function AsrCoveragePage() {
  const [params, setParams] = useSearchParams();
  const [onlyStarted, setOnlyStarted] = useState(false);
  /* Раскрытых глав может быть несколько разом: сравнить, что записано в двух соседних,
     — обычное дело, и закрывать предыдущую за человека было бы самоуправством. */
  const [openChapters, setOpenChapters] = useState<Set<string>>(() => new Set());
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { pushToast } = useToast();

  const meQuery = useQuery({ queryKey: ["me"], queryFn: () => apiGet<MeResponse>("/api/me") });
  /* Тот же признак, каким право на удаление решает сервер (`delete_audio_file`): здесь
     только предсказание для интерфейса. Суточное право диктора на свой дубль живёт на
     экране записи — в описи главы стирает тот, кто отвечает за книгу целиком. */
  const canDeleteTakes = (meQuery.data?.roles ?? []).some((role) => role === "admin" || role === "author");

  // Отметку «Глава сдана» ставит только администратор (сервер отвечает 403 остальным).
  const isAdmin = (meQuery.data?.roles ?? []).includes("admin");

  const booksQuery = useQuery({ queryKey: ["books"], queryFn: () => apiGet<BooksResponse>("/api/books") });
  const books = useMemo(
    () => (booksQuery.data?.items ?? []).filter((book) => PUBLISHED_BOOK.has(book.status)),
    [booksQuery.data],
  );
  const bookId = params.get("book_id") || books[0]?.id || "";
  const book = books.find((item) => item.id === bookId);

  const coverage = useQuery({
    queryKey: ["v2", "book-recording", bookId],
    queryFn: () => apiGet<BookRecordingResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/recording`),
    enabled: Boolean(bookId),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    // Эмбиент сочиняется минутами на трек: пока идёт хоть одна глава, ход обновляется сам.
    refetchInterval: (query) =>
      (query.state.data?.chapters ?? []).some((chapter) => chapter.ambient_state === "running") ? 5_000 : false,
  });

  const recognise = useMutation({
    mutationFn: (chapter: BookRecordingChapter) =>
      apiPostJson<{ ok: boolean; job_id?: string }>(`/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/asr`, {}),
    onSuccess: async (_result, chapter) => {
      pushToast({
        tone: "success",
        title: "Transcription started",
        detail: `Chapter ${chapter.chapter_index}. Allow about a minute per file; results will appear here automatically.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Could not start", detail: describeApiError(error, "the server did not respond") }),
  });

  /* Строгая проверка рассказчика — платный прогон. Сама ставится,
     когда глава собирается; кнопка — для повтора по решению владельца. */
  const auditChapter = useMutation({
    mutationFn: (chapter: BookRecordingChapter) =>
      apiPostJson<{ ok: boolean; job_id?: string }>(`/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/audit`, {}),
    onSuccess: async (_result, chapter) => {
      pushToast({
        tone: "success",
        title: "Strict audit started",
        detail: `Chapter ${chapter.chapter_index}. The paid audit has started; results will appear here.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: async (error) => {
      const detail = error instanceof ApiError && error.errorCode === "chapter_not_assembled"
        ? "The chapter has changed. Rebuild it first"
        : error instanceof ApiError && error.errorCode === "already_queued"
          ? "An audit is already queued or running; no duplicate was started"
          : error instanceof ApiError && error.errorCode === "audit_disabled"
            ? "Strict audit is disabled"
            : error instanceof ApiError && error.errorCode === "audit_missing_key"
              ? "Add a RouterAI key in AI settings"
              : error instanceof ApiError && error.errorCode === "audit_over_limit"
                ? "Monthly limit reached; check spending settings"
                : error instanceof ApiError && error.errorCode === "audit_budget_unavailable"
                  ? "Could not check the monthly limit"
            : describeApiError(error, "the server did not respond");
      pushToast({ tone: "error", title: "Could not start", detail });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
  });

  /* «Сдана» = сведена, прослушана и выложена; её ставит человек, а не скачивание архива.
     Когда сдана последняя опубликованная глава, спрашиваем про «Озвучена»: сервер сам
     книгу не закрывает — это решение владельца, с письмами и закрытием сроков. */
  const release = useMutation({
    mutationFn: (chapter: BookRecordingChapter) =>
      apiPostJson<{ ok: boolean; all_released: boolean; voiced: boolean }>(
        `/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/released`,
        { released: !chapter.released_at },
      ),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
      if (result.all_released && !result.voiced && window.confirm("All chapters are delivered. Mark the book as recorded?")) {
        try {
          await apiPostJson(`/api/v2/books/${encodeURIComponent(bookId)}/voiced`, { voiced: true });
          await queryClient.invalidateQueries({ queryKey: ["books"] });
          navigate(`/books/${encodeURIComponent(bookId)}?thanks=1`);
        } catch (error) {
          pushToast({ tone: "error", title: "Book status not changed", detail: describeApiError(error, "the server did not respond") });
        }
      }
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Mark not saved", detail: describeApiError(error, "the server did not respond") }),
  });

  const verify = useMutation({
    mutationFn: (chapter: BookRecordingChapter) =>
      apiPostJson<{ ok: boolean; job_id?: string }>(`/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/verify`, {}),
    onSuccess: async (_result, chapter) => {
      pushToast({
        tone: "success",
        title: "Alignment started",
        detail: `Chapter ${chapter.chapter_index}. Files are being read from the server; large chapters may take several minutes.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) => {
      // Дедупликация на сервере отвечает 409 already_queued: сверка уже идёт, а не
      // «не вышло» — кнопка не должна врать про провал там, где просто нельзя запускать дважды.
      if (error instanceof ApiError && error.errorCode === "already_queued") {
        pushToast({ tone: "error", title: "Could not complete the action", detail: "Alignment is already running." });
        return;
      }
      pushToast({ tone: "error", title: "Could not complete the action", detail: describeApiError(error, "the server did not respond") });
    },
  });

  // Кнопки «проект» (положить .sesx на NAS раньше круга) и ссылки «сессия» нет с 24.10:
  // скачивание главы — одна ссылка, архив с сессией внутри. На NAS проект фон кладёт сам.

  // Сначала смета (сколько треков и минут — её считает раскладка по таймлайну, в строке
  // её нет), потом подтверждение: каждый трек списывается из квоты ElevenLabs.
  const ambient = useMutation({
    mutationFn: async (chapter: BookRecordingChapter) => {
      const plan = await ambientPlan(chapter.chapter_id);
      if (!plan.tracks) return { started: false, nothing: true, skipped: plan.skipped };
      const skipped = plan.skipped
        ? `, ${plan.skipped} ${plural(plan.skipped, "scene", "scenes", "scenes")} without a timeline position ${plural(plan.skipped, "will be skipped", "will be skipped", "will be skipped")}`
        : "";
      const tracks = `${plan.tracks} ${plural(plan.tracks, "track", "tracks", "tracks")}`;
      if (!window.confirm(`Generate ${tracks} · ${plan.minutes} min of ambience? This will use ${tracks} from your ElevenLabs quota${skipped}.`)) {
        return { started: false, nothing: false, skipped: 0 };
      }
      await startChapterAmbient(chapter.chapter_id);
      return { started: true, nothing: false, skipped: 0 };
    },
    onSuccess: async (result, chapter) => {
      if (result.nothing) {
        pushToast({
          tone: "error",
          title: "Nothing to generate",
          detail: result.skipped
            ? `Chapter ${chapter.chapter_index}: scenes without tracks have no timeline position.`
            : `Chapter ${chapter.chapter_index}: all scenes already have tracks.`,
        });
        return;
      }
      if (!result.started) return;
      pushToast({
        tone: "success",
        title: "Ambience generation started",
        detail: `Chapter ${chapter.chapter_index}. Each track takes minutes to generate. Track progress here; completed tracks appear in the chapter project.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not start", detail: describeAmbientError(error) }),
  });

  // Без подтверждения: остановка ничего не тратит, а текущий трек (уже оплачиваемый)
  // всё равно доделается и ляжет в проект.
  const stopAmbient = useMutation({
    mutationFn: (chapter: BookRecordingChapter) => stopChapterAmbient(chapter.chapter_id),
    onSuccess: async (result, chapter) => {
      pushToast(
        result.stopped
          ? { tone: "success", title: "Stop requested; the current track will finish", detail: `Chapter ${chapter.chapter_index}.` }
          : { tone: "error", title: "Nothing to stop", detail: `Chapter ${chapter.chapter_index}: generation has already finished.` },
      );
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not stop", detail: describeAmbientError(error) }),
  });

  const chapters = coverage.data?.chapters ?? [];
  const rows = onlyStarted ? chapters.filter((chapter) => chapter.recorded_roles > 0) : chapters;
  const canDownload = coverage.data?.can_download ?? false;

  /** Эмбиент в ряду действий главы: кнопка, ход или «✓». Поля считаются только
   * редактору — у диктора все нули, и ничего не рисуется. */
  function ambientControl(row: BookRecordingChapter): ReactNode {
    if (!canDownload || !row.ready) return null;
    if (row.ambient_state === "running") {
      return (
        <>
          <span className="asr-ambient num" title="Tracks are generated one at a time and added to the chapter project immediately">
            ambience {row.ambient_done}/{row.ambient_done + row.ambient_todo}
          </span>
          <button
            type="button"
            className="asr-run"
            disabled={stopAmbient.isPending}
            onClick={stopRow(() => stopAmbient.mutate(row))}
            title="Stop generation after the current track finishes"
          >
            stop
          </button>
        </>
      );
    }
    if (row.ambient_todo > row.ambient_skipped) {
      return (
        <button
          type="button"
          className="asr-run"
          disabled={ambient.isPending}
          onClick={stopRow(() => ambient.mutate(row))}
          title={`Generate a unique ElevenLabs track for each scene without one: ${row.ambient_todo - row.ambient_skipped}`}
        >
          ambience
        </button>
      );
    }
    if (row.ambient_state === "done") {
      return (
        <span className="asr-ambient" title={`All chapter scenes on the timeline have tracks: ${row.ambient_done}`}>
          ambience ✓
        </span>
      );
    }
    return null;
  }

  /** Беда последнего прогона эмбиента — строкой под действиями: в строку она не влезает,
   * а раздвигать колонку ради редкого случая — отнимать ширину у названий всех глав. */
  function ambientNote(row: BookRecordingChapter): ReactNode {
    if (!canDownload || !row.ready) return null;
    if (row.ambient_broken) {
      // Битый трек держит архив главы, но это не беда диктора: его чинит новая генерация.
      return (
        <span
          className="asr-ambient-note asr-broken"
          title="An ambience file failed verification, so the chapter archive cannot be built. Regenerate it in the scene card (Reader → Sound)"
        >
          · damaged ambience: {row.ambient_broken}
        </span>
      );
    }
    const [note, title] =
      row.ambient_state === "quota"
        ? ["quota exhausted", "ElevenLabs track quota is exhausted. Existing tracks are safe; generate the rest after topping up"]
        : row.ambient_state === "bad_key"
          ? ["ElevenLabs key rejected", "ElevenLabs rejected the server key. Existing tracks are safe; set a valid ELEVENLABS_API_KEY"]
          : row.ambient_state === "disk_full"
          ? ["out of disk space", "The server ran out of disk space for this track; no quota was charged"]
          : row.ambient_state === "failed"
            ? [row.ambient_failed ? `errors ${row.ambient_failed}` : "run failed",
               "Some tracks failed. Use the button to generate them again; see each scene card for details"]
            : ["", ""];
    if (!note) return null;
    return (
      <span className="asr-ambient-note asr-broken" title={title}>
        · {note}
      </span>
    );
  }

  const columns = useMemo<Column<BookRecordingChapter>[]>(
    () => [
      {
        key: "title",
        header: "Chapter",
        render: (row) => (
          <span className="asr-title">
            {/* Треугольник не кнопка, а указатель: нажимается вся строка, и своя
                мишень рядом с общей только сбивала бы с толку. */}
            <span className="asr-caret" aria-hidden="true">{openChapters.has(row.chapter_id) ? "▾" : "▸"}</span>
            <span className="asr-index num">{row.chapter_index}.</span>
            <span className="asr-title-text" title={row.chapter_title || "Untitled"}>{row.chapter_title || "Untitled"}</span>
            {row.integrity_bad > 0 || row.session_outdated || row.session_markers_skipped > 0 ? <span className="asr-broken" title="There is a file or project issue; expand the chapter">⚠</span> : null}
            {row.released_at ? <span className="asr-done" title="Mixed, reviewed and published">Delivered · {shortDate(row.released_at)}</span> : null}
            {row.delivered_at && !row.released_at ? <span className="asr-done" title="The full archive was downloaded; mixing has started">all recorded</span> : null}
          </span>
        ),
      },
      {
        key: "duration",
        header: <span title="Estimated from chapter text at 150 words per minute">Duration ≈</span>,
        className: "asr-col-duration",
        nowrap: true,
        render: (row) => row.estimated_duration_seconds == null ? "—" : <span className="num" title="Estimated from chapter text at 150 words per minute">{duration(row.estimated_duration_seconds)}</span>,
      },
      {
        key: "asr",
        header: "Recorded lines",
        className: "asr-col-bar",
        render: (row) => {
          if (row.asr_coverage === null) {
            return row.recorded_roles && canDownload ? (
              <button type="button" className="asr-run" disabled={recognise.isPending}
                onClick={stopRow(() => recognise.mutate(row))}
                title="Transcribe recordings and compare with the script">transcribe</button>
            ) : <span className="cast-muted">—</span>;
          }
          const whole = row.asr_coverage >= 1;
          const value = whole ? 100 : Math.min(99, Math.round(row.asr_coverage * 100));
          return (
            <div>
            <span className={row.asr_language_stale ? "asr-bar is-profile-stale" : "asr-bar"} role="progressbar"
              aria-label={`${row.asr_language_stale ? "Previous language, saved lines" : "Lines found"}: ${row.matched_lines} of ${row.total_lines}`}
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.max(0, value))}>
              <span className={whole ? "asr-bar-fill is-ready" : "asr-bar-fill"} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
              <span className={whole ? "asr-bar-value num" : "asr-bar-value num asr-holes"}>{value}% · {row.matched_lines}/{row.total_lines}</span>
            </span>
            {row.asr_language_stale ? <small className="asr-profile-stale">Previous book language · review needed</small> : null}
            </div>
          );
        },
      },
      {
        key: "audit",
        header: "Narrator errors",
        align: "right",
        className: "asr-col-audit",
        nowrap: true,
        render: (row) => {
          const audit = row.audit;
          const busy = audit?.status === "queued" || audit?.status === "running" || audit?.status === "unknown";
          const label = !audit ? "—" : audit.status === "unknown" ? "outcome unknown" : busy ? "auditing…"
            : audit.status === "stale" ? "outdated"
            : audit.status === "failed" ? "audit failed"
            : audit.status === "skipped" ? "skipped"
            : audit.status === "done" ? `${audit.confirmed} ${audit.confirmed === 1 ? "error" : "errors"}` : "no result";
          const title = !audit ? "No audit yet"
            : audit.status === "unknown" ? "Outcome unknown; no automatic retry. Check the task log."
            : audit.status === "stale" ? "The script or recording changed after the audit. Rebuild the chapter"
            : audit.status === "failed" ? "The run stopped. See the task log for details"
            : audit.status === "skipped" ? (audit.error_message?.includes("narrator_input_changed_after_payment")
              ? "The narrator recording changed during the audit. The paid result is saved; automatic retry is disabled."
              : "Audit skipped. See the task log for conditions and payment status")
            : `Strict audit (${audit.model}): flagged ${audit.flags}, confirmed ${audit.confirmed}. STT cost ${audit.stt_cost_rub == null ? "unknown" : `${audit.stt_cost_rub.toFixed(2)} ₽`}`;
          return (
            <span className="asr-links">
              <span className={audit?.confirmed ? "num asr-holes" : "cast-muted"} title={title}>{label}</span>
              {isAdmin && row.audit_assembled && !busy && audit?.status !== "stale" ? (
                <button
                  type="button"
                  className="asr-run"
                  disabled={auditChapter.isPending}
                  onClick={stopRow(() => auditChapter.mutate(row))}
                  title="Paid audit of the assembled narrator recording. Cost depends on duration and rates; each retry is charged separately"
                >
                  {audit ? "retry" : "audit"}
                </button>
              ) : null}
            </span>
          );
        },
      },
      {
        key: "backup",
        header: "Backup",
        className: "asr-col-backup",
        render: (row) => {
          const total = row.mirrored_files + row.unmirrored_files;
          if (!total) return <span className="cast-muted">—</span>;
          if (row.mirror_broken || row.stale_unmirrored) return <span className="asr-broken" title={row.mirror_broken ? "NAS copy failed verification; the server file is intact" : "Copy pending for over a day; check the NAS"}>Error</span>;
          if (row.unmirrored_files) return <span className="cast-muted" title={`Copied to NAS: ${row.mirrored_files} of ${total} files`}>Copying</span>;
          return <span title={`All ${total} chapter files verified on NAS`}>Verified ✓</span>;
        },
      },
      {
        key: "get",
        header: <span className="asr-sr">Archive</span>,
        align: "right",
        className: "asr-col-get",
        nowrap: true,
        render: (row) => (
          <>
          {isAdmin && (row.released_at || row.delivered_at || row.ready) ? (
            <button
              type="button"
              className="asr-get asr-release"
              disabled={release.isPending}
              onClick={stopRow(() => release.mutate(row))}
              title={row.released_at ? "Remove delivered mark" : "Chapter mixed, reviewed and published"}
            >
              {row.released_at ? "Remove mark" : "Chapter delivered"}
            </button>
          ) : null}
          {row.recorded_roles && canDownload ? (
            <>
              <span className="asr-links">
                {/* Голый `.sesx` отдельной ссылкой убран 24.10: в нём пути сервера, и на
                    своём компьютере Audition искал файлы вручную. Сессия едет в архиве,
                    рядом с дублями, и зовёт их по имени. */}
                <a
                  className="asr-get"
                  href={`/api/v2/chapters/${encodeURIComponent(row.chapter_id)}/archive.zip`}
                  onClick={stopRow()}
                  title={`${row.ready ? "Chapter takes" : "Existing recordings"}, ambience and the Audition session in one archive. Extract it and open the .sesx session; files are linked automatically`}
                >
                  {row.ready ? "archive" : "partial archive"}
                </a>
                {ambientControl(row)}
              </span>
              {ambientNote(row)}
            </>
          ) : null}
          </>
        ),
      },
    ],
    [canDownload, isAdmin, release, recognise, auditChapter, ambient, stopAmbient, openChapters],
  );

  const toggleChapter = useCallback((row: BookRecordingChapter) => {
    setOpenChapters((open) => {
      const next = new Set(open);
      if (!next.delete(row.chapter_id)) next.add(row.chapter_id);
      return next;
    });
  }, []);

  const renderDetail = useCallback(
    (row: BookRecordingChapter) => {
      if (!openChapters.has(row.chapter_id)) return null;
      return <>
        <div className="asr-detail-status">
          {row.integrity_bad > 0 ? <span className="asr-broken">File verification failed: {row.integrity_bad}</span>
            : row.integrity_checked ? <span>Files verified: {row.integrity_ok} ✓</span>
            : row.integrity_files > 0 && row.integrity_skipped === row.integrity_files ? <span>No checksums available for file verification</span>
            : row.recorded_roles && canDownload ? <button type="button" className="asr-run" disabled={verify.isPending} onClick={() => verify.mutate(row)}>Verify files</button> : null}
          {row.session_outdated ? <span className="asr-broken">Project outdated</span> : null}
          {row.session_markers_skipped > 0 ? <span className="asr-broken">{row.session_markers_skipped} {plural(row.session_markers_skipped, "marker", "markers", "markers")} without a position</span> : null}
        </div>
        <ChapterFilesRow chapterId={row.chapter_id} bookId={bookId} canDelete={canDeleteTakes} />
      </>;
    },
    [openChapters, bookId, canDeleteTakes, canDownload, verify],
  );

  /* Прогресс книги — по собранным главам: все роли записаны и проект .sesx лежит на
     NAS. Сумма «роль × глава» (сотни при касте в семьдесят) читалась как число ролей
     и сбивала с толку. Устаревший проект не в счёт — его ещё пересоберут.
     «Собрана», а не «сдана»: «сдана» в таблице — отмечена человеком (сведена и выложена),
     а «всё записано» — архив уже забран в сведение. */
  const assembled = chapters.filter((chapter) => chapter.ready && chapter.session_archived && !chapter.session_outdated).length;
  const bookPercent = chapters.length ? Math.round((assembled / chapters.length) * 100) : 0;

  const ready = coverage.data?.ready_chapters ?? 0;
  const started = coverage.data?.started_chapters ?? 0;
  const total = coverage.data?.total_chapters ?? 0;
  const missingLines = coverage.data?.missing_lines ?? 0;
  const delivered = coverage.data?.delivered_chapters ?? 0;
  const released = chapters.filter((chapter) => chapter.released_at).length;

  return (
    <div className="asr">
      <PageHeader
        title="ASR · coverage"
        subtitle={
          coverage.isLoading
            ? "Calculating coverage…"
            : total
              ? `Chapters: ${total} · in progress ${started} · ready ${ready} · all recorded ${delivered} · delivered ${released}${missingLines ? ` · missing lines ${missingLines}` : ""}`
              : "No chapters"
        }
        actions={
          books.length > 1 ? (
            <select
              className="ui-input asr-book"
              value={bookId}
              aria-label="Book"
              onChange={(event) => setParams({ book_id: event.target.value }, { replace: true })}
            >
              {books.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.display_title || item.title}
                </option>
              ))}
            </select>
          ) : undefined
        }
      />

      <div className="asr-summary">
        <div
          className="asr-book-bar"
          role="progressbar"
          aria-label={`Assembled chapters for book ${book ? `«${book.display_title || book.title}»` : ""}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={bookPercent}
          title="A chapter is assembled when all roles are recorded and its .sesx project is on the NAS. Recorded lines shows script lines found by transcription"
        >
          <span className={chapters.length && assembled === chapters.length ? "asr-book-fill is-ready" : "asr-book-fill"} style={{ width: `${bookPercent}%` }} />
          <span className="asr-book-title">{book ? book.display_title || book.title : "—"}</span>
          <span className="asr-book-value num">
            chapters assembled {assembled}<span className="asr-of"> of {chapters.length}</span> · {bookPercent}%
          </span>
        </div>
        <Button
          size="sm"
          variant="secondary"
          aria-pressed={onlyStarted}
          icon={<Icon name="filter" />}
          onClick={() => setOnlyStarted((value) => !value)}
        >
          Started only
        </Button>
        {onlyStarted ? <span className="asr-count num">{rows.length} of {total}</span> : null}
      </div>

      {coverage.data?.asr_language_stale ? (
        <p className="ui-note" role="status">
          Some ASR results use the previous book language. Their coverage is retained for reference and needs review.
          Changing the language does not transcribe recordings again; starting transcription is a separate paid action.
        </p>
      ) : null}

      {coverage.isError ? (
        <div className="ui-note ui-note--error" role="alert">
          Could not load coverage: {describeApiError(coverage.error, "the server did not respond")}.
        </div>
      ) : (
        <DataTable
          className="asr-table"
          aria-label="Book coverage by chapter"
          columns={columns}
          rows={rows}
          rowKey={(row) => row.chapter_id}
          onRowClick={toggleChapter}
          rowClassName={(row) => (row.released_at ? "asr-row-released" : undefined)}
          rowLabel={(row) => `Chapter inventory ${row.chapter_index}. ${row.chapter_title}`}
          renderDetail={renderDetail}
          loading={coverage.isLoading || booksQuery.isLoading}
          skeletonRows={8}
          empty={
            <EmptyState
              icon="mic"
              text={
                onlyStarted
                  ? "No chapters have recordings yet."
                  : book
                    ? "This book has no chapters open for recording."
                    : "No books have been published to voice actors yet."
              }
              action={<LinkButton to="/recording">Open script</LinkButton>}
            />
          }
        />
      )}
    </div>
  );
}
