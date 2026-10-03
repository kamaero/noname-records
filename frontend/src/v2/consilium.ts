/**
 * Правила карточки консилиума без React: кто стоит за каким именем, в каком порядке идут
 * находки, какая следующая. Вынесено сюда потому, что ошибка в этих правилах ставит
 * чужой голос одним кликом, — и проверяется `scripts/check-consilium.mjs`.
 */
import {
  NARRATOR,
  UNSURE,
  type ConsiliumItem,
  type ConsiliumKind,
  type ConsiliumRun,
  type ReassignImpact,
  type RecordingImpact,
} from "./types";

export const CONSILIUM_KINDS: ConsiliumKind[] = ["wrong_voice", "identity_play", "readers_split", "narrator_border"];

export const KIND_TITLES: Record<ConsiliumKind, string> = {
  wrong_voice: "Чужой голос",
  identity_play: "Автор играет с личностью",
  readers_split: "Чтецы разошлись",
  narrator_border: "Граница рассказчика",
};

export const KIND_NOTES: Record<ConsiliumKind, string> = {
  wrong_voice: "Оба чтеца независимо назвали другого персонажа.",
  identity_play: "Текст называет одного, сценарий ставит другого — автор может играть с читателем нарочно. Решение режиссёрское.",
  readers_split: "Два чтеца прочли по-разному, и оба не так, как в сценарии. Место объективно трудное.",
  narrator_border: "Персонаж или повествование — это условность студии (песни, надписи, воспоминания), а не факт текста.",
};

export type CandidateBacker = "arbiter" | "readers" | "opus" | "sol" | "script";

export type ConsiliumCandidate = {
  name: string;
  backers: CandidateBacker[];
  /** доказанный кандидат арбитра — первый и выделенный */
  primary: boolean;
  /** имя из сценария: выбор = «оставить как есть» */
  keep: boolean;
};

const BACKER_ORDER: CandidateBacker[] = ["arbiter", "readers", "opus", "sol", "script"];

export const BACKER_LABELS: Record<CandidateBacker, string> = {
  arbiter: "арбитр",
  readers: "чтецы",
  opus: "opus",
  sol: "sol",
  script: "сценарий",
};

const isApplicable = (name: string, cast: ReadonlySet<string>) => name === NARRATOR || cast.has(name);

/** Имя, которое можно поставить кнопкой; всё прочее — текст «ответ невнятный». */
export function readerAnswer(name: string, cast: ReadonlySet<string>): string {
  const trimmed = (name || "").trim();
  if (!trimmed || trimmed === UNSURE) return "не уверен";
  return isApplicable(trimmed, cast) ? trimmed : "ответ невнятный";
}

const proven = (item: ConsiliumItem) => item.evidence_proven && (item.arbiter_verdict === "change" || item.arbiter_verdict === "keep_current");

export function buildCandidates(item: ConsiliumItem, cast: ReadonlySet<string>): ConsiliumCandidate[] {
  const byName = new Map<string, ConsiliumCandidate>();
  const add = (raw: string, backer: CandidateBacker) => {
    const name = (raw || "").trim();
    if (!name || !isApplicable(name, cast)) return;
    const entry = byName.get(name) ?? { name, backers: [], primary: false, keep: false };
    if (!entry.backers.includes(backer)) entry.backers.push(backer);
    byName.set(name, entry);
  };
  // Имя арбитра — его голос только при доказанном вердикте; недоказанное имя он сам
  // объявил догадкой, и подпись «арбитр» выдала бы её за решение.
  if (proven(item)) add(item.arbiter_verdict === "keep_current" ? item.current_speaker : item.arbiter_speaker, "arbiter");
  if (item.readers_speaker) add(item.readers_speaker, "readers");
  else {
    add(item.reader_opus, "opus");
    add(item.reader_sol, "sol");
  }
  add(item.current_speaker, "script");

  const list = [...byName.values()];
  for (const entry of list) {
    entry.backers.sort((a, b) => BACKER_ORDER.indexOf(a) - BACKER_ORDER.indexOf(b));
    entry.keep = entry.name === item.current_speaker;
    entry.primary = entry.backers.includes("arbiter");
  }
  // Выделенный — первым; остальные в порядке добавления, сценарий последним.
  return [...list.filter((c) => c.primary), ...list.filter((c) => !c.primary && !c.keep), ...list.filter((c) => !c.primary && c.keep)];
}

function rank(item: ConsiliumItem): number {
  if (item.arbiter_verdict === "change" && item.evidence_proven) return 0;
  if (item.arbiter_verdict === "keep_current" && item.evidence_proven) return 1;
  return 2;
}

