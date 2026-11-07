import { ApiError, apiGet, apiPostJson } from "../api/client";

export type SoundtrackLayer = "music" | "ambience" | "fx";
export type SoundtrackWork = {
  id: string; title: string; author: string; source: "openverse" | "collection" | "elevenlabs" | "youtube";
  provider?: string; external_id?: string; source_url: string; license: string; license_version: string;
  license_url: string; attribution: string; duration_seconds?: number | null; category?: string;
  preview_url?: string; download_url?: string; rights_status?: "compatible" | "share_alike" | "unknown";
  rights_warning?: string;
};
export type SoundtrackEntry = SoundtrackWork & {
  work_id: string; scene_id: string | null; scene_title: string; scene_missing: boolean;
  layer: SoundtrackLayer; used: boolean; original: Record<string, unknown>; overrides: Record<string, unknown>;
  ambient_track_id?: string;
};
export type SoundtrackResponse = {
  ok: boolean; chapter_id: string; book_id: string; version: number; locked: boolean;
  entries: SoundtrackEntry[]; rejections: Array<{ scene_id: string | null; layer: SoundtrackLayer; openverse_id: string }>;
  snapshots: Array<{ id: string; version: number; created_at: string; entries: SoundtrackEntry[] }>;
  legacy_tracks: Array<{ ambient_track_id: string; audio_file_id: string; title: string; status: string; preview_url: string }>;
};
export type SoundtrackSearch = { ok: boolean; results: SoundtrackWork[]; page: number; page_count: number };
export const soundtrackPath = (chapter: string) => `/api/v2/chapters/${encodeURIComponent(chapter)}/soundtrack`;
export const soundtrackKey = (chapter: string) => ["v2", "soundtrack", chapter] as const;
export const getSoundtrack = (chapter: string) => apiGet<SoundtrackResponse>(soundtrackPath(chapter));
export const changeSoundtrack = (chapter: string, body: Record<string, unknown>) => apiPostJson<SoundtrackResponse>(soundtrackPath(chapter), body);
export const searchKey = (chapter: string, layer: SoundtrackLayer, query: string, page: number) => ["v2", "openverse", chapter, layer, query, page] as const;

// Общая очередь ограничивает даже одновременные запросы трёх слоёв и подготовки главы.
let running = 0;
const waiting: Array<() => void> = [];
async function limited<T>(run: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  if (running >= 2) await new Promise<void>((resolve) => waiting.push(resolve));
  if (signal?.aborted) { waiting.shift()?.(); throw new DOMException("Aborted", "AbortError"); }
  running += 1;
  try { return await run(); }
  finally { running -= 1; waiting.shift()?.(); }
}
export function searchSoundtrack(chapter: string, layer: SoundtrackLayer, query: string, page: number, signal?: AbortSignal) {
  return limited(async () => {
    const params = new URLSearchParams({ q: query, page: String(page), layer });
    const response = await fetch(`${soundtrackPath(chapter)}/search?${params}`, { credentials: "include", signal });
    const body = await response.json();
    if (!response.ok) throw new ApiError(body.detail || body.error || "Каталог недоступен", { status: response.status, errorCode: body.error });
    return body as SoundtrackSearch;
  }, signal);
}
