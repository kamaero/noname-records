/**
 * Query keys, fetchers and adapters for the editor endpoints
 * (`/api/v2/books/{id}/stress-queue|cast|profile`). Everything here is only
 * used when the chapter payload says `can_edit`.
 */
import { useQuery, type QueryClient } from "@tanstack/react-query";
import { postWithLimit } from "../utils/limitConfirm";
import { ApiError, apiGet, apiPostJson, describeApiError } from "../api/client";
import type { BudgetCharacterRow } from "../types";
import type {
  AuditionsResponse,
  BookCastCharacter,
  BookCastResponse,
  ApproveChapterResponse,
  BookProfileResponse,
  ConsiliumDecision,
  ConsiliumEstimate,
  ConsiliumMode,
  ConsiliumResponse,
  CreateRoleResponse,
  DisputedResponse,
  ReassignResponse,
  ReassignSpan,
  RecordingImpact,
  StressQueueResponse,
  StressQueueScope,
} from "./types";
import { isConsiliumRunActive } from "./consilium";

export const editorKeys = {
  /** prefix — invalidating it refetches every open chapter */
  chapters: ["v2", "chapter-script"] as const,
  queue: (bookId: string) => ["v2", "stress-queue", bookId] as const,
  disputed: (bookId: string) => ["v2", "disputed", bookId] as const,
  cast: (bookId: string) => ["v2", "book-cast", bookId] as const,
  auditions: (bookId: string) => ["v2", "auditions", bookId] as const,
  profile: (bookId: string) => ["v2", "book-profile", bookId] as const,
  consilium: (bookId: string) => ["v2", "consilium", bookId] as const,
  consiliumEstimate: (bookId: string) => ["v2", "consilium-estimate", bookId] as const,
};

const book = (bookId: string) => `/api/v2/books/${encodeURIComponent(bookId)}`;

export function useStressQueue(bookId: string, enabled: boolean, scope: StressQueueScope = "all", limit = 200) {
  return useQuery({
    queryKey: [...editorKeys.queue(bookId), scope, limit],
    queryFn: () =>
      apiGet<StressQueueResponse>(`${book(bookId)}/stress-queue?scope=${scope}&limit=${limit}`),
    enabled: enabled && Boolean(bookId),
    staleTime: 30_000,
  });
}

/** «Ударение тут не нужно» — the word leaves this book's queue (or comes back). */
export function skipStressWord(bookId: string, word: string, undo = false): Promise<{ ok: boolean; changed: boolean }> {
  return apiPostJson<{ ok: boolean; changed: boolean }>(`${book(bookId)}/stress-skip`, { word, undo });
}

/** Who a character is and how he should sound. A field left out is left alone. */
export type CharacterAbout = { race?: string; age?: string; temperament?: string; note?: string };

export function saveCharacterAbout(characterId: string, patch: CharacterAbout) {
  return apiPostJson<{ ok: boolean; character: { race: string; age: string; temperament: string; note: string } }>(
    `/api/v2/characters/${encodeURIComponent(characterId)}/about`,
    patch,
  );
}

/** A role the cast did not have. Returns the existing one when the name is already there. */
export function createRole(bookId: string, name: string): Promise<CreateRoleResponse> {
  return apiPostJson<CreateRoleResponse>(`${book(bookId)}/characters`, { name });
}

/** The author's mark on one chapter. `approved: false` takes it back. */
export function approveChapter(chapterId: string, approved: boolean): Promise<ApproveChapterResponse> {
  return apiPostJson<ApproveChapterResponse>(`/api/v2/chapters/${encodeURIComponent(chapterId)}/approve`, { approved });
}

export function useDisputed(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: editorKeys.disputed(bookId),
    queryFn: () => apiGet<DisputedResponse>(`${book(bookId)}/disputed`),
    enabled: enabled && Boolean(bookId),
    staleTime: 30_000,
  });
}

export function useConsilium(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: editorKeys.consilium(bookId),
    queryFn: () => apiGet<ConsiliumResponse>(`${book(bookId)}/consilium`),
    enabled: enabled && Boolean(bookId),
    staleTime: 30_000,
    // пока прогон идёт, ход обновляется сам; готовые находки появляются без перезагрузки
    refetchInterval: (query) => (isConsiliumRunActive(query.state.data?.run ?? null) ? 5_000 : false),
  });
}

