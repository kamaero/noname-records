/** Чистые помощники звукового слоя Читалки. */
import type { SoundMarker, SoundPlace } from "./types";

export function bySegment(markers: SoundMarker[]): Map<string, SoundMarker[]> {
  const out = new Map<string, SoundMarker[]>();
  for (const marker of markers) out.set(marker.segment_id, [...(out.get(marker.segment_id) ?? []), marker]);
  return out;
}

/** Номер сцены в главе: сервер отдаёт маркеры по порядку абзацев. */
export function sceneNumbers(markers: SoundMarker[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const marker of markers) if (marker.kind === "scene") out.set(marker.id, out.size + 1);
  return out;
}

/** «СЦЕНА 2 · Таверна у Ворот · ночь · дождь» — пустые части пропускаются. */
export function sceneTitle(marker: SoundMarker, place: SoundPlace | undefined, number: number): string {
  return [`СЦЕНА ${number}`, place?.name, marker.payload.time_of_day, marker.payload.weather].filter(Boolean).join(" · ");
}

export async function copyQuery(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
