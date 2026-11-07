/**
 * Запросы звукового слоя (`/api/v2/.../sound`). Ручки отдают данные только редактору,
 * поэтому все хуки включаются лишь при `can_edit` — диктор их не дёргает вовсе.
 */
import { useQuery } from "@tanstack/react-query";
import { postWithLimit } from "../utils/limitConfirm";
import { ApiError, apiDelete, apiGet, apiPatchJson, apiPostJson, describeApiError } from "../api/client";
import type { AmbientPlan } from "../types";
import type {
  SoundBookResponse,
  SoundChapterResponse,
  SoundEstimate,
  SoundKind,
  SoundMarker,
  SoundMode,
  SoundRun,
} from "./types";

export const soundKeys = {
  chapter: (chapterId: string) => ["v2", "sound-chapter", chapterId] as const,
  book: (bookId: string) => ["v2", "sound-book", bookId] as const,
  estimate: (bookId: string) => ["v2", "sound-estimate", bookId] as const,
};

const chapterPath = (chapterId: string) => `/api/v2/chapters/${encodeURIComponent(chapterId)}/sound`;
const bookPath = (bookId: string) => `/api/v2/books/${encodeURIComponent(bookId)}/sound`;

export function isSoundRunActive(run: SoundRun | null | undefined): boolean {
  return Boolean(run && (run.status === "queued" || run.status === "running"));
}

export function useSoundChapter(chapterId: string, enabled: boolean) {
  return useQuery({
    queryKey: soundKeys.chapter(chapterId),
    queryFn: () => apiGet<SoundChapterResponse>(chapterPath(chapterId)),
    enabled: enabled && Boolean(chapterId),
    staleTime: 30_000,
  });
}

export function useSoundBook(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: soundKeys.book(bookId),
    queryFn: () => apiGet<SoundBookResponse>(bookPath(bookId)),
    enabled: enabled && Boolean(bookId),
    staleTime: 30_000,
    // пока прогон идёт, ход и растущий список мест обновляются сами
    refetchInterval: (query) => (isSoundRunActive(query.state.data?.run ?? null) ? 5_000 : false),
  });
}

export function useSoundEstimate(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: soundKeys.estimate(bookId),
    queryFn: () => apiGet<SoundEstimate>(`${bookPath(bookId)}/estimate`),
    enabled: enabled && Boolean(bookId),
    staleTime: 0,
  });
}

type MarkerResponse = { ok: boolean; marker: SoundMarker };
type MarkerFields = Partial<SoundMarker["payload"]> & { place_id?: string; quote?: string };

/** Ручная сцена/переход/звук у абзаца. */
export function addSoundMarker(
  chapterId: string,
  kind: SoundKind,
  segmentId: string,
  fields: MarkerFields = {},
): Promise<MarkerResponse> {
  return apiPostJson<MarkerResponse>(`${chapterPath(chapterId)}/markers`, { ...fields, kind, segment_id: segmentId });
}

const markerPath = (markerId: string) => `/api/v2/sound/markers/${encodeURIComponent(markerId)}`;

/** Правка полей; `segment_id` — перенос на другой абзац (звук может нести новую `quote`). */
export function editSoundMarker(markerId: string, fields: MarkerFields & { segment_id?: string }): Promise<MarkerResponse> {
  return apiPatchJson<MarkerResponse>(markerPath(markerId), fields);
}

/** Отклонить маркер — повторный прогон его не воскресит. */
export function dismissSoundMarker(markerId: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(markerPath(markerId));
}

export function editSoundPlace(
  placeId: string,
  fields: { name?: string; description?: string; ambience_queries?: string[] },
): Promise<{ ok: boolean }> {
  return apiPatchJson<{ ok: boolean }>(`/api/v2/sound/places/${encodeURIComponent(placeId)}`, fields);
}

/** Кандидат на склейку: `merge: true` — одно место, `false` — разные. */
export function decideSoundPair(pairId: string, merge: boolean): Promise<{ ok: boolean; status: string }> {
  return apiPostJson<{ ok: boolean; status: string }>(`/api/v2/sound/pairs/${encodeURIComponent(pairId)}`, { merge });
}