export function useConsiliumEstimate(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: editorKeys.consiliumEstimate(bookId),
    queryFn: () => apiGet<ConsiliumEstimate>(`${book(bookId)}/consilium/estimate`),
    enabled: enabled && Boolean(bookId),
    staleTime: 0,
  });
}

export function startConsilium(bookId: string, mode: ConsiliumMode): Promise<{ ok: boolean; run_id: string }> {
  return postWithLimit<{ ok: boolean; run_id: string }>(`${book(bookId)}/consilium/run`, { mode });
}

export function stopConsilium(bookId: string): Promise<{ ok: boolean; stopped: boolean }> {
  return apiPostJson<{ ok: boolean; stopped: boolean }>(`${book(bookId)}/consilium/stop`, {});
}

const finding = (id: string) => `/api/v2/consilium/${encodeURIComponent(id)}`;

/** Без имени решает арбитр (только доказанная смена); с именем — решил человек. */
export function acceptFinding(id: string, speaker?: string): Promise<ConsiliumDecision> {
  return apiPostJson<ConsiliumDecision>(`${finding(id)}/accept`, speaker === undefined ? {} : { speaker });
}

export function dismissFinding(id: string): Promise<{ ok: boolean; finding_id: string }> {
  return apiPostJson<{ ok: boolean; finding_id: string }>(`${finding(id)}/dismiss`, {});
}

/** `GET /api/v2/consilium/{id}/recording-impact`: предупреждение о звуке для каждого имени каста. */
export function fetchRecordingImpact(id: string): Promise<RecordingImpact & { ok: boolean }> {
  return apiGet<RecordingImpact & { ok: boolean }>(`${finding(id)}/recording-impact`);
}

const CONSILIUM_ERROR_LABELS: Record<string, string> = {
  finding_not_found: "Находки больше нет. Обновите список.",
  finding_already_decided: "Эту находку уже решили — возможно, в другой вкладке. Список обновлён.",
  unknown_speaker: "Такой роли нет в касте книги.",
  span_not_in_markup: "Это место правили после прогона — посмотрите заново.",
  speaker_already_changed: "Это место правили после прогона — посмотрите заново.",
  arbiter_says_undecidable: "Арбитр не доказал — выберите имя сами.",
  arbiter_says_keep_current: "Арбитр подтвердил сценарий — выберите имя сами.",
  no_speaker_to_apply: "Применять нечего — выберите имя сами.",
  read_only: "Решать находки может только тот, кто правит разметку.",
  already_running: "Прогон консилиума уже идёт.",
  book_busy: "Книга сейчас размечается — дождитесь конца прогона.",
  no_credits: "На балансе RouterAI не хватает денег на этот прогон.",
  bad_mode: "Неизвестный режим прогона.",
  no_markup: "У книги нет размеченных глав — сверять не с чем.",
};

export function describeConsiliumError(error: unknown): string {
  const code = error instanceof ApiError ? error.errorCode : "";
  return CONSILIUM_ERROR_LABELS[code] || describeApiError(error, "Решение не сохранилось.");
}

/** Место правили после прогона: карточка оставляет только «оставить» и «другая роль…». */
export function isStaleFindingError(error: unknown): boolean {
  const code = error instanceof ApiError ? error.errorCode : "";
  return code === "span_not_in_markup" || code === "speaker_already_changed";
}

export function useBookCast(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: editorKeys.cast(bookId),
    queryFn: () => apiGet<BookCastResponse>(`${book(bookId)}/cast`),
    enabled: enabled && Boolean(bookId),
    staleTime: 30_000,
  });
}

/** Пробы на роли этой книги. Владелец и автор видят все, диктор — только свои. */
export function useBookAuditions(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: editorKeys.auditions(bookId),
    queryFn: () => apiGet<AuditionsResponse>(`${book(bookId)}/auditions`),
    enabled: enabled && Boolean(bookId),
    staleTime: 30_000,
  });
}

export function useBookProfile(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: editorKeys.profile(bookId),
    queryFn: () => apiGet<BookProfileResponse>(`${book(bookId)}/profile`),
    enabled: enabled && Boolean(bookId),
    staleTime: 60_000,
  });
}

/** `POST /api/v2/segments/{id}/attribution`: the operator's word on who speaks in one segment. */
export function reassignSegment(segmentId: string, spans: ReassignSpan[]): Promise<ReassignResponse> {
  return apiPostJson<ReassignResponse>(`/api/v2/segments/${encodeURIComponent(segmentId)}/attribution`, { spans });
}

