/**
 * Fetching and actions for the Book hub: the polled v2 progress and the
 * mutations behind every button (run / stop / v1 repair / delete).
 * Every action toasts and refetches what it could have changed.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { postWithLimit } from "../../../utils/limitConfirm";
import { ApiError, apiGet, apiPostJson, describeApiError } from "../../../api/client";
import { useToast } from "../../ToastProvider";
import { invalidateReader } from "../../../v2/editorApi";
import { HUB_STEP_LABELS, isRunActive, plural, type HubProgress, type HubStepKey } from "../../../viewModels/bookHub";
import type { CharacterMapMergeResponse } from "../../../types";

export const hubKeys = {
  /** shared with the library so a visited row is already warm here */
  progress: (bookId: string) => ["v2-progress", bookId] as const,
  readiness: (bookId: string) => ["book-readiness", bookId] as const,
  recording: (bookId: string) => ["v2", "book-recording", bookId] as const,
  chapters: (bookId: string) => ["v2", "book-chapters", bookId] as const,
  /** Тот же ключ, которым карту читают `CastCard` и `CastPage` — ими же его и
      инвалидирует `useCharacterMapActions`; разойдись ключи, и слияние обновляло
      бы кэш, которого никто не читает. */
  characterMap: (bookId: string) => ["v2", "character-map", bookId] as const,
  /** одна на студию: книга, по которой бот принимает дубли */
  recordingBook: ["studio", "recording-book"] as const,
};

type RecordingBookResponse = { ok: boolean; book_id: string };

/** Какая книга записывается сейчас и переключатель для админа. Включение у одной книги
    снимает отметку с другой: поле на сервере одно. */
export function useRecordingBook() {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const query = useQuery({
    queryKey: hubKeys.recordingBook,
    queryFn: () => apiGet<RecordingBookResponse>("/api/studio/recording-book"),
    retry: false,
    staleTime: 30_000,
  });
  const mutation = useMutation({
    mutationFn: (bookId: string | null) =>
      apiPostJson<RecordingBookResponse>("/api/studio/recording-book", { book_id: bookId }),
    onSuccess: (result) => {
      queryClient.setQueryData(hubKeys.recordingBook, result);
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Could not switch", detail: describeApiError(error, "the server did not respond") });
    },
  });
  return { activeBookId: query.data?.book_id ?? "", loading: query.isLoading, switching: mutation.isPending, set: mutation.mutate };
}

const v2 = (bookId: string) => `/api/v2/books/${encodeURIComponent(bookId)}`;
const v1 = (bookId: string) => `/api/books/${encodeURIComponent(bookId)}`;

async function fetchProgress(bookId: string): Promise<HubProgress | null> {
  try {
    return await apiGet<HubProgress>(`${v2(bookId)}/progress`);
  } catch (error) {
    // not deployed yet / no v2 data — the page shows «standard, all pending»
    if (error instanceof ApiError && (error.status === 404 || error.status === 405)) return null;
    throw error;
  }
}

/** Polls every 5 s while a run is active, every 30 s otherwise. */
export function useHubProgress(bookId: string, enabled = true) {
  return useQuery({
    queryKey: hubKeys.progress(bookId),
    queryFn: () => fetchProgress(bookId),
    enabled: enabled && Boolean(bookId),
    retry: false,
    staleTime: 4_000,
    refetchInterval: (query) => (isRunActive(query.state.data?.run) ? 5_000 : 30_000),
  });
}

export type RunBody = { steps?: HubStepKey[]; force?: boolean };
type OkResponse = { ok: boolean; error?: string } & Record<string, unknown>;

function stepsText(body: RunBody): string {
  if (!body.steps?.length) return "all steps";
  return body.steps.map((key) => HUB_STEP_LABELS[key] || key).join(", ");
}

