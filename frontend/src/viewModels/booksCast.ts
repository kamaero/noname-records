import type { CSSProperties } from "react";
import type { BooksResponse, BudgetCharacterRow, CharacterMapRow, RoleIntersection } from "../types";

export type FontWeightOption = "400" | "500" | "600" | "700" | "800" | "900";
export type FontStyleOption = "normal" | "italic";

export const FONT_WEIGHT_OPTIONS: Array<{ value: FontWeightOption; label: string }> = [
  { value: "400", label: "Обычный 400" },
  { value: "500", label: "Medium 500" },
  { value: "600", label: "SemiBold 600" },
  { value: "700", label: "Bold 700" },
  { value: "800", label: "ExtraBold 800" },
  { value: "900", label: "Black 900" },
];

export const FONT_STYLE_OPTIONS: Array<{ value: FontStyleOption; label: string }> = [
  { value: "normal", label: "Прямой" },
  { value: "italic", label: "Курсив" },
];

const AUTO_BACKGROUND_STRIDE = 251;
const AUTO_CANDIDATE_SCAN_LIMIT = 18000;
const AUTO_REPAIR_SCAN_LIMIT = 30000;
const AUTO_REPAIR_PASSES = 4;
const AUTO_TEXT_COLORS = ["#11161D", "#5B1838", "#17324A", "#29402B", "#5A3210", "#F8FBFF", "#FFF4DC"];

