/**
 * Turning one segment's text + annotations into React nodes.
 *
 * The heavy lifting is in pure helpers (`splitRuns`, `findHits`, `findWords`,
 * `sliceWithMarks`, `groupByWord`) that work on offsets only, so they can be
 * reasoned about — and unit-tested once the frontend has a runner — without
 * React. `renderSegment` just wraps their output in elements.
 */
import { Fragment, type CSSProperties, type ReactNode } from "react";
import { NARRATOR, UNSURE, type ReaderRemarkCandidate, type ReaderSpan, type ReaderStress, type RoleStyle } from "./types";

/** A stretch of text that belongs to one span (or to nobody when `span` is null). */
export type Run = { start: number; end: number; span: ReaderSpan | null };

/** A piece of a run: plain text, a stressed vowel, and/or part of a search hit. */
export type Part = { start: number; end: number; text: string; vowel: boolean; hit: boolean };

/** [start, end) of one Cyrillic word inside the segment text. */
export type WordRange = { start: number; end: number };

/** Consecutive parts of one run that belong to the same word (or to no word). */
export type Piece = { word: WordRange | null; parts: Part[] };

/**
 * Consecutive pieces of one run that fall inside the same remark candidate (or
 * inside none). `remark` is the candidate's own index in the `remarks` array
 * passed to `sliceWithMarks`, not just a boolean — two candidates can sit back to
 * back with no gap between them (the operator decides which one, see the remark
 * picker brief), and a boolean would merge them into one clickable stretch.
 */
export type RemarkGroup = { remark: number; pieces: Piece[] };

export type MarkedRun = { run: Run; groups: RemarkGroup[] };

const clamp = (value: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, value));

/** Spans sorted and clamped to the text, with unattributed gaps filled by `span: null`. */
export function splitRuns(textLength: number, spans: ReaderSpan[]): Run[] {
  const sorted = [...spans]
    .map((span) => ({ ...span, start: clamp(span.start, 0, textLength), end: clamp(span.end, 0, textLength) }))
    .filter((span) => span.end > span.start)
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const runs: Run[] = [];
  let cursor = 0;
  for (const span of sorted) {
    const start = Math.max(span.start, cursor);
    if (span.end <= start) continue; // swallowed by an earlier overlapping span
    if (start > cursor) runs.push({ start: cursor, end: start, span: null });
    runs.push({ start, end: span.end, span });
    cursor = span.end;
  }
  if (cursor < textLength) runs.push({ start: cursor, end: textLength, span: null });
  return runs;
}

/** Non-overlapping [start, end) ranges where `query` occurs in `text`, case-insensitive. */
export function findHits(text: string, query: string): Array<[number, number]> {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const haystack = text.toLowerCase();
  if (haystack.length !== text.length) return []; // case folding changed length; offsets would drift
  const hits: Array<[number, number]> = [];
  let from = 0;
  for (;;) {
    const at = haystack.indexOf(needle, from);
    if (at < 0) break;
    hits.push([at, at + needle.length]);
    from = at + needle.length;
  }
  return hits;
}

const COMBINING_ACUTE = "́";
/** A Cyrillic word, hyphenated compounds included («кто-то»); a stray U+0301 in the text stays inside the word. */
const WORD_RE = /[А-Яа-яЁё́]+(?:-[А-Яа-яЁё́]+)*/g;

/**
 * [start, end) of every Cyrillic word with at least `minLetters` letters, in text
 * order. Offsets are UTF-16 (same as `ReaderStress.start`), so they can be used
 * directly with `text.slice`.
 */
export function findWords(text: string, minLetters = 2): WordRange[] {
  const out: WordRange[] = [];
  const re = new RegExp(WORD_RE.source, "g");
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const letters = match[0].replace(/[́-]/g, "").length;
    if (letters >= minLetters) out.push({ start: match.index, end: match.index + match[0].length });
  }
  return out;
}

/** Lower-case, stress-free key used to compare a word from the queue with words in the text. */
export function wordKey(word: string): string {
  return word.replace(/́/g, "").toLowerCase();
}

/** The first word in `text` equal to `word` (ignoring case and stress marks), or null. */
export function findWordOccurrence(text: string, word: string): WordRange | null {
  const key = wordKey(word.trim());
  if (!key) return null;
  for (const range of findWords(text, 1)) {
    if (wordKey(text.slice(range.start, range.end)) === key) return range;
  }
  return null;
}

/** The stress mark that covers the word at [start, end), if any (bounds may differ on hyphenated compounds). */
export function stressForWord(stress: ReaderStress[], start: number, end: number): ReaderStress | null {
  for (const mark of stress) {
    if (mark.start < end && mark.end > start) return mark;
  }
  return null;
}