export function useHubActions(bookId: string, options: { onDeleted?: () => void } = {}) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: hubKeys.progress(bookId) }),
      queryClient.invalidateQueries({ queryKey: ["books"] }),
    ]);
  };

  const run = useMutation({
    mutationFn: (body: RunBody) => postWithLimit<OkResponse>(`${v2(bookId)}/run`, body),
    onSuccess: async (result, body) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Annotation not started", detail: result.error || "The server returned no details." });
        return;
      }
      pushToast({ tone: "success", title: "Annotation queued", detail: `${stepsText(body)}${body.force ? " · fresh run" : ""}` });
      await refresh();
    },
    onError: (error) => {
      const busy = error instanceof ApiError && error.status === 409;
      pushToast({
        tone: "error",
        title: busy ? "Annotation already running" : "Could not start annotation",
        detail: busy ? "Wait for the current run to finish or stop it." : describeApiError(error, "The server did not respond."),
      });
    },
  });

  const stop = useMutation({
    mutationFn: () => apiPostJson<OkResponse>(`${v2(bookId)}/stop`, {}),
    onSuccess: async () => {
      pushToast({ tone: "info", title: "Stop requested", detail: "The run will stop after the current chapter finishes." });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not stop", detail: describeApiError(error, "The server did not respond.") }),
  });

  /** v1 operator actions for books still on the old pipeline. */
  const repair = useMutation({
    mutationFn: (actionType: "stop_pipeline" | "continue_pipeline") =>
      apiPostJson<OkResponse>(`${v1(bookId)}/repair`, { action_type: actionType, reason: "Book hub: operator action" }),
    onSuccess: async (result, actionType) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Action failed", detail: result.error || "The server returned no details." });
        return;
      }
      pushToast({ tone: "success", title: actionType === "stop_pipeline" ? "v1 stop requested" : "v1 pipeline resumed" });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Action failed", detail: describeApiError(error, "The server did not respond.") }),
  });

  const remove = useMutation({
    mutationFn: () => apiPostJson<OkResponse>(`${v1(bookId)}/delete`, {}),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Book not deleted", detail: result.error || "The server returned no details." });
        return;
      }
      pushToast({ tone: "success", title: "Book deleted" });
      await queryClient.invalidateQueries({ queryKey: ["books"] });
      options.onDeleted?.();
    },
    onError: (error) => pushToast({ tone: "error", title: "Book not deleted", detail: describeApiError(error, "The server did not respond.") }),
  });

  // «Прочитал всё»: the author's mark on every marked-up chapter at once.
  const approveAll = useMutation({
    mutationFn: () => apiPostJson<OkResponse & { approved_now?: number; approved?: number; attributed?: number }>(`${v2(bookId)}/approve-all`, {}),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Not marked", detail: result.error || "The server returned no details." });
        return;
      }
      pushToast({
        tone: "success",
        title: `Chapters reviewed: ${result.approved ?? 0}`,
        detail: result.approved_now ? `marked now: ${result.approved_now}` : "all chapters already marked",
      });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Not marked", detail: describeApiError(error, "Could not mark chapters.") }),
  });

  // Open every approved chapter at once, without waiting for the button.
  const autoPublish = useMutation({
    mutationFn: (enabled: boolean) => apiPostJson<OkResponse>(`${v2(bookId)}/auto-publish`, { enabled }),
    onSuccess: async (result, enabled) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Not saved", detail: result.error || "The server returned no details." });
        return;
      }
      pushToast({
        tone: "success",
        title: enabled ? "Chapters will be published immediately after approval" : "Automatic publication disabled",
        detail: enabled ? "Voice actors will receive one notification for the first published chapter." : undefined,
      });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Not saved", detail: describeApiError(error, "Could not switch.") }),
  });

  const publish = useMutation({
    mutationFn: () => apiPostJson<OkResponse & { published?: number; skipped?: number; notified?: number; gated_by_approval?: boolean }>(`${v2(bookId)}/publish`, {}),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Book not published", detail: result.error || "The server returned no details." });
        return;
      }
      const parts = [`chapters published ${result.published ?? 0}`];
      if (result.gated_by_approval) parts.push("reviewed only");
      if (result.skipped) parts.push(`unannotated ${result.skipped}`);
      if (result.notified) parts.push(`voice actors notified ${result.notified}`);
      pushToast({ tone: "success", title: "Book published to voice actors", detail: parts.join(" · ") });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Book not published", detail: describeApiError(error, "The server did not respond.") }),
  });

  const unpublish = useMutation({
    mutationFn: () => apiPostJson<OkResponse>(`${v2(bookId)}/unpublish`, {}),
    onSuccess: async () => {
      pushToast({ tone: "info", title: "Book returned to review" });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not return to review", detail: describeApiError(error, "The server did not respond.") }),
  });

  return { run, stop, repair, remove, approveAll, autoPublish, publish, unpublish };
}

export type HubActions = ReturnType<typeof useHubActions>;

// `useCharacterMap` (список без пересечений) жил здесь для карточки хаба, затем
// уступил место отдельному экрану карты персонажей (`/books/:bookId/roles`) — тот
// экран исчез следом, его содержимое въехало в Каст (`/books/:bookId/cast`).
// Карточка хаба (`CastCard`) и страница каста (`CastPage`) теперь читают
// `.../character-map` каждая своим запросом с этим же ключом. `hubKeys.characterMap`
// остаётся — им инвалидирует себя `useCharacterMapActions` ниже.