export function startSound(bookId: string, mode: SoundMode): Promise<{ ok: boolean; run_id: string }> {
  return postWithLimit<{ ok: boolean; run_id: string }>(`${bookPath(bookId)}/run`, { mode });
}

export function stopSound(bookId: string): Promise<{ ok: boolean; stopped: boolean }> {
  return apiPostJson<{ ok: boolean; stopped: boolean }>(`${bookPath(bookId)}/stop`, {});
}

/** Перечитать одну главу. */
export function rerunSoundChapter(chapterId: string): Promise<{ ok: boolean; run_id: string }> {
  return postWithLimit<{ ok: boolean; run_id: string }>(`${chapterPath(chapterId)}/run`, {});
}

/** Смета эмбиента главы — для подтверждения: сколько треков, минут и пропусков. */
export function ambientPlan(chapterId: string): Promise<AmbientPlan> {
  return apiGet<AmbientPlan>(`/api/v2/chapters/${encodeURIComponent(chapterId)}/ambient/plan`);
}

/** Сгенерировать эмбиент сценам главы, у которых ещё нет готового трека. */
export function startChapterAmbient(chapterId: string): Promise<{ ok: boolean; run_id: string }> {
  return postWithLimit<{ ok: boolean; run_id: string }>(`/api/v2/chapters/${encodeURIComponent(chapterId)}/ambient`, {});
}

/** Остановить генерацию эмбиента главы: текущий трек доделается, следующий не начнётся. */
export function stopChapterAmbient(chapterId: string): Promise<{ ok: boolean; stopped: boolean }> {
  return apiPostJson<{ ok: boolean; stopped: boolean }>(`/api/v2/chapters/${encodeURIComponent(chapterId)}/ambient/stop`, {});
}

/** Перегенерировать трек одной сцены; `prompt` — только если его правили (иначе новый пишет Opus). */
export function regenerateSceneAmbient(markerId: string, prompt?: string): Promise<{ ok: boolean; run_id: string }> {
  return postWithLimit<{ ok: boolean; run_id: string }>(`${markerPath(markerId)}/ambient`, prompt ? { prompt } : {});
}

export const ambientAudioUrl = (audioFileId: string) => `/api/v2/ambient/${encodeURIComponent(audioFileId)}/audio`;

/** Коды ошибок ручек эмбиента → слова для человека. */
const AMBIENT_ERROR_LABELS: Record<string, string> = {
  already_running: "Generation already running",
  not_ready: "Chapter recording is incomplete",
  nothing_to_do: "Nothing to generate",
  no_key: "No ElevenLabs key configured on the server",
  not_placed: "This scene has no timeline position; record it first",
  not_scene: "Ambience is available for scenes only",
  bad_prompt: "Prompt is invalid or exceeds 2,000 characters",
  queue_unavailable: "Queue unavailable; try again later",
  read_only: "Only editors can generate ambience",
  not_found: "This scene no longer exists; refresh the page",
};

export function describeAmbientError(error: unknown, fallback = "the server did not respond"): string {
  const code = error instanceof ApiError ? error.errorCode : "";
  return AMBIENT_ERROR_LABELS[code] || describeApiError(error, fallback);
}

/** Коды ошибок ручек слоя → слова для человека. */
const SOUND_ERROR_LABELS: Record<string, string> = {
  bad_quote: "This quote is not in the paragraph",
  bad_segment: "This paragraph belongs to another chapter",
  bad_place: "This location no longer exists; refresh the page",
  bad_kind: "Unknown marker type",
  bad_name: "Location name cannot be empty",
  bad_merge: "Invalid decision for this pair",
  not_found: "This item was deleted or merged. Refresh the page",
  already_running: "a run is already in progress",
  book_busy: "book annotation is running",
  no_credits: "insufficient RouterAI balance",
  no_markup: "this chapter has no annotation to process",
  bad_mode: "Unknown run mode",
};

export function describeSoundError(error: unknown, fallback = "Not saved."): string {
  const code = error instanceof ApiError ? error.errorCode : "";
  return SOUND_ERROR_LABELS[code] || describeApiError(error, fallback);
}

/** «a, b ,, c» → ["a", "b", "c"]: запросы правятся одной строкой через запятую. */
export function splitQueries(text: string): string[] {
  return text.split(",").map((part) => part.trim()).filter(Boolean);
}