/** Absolute offsets of stressed vowels that actually fall inside their word and the text. */
export function stressPositions(textLength: number, stress: ReaderStress[]): Set<number> {
  const positions = new Set<number>();
  for (const mark of stress) {
    const at = mark.start + mark.vowel;
    if (at >= mark.start && at < mark.end && at >= 0 && at < textLength) positions.add(at);
  }
  return positions;
}

/**
 * Group a run's parts by the word they fall in. Because `sliceWithMarks` cuts at
 * every word boundary, each part is either wholly inside one word or wholly
 * outside every word, so a single forward pointer over `words` suffices.
 */
export function groupByWord(parts: Part[], words: WordRange[]): Piece[] {
  const pieces: Piece[] = [];
  let wi = 0;
  for (const part of parts) {
    while (wi < words.length && words[wi].end <= part.start) wi += 1;
    const word = wi < words.length && words[wi].start <= part.start && part.end <= words[wi].end ? words[wi] : null;
    const last = pieces[pieces.length - 1];
    if (last && last.word === word) last.parts.push(part);
    else pieces.push({ word, parts: [part] });
  }
  return pieces;
}

/**
 * Split a run's parts into `RemarkGroup`s by which remark candidate they fall
 * in. Parts never straddle a remark boundary (`sliceWithMarks` cuts at every
 * candidate's start/end below), so each part belongs wholly to one candidate or
 * to none — a single forward pointer over `remarks` suffices, same trick as
 * `groupByWord`.
 */
function groupByRemark(parts: Part[], remarks: ReaderRemarkCandidate[]): Array<{ remark: number; parts: Part[] }> {
  const groups: Array<{ remark: number; parts: Part[] }> = [];
  let ri = 0;
  for (const part of parts) {
    while (ri < remarks.length && remarks[ri].end <= part.start) ri += 1;
    const index = ri < remarks.length && remarks[ri].start <= part.start && part.end <= remarks[ri].end ? ri : -1;
    const last = groups[groups.length - 1];
    if (last && last.remark === index) last.parts.push(part);
    else groups.push({ remark: index, parts: [part] });
  }
  return groups;
}

/**
 * Cut the text into runs (by span) and each run into parts (by stressed vowel,
 * search hit, remark candidate and — when `words` are given — word boundary),
 * group by remark candidate first (so a click target never straddles two of
 * them) and each candidate's own parts by word second, so that rendering is a
 * flat map over small typed pieces. With `words = []` every run is a single
 * word-less piece: exactly the pre-editor rendering. With `remarks = []` every
 * run is a single ungrouped `RemarkGroup`: exactly today's rendering, before
 * this feature existed.
 */
export function sliceWithMarks(
  text: string,
  spans: ReaderSpan[],
  stress: ReaderStress[],
  query = "",
  words: WordRange[] = [],
  remarks: ReaderRemarkCandidate[] = [],
): MarkedRun[] {
  const runs = splitRuns(text.length, spans);
  const vowels = stressPositions(text.length, stress);
  const hits = findHits(text, query);
  return runs.map((run) => {
    const cuts = new Set<number>([run.start, run.end]);
    for (const at of vowels) {
      if (at >= run.start && at < run.end) {
        cuts.add(at);
        cuts.add(at + 1);
      }
    }
    for (const [hs, he] of hits) {
      if (he > run.start && hs < run.end) {
        cuts.add(clamp(hs, run.start, run.end));
        cuts.add(clamp(he, run.start, run.end));
      }
    }
    for (const word of words) {
      if (word.end > run.start && word.start < run.end) {
        cuts.add(clamp(word.start, run.start, run.end));
        cuts.add(clamp(word.end, run.start, run.end));
      }
    }
    for (const remark of remarks) {
      if (remark.end > run.start && remark.start < run.end) {
        cuts.add(clamp(remark.start, run.start, run.end));
        cuts.add(clamp(remark.end, run.start, run.end));
      }
    }
    const bounds = [...cuts].sort((a, b) => a - b);
    const parts: Part[] = [];
    for (let i = 0; i + 1 < bounds.length; i += 1) {
      const start = bounds[i];
      const end = bounds[i + 1];
      if (end <= start) continue;
      parts.push({
        start,
        end,
        text: text.slice(start, end),
        vowel: end - start === 1 && vowels.has(start),
        hit: hits.some(([hs, he]) => start >= hs && end <= he),
      });
    }
    const groups: RemarkGroup[] = groupByRemark(parts, remarks).map((group) => ({
      remark: group.remark,
      pieces: groupByWord(group.parts, words),
    }));
    return { run, groups };
  });
}