/**
 * `CharacterMapError.code` в словах автора. Сырой код показывать нельзя: он
 * пришёл из внутреннего исключения бэкенда, а не был написан для человека —
 * `describeApiError` в его отсутствие вернул бы именно код (`payload.error`
 * — единственное поле, которое сервер здесь присылает).
 */
const CHARACTER_MAP_ERRORS: Record<string, string> = {
  speaks: "This role has lines. Merge it instead of deleting it.",
  target_not_in_cast: "The target role must exist in the cast. Create it first, then merge.",
  has_audio: "Takes are recorded under this name.",
  placeholder: "This is an annotation marker, not a character.",
  not_found: "This name is absent from both cast and annotation.",
  same_name: "These names are identical.",
  does_not_speak: "This role has no lines to add to the cast.",
  narrator_is_not_a_role: "This is an alias of the narrator. Only one narrator is allowed.",
  nothing_to_merge: "This role has no lines to merge. Use deletion instead.",
  bad_json: "Could not parse the request.",
};

function describeCharacterMapError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && CHARACTER_MAP_ERRORS[error.errorCode]) {
    return CHARACTER_MAP_ERRORS[error.errorCode];
  }
  return describeApiError(error, fallback);
}

/** Тот же перевод для отказа, пришедшего 200-м ответом с `ok: false` (защита по образцу соседних действий). */
function characterMapErrorText(code: string | undefined): string {
  return (code && CHARACTER_MAP_ERRORS[code]) || code || "The server returned no details.";
}

export function useCharacterMapActions(bookId: string) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();

  // Слияние трогает разметку по всем главам сразу — читатель должен перечитать
  // главы и каст; удаление/заведение меняют только карту, каст обновить хватит.
  const refresh = (touchesMarkup: boolean) =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: hubKeys.characterMap(bookId) }),
      invalidateReader(queryClient, bookId, touchesMarkup ? ["chapters", "cast"] : ["cast"]),
    ]);

  const deleteRow = useMutation({
    mutationFn: (name: string) => apiPostJson<OkResponse & { deleted?: number }>(`${v2(bookId)}/character-map/delete`, { name }),
    onSuccess: async (result, name) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Not deleted", detail: characterMapErrorText(result.error) });
        return;
      }
      pushToast({ tone: "success", title: "Deleted", detail: `«${name}» removed from cast.` });
      await refresh(false);
    },
    onError: (error) => pushToast({ tone: "error", title: "Not deleted", detail: describeCharacterMapError(error, "The server did not respond.") }),
  });

  const adopt = useMutation({
    mutationFn: (name: string) => apiPostJson<OkResponse & { created?: number }>(`${v2(bookId)}/character-map/adopt`, { name }),
    onSuccess: async (result, name) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Not created", detail: characterMapErrorText(result.error) });
        return;
      }
      pushToast({
        tone: "success",
        title: result.created ? "Added to cast" : "Already in cast",
        detail: result.created ? `«${name}» added to the character cast.` : `«${name}» was already in the cast.`,
      });
      await refresh(false);
    },
    onError: (error) => pushToast({ tone: "error", title: "Not created", detail: describeCharacterMapError(error, "The server did not respond.") }),
  });

  const merge = useMutation({
    mutationFn: (body: { source: string; target: string }) =>
      apiPostJson<CharacterMapMergeResponse>(`${v2(bookId)}/character-map/merge`, body),
    onSuccess: async (result, body) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Not merged", detail: characterMapErrorText(result.error) });
        return;
      }
      // Лист обещал «переедет N реплик» — тост обязан отчитаться в тех же
      // единицах, а не в сегментах: сегментов всегда меньше, чем реплик.
      const lines = result.lines ?? 0;
      pushToast({
        tone: "success",
        title: `Merged into «${result.target ?? body.target}»`,
        detail: `«${result.source ?? body.source}» moved here ${lines} ${plural(lines, "line", "lines", "lines")} and was removed from the cast.`,
      });
      await refresh(true);
    },
    onError: (error) => pushToast({ tone: "error", title: "Not merged", detail: describeCharacterMapError(error, "The server did not respond.") }),
  });

  return { deleteRow, adopt, merge };
}

export type CharacterMapActions = ReturnType<typeof useCharacterMapActions>;