export function sortFindings(items: readonly ConsiliumItem[]): ConsiliumItem[] {
  return [...items].sort(
    (a, b) =>
      CONSILIUM_KINDS.indexOf(a.kind) - CONSILIUM_KINDS.indexOf(b.kind) ||
      rank(a) - rank(b) ||
      a.chapter_index - b.chapter_index ||
      a.ordinal - b.ordinal,
  );
}

/** Следующая нерешённая после `currentId` в порядке списка, по кругу; null — больше нет. */
export function nextFinding(items: readonly ConsiliumItem[], currentId: string): ConsiliumItem | null {
  const ordered = sortFindings(items);
  const at = ordered.findIndex((item) => item.id === currentId);
  for (let step = 1; step <= ordered.length; step += 1) {
    const candidate = ordered[(Math.max(at, 0) + step) % ordered.length];
    if (candidate.id !== currentId && candidate.status === "new") return candidate;
  }
  return null;
}

export function findingTag(item: ConsiliumItem): string {
  const readers = item.readers_speaker || [item.reader_opus, item.reader_sol].filter(Boolean).join(" / ");
  const status = rank(item) === 0 ? "доказано" : rank(item) === 1 ? "сценарий подтверждён" : "не доказано";
  return `${item.current_speaker} → ${readers} · ${status}`;
}

export function decisionLine(item: ConsiliumItem): string {
  if (item.status === "gone") return "снята: место больше не спорное";
  const when = item.decided_at ? `${item.decided_at.slice(8, 10)}.${item.decided_at.slice(5, 7)}` : "";
  const who = [item.decided_by, when].filter(Boolean).join(", ");
  const what = item.status === "accepted" ? `принято: ${item.decided_speaker}` : "оставлено как есть";
  return who ? `${what} · ${who}` : what;
}

const rub = (value: number) => Math.round(value).toLocaleString("ru-RU").replace(/\u00a0/g, " ");

export function isConsiliumRunActive(run: ConsiliumRun | null | undefined): boolean {
  return Boolean(run && (run.status === "queued" || run.status === "running"));
}

export function runProgressLine(run: ConsiliumRun): string {
  if (run.status === "queued" || run.phase === "queued") return "В очереди…";
  const money = `потрачено ${rub(run.spent_rub)} ₽ из ≈ ${rub(run.estimate_rub)}`;
  if (run.phase === "arbiter" || run.phase === "saving") return `Арбитр: ${run.arbiter_done} из ${run.arbiter_total} · ${money}`;
  return `Чтецы: гл. ${run.chapters_done} из ${run.chapters_total} · ${money}`;
}

/** Примечание к имени в карточке: только когда глава записана и есть что сказать. */
export function impactNote(impacts: RecordingImpact | undefined, name: string): { level: "info" | "warn"; text: string } | null {
  const entry = impacts?.recorded ? impacts.by_speaker[name] : undefined;
  if (!entry || !entry.text) return null;
  return { level: entry.level, text: entry.text };
}

/** Тост после ручной правки роли: предупреждения первыми; `null` — сказать нечего. */
export function reassignImpactToast(items: readonly ReassignImpact[] | undefined):
  { tone: "info" | "error"; title: string; detail: string } | null {
  const said = (items ?? []).filter((item) => item.text);
  if (!said.length) return null;
  const warn = said.some((item) => item.level === "warn");
  const ordered = [...said.filter((item) => item.level === "warn"), ...said.filter((item) => item.level !== "warn")];
  return { tone: warn ? "error" : "info", title: warn ? "Звук главы: нужна дозапись" : "Звук главы",
           detail: ordered.map((item) => item.text).join("\n") };
}

export function runOutcomeLine(run: ConsiliumRun, counts: { new?: number }): string {
  if (run.status === "done" && run.result) {
    const day = run.finished_at ? `${run.finished_at.slice(8, 10)}.${run.finished_at.slice(5, 7)}` : "";
    // Неполная глава — не «всё проверено»: пересверка её дочитает, и человеку надо это знать.
    const incomplete = run.chapters_incomplete > 0 ? ` · неполных глав ${run.chapters_incomplete}` : "";
    return `Консилиум ${day}: ${run.result.findings} находок, нерешённых ${counts.new ?? 0}${incomplete}`;
  }
  if (run.status === "stopped") return `Консилиум остановлен: ${run.error || "без причины"}`;
  if (run.status === "failed") return `Консилиум упал: ${run.error || "без подробностей"}`;
  return "";
}
