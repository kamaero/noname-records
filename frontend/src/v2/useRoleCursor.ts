/**
 * Walking an actor's own lines with the arrow keys.
 *
 * In «моя роль» the reader is a list of the actor's replicas with context around
 * them, and the way to work through it is to go to the next one — not to hunt for
 * the next tinted paragraph with the scrollbar. Up and Down step between them, the
 * current one is outlined, and everything else about the page stays as it is.
 *
 * The keys are only taken while a role is selected and nothing is being typed into:
 * a search field, a stress constructor and the role picker all use the arrows
 * themselves, and a reader who is not filtering wants the page to scroll.
 */
import { useCallback, useEffect, useState } from "react";
import { segmentDomId } from "./Paragraph";
import type { ReaderSegment } from "./types";

const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Indexes of the segments that belong to the selected roles, in reading order. */
export function mineIndexes(segments: ReaderSegment[], selected: ReadonlySet<string>): number[] {
  if (selected.size === 0) return [];
  const out: number[] = [];
  segments.forEach((segment, index) => {
    if (segment.spans.some((span) => selected.has(span.speaker))) out.push(index);
  });
  return out;
}

/** The step an arrow key makes: `null` when the key is not ours to take. */
export function stepFor(key: string): -1 | 1 | null {
  if (key === "ArrowDown") return 1;
  if (key === "ArrowUp") return -1;
  return null;
}

/**
 * Where the cursor lands: the next line after `from`, wrapping at the ends so the
 * last line does not swallow the key. `from` is `null` before the first press —
 * Down then starts at the first line, Up at the last.
 */
export function nextCursor(total: number, from: number | null, step: -1 | 1): number {
  if (total <= 0) return -1;
  if (from === null) return step === 1 ? 0 : total - 1;
  return (from + step + total) % total;
}

type RoleCursorArgs = {
  segments: ReaderSegment[];
  selected: ReadonlySet<string>;
  /** something modal is open (stress constructor, role picker, a sheet): keys are theirs */
  busy: boolean;
  /** the chapter, so the cursor starts over when it changes */
  chapterId: string;
};

export function useRoleCursor({ segments, selected, busy, chapterId }: RoleCursorArgs) {
  const [cursor, setCursor] = useState<number | null>(null);
  const rolesKey = [...selected].sort().join("");

  useEffect(() => setCursor(null), [chapterId, rolesKey]);

  const indexes = mineIndexes(segments, selected);
  const total = indexes.length;

  const go = useCallback(
    (step: -1 | 1) => {
      if (total === 0) return;
      setCursor((current) => {
        const next = nextCursor(total, current, step);
        const segment = segments[indexes[next]];
        if (segment) {
          requestAnimationFrame(() => {
            document.getElementById(segmentDomId(segment.id))?.scrollIntoView({ block: "center", behavior: "smooth" });
          });
        }
        return next;
      });
    },
    // `indexes` is derived from these two and rebuilt on every render; depending on it
    // directly would rebuild the callback constantly
    [segments, selected, total], // eslint-disable-line react-hooks/exhaustive-deps
  );

  useEffect(() => {
    if (total === 0 || busy) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (TYPING.has(target.tagName) || target.isContentEditable)) return;
      const step = stepFor(event.key);
      if (step === null) return;
      event.preventDefault();
      go(step);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [total, busy, go]);

  const currentSegmentId = cursor !== null && indexes[cursor] !== undefined ? segments[indexes[cursor]]?.id ?? "" : "";
  return { currentSegmentId, position: cursor === null ? 0 : cursor + 1, total, go };
}