export type RenderOptions = {
  showStress: boolean;
  query: string;
  styleOf: (speaker: string) => RoleStyle | undefined;
  /** editor mode: wrap every word in a clickable `.v2r-word` */
  words?: boolean;
  /** the word whose stress constructor is open, to highlight it */
  activeWord?: WordRange | null;
  /** editor mode: role chips become buttons (`.v2r-chip--btn`) that open the role picker */
  roles?: boolean;
  /** the span whose role picker is open, to highlight its chip */
  activeSpan?: WordRange | null;
  /**
   * Remark candidates to underline as clickable (author/operator only — see the
   * remark picker brief). Absent or empty means no cutting, no wrapping: a dictor's
   * screen renders exactly as it did before this feature existed.
   */
  remarkCandidates?: ReaderRemarkCandidate[];
};

function renderPart(part: Part, key: number, showStress: boolean): ReactNode {
  let node: ReactNode = part.text;
  if (part.vowel && showStress) {
    node = (
      <u className="v2r-stress" key={key}>
        {part.text + COMBINING_ACUTE}
      </u>
    );
  }
  if (part.hit) {
    node = (
      <mark className="v2r-hit" key={key}>
        {node}
      </mark>
    );
  }
  return node;
}

function renderPiece(piece: Piece, key: number, opts: RenderOptions): ReactNode {
  const children = piece.parts.map((part, j) => renderPart(part, j, opts.showStress));
  if (!piece.word) return <Fragment key={key}>{children}</Fragment>;
  const active = Boolean(opts.activeWord && opts.activeWord.start === piece.word.start && opts.activeWord.end === piece.word.end);
  return (
    <span
      key={key}
      className={active ? "v2r-word is-active" : "v2r-word"}
      data-start={piece.word.start}
      data-end={piece.word.end}
    >
      {children}
    </span>
  );
}

/**
 * A remark group's pieces, wrapped in a clickable dashed-underline span when it is
 * an actual candidate (`remark.remark >= 0`). Stress marks and search hits inside
 * (rendered by `renderPiece`/`renderPart`) are untouched — this wraps a level
 * above word-grouping, not inside it.
 */
function renderRemarkGroup(group: RemarkGroup, key: number, opts: RenderOptions, candidate: ReaderRemarkCandidate | null): ReactNode {
  const children = group.pieces.map((piece, j) => renderPiece(piece, j, opts));
  if (!candidate) return <Fragment key={key}>{children}</Fragment>;
  return (
    <span
      key={key}
      className="v2r-remark"
      data-start={candidate.start}
      data-end={candidate.end}
      title="Отдать эти слова Рассказчику"
    >
      {children}
    </span>
  );
}

function roleVars(style: RoleStyle | undefined): CSSProperties {
  // Cast colours are data, not design tokens — they are the one thing passed inline.
  return {
    "--role-color": style?.color || "transparent",
    "--role-text": style?.text_color || "inherit",
    "--role-weight": style?.weight || "600",
    "--role-style": style?.font_style || "normal",
  } as CSSProperties;
}

/** The segment's text as React nodes: role spans with chips, stress marks, search hits, (editor) clickable words. */
export function renderSegment(
  text: string,
  spans: ReaderSpan[],
  stress: ReaderStress[],
  opts: RenderOptions,
): ReactNode[] {
  const words = opts.words ? findWords(text) : [];
  const remarks = opts.remarkCandidates ?? [];
  return sliceWithMarks(text, spans, stress, opts.query, words, remarks).map(({ run, groups }, index) => {
    const children = groups.map((group, j) =>
      renderRemarkGroup(group, j, opts, group.remark >= 0 ? remarks[group.remark] : null),
    );
    const speaker = run.span?.speaker || "";
    if (!run.span || speaker === NARRATOR) {
      return <span key={index} className="v2r-narr">{children}</span>;
    }
    const unsure = speaker === UNSURE;
    const confidence = Math.round((run.span.confidence || 0) * 100);
    const label = `[${unsure ? "?" : speaker}]`;
    const activeChip = Boolean(opts.activeSpan && opts.activeSpan.start === run.start && opts.activeSpan.end === run.end);
    const chip = opts.roles ? (
      <button
        type="button"
        className={activeChip ? "v2r-chip v2r-chip--btn is-active" : "v2r-chip v2r-chip--btn"}
        data-start={run.start}
        data-end={run.end}
        title="Переназначить роль"
        aria-label={`Переназначить роль: ${unsure ? "не определено" : speaker}`}
      >
        {label}
      </button>
    ) : (
      <span className="v2r-chip">{label}</span>
    );
    return (
      <span
        key={index}
        className={unsure ? "v2r-span v2r-span--unsure" : "v2r-span"}
        style={roleVars(opts.styleOf(speaker))}
        title={`${unsure ? "Не определено" : speaker} · ${confidence}%${run.span.source ? ` · ${run.span.source}` : ""}`}
      >
        {chip}
        {children}
      </span>
    );
  });
}
