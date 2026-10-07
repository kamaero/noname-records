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
};

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
  if (!body.steps?.length) return "все шаги";
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
        pushToast({ tone: "error", title: "Разметка не запущена", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      pushToast({ tone: "success", title: "Разметка поставлена в очередь", detail: `${stepsText(body)}${body.force ? " · заново" : ""}` });
      await refresh();
    },
    onError: (error) => {
      const busy = error instanceof ApiError && error.status === 409;
      pushToast({
        tone: "error",
        title: busy ? "Разметка уже идёт" : "Не удалось запустить разметку",
        detail: busy ? "Дождитесь конца прогона или остановите его." : describeApiError(error, "Сервер не ответил."),
      });
    },
  });

  const stop = useMutation({
    mutationFn: () => apiPostJson<OkResponse>(`${v2(bookId)}/stop`, {}),
    onSuccess: async () => {
      pushToast({ tone: "info", title: "Остановка запрошена", detail: "Текущая глава закончится, потом прогон остановится." });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не удалось остановить", detail: describeApiError(error, "Сервер не ответил.") }),
  });

  /** v1 operator actions for books still on the old pipeline. */
  const repair = useMutation({
    mutationFn: (actionType: "stop_pipeline" | "continue_pipeline") =>
      apiPostJson<OkResponse>(`${v1(bookId)}/repair`, { action_type: actionType, reason: "Хаб книги: действие оператора" }),
    onSuccess: async (result, actionType) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Действие не выполнено", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      pushToast({ tone: "success", title: actionType === "stop_pipeline" ? "Остановка v1 запрошена" : "Пайплайн v1 продолжен" });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Действие не выполнено", detail: describeApiError(error, "Сервер не ответил.") }),
  });

  const remove = useMutation({
    mutationFn: () => apiPostJson<OkResponse>(`${v1(bookId)}/delete`, {}),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Книга не удалена", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      pushToast({ tone: "success", title: "Книга удалена" });
      await queryClient.invalidateQueries({ queryKey: ["books"] });
      options.onDeleted?.();
    },
    onError: (error) => pushToast({ tone: "error", title: "Книга не удалена", detail: describeApiError(error, "Сервер не ответил.") }),
  });

  // «Прочитал всё»: the author's mark on every marked-up chapter at once.
  const approveAll = useMutation({
    mutationFn: () => apiPostJson<OkResponse & { approved_now?: number; approved?: number; attributed?: number }>(`${v2(bookId)}/approve-all`, {}),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Не отмечено", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      pushToast({
        tone: "success",
        title: `Проверено глав: ${result.approved ?? 0}`,
        detail: result.approved_now ? `отмечено сейчас: ${result.approved_now}` : "все главы уже были отмечены",
      });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не отмечено", detail: describeApiError(error, "Не удалось отметить главы.") }),
  });

  // Open every approved chapter at once, without waiting for the button.
  const autoPublish = useMutation({
    mutationFn: (enabled: boolean) => apiPostJson<OkResponse>(`${v2(bookId)}/auto-publish`, { enabled }),
    onSuccess: async (result, enabled) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Не сохранилось", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      pushToast({
        tone: "success",
        title: enabled ? "Главы будут открываться сразу после одобрения" : "Автопубликация выключена",
        detail: enabled ? "Дикторы получат одно уведомление на первую открытую главу." : undefined,
      });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не сохранилось", detail: describeApiError(error, "Не удалось переключить.") }),
  });

  const publish = useMutation({
    mutationFn: () => apiPostJson<OkResponse & { published?: number; skipped?: number; notified?: number; gated_by_approval?: boolean }>(`${v2(bookId)}/publish`, {}),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Книга не опубликована", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      const parts = [`глав опубликовано ${result.published ?? 0}`];
      if (result.gated_by_approval) parts.push("только проверенные");
      if (result.skipped) parts.push(`без разметки ${result.skipped}`);
      if (result.notified) parts.push(`уведомлено дикторов ${result.notified}`);
      pushToast({ tone: "success", title: "Книга опубликована дикторам", detail: parts.join(" · ") });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Книга не опубликована", detail: describeApiError(error, "Сервер не ответил.") }),
  });

  const unpublish = useMutation({
    mutationFn: () => apiPostJson<OkResponse>(`${v2(bookId)}/unpublish`, {}),
    onSuccess: async () => {
      pushToast({ tone: "info", title: "Книга возвращена на проверку" });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не удалось вернуть на проверку", detail: describeApiError(error, "Сервер не ответил.") }),
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
  speaks: "У этого имени есть реплики — его нельзя удалить, только слить.",
  target_not_in_cast: "Слить можно только в имя, которое есть в карте. Сначала заведите его в карте, потом сливайте.",
  has_audio: "На это имя записаны дубли.",
  placeholder: "Это служебная метка разметки, а не персонаж.",
  not_found: "Такого имени нет ни в карте, ни в разметке.",
  same_name: "Это одно и то же имя.",
  does_not_speak: "Это имя ничего не говорит — заводить в карте нечего.",
  narrator_is_not_a_role: "Это синоним рассказчика. В карте он один — второго заводить нельзя.",
  nothing_to_merge: "У этого имени нет реплик — сливать нечего, это удаление под видом слияния.",
  bad_json: "Не удалось разобрать запрос.",
};

function describeCharacterMapError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && CHARACTER_MAP_ERRORS[error.errorCode]) {
    return CHARACTER_MAP_ERRORS[error.errorCode];
  }
  return describeApiError(error, fallback);
}