function hslToHex(hue: number, saturation: number, lightness: number): string {
  const chroma = (1 - Math.abs((2 * lightness) - 1)) * saturation;
  const huePrime = hue / 60;
  const x = chroma * (1 - Math.abs((huePrime % 2) - 1));
  let r = 0;
  let g = 0;
  let b = 0;
  if (huePrime >= 0 && huePrime < 1) {
    r = chroma;
    g = x;
  } else if (huePrime < 2) {
    r = x;
    g = chroma;
  } else if (huePrime < 3) {
    g = chroma;
    b = x;
  } else if (huePrime < 4) {
    g = x;
    b = chroma;
  } else if (huePrime < 5) {
    r = x;
    b = chroma;
  } else {
    r = chroma;
    b = x;
  }
  const match = lightness - (chroma / 2);
  const toHex = (value: number) => Math.round((value + match) * 255).toString(16).padStart(2, "0").toUpperCase();
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function uniqueColors(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    const normalized = normalizeHexColor(value, "#13333B");
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

const GENERATED_AUTO_BACKGROUND_PALETTE = Array.from({ length: 60 }, (_, index) => index * 6).flatMap((hue) => (
  [0.55, 0.7, 0.85, 0.98].flatMap((saturation) => (
    [0.25, 0.34, 0.43, 0.52, 0.61, 0.7, 0.79].map((lightness) => hslToHex(hue, saturation, lightness))
  ))
));

const AUTO_BACKGROUND_PALETTE = uniqueColors(GENERATED_AUTO_BACKGROUND_PALETTE).map((_, index, values) => (
  values[(index * AUTO_BACKGROUND_STRIDE) % values.length]
));

const AUTO_TYPOGRAPHY_PROFILES: Array<{ text: string; weight: FontWeightOption; style: FontStyleOption }> = (
  FONT_WEIGHT_OPTIONS.flatMap((weight) => (
    FONT_STYLE_OPTIONS.flatMap((style) => (
      AUTO_TEXT_COLORS.map((text) => ({ text, weight: weight.value, style: style.value }))
    ))
  ))
);

export type PaletteDraft = {
  background: string;
  text: string;
  weight: FontWeightOption;
  style: FontStyleOption;
};

export type PaletteConflict = {
  leftId: string;
  leftName: string;
  rightId: string;
  rightName: string;
  chapters: number[];
  backgroundDistance: number;
  textDistance: number;
};

export type PaletteResolutionEntry = {
  row: BudgetCharacterRow;
  palette: PaletteDraft;
};

export function formatCastDuration(seconds: number): string {
  const safe = Math.max(0, Number(seconds || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  if (hours) {
    const minutesInHour = Math.floor((safe % 3600) / 60);
    return `${hours}h ${String(minutesInHour).padStart(2, "0")}m`;
  }
  if (!minutes) return `${rest}s`;
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

export function formatCastCurrency(value: number): string {
  return `${Math.max(0, Number(value || 0)).toLocaleString("ru-RU")} ₽`;
}

export function sortCastRows(rows: BudgetCharacterRow[]): BudgetCharacterRow[] {
  return [...rows].sort((left, right) => {
    if (left.is_narrator && !right.is_narrator) return -1;
    if (!left.is_narrator && right.is_narrator) return 1;
    if (right.lines_count !== left.lines_count) return right.lines_count - left.lines_count;
    if (right.approx_seconds !== left.approx_seconds) return right.approx_seconds - left.approx_seconds;
    return left.name.localeCompare(right.name, "ru");
  });
}

export function formatBookCardMeta(book: BooksResponse["items"][number]): string {
  const chapters = `${book.chapter_count} ${book.chapter_count === 1 ? "глава" : book.chapter_count < 5 ? "главы" : "глав"}`;
  const characters = `${book.character_count} ${book.character_count === 1 ? "персонаж" : book.character_count < 5 ? "персонажа" : "персонажей"}`;
  return `${chapters} · ${characters}`;
}

export function isBookReadyForRecording(status: string): boolean {
  return String(status || "").trim().toLowerCase() === "published_to_dictor";
}

export function normalizeHexColor(value: string, fallback: string): string {
  const clean = String(value || "").trim();
  if (/^#[0-9a-f]{6}$/i.test(clean)) return clean.toUpperCase();
  if (/^[0-9a-f]{6}$/i.test(clean)) return `#${clean.toUpperCase()}`;
  return fallback.toUpperCase();
}

function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const normalized = normalizeHexColor(hex, "#000000").slice(1);
  return {
    r: Number.parseInt(normalized.slice(0, 2), 16),
    g: Number.parseInt(normalized.slice(2, 4), 16),
    b: Number.parseInt(normalized.slice(4, 6), 16),
  };
}

function colorDistance(first: string, second: string): number {
  const a = hexToRgb(first);
  const b = hexToRgb(second);
  return Math.sqrt(((a.r - b.r) ** 2) + ((a.g - b.g) ** 2) + ((a.b - b.b) ** 2));
}

function palettesEqual(left: PaletteDraft, right: PaletteDraft): boolean {
  return (
    normalizeHexColor(left.background, "#13333B") === normalizeHexColor(right.background, "#13333B")
    && normalizeHexColor(left.text, "#D7FFE9") === normalizeHexColor(right.text, "#D7FFE9")
    && left.weight === right.weight
    && left.style === right.style
  );
}

function intersectChapters(left: number[], right: number[]): number[] {
  const rightSet = new Set(right);
  return left.filter((chapter) => rightSet.has(chapter));
}

/** Below this many lines a role is not on a page often enough to be confused with anybody. */
export const PALETTE_WARN_MIN_LINES = 3;

/**
 * Which roles a chosen colour would be confusable with — a warning, not a verdict.
 *
 * The editor used to refuse a colour that clashed with any of the book's roles, all
 * 148 of them, most of which never share a page: choosing violet for Пупип was
 * rejected because of Тупуг from chapter 57. The rule here is the one the rest of the
 * product uses — the two roles have to meet in a chapter and both have to actually
 * speak — and the answer is shown next to the colour instead of blocking the save.
 */
export function palettePeers(
  row: BudgetCharacterRow,
  draft: PaletteDraft,
  others: BudgetCharacterRow[],
  minLines = PALETTE_WARN_MIN_LINES,
): PaletteConflict[] {
  return others
    .filter((item) => item.character_id !== row.character_id)
    .filter((item) => !item.is_narrator && Number(item.lines_count || 0) >= minLines)
    .map((item) => buildPaletteConflict(row, item, draft))
    .filter((item): item is PaletteConflict => Boolean(item))
    .sort((a, b) => a.backgroundDistance - b.backgroundDistance);
}

export function buildPaletteConflict(
  left: BudgetCharacterRow,
  right: BudgetCharacterRow,
  leftPalette?: PaletteDraft,
  rightPalette?: PaletteDraft,
): PaletteConflict | null {
  if (left.is_narrator || right.is_narrator) return null;
  const shared = intersectChapters(left.appears_in_chapters || [], right.appears_in_chapters || []);
  if (!shared.length) return null;
  const leftBg = normalizeHexColor(leftPalette?.background || left.character_color || "#13333B", "#13333B");
  const rightBg = normalizeHexColor(rightPalette?.background || right.character_color || "#13333B", "#13333B");
  const leftText = normalizeHexColor(leftPalette?.text || left.character_text_color || "#D7FFE9", "#D7FFE9");
  const rightText = normalizeHexColor(rightPalette?.text || right.character_text_color || "#D7FFE9", "#D7FFE9");
  const leftWeight = leftPalette?.weight || ((left.character_font_weight || "700") as FontWeightOption);
  const rightWeight = rightPalette?.weight || ((right.character_font_weight || "700") as FontWeightOption);
  const leftStyle = leftPalette?.style || ((left.character_font_style || "normal") as FontStyleOption);
  const rightStyle = rightPalette?.style || ((right.character_font_style || "normal") as FontStyleOption);
  const bgDistance = colorDistance(leftBg, rightBg);
  const textDistance = colorDistance(leftText, rightText);
  const sameTypography = leftWeight === rightWeight && leftStyle === rightStyle;
  const conflict = bgDistance < 24 || (sameTypography && (bgDistance < 96 || (bgDistance < 132 && textDistance < 80)));
  if (!conflict) return null;
  return {
    leftId: left.character_id,
    leftName: left.name,
    rightId: right.character_id,
    rightName: right.name,
    chapters: shared.slice(0, 8),
    backgroundDistance: Math.round(bgDistance),
    textDistance: Math.round(textDistance),
  };
}

export function findPaletteConflicts(rows: BudgetCharacterRow[]): PaletteConflict[] {
  const conflicts: PaletteConflict[] = [];
  for (let index = 0; index < rows.length; index += 1) {
    for (let second = index + 1; second < rows.length; second += 1) {
      const conflict = buildPaletteConflict(rows[index], rows[second]);
      if (conflict) conflicts.push(conflict);
    }
  }
  return conflicts.sort((left, right) => left.backgroundDistance - right.backgroundDistance);
}

export type MeaningfulPaletteConflict = PaletteConflict & {
  /** how many chapters both roles speak in (not truncated like `chapters`) */
  sharedChapters: number;
  /** the smaller of the two line counts — a pair with a one-line role is not worth a fix */
  minLines: number;
  /** sharedChapters × minLines: the order the operator should look at them in */
  weight: number;
};

export type MeaningfulPaletteConflictOptions = {
  /** both roles must have at least this many lines (default 3) */
  minLines?: number;
  /** both roles must share at least one chapter (default true; false = the whole book) */
  sameChapter?: boolean;
};

/**
 * `findPaletteConflicts` narrowed to pairs that can confuse an actor: both roles
 * speak (≥ `minLines`) and, by default, meet in a chapter. On a 200-role book the
 * full pairing is hundreds of lines; this is the handful worth fixing, heaviest
 * first. `findPaletteConflicts` stays as is for the v1 screens.
 */
export function findMeaningfulPaletteConflicts(
  rows: BudgetCharacterRow[],
  { minLines = 3, sameChapter = true }: MeaningfulPaletteConflictOptions = {},
): MeaningfulPaletteConflict[] {
  const byId = new Map(rows.map((row) => [row.character_id, row] as const));
  const out: MeaningfulPaletteConflict[] = [];
  for (const conflict of findPaletteConflicts(rows)) {
    const left = byId.get(conflict.leftId);
    const right = byId.get(conflict.rightId);
    if (!left || !right) continue;
    const lines = Math.min(Number(left.lines_count || 0), Number(right.lines_count || 0));
    if (lines < minLines) continue;
    const shared = intersectChapters(left.appears_in_chapters || [], right.appears_in_chapters || []).length;
    if (sameChapter && shared === 0) continue;
    out.push({ ...conflict, sharedChapters: shared, minLines: lines, weight: Math.max(shared, 1) * lines });
  }
  return out.sort(
    (a, b) => b.weight - a.weight || a.backgroundDistance - b.backgroundDistance || a.leftName.localeCompare(b.leftName, "ru"),
  );
}

function paletteConflictScore(left: PaletteDraft, right: PaletteDraft): number {
  const bgDistance = colorDistance(left.background, right.background);
  const textDistance = colorDistance(left.text, right.text);
  let score = bgDistance + (textDistance * 0.35);
  if (left.weight !== right.weight) score += 18;
  if (left.style !== right.style) score += 22;
  return score;
}

function paletteCandidateAt(index: number): PaletteDraft {
  const profileCount = AUTO_TYPOGRAPHY_PROFILES.length;
  const background = AUTO_BACKGROUND_PALETTE[Math.floor(index / profileCount) % AUTO_BACKGROUND_PALETTE.length];
  const profile = AUTO_TYPOGRAPHY_PROFILES[index % profileCount];
  return {
    background,
    text: profile.text,
    weight: profile.weight,
    style: profile.style,
  };
}

function buildSharedChapterNeighbors(rows: BudgetCharacterRow[]): Map<string, BudgetCharacterRow[]> {
  const out = new Map<string, BudgetCharacterRow[]>();
  for (const row of rows) {
    out.set(row.character_id, []);
  }
  for (let index = 0; index < rows.length; index += 1) {
    for (let second = index + 1; second < rows.length; second += 1) {
      if (!intersectChapters(rows[index].appears_in_chapters || [], rows[second].appears_in_chapters || []).length) {
        continue;
      }
      out.get(rows[index].character_id)?.push(rows[second]);
      out.get(rows[second].character_id)?.push(rows[index]);
    }
  }
  return out;
}

function conflictDegreeByRow(conflicts: PaletteConflict[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const conflict of conflicts) {
    out.set(conflict.leftId, (out.get(conflict.leftId) || 0) + 1);
    out.set(conflict.rightId, (out.get(conflict.rightId) || 0) + 1);
  }
  return out;
}

function findPaletteConflictsWithAssigned(rows: BudgetCharacterRow[], assigned: Map<string, PaletteDraft>): PaletteConflict[] {
  const conflicts: PaletteConflict[] = [];
  for (let index = 0; index < rows.length; index += 1) {
    for (let second = index + 1; second < rows.length; second += 1) {
      const conflict = buildPaletteConflict(
        rows[index],
        rows[second],
        assigned.get(rows[index].character_id),
        assigned.get(rows[second].character_id),
      );
      if (conflict) conflicts.push(conflict);
    }
  }
  return conflicts.sort((left, right) => left.backgroundDistance - right.backgroundDistance);
}

function choosePaletteForRow(
  row: BudgetCharacterRow,
  assigned: Map<string, PaletteDraft>,
  neighborsById: Map<string, BudgetCharacterRow[]>,
  offsetSeed: number,
  scanLimit = AUTO_CANDIDATE_SCAN_LIMIT,
): PaletteDraft {
  const candidateCount = AUTO_BACKGROUND_PALETTE.length * AUTO_TYPOGRAPHY_PROFILES.length;
  const scanCount = Math.min(scanLimit, candidateCount);
  const offset = (offsetSeed * AUTO_BACKGROUND_STRIDE) % candidateCount;
  let best = paletteFromRow(row);
  let bestConflictCount = Number.POSITIVE_INFINITY;
  let bestScore = -1;
  const neighbors = neighborsById.get(row.character_id) || [];
  for (let index = 0; index < scanCount; index += 1) {
    const candidate = paletteCandidateAt((offset + index) % candidateCount);
    let conflictCount = 0;
    let score = 0;
    for (const neighbor of neighbors) {
      const neighborPalette = assigned.get(neighbor.character_id) || paletteFromRow(neighbor);
      const conflict = buildPaletteConflict(row, neighbor, candidate, neighborPalette);
      if (conflict) conflictCount += 1;
      if (conflictCount > bestConflictCount) break;
      if (index < 1000) {
        score += paletteConflictScore(candidate, neighborPalette);
      }
    }
    if (conflictCount === 0) {
      return candidate;
    }
    if (conflictCount < bestConflictCount || (conflictCount === bestConflictCount && score > bestScore)) {
      best = candidate;
      bestConflictCount = conflictCount;
      bestScore = score;
    }
  }
  return best;
}

export function buildPaletteResolution(rows: BudgetCharacterRow[], conflicts: PaletteConflict[]): PaletteResolutionEntry[] {
  const rowsById = new Map(rows.map((row) => [row.character_id, row]));
  const neighborsById = buildSharedChapterNeighbors(rows);
  const assigned = new Map<string, PaletteDraft>();
  const initialDegree = conflictDegreeByRow(conflicts);
  const workRows = Array.from(new Set(conflicts.flatMap((conflict) => [conflict.leftId, conflict.rightId])))
    .map((id) => rowsById.get(id))
    .filter((row): row is BudgetCharacterRow => Boolean(row))
    .sort((left, right) => {
      const degreeDiff = (initialDegree.get(right.character_id) || 0) - (initialDegree.get(left.character_id) || 0);
      if (degreeDiff !== 0) return degreeDiff;
      if (right.lines_count !== left.lines_count) return right.lines_count - left.lines_count;
      if (right.chapter_count !== left.chapter_count) return right.chapter_count - left.chapter_count;
      return left.name.localeCompare(right.name, "ru");
    });

  workRows.forEach((row, index) => {
    assigned.set(row.character_id, choosePaletteForRow(row, assigned, neighborsById, index));
  });

  for (let pass = 1; pass <= AUTO_REPAIR_PASSES; pass += 1) {
    const remaining = findPaletteConflictsWithAssigned(rows, assigned);
    if (!remaining.length) break;
    const remainingDegree = conflictDegreeByRow(remaining);
    const focusRows = Array.from(new Set(remaining.flatMap((conflict) => [conflict.leftId, conflict.rightId])))
      .map((id) => rowsById.get(id))
      .filter((row): row is BudgetCharacterRow => Boolean(row))
      .sort((left, right) => {
        const degreeDiff = (remainingDegree.get(left.character_id) || 0) - (remainingDegree.get(right.character_id) || 0);
        if (degreeDiff !== 0) return degreeDiff;
        if (left.lines_count !== right.lines_count) return left.lines_count - right.lines_count;
        return left.name.localeCompare(right.name, "ru");
      });
    for (const [index, row] of focusRows.entries()) {
      assigned.set(row.character_id, choosePaletteForRow(row, assigned, neighborsById, (pass * 10000) + index, AUTO_REPAIR_SCAN_LIMIT));
    }
  }

  return workRows
    .map((row) => ({ row, palette: assigned.get(row.character_id) || paletteFromRow(row) }))
    .filter((entry) => !palettesEqual(paletteFromRow(entry.row), entry.palette));
}

export function paletteFromRow(row: BudgetCharacterRow): PaletteDraft {
  return {
    background: normalizeHexColor(row.character_color || "#13333B", "#13333B"),
    text: normalizeHexColor(row.character_text_color || "#D7FFE9", "#D7FFE9"),
    weight: (row.character_font_weight || "700") as FontWeightOption,
    style: (row.character_font_style || "normal") as FontStyleOption,
  };
}

export function characterSwatchStyle(row: BudgetCharacterRow): CSSProperties {
  if (row.is_narrator) {
    return {
      background: "rgba(12, 20, 26, 0.9)",
      color: "#d7ffe9",
      fontWeight: 800,
      fontStyle: "normal",
      borderColor: "rgba(94, 196, 149, 0.18)",
    };
  }
  return {
    background: row.character_color || "rgba(4, 18, 24, 0.88)",
    color: row.character_text_color || "#d7ffe9",
    fontWeight: Number.parseInt(row.character_font_weight || "", 10) || 700,
    fontStyle: row.character_font_style || "normal",
    borderColor: row.character_color || "rgba(94, 196, 149, 0.18)",
    boxShadow: row.character_color ? `0 0 0 1px ${row.character_color}33` : undefined,
  };
}

export function characterSwatchStyleFromPalette(palette: PaletteDraft): CSSProperties {
  return {
    background: palette.background,
    color: palette.text,
    fontWeight: Number.parseInt(palette.weight, 10) || 700,
    fontStyle: palette.style,
    borderColor: palette.background,
    boxShadow: `0 0 0 1px ${palette.background}33`,
  };
}

/* ============================================================
   Cast page (v2) — pure helpers over the same budget rows.
   ============================================================ */

/**
 * `GET /api/budget/{id}` sends `aliases` (a comma list) that `BudgetCharacterRow` does not
 * declare. `age`, `actor_tentative`, `canon_status`, `claimed_chapters`, `chapter_numbers`
 * and `has_audio` come only from the character map (`mergeRoleFacts`) — the budget response
 * knows nothing about them.
 */
export type CastRow = BudgetCharacterRow & {
  aliases?: string;
  age?: string;
  /** назначение предварительное — владелец пометил его «?» */
  actor_tentative?: boolean;
  canon_status?: "" | "known" | "new";
  /** какие главы назвало извлечение (`appears_in`); пусто — заявки нет, сравнивать не с чем */
  claimed_chapters?: number[];
  /** какие главы роль реально звучит по разметке — из карты, чтобы расхождение называло главы */
  chapter_numbers?: number[];
  /** записаны дубли диктора на это имя — сливать/удалять без потери аудио нельзя */
  has_audio?: boolean;
};

/** The subset of `GET /api/v2/books/{id}/cast` the page reads. */
export type CastCountsSource = {
  character_id: string;
  appears_in_chapters: number[];
  lines_count: number;
};

export type CastSortKey = "role" | "actor" | "lines" | "sum";
export type CastSortDir = "asc" | "desc";

/** Aliases as v1 stores them: a comma list; the character's own name is not an alias. */
export function splitCastAliases(row: Pick<CastRow, "name" | "aliases">): string[] {
  const text = String(row.aliases || "").trim();
  if (!text) return [];
  const own = row.name.trim().toLowerCase();
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of text.replace(/;/g, ",").replace(/\n/g, ",").split(",")) {
    const alias = part.trim();
    const key = alias.toLowerCase();
    if (!alias || key === own || seen.has(key)) continue;
    seen.add(key);
    out.push(alias);
  }
  return out;
}

/**
 * Budget rows with `appears_in_chapters` / `lines_count` taken from the v2 cast
 * (effective attributions — what the actor will read) where the v2 response
 * knows the character; the rest of the row (rate, sum, colours) stays v1.
 */
export function mergeCastCounts(rows: CastRow[], v2: CastCountsSource[] | undefined): CastRow[] {
  if (!v2 || !v2.length) return rows;
  const byId = new Map(v2.filter((item) => item.character_id).map((item) => [item.character_id, item]));
  return rows.map((row) => {
    const source = byId.get(row.character_id);
    if (!source) return row;
    const chapters = Array.isArray(source.appears_in_chapters) ? [...source.appears_in_chapters].sort((a, b) => a - b) : [];
    return {
      ...row,
      appears_in_chapters: chapters,
      chapter_count: chapters.length,
      lines_count: Number(source.lines_count || 0),
    };
  });
}

/**
 * Подмешать к строке сметы то, что знает только карта.
 *
 * Сводим по `character_id`, а не по имени: имя — ровно то, что на этом экране
 * чинят, и опечатка развела бы одну роль на две строки. Строка карты без
 * идентификатора — это имя, которое говорит в разметке, но роли для него нет;
 * сводить его не с чем, и в таблице сметы его тоже нет.
 */
export function mergeRoleFacts(rows: CastRow[], map: CharacterMapRow[] | undefined): CastRow[] {
  if (!map || !map.length) return rows;
  const byId = new Map(map.filter((item) => item.character_id).map((item) => [item.character_id, item]));
  return rows.map((row) => {
    const facts = byId.get(row.character_id);
    if (!facts) return row;
    return {
      ...row,
      age: facts.age,
      aliases: facts.aliases,
      actor_tentative: facts.actor_tentative,
      canon_status: facts.canon_status,
      has_audio: facts.has_audio,
      claimed_chapters: facts.claimed_chapters,
      chapter_numbers: facts.chapter_numbers,
    };
  });
}

export type ChapterMismatch = {
  /** сколько глав назвало извлечение */
  claimed: number;
  /** сколько глав насчитано по разметке */
  counted: number;
  /** заявлены извлечением, но разметки в них нет — обычно там и лежит искомый текст */
  missing: number[];
  /** размечены, но извлечение их не называло — либо заявка неполна, либо роль путают с другой */
  extra: number[];
};

/**
 * Расхождение «заявлено извлечением / посчитано по разметке» — сигнал о тексте,
 * а не о роли.
 *
 * Флаг зажигается ТОЛЬКО когда извлечение вообще сделало заявку. Пустой
 * `claimed_chapters` значит сразу два разных случая: «назвало ноль глав» и «про
 * роль не говорило вовсе», а на живой книге второе — 58 ролей из 201, включая
 * рассказчика первой строкой. Условие перенесено с удалённого экрана карты
 * персонажей (`CharacterMapPage`, коммит c227819), где оно и стояло; вместе с
 * ним вернулись и номера глав — подсказка обязана сказать, куда идти смотреть,
 * а не только на сколько разошёлся счёт.
 *
 * Считаем по `chapter_numbers` из ответа карты — тому же ответу, откуда пришла
 * заявка: сравнивать заявку с числом из другого запроса значит сличать два
 * разных подсчёта. Без карты остаётся `appears_in_chapters` сметы.
 */
export function chapterMismatch(row: CastRow): ChapterMismatch | null {
  const claimedList = row.claimed_chapters || [];
  if (!claimedList.length) return null;
  const countedList = row.chapter_numbers || row.appears_in_chapters || [];
  if (claimedList.length === countedList.length) return null;
  const counted = new Set(countedList);
  const claimed = new Set(claimedList);
  return {
    claimed: claimedList.length,
    counted: countedList.length,
    missing: claimedList.filter((chapter) => !counted.has(chapter)),
    extra: countedList.filter((chapter) => !claimed.has(chapter)),
  };
}

/** «заявлено 46, размечено 45 — нет разметки в главах 15 и 39»: слова и номера
 *  глав, а не голая разница в счёте. */
export function describeChapterMismatch(mismatch: ChapterMismatch): string {
  const parts = [`заявлено ${mismatch.claimed}, размечено ${mismatch.counted}`];
  if (mismatch.missing.length) {
    parts.push(`нет разметки в ${pluralRu(mismatch.missing.length, "главе", "главах", "главах")} ${mismatch.missing.join(", ")}`);
  }
  if (mismatch.extra.length) {
    parts.push(`размечено, но не заявлено, в ${pluralRu(mismatch.extra.length, "главе", "главах", "главах")} ${mismatch.extra.join(", ")}`);
  }
  return parts.join(" — ");
}

/** Distinct actor names already used in the cast, for the actor cell's datalist. */
export function collectActorNames(rows: CastRow[]): string[] {
  const seen = new Map<string, string>();
  for (const row of rows) {
    const name = String(row.actor_name || "").trim();
    if (!name) continue;
    const key = name.toLowerCase();
    if (!seen.has(key)) seen.set(key, name);
  }
  return Array.from(seen.values()).sort((a, b) => a.localeCompare(b, "ru"));
}

/** Search by role name, alias or actor; optionally only rows without an actor. */
export function filterCastRows(rows: CastRow[], query: string, onlyUnassigned: boolean): CastRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter((row) => {
    if (onlyUnassigned && String(row.actor_name || "").trim()) return false;
    if (!q) return true;
    if (row.name.toLowerCase().includes(q)) return true;
    if (String(row.actor_name || "").toLowerCase().includes(q)) return true;
    return splitCastAliases(row).some((alias) => alias.toLowerCase().includes(q));
  });
}

/** Column sort; the narrator stays first whatever the key. */
export function sortCastRowsBy(rows: CastRow[], key: CastSortKey | null, dir: CastSortDir): CastRow[] {
  const base = sortCastRows(rows) as CastRow[];
  if (!key) return base;
  const sign = dir === "asc" ? 1 : -1;
  const compare = (left: CastRow, right: CastRow): number => {
    switch (key) {
      case "role":
        return left.name.localeCompare(right.name, "ru") * sign;
      case "actor": {
        const a = String(left.actor_name || "").trim();
        const b = String(right.actor_name || "").trim();
        if (!a && b) return 1;
        if (a && !b) return -1;
        return a.localeCompare(b, "ru") * sign;
      }
      case "lines":
        return (left.lines_count - right.lines_count) * sign;
      case "sum":
        return (left.total_rub - right.total_rub) * sign;
      default:
        return 0;
    }
  };
  return [...base].sort((left, right) => {
    if (left.is_narrator && !right.is_narrator) return -1;
    if (!left.is_narrator && right.is_narrator) return 1;
    const primary = compare(left, right);
    if (primary !== 0) return primary;
    return left.name.localeCompare(right.name, "ru");
  });
}

/** `860282` → `860 282` (no currency sign; the line adds ₽ once). */
export function formatRub(value: number): string {
  return Math.max(0, Math.round(Number(value || 0))).toLocaleString("ru-RU");
}

export function pluralRu(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return many;
  if (last > 1 && last < 5) return few;
  if (last === 1) return one;
  return many;
}

export type CastSummary = { roles: number; withActor: number; lines: number };

/** «N ролей · N с актёром · N реплик» — roles exclude the narrator, lines are the sum shown in the table. */
export function summarizeCast(rows: CastRow[]): CastSummary {
  const voiced = rows.filter((row) => !row.is_narrator);
  return {
    roles: voiced.length,
    withActor: voiced.filter((row) => String(row.actor_name || "").trim()).length,
    lines: voiced.reduce((acc, row) => acc + Number(row.lines_count || 0), 0),
  };
}

export function formatCastSubtitle(summary: CastSummary): string {
  return [
    `${summary.roles.toLocaleString("ru-RU")} ${pluralRu(summary.roles, "роль", "роли", "ролей")}`,
    `${summary.withActor.toLocaleString("ru-RU")} с актёром`,
    `${summary.lines.toLocaleString("ru-RU")} ${pluralRu(summary.lines, "реплика", "реплики", "реплик")}`,
  ].join(" · ");
}

/* ============================================================
   Hub cast card — the same request as the cast page (`useBookCast`)
   plus `/character-map`, narrowed to a to-do list: what to look at
   first when opening a book, not the whole roster.
   ============================================================ */

export type CastTodoOptions = {
  /** how many roles without an actor to keep, heaviest first (default 5) */
  roles?: number;
  /** how many unacknowledged crossings to keep (default 3) */
  crossings?: number;
};

/** One line of the crossings list — the fields `<actor>: <role_a> ↔ <role_b> · гл. <chapter>` needs. */
export type CastTodoCrossing = Pick<RoleIntersection, "actor" | "role_a" | "role_b" | "chapter">;

export type CastTodoResult = {
  /** voiced, non-narrator roles with no actor yet, heaviest first */
  freeRoles: BudgetCharacterRow[];
  /** how many more such roles exist past `freeRoles` */
  freeMore: number;
  /** unacknowledged actor crossings, in the order `/character-map` returned them */
  crossings: CastTodoCrossing[];
  /** how many more unacknowledged crossings exist past `crossings` */
  crossingsMore: number;
};

/**
 * The hub's cast card is a to-do list, not a table: roles nobody cast yet and
 * crossings nobody signed off on. Both slices are cheap — a book has at most a
 * few hundred roles — so this only filters, sorts and slices; a book with
 * neither is the caller's cue to show "по касту всё назначено".
 */
export function castTodo(voicedRows: BudgetCharacterRow[], intersections: RoleIntersection[], options: CastTodoOptions = {}): CastTodoResult {
  const roleLimit = options.roles ?? 5;
  const crossingLimit = options.crossings ?? 3;
  const free = voicedRows
    .filter((row) => !row.is_narrator && !String(row.actor_name || "").trim())
    .sort((left, right) => right.lines_count - left.lines_count || left.name.localeCompare(right.name, "ru"));
  const unacknowledged = intersections.filter((item) => !item.acknowledged);
  return {
    freeRoles: free.slice(0, roleLimit),
    freeMore: Math.max(0, free.length - roleLimit),
    crossings: unacknowledged.slice(0, crossingLimit).map(({ actor, role_a, role_b, chapter }) => ({ actor, role_a, role_b, chapter })),
    crossingsMore: Math.max(0, unacknowledged.length - crossingLimit),
  };
}
