/**
 * Which paragraphs to show, dim, or fold — a pure function of the segments, the
 * roles the actor picked, the context radius and the search query.
 */
import type { ReaderSegment } from "./types";

export type PlanItem =
  | { type: "segment"; index: number; mine: boolean; dim: boolean }
  | { type: "gap"; from: number; to: number; count: number };

export function planVisibility(
  segments: ReaderSegment[],
  selected: ReadonlySet<string>,
  radius: number,
  query: string,
): PlanItem[] {
  const needle = query.trim().toLowerCase();
  const roleMode = selected.size > 0;
  const n = segments.length;

  const matches = segments.map((segment) => !needle || segment.text.toLowerCase().includes(needle));
  const mine = segments.map((segment) => roleMode && segment.spans.some((span) => selected.has(span.speaker)));

  const near = new Array<boolean>(n).fill(!roleMode);
  if (roleMode) {
    for (let i = 0; i < n; i += 1) {
      if (!mine[i]) continue;
      for (let j = Math.max(0, i - radius); j <= Math.min(n - 1, i + radius); j += 1) near[j] = true;
    }
  }

  const items: PlanItem[] = [];
  let gapStart = -1;
  const closeGap = (end: number) => {
    if (gapStart >= 0) {
      items.push({ type: "gap", from: gapStart, to: end, count: end - gapStart });
      gapStart = -1;
    }
  };
  for (let i = 0; i < n; i += 1) {
    const heading = segments[i].kind === "heading";
    const visible = matches[i] && (near[i] || (heading && !needle));
    if (!visible) {
      if (gapStart < 0) gapStart = i;
      continue;
    }
    closeGap(i);
    items.push({ type: "segment", index: i, mine: mine[i], dim: roleMode && !mine[i] });
  }
  closeGap(n);
  return items;
}