/** Тот же перевод для отказа, пришедшего 200-м ответом с `ok: false` (защита по образцу соседних действий). */
function characterMapErrorText(code: string | undefined): string {
  return (code && CHARACTER_MAP_ERRORS[code]) || code || "Сервер ответил без подробностей.";
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
        pushToast({ tone: "error", title: "Не удалено", detail: characterMapErrorText(result.error) });
        return;
      }
      pushToast({ tone: "success", title: "Удалено", detail: `«${name}» убран из карты.` });
      await refresh(false);
    },
    onError: (error) => pushToast({ tone: "error", title: "Не удалено", detail: describeCharacterMapError(error, "Сервер не ответил.") }),
  });

  const adopt = useMutation({
    mutationFn: (name: string) => apiPostJson<OkResponse & { created?: number }>(`${v2(bookId)}/character-map/adopt`, { name }),
    onSuccess: async (result, name) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Не заведено", detail: characterMapErrorText(result.error) });
        return;
      }
      pushToast({
        tone: "success",
        title: result.created ? "Заведено в карте" : "Уже в карте",
        detail: result.created ? `«${name}» появился в карте персонажей.` : `«${name}» и так уже был в карте.`,
      });
      await refresh(false);
    },
    onError: (error) => pushToast({ tone: "error", title: "Не заведено", detail: describeCharacterMapError(error, "Сервер не ответил.") }),
  });

  const merge = useMutation({
    mutationFn: (body: { source: string; target: string }) =>
      apiPostJson<CharacterMapMergeResponse>(`${v2(bookId)}/character-map/merge`, body),
    onSuccess: async (result, body) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Не слито", detail: characterMapErrorText(result.error) });
        return;
      }
      // Лист обещал «переедет N реплик» — тост обязан отчитаться в тех же
      // единицах, а не в сегментах: сегментов всегда меньше, чем реплик.
      const lines = result.lines ?? 0;
      pushToast({
        tone: "success",
        title: `Слито в «${result.target ?? body.target}»`,
        detail: `«${result.source ?? body.source}» перенёс сюда ${lines} ${plural(lines, "реплика", "реплики", "реплик")} и убран из карты.`,
      });
      await refresh(true);
    },
    onError: (error) => pushToast({ tone: "error", title: "Не слито", detail: describeCharacterMapError(error, "Сервер не ответил.") }),
  });

  return { deleteRow, adopt, merge };
}

export type CharacterMapActions = ReturnType<typeof useCharacterMapActions>;
