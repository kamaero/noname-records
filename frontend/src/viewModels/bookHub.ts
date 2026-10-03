/**
 * Pure helpers for the Book hub (`/books/:bookId`): the v2 progress payload
 * normalised to six steps, the one primary action for the header, per-step
 * actions, and small formatters. No React, no fetching.
 */
import type { BookCastCharacter } from "../v2/types";

export type HubStepState = "done" | "running" | "pending" | "failed" | "skipped";
export type HubStepKey = "segment" | "cast" | "attribute" | "stress" | "review" | "publish";

export const HUB_STEP_KEYS: HubStepKey[] = ["segment", "cast", "attribute", "stress", "review", "publish"];
export const HUB_STEP_LABELS: Record<HubStepKey, string> = {
  segment: "Сегментация",
  cast: "Персонажи",
  attribute: "Роли",
  stress: "Ударения",
  review: "Проверка",
  publish: "Публикация",
};
export const STEP_STATE_WORD: Record<HubStepState, string> = {
  done: "готово",
  running: "идёт",
  pending: "ждёт",
  failed: "ошибка",
  skipped: "пропущено",
};

export type HubStep = { key: string; label: string; state: HubStepState; detail?: string };
/** `tokens` is `{prompt, completion}` per contract; older builds sent one number. */
export type HubTokens = number | { prompt?: number; completion?: number } | null;
export type HubRun = {
  id?: string;
  status: string;
  step: string;
  chapters_total: number;
  chapters_done: number;
  calls?: number;
  tokens?: HubTokens;
  started_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
};
/** One row of the model catalogue as the hub shows it. */
export type HubModelChoice = {
  key: string;
  label: string;
  provider: string;
  model: string;
  /** what the provider bills in: "RUB" or "USD" */
  currency: string;
  /** the provider's own numbers, per million tokens, in `currency` */
  price_in: number;
  price_out: number;
  /** converted to rubles at the studio's rate; null when that rate is unknown */
  rub_in: number | null;
  rub_out: number | null;
  rub_cache_read: number | null;
  /** the same in the provider's expensive window, when it has one */
  peak_rub_in: number | null;
  peak_rub_out: number | null;
  peak_hours: string;
  /** the provider may train on the text it is sent — the author's decision */
  trains_on_text: boolean;
  note: string;
  /** what the bench measured, in words; empty when never measured */
  accuracy: string;
};

export type HubModel = HubModelChoice & {
  choices: HubModelChoice[];
  /** rubles per dollar, as the studio has it */
  usd_rub_rate: number;
  /** what the last run would have cost on this model, or null with no tariff */
  last_run_cost_rub: number | null;
  /** whether that run fell in the provider's expensive window */
  last_run_peak: boolean | null;
};

/** how far the author has read: marked chapters out of the marked-up ones */
export type HubReview = { total: number; approved: number; published: number; attributed: number };

export type HubProgress = {
  model?: HubModel;
  review?: HubReview;
  /** an approved chapter opens for recording without waiting for the button */
  auto_publish?: boolean;
  mode: string;
  status?: string;
  stop_requested?: boolean;
  run: HubRun | null;
  steps: HubStep[];
  counts: Record<string, number>;
};

const ACTIVE_RUN = new Set(["queued", "running", "processing", "starting", "stopping"]);
const REVIEW_STATUSES = new Set([
  "author_review",
  "needs_review",
  "pending_review",
  "partially_approved",
  "done",
  "published",
  "published_to_dictor",
  "ready_for_mix",
]);

export function isRunActive(run: HubRun | null | undefined): boolean {
  return Boolean(run && ACTIVE_RUN.has(String(run.status || "").toLowerCase()));
}

export function emptyProgress(mode = "standard"): HubProgress {
  return {
    mode,
    run: null,
    steps: HUB_STEP_KEYS.map((key) => ({ key, label: HUB_STEP_LABELS[key], state: "pending" })),
    counts: {},
  };
}

const REVIEW_WORDS: Record<string, string> = {
  author_review: "передана автору на проверку",
  approved: "утверждена",
  published_to_dictor: "утверждена и опубликована",
  partially_approved: "утверждена частично",
};

/** The last two steps come from the backend as «статус <code>»; people read words, not codes. */
function humanStepDetail(key: HubStepKey, detail: string | undefined, status: string): string | undefined {
  if (!detail || !/^статус /.test(detail)) return detail;
  if (key === "review") return REVIEW_WORDS[status] || "ждёт конца разметки";
  if (key === "publish") return status === "published_to_dictor" ? "дикторы получили сценарий" : "после проверки";
  return detail;
}

