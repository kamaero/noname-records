/**
 * Pure helpers behind «Переназначить роль»: which spans the reader sends for a
 * segment (whole paragraph, one replica, or a text selection inside it), and how
 * a DOM selection inside a rendered paragraph maps back to text offsets. Nothing
 * here touches React or the network.
 */
import { splitRuns } from "./render";
import { NARRATOR, type ReaderSegment, type ReaderSpan, type ReassignSpan } from "./types";

/** [start, end) inside the segment text. */
export type TextRange = { start: number; end: number };

const COMBINING_ACUTE = "́";

/** Segments whose effective spans carry an operator decision. */
export function hasOperatorSpans(spans: ReaderSpan[]): boolean {
  return spans.some((span) => String(span.source || "").toLowerCase() === "operator");
}

/** Spans of the segment as a full cover of the text, narrator filling the gaps. */
function coverage(segment: ReaderSegment): Array<TextRange & { speaker: string }> {
  return splitRuns(segment.text.length, segment.spans).map((run) => ({
    start: run.start,
    end: run.end,
    speaker: run.span?.speaker || NARRATOR,
  }));
}

/** Merge touching pieces with one speaker; whitespace-only gaps join their neighbour. */
function mergePieces(text: string, pieces: Array<TextRange & { speaker: string }>): ReassignSpan[] {
  const out: Array<TextRange & { speaker: string }> = [];
  for (const piece of pieces) {
    if (piece.end <= piece.start) continue;
    const last = out[out.length - 1];
    const blank = !text.slice(piece.start, piece.end).trim();
    if (last && (last.speaker === piece.speaker || blank) && last.end === piece.start) {
      last.end = piece.end;
      continue;
    }
    if (blank && !last) {
      // leading whitespace: give it to whoever comes next
      out.push({ ...piece, speaker: "" });
      continue;
    }
    if (last && last.speaker === "" && last.end === piece.start) {
      last.end = piece.end;
      last.speaker = piece.speaker;
      continue;
    }
    out.push({ ...piece });
  }
  return out
    .filter((piece) => piece.end > piece.start)
    .map((piece) => ({ start: piece.start, end: piece.end, speaker: piece.speaker || NARRATOR, confidence: 1 }));
}

/** The whole segment to one speaker. */
export function spansForWhole(segment: ReaderSegment, speaker: string): ReassignSpan[] {
  return [{ start: 0, end: segment.text.length, speaker, confidence: 1 }];
}

/**
 * `range` to `speaker`, everything else keeps its current speaker (narrator where
 * nothing was attributed). Used both for «только выделенное» and «эта реплика».
 */
export function spansForRange(segment: ReaderSegment, range: TextRange, speaker: string): ReassignSpan[] {
  const start = Math.max(0, Math.min(range.start, range.end));
  const end = Math.min(segment.text.length, Math.max(range.start, range.end));
  if (end <= start) return spansForWhole(segment, speaker);
  const pieces: Array<TextRange & { speaker: string }> = [];
  for (const piece of coverage(segment)) {
    if (piece.end <= start || piece.start >= end) {
      pieces.push(piece);
      continue;
    }
    if (piece.start < start) pieces.push({ start: piece.start, end: start, speaker: piece.speaker });
    if (piece.end > end) pieces.push({ start: end, end: piece.end, speaker: piece.speaker });
  }
  pieces.push({ start, end, speaker });
  pieces.sort((a, b) => a.start - b.start);
  return mergePieces(segment.text, pieces);
}

/** Trim a range to the non-whitespace it contains; null when nothing is left. */
export function trimRange(text: string, range: TextRange): TextRange | null {
  let { start, end } = range;
  start = Math.max(0, start);
  end = Math.min(text.length, end);
  while (start < end && /\s/.test(text[start])) start += 1;
  while (end > start && /\s/.test(text[end - 1])) end -= 1;
  return end > start ? { start, end } : null;
}

/** The span the chip at `offset` belongs to, or null for narration. */
export function spanAt(segment: ReaderSegment, offset: number): ReaderSpan | null {
  for (const span of segment.spans) {
    if (span.start <= offset && offset < span.end) return span;
  }
  return null;
}

/**
 * Text offset of a DOM position inside a rendered paragraph. The rendering adds
 * two things the text does not have — the `[Роль]` chips and the combining acute
 * after a stressed vowel — so they are skipped while counting.
 */
export function domPointToOffset(root: Element, node: Node, offset: number): number | null {
  const doc = root.ownerDocument || document;
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let position = 0;
  // element positions (node is an element, offset = child index) resolve to the text before that child
  const target: { node: Node; offset: number } = { node, offset };
  if (node.nodeType !== Node.TEXT_NODE) {
    const child = node.childNodes[offset] ?? null;
    if (child === null) {
      // after the last child: count everything inside `node`
      let inside = 0;
      let cursor: Node | null = walker.nextNode();
      let seen = false;
      while (cursor) {
        if (node.contains(cursor)) seen = true;
        else if (seen) break;
        inside += textLength(cursor as Text);
        cursor = walker.nextNode();
      }
      return node === root || seen ? inside : null;
    }
    target.node = child;
    target.offset = 0;
  }
  let current: Node | null = walker.nextNode();
  while (current) {
    const text = current as Text;
    const length = textLength(text);
    if (current === target.node) return position + Math.min(target.offset, length);
    if (target.node.nodeType !== Node.TEXT_NODE && target.node.contains(current)) return position;
    position += length;
    current = walker.nextNode();
  }
  return target.node.nodeType === Node.TEXT_NODE ? null : position;
}

/** Characters of `node` that exist in the segment text. */
function textLength(node: Text): number {
  const parent = node.parentElement;
  if (parent?.closest(".v2r-chip, .v2r-role-hint, .v2r-op, .v2r-cons-mark, .snd-marks")) return 0;
  const raw = node.data.length;
  if (parent?.closest(".v2r-stress") && node.data.endsWith(COMBINING_ACUTE)) return raw - 1;
  return raw;
}

/**
 * The current window selection as a range inside the paragraph element `root`,
 * or null when there is none, it is collapsed, or it crosses the paragraph.
 */
export function selectionInside(root: Element, text: string): TextRange | null {
  const selection = root.ownerDocument?.getSelection?.() ?? window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const start = domPointToOffset(root, range.startContainer, range.startOffset);
  const end = domPointToOffset(root, range.endContainer, range.endOffset);
  if (start === null || end === null) return null;
  return trimRange(text, { start: Math.min(start, end), end: Math.max(start, end) });
}

/** «Дгарнин», «Рассказчик», or a search query, folded for matching. */
export function foldName(value: string): string {
  return value.replace(/́/g, "").trim().toLowerCase().replace(/ё/g, "е");
}