/** The endpoint's error codes in the operator's words. */
const REASSIGN_ERROR_LABELS: Record<string, string> = {
  spans_required: "Нет диапазонов для сохранения.",
  span_not_object: "Диапазон передан в неверном виде.",
  speaker_required: "Не указана роль.",
  spans_overlap: "Диапазоны пересекаются.",
  unknown_speaker: "Такой роли нет в касте книги. Добавьте её в каст или выберите другую.",
  empty_span: "Пустой диапазон: выделите хотя бы один символ.",
  bad_offsets: "Границы диапазона не совпадают с текстом. Обновите главу.",
  segment_not_found: "Абзац не найден. Обновите главу.",
};

export function describeReassignError(error: unknown): string {
  const code = error instanceof ApiError ? error.errorCode : "";
  if (REASSIGN_ERROR_LABELS[code]) return REASSIGN_ERROR_LABELS[code];
  // a bare 404 (no code) is the route itself missing: a server older than this reader
  if (error instanceof ApiError && error.status === 404 && !code) return "Сервер не знает этот запрос. Обновите страницу; если не помогло — сервер старее интерфейса.";
  return describeApiError(error, "Не удалось изменить роль.");
}

export type ReaderQueryPart = "chapters" | "queue" | "disputed" | "cast" | "profile" | "consilium";

/** Refetch what an edit could have changed, so the reader repaints. */
export function invalidateReader(queryClient: QueryClient, bookId: string, parts: ReaderQueryPart[]): Promise<unknown> {
  const jobs: Promise<unknown>[] = [];
  if (parts.includes("chapters")) jobs.push(queryClient.invalidateQueries({ queryKey: editorKeys.chapters }));
  if (parts.includes("queue")) jobs.push(queryClient.invalidateQueries({ queryKey: editorKeys.queue(bookId) }));
  if (parts.includes("disputed")) jobs.push(queryClient.invalidateQueries({ queryKey: editorKeys.disputed(bookId) }));
  if (parts.includes("cast")) {
    jobs.push(queryClient.invalidateQueries({ queryKey: editorKeys.cast(bookId) }));
    jobs.push(queryClient.invalidateQueries({ queryKey: ["book-budget", bookId] }));
  }
  if (parts.includes("profile")) jobs.push(queryClient.invalidateQueries({ queryKey: editorKeys.profile(bookId) }));
  if (parts.includes("consilium")) jobs.push(queryClient.invalidateQueries({ queryKey: editorKeys.consilium(bookId) }));
  return Promise.all(jobs);
}

/**
 * `/api/v2/books/{id}/cast` carries only the subset of `BudgetCharacterRow` the
 * palette helpers (`buildPaletteConflict`, `buildPaletteResolution`) read; the
 * rest is filled with neutral defaults so the v1 pure functions can be reused.
 */
export function toBudgetRow(character: BookCastCharacter): BudgetCharacterRow {
  const chapters = Array.isArray(character.appears_in_chapters) ? character.appears_in_chapters : [];
  return {
    character_id: character.character_id,
    char_map_id: character.char_map_id || "",
    name: character.name,
    is_narrator: Boolean(character.is_narrator),
    race: character.race || "",
    temperament: character.temperament || "",
    note: character.note || "",
    actor_name: character.actor_name || "",
    appears_in_chapters: chapters,
    chapter_count: chapters.length,
    lines_count: Number(character.lines_count || 0),
    approx_seconds: 0,
    fact_seconds: 0,
    calc_mode: "",
    manual_rate_rub_per_min: null,
    manual_fixed_rub: null,
    character_color: character.character_color || "",
    character_text_color: character.character_text_color || "",
    character_font_weight: character.character_font_weight || "",
    character_font_style: character.character_font_style || "",
    total_rub: 0,
  };
}

/** Human labels for `ReaderStress.source`. */
const STRESS_SOURCE_LABELS: Record<string, string> = {
  dict: "словарь",
  wiktionary: "Викисловарь",
  author: "словарь автора",
  book: "словарь книги",
  context: "контекст",
  operator: "оператор",
  text: "в тексте",
  rule: "правило",
  llm: "модель",
};

export function stressSourceLabel(source: string | undefined | null): string {
  const key = String(source || "").trim().toLowerCase();
  if (!key) return "нет";
  return STRESS_SOURCE_LABELS[key] || key;
}

/** `2026-06-10T12:00:00Z` → `10.06.2026, 15:00`; anything unparsable is shown as is. */
export function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