/** Always six steps in pipeline order with labels; a 404 (null) becomes «standard, all pending». */
export function normalizeProgress(raw: Partial<HubProgress> | null | undefined): HubProgress {
  if (!raw) return emptyProgress();
  const byKey = new Map((raw.steps ?? []).map((s) => [s.key, s]));
  const status = String(raw.status || "");
  return {
    model: raw.model,
    review: raw.review ?? { total: 0, approved: 0, published: 0, attributed: 0 },
    auto_publish: Boolean(raw.auto_publish),
    mode: String(raw.mode || "standard"),
    status: raw.status,
    stop_requested: Boolean(raw.stop_requested),
    run: raw.run ?? null,
    steps: HUB_STEP_KEYS.map((key) => {
      const step = byKey.get(key);
      return {
        key,
        label: step?.label || HUB_STEP_LABELS[key],
        state: step?.state || "pending",
        detail: humanStepDetail(key, step?.detail, status),
      };
    }),
    counts: raw.counts ?? {},
  };
}

export function tokenTotal(tokens: HubTokens | undefined): number {
  if (!tokens) return 0;
  if (typeof tokens === "number") return tokens;
  return Number(tokens.prompt || 0) + Number(tokens.completion || 0);
}

export function formatTokens(tokens: HubTokens | undefined): string {
  if (!tokens) return "—";
  if (typeof tokens === "number") return tokens.toLocaleString("ru-RU");
  return `${Number(tokens.prompt || 0).toLocaleString("ru-RU")} + ${Number(tokens.completion || 0).toLocaleString("ru-RU")}`;
}

export function runFraction(run: HubRun | null | undefined): number {
  if (!run || !run.chapters_total) return 0;
  return Math.max(0, Math.min(1, run.chapters_done / run.chapters_total));
}

/** The status the header chip shows: the live run wins over the stored book status. */
export function hubStatusCode(bookStatus: string, progress: HubProgress): string {
  if (progress.stop_requested && isRunActive(progress.run)) return "stopping";
  if (isRunActive(progress.run)) return "processing";
  if (progress.run?.status === "failed") return "failed";
  return bookStatus;
}

export type PrimaryKind = "run" | "resume" | "stop" | "retry" | "reader" | "publish";
export type PrimaryAction = { kind: PrimaryKind; label: string };

/** One primary action per screen, chosen by state. `hasReader` = at least one chapter has v2 segments. */
export function choosePrimaryAction(status: string, progress: HubProgress, hasReader: boolean): PrimaryAction {
  const run = progress.run;
  if (isRunActive(run)) return { kind: "stop", label: "Остановить" };
  if (run?.status === "failed") return { kind: "retry", label: "Повторить" };
  if (status === "approved") return { kind: "publish", label: "Опубликовать дикторам" };
  if (hasReader && (REVIEW_STATUSES.has(status) || run?.status === "done")) return { kind: "reader", label: "Открыть читалку" };
  if (run?.status === "stopped") return { kind: "resume", label: "Продолжить разметку" };
  if (!run && hasReader) return { kind: "reader", label: "Открыть читалку" };
  return { kind: "run", label: "Запустить разметку" };
}

export type StepActionKind = "stress" | "reader" | "publish";
export type StepAction = { kind: StepActionKind; label: string };

// Ни «Персонажи» (cast), ни «Роли» (attribute) не несут своей кнопки повтора:
// гейт утверждения ушёл вместе со старым экраном приёмки, а карта персонажей —
// вместе со своим отдельным экраном. Повтор обоих шагов — в меню книги
// (см. `BookCommandCenterPage.tsx`), там же, где честно названы последствия
// дублей у «Персонажей».
export function stepAction(key: string): StepAction | null {
  switch (key) {
    case "stress":
      return { kind: "stress", label: "Ударения" };
    case "review":
      return { kind: "reader", label: "Открыть читалку" };
    case "publish":
      return { kind: "publish", label: "Опубликовать" };
    default:
      return null;
  }
}

/** The cast table shows the roles with the most lines; the narrator is a role too. */
export function topCast(rows: BookCastCharacter[], limit = 12): BookCastCharacter[] {
  return [...rows]
    .sort((a, b) => Number(b.lines_count || 0) - Number(a.lines_count || 0) || a.name.localeCompare(b.name, "ru"))
    .slice(0, limit);
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** «автор · 60 глав · 24 роли» for the header subtitle; empty parts are dropped. */
export function hubSubtitle(author: string, chapters: number, roles: number): string {
  const parts: string[] = [];
  if (author) parts.push(author);
  if (chapters) parts.push(`${chapters} ${plural(chapters, "глава", "главы", "глав")}`);
  if (roles) parts.push(`${roles} ${plural(roles, "роль", "роли", "ролей")}`);
  return parts.join(" · ");
}

