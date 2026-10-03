import { useCallback, useEffect, useState } from "react";

export type FontSize = "s" | "m" | "l";

export type ReaderPrefs = {
  /** render stress marks (U+0301 + underlined vowel) */
  stress: boolean;
  /** every mark, «или» and «даже» included; off = only rare words and human marks */
  stressAll: boolean;
  /** how many paragraphs around each of "my" lines stay visible in role mode */
  radius: number;
  font: FontSize;
  /** chapter rail open on wide screens */
  rail: boolean;
  /** cast column open on wide screens (a sheet on a phone) */
  cast: boolean;
  /** split view: the author's original next to the script (desktop only) */
  compare: boolean;
  /** editor: звуковой слой на полях (сцены, переходы, звуки) */
  sound: boolean;
};

const STORAGE_KEY = "v2reader.prefs";

export const DEFAULT_PREFS: ReaderPrefs = { stress: true, stressAll: false, radius: 2, font: "m", rail: true, cast: true, compare: false, sound: true };

function readPrefs(): ReaderPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as Partial<ReaderPrefs>;
    return {
      stress: typeof parsed.stress === "boolean" ? parsed.stress : DEFAULT_PREFS.stress,
      stressAll: typeof parsed.stressAll === "boolean" ? parsed.stressAll : DEFAULT_PREFS.stressAll,
      radius: Number.isFinite(parsed.radius) ? Math.max(0, Math.min(10, Number(parsed.radius))) : DEFAULT_PREFS.radius,
      font: parsed.font === "s" || parsed.font === "l" ? parsed.font : "m",
      rail: typeof parsed.rail === "boolean" ? parsed.rail : DEFAULT_PREFS.rail,
      cast: typeof parsed.cast === "boolean" ? parsed.cast : DEFAULT_PREFS.cast,
      compare: typeof parsed.compare === "boolean" ? parsed.compare : DEFAULT_PREFS.compare,
      sound: typeof parsed.sound === "boolean" ? parsed.sound : DEFAULT_PREFS.sound,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function useReaderPrefs() {
  const [prefs, setPrefs] = useState<ReaderPrefs>(readPrefs);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch {
      /* storage unavailable — prefs live for the session only */
    }
  }, [prefs]);

  const update = useCallback((patch: Partial<ReaderPrefs>) => {
    setPrefs((current) => ({ ...current, ...patch }));
  }, []);

  return [prefs, update] as const;
}
