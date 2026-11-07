/**
 * «Ударения: нет · редкие · все» — one control for the chapter reader and «Все реплики роли».
 *
 * «Редкие» is the default: a mark over «или» and «даже» helps nobody, and among two
 * hundred thousand marks a book's «ка́рлица» is lost. Rare words (the backend's `rare`
 * flag, by word frequency) and every mark a person set stay visible.
 */
import type { ReaderPrefs } from "./useReaderPrefs";
import type { ReaderStress } from "./types";

type Mode = "off" | "rare" | "all";

const MODES: Array<{ key: Mode; label: string; title: string }> = [
  { key: "off", label: "none", title: "Hide stress marks" },
  { key: "rare", label: "rare", title: "Rare words that are easy to mispronounce, plus all manually added stress marks" },
  { key: "all", label: "all", title: "All available stress marks, including common words" },
];

/** Marks a person set are shown in every mode but «нет»: they are the decisions, not the noise. */
const HUMAN_SOURCES = new Set(["operator", "book", "author", "text"]);

export function stressMode(prefs: Pick<ReaderPrefs, "stress" | "stressAll">): Mode {
  if (!prefs.stress) return "off";
  return prefs.stressAll ? "all" : "rare";
}

/** The marks to draw in «редкие»; an older payload without the flag shows everything. */
export function visibleStress(stress: ReaderStress[], all: boolean): ReaderStress[] {
  if (all) return stress;
  return stress.filter((mark) => mark.rare !== false || HUMAN_SOURCES.has(mark.source));
}

export function StressToggle({ prefs, onPrefs, hint }: {
  prefs: ReaderPrefs;
  onPrefs: (patch: Partial<ReaderPrefs>) => void;
  /** extra line for the group's tooltip, e.g. «щёлкните по слову…» */
  hint?: string;
}) {
  const mode = stressMode(prefs);
  return (
    <div className="v2r-seg v2r-stress-seg" role="group" aria-label="Word stress" title={hint}>
      <span className="v2r-seg-label">Word stress</span>
      {MODES.map((option) => (
        <button
          key={option.key}
          type="button"
          className={mode === option.key ? "v2r-seg-btn is-on" : "v2r-seg-btn"}
          onClick={() => onPrefs(option.key === "off" ? { stress: false } : { stress: true, stressAll: option.key === "all" })}
          aria-pressed={mode === option.key}
          title={option.title}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
