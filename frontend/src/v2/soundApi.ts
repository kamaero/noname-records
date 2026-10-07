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
  already_running: "Генерация уже идёт",
  not_ready: "Глава записана не полностью",
  nothing_to_do: "Нечего генерировать",
  no_key: "Ключ ElevenLabs не задан на сервере",
  not_placed: "У сцены нет места на таймлайне — сначала нужна запись",
  not_scene: "Эмбиент бывает только у сцены",
  bad_prompt: "Промпт не распознан или длиннее 2000 знаков",
  queue_unavailable: "Очередь недоступна, попробуйте позже",
  read_only: "Эмбиент запускает только редактор",
  not_found: "Этой сцены уже нет — обновите страницу",
};

export function describeAmbientError(error: unknown, fallback = "сервер не ответил"): string {
  const code = error instanceof ApiError ? error.errorCode : "";
  return AMBIENT_ERROR_LABELS[code] || describeApiError(error, fallback);
}

/** Коды ошибок ручек слоя → слова для человека. */
const SOUND_ERROR_LABELS: Record<string, string> = {
  bad_quote: "Этой цитаты нет в абзаце",
  bad_segment: "Абзац из другой главы",
  bad_place: "Такого места в книге нет — обновите страницу",
  bad_kind: "Неизвестный вид маркера",
  bad_name: "Имя места не может быть пустым",
  bad_merge: "Решение по паре не распознано",
  not_found: "Этого уже нет — его удалили или склеили. Обновите страницу",
  already_running: "прогон уже идёт",
  book_busy: "книга сейчас размечается",
  no_credits: "не хватает денег на балансе RouterAI",
  no_markup: "в главе нет разметки — читать нечего",
  bad_mode: "Неизвестный режим прогона",
};

export function describeSoundError(error: unknown, fallback = "Не сохранилось."): string {
  const code = error instanceof ApiError ? error.errorCode : "";
  return SOUND_ERROR_LABELS[code] || describeApiError(error, fallback);
}

/** «a, b ,, c» → ["a", "b", "c"]: запросы правятся одной строкой через запятую. */
export function splitQueries(text: string): string[] {
  return text.split(",").map((part) => part.trim()).filter(Boolean);
}
