/**
 * The two-pane «оригинал ↔ сценарий» view: where the divider sits, whether the
 * panes scroll together, and the arithmetic that keeps them in step.
 *
 * v1 mirrored `scrollTop` between the panes, which drifted apart as soon as the
 * two columns wrapped their lines differently. Here both panes tag their blocks
 * with the segment id (`data-seg`), so the follower is aligned on the same
 * paragraph the leader shows at its top — the panes stay together no matter how
 * differently they wrap, and how much the script side adds around a line.
 */
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

const STORAGE_KEY = "v2reader.split";
export const MIN_RATIO = 0.25;
export const MAX_RATIO = 0.75;
/** a block counts as "the one at the top" once its bottom edge is this far below the pane's top */
const TOP_SLACK = 4;

export const clampRatio = (value: number): number =>
  Number.isFinite(value) ? Math.min(MAX_RATIO, Math.max(MIN_RATIO, value)) : 0.5;

export type SplitPrefs = { ratio: number; linked: boolean };

const DEFAULTS: SplitPrefs = { ratio: 0.5, linked: true };

function readPrefs(): SplitPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<SplitPrefs>;
    return {
      ratio: clampRatio(Number(parsed.ratio)),
      linked: typeof parsed.linked === "boolean" ? parsed.linked : DEFAULTS.linked,
    };
  } catch {
    return DEFAULTS;
  }
}

export type Anchor = {
  id: string;
  /** where the block starts, relative to the pane's top edge (negative once it runs off the top) */
  offset: number;
  /** how much of the block is already above the fold, 0…1 */
  fraction: number;
};

/** The segment shown at the top of `pane`, and how far into it the reader is. */
export function topAnchor(pane: HTMLElement): Anchor | null {
  const paneTop = pane.getBoundingClientRect().top;
  for (const block of pane.querySelectorAll<HTMLElement>("[data-seg]")) {
    const box = block.getBoundingClientRect();
    if (box.bottom <= paneTop + TOP_SLACK) continue;
    const height = box.height || 1;
    return {
      id: block.dataset.seg || "",
      offset: box.top - paneTop,
      fraction: Math.min(1, Math.max(0, (paneTop - box.top) / height)),
    };
  }
  return null;
}

/**
 * Scroll `follower` so the reader is at the same place in the same paragraph.
 *
 * A paragraph is rarely the same height in both panes — the script side carries
 * role chips and stress marks and wraps differently — so matching the top edges
 * only works while the paragraph starts below the fold. Once it runs off the top,
 * the panes are matched on *how much of it* is already read; otherwise the shorter
 * copy slides a whole paragraph out of step.
 *
 * Falls back to the same relative position when the follower does not show that
 * segment at all (role mode folds paragraphs away, gaps have no segment).
 */
export function alignTo(follower: HTMLElement, anchor: Anchor | null, ratio: number): void {
  if (anchor?.id) {
    const target = follower.querySelector<HTMLElement>(`[data-seg="${CSS.escape(anchor.id)}"]`);
    if (target) {
      const box = target.getBoundingClientRect();
      const wanted = anchor.offset >= 0 ? anchor.offset : -anchor.fraction * (box.height || 1);
      follower.scrollTop += box.top - follower.getBoundingClientRect().top - wanted;
      return;
    }
  }
  const span = follower.scrollHeight - follower.clientHeight;
  if (span > 0) follower.scrollTop = span * Math.min(1, Math.max(0, ratio));
}

/**
 * `layoutKey` names everything above the panes that changes their height — the
 * chapter, the loaded payload, the font, the legend and rail toggles. A resize
 * observer on the page would either miss the first paint or chase its own tail
 * (the panes are part of the page it would be observing), so the caller says.
 */
export function useSplitView(active: boolean, layoutKey: string) {
  const [prefs, setPrefs] = useState<SplitPrefs>(readPrefs);
  const hostRef = useRef<HTMLDivElement | null>(null);
  /** height of the panes, and of the whole two-column body they sit in */
  const [{ height, bodyHeight }, setHeights] = useState({ height: 0, bodyHeight: 0 });
  const sourceRef = useRef<HTMLDivElement | null>(null);
  const scriptRef = useRef<HTMLDivElement | null>(null);
  const drivenBy = useRef<"source" | "script" | null>(null);
  const dragRef = useRef<{ startX: number; startRatio: number; width: number } | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch {
      /* storage unavailable — the split lives for this session only */
    }
  }, [prefs]);

  /**
   * The two panes fill what is left of the window below them. Measured rather than
   * guessed with a CSS constant: the cast legend above them folds open and shut and
   * grows with the chapter's cast, so any fixed offset is wrong for someone.
   */
  useEffect(() => {
    if (!active) return undefined;
    const measure = () => {
      const host = hostRef.current;
      if (!host) return;
      const top = host.getBoundingClientRect().top;
      const bodyTop = host.closest(".v2r-body")?.getBoundingClientRect().top ?? top;
      const next = {
        height: Math.max(320, Math.round(window.innerHeight - top - 16)),
        // the chapter rail is capped to the same bottom edge, so nothing hangs below
        bodyHeight: Math.max(320, Math.round(window.innerHeight - bodyTop - 16)),
      };
      setHeights((current) =>
        current.height === next.height && current.bodyHeight === next.bodyHeight ? current : next,
      );
    };
    // Now, after the next paint, and again once late content (the palette-conflict
    // line, a slow font) has settled. The observer on the column that holds the panes
    // catches anything above them growing afterwards; it converges in one tick,
    // because our own height changes the column's height but never the panes' top.
    measure();
    const frame = window.requestAnimationFrame(measure);
    const timer = window.setTimeout(measure, 200);
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    const column = hostRef.current?.parentElement;
    if (column) observer.observe(column);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      window.removeEventListener("resize", measure);
      observer.disconnect();
    };
  }, [active, layoutKey]);

  const setRatio = useCallback((value: number) => setPrefs((current) => ({ ...current, ratio: clampRatio(value) })), []);
  const toggleLinked = useCallback(() => setPrefs((current) => ({ ...current, linked: !current.linked })), []);

  const onSplitterDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const width = event.currentTarget.parentElement?.getBoundingClientRect().width || 0;
      if (!width) return;
      dragRef.current = { startX: event.clientX, startRatio: prefs.ratio, width };
      event.currentTarget.setPointerCapture?.(event.pointerId);
      document.body.classList.add("is-resizing");
    },
    [prefs.ratio],
  );

  useEffect(() => {
    if (!active) return undefined;
    function onMove(event: PointerEvent) {
      const drag = dragRef.current;
      if (!drag) return;
      setRatio(drag.startRatio + (event.clientX - drag.startX) / drag.width);
    }
    function onUp() {
      if (!dragRef.current) return;
      dragRef.current = null;
      document.body.classList.remove("is-resizing");
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("is-resizing");
    };
  }, [active, setRatio]);

  /** One pane scrolled: pull the other one to the same paragraph. */
  const onPaneScroll = useCallback(
    (side: "source" | "script") => {
      if (!prefs.linked) return;
      const leader = (side === "source" ? sourceRef : scriptRef).current;
      const follower = (side === "source" ? scriptRef : sourceRef).current;
      if (!leader || !follower) return;
      // The follower's own scroll event must not bounce back and fight the leader.
      if (drivenBy.current && drivenBy.current !== side) return;
      drivenBy.current = side;
      const span = leader.scrollHeight - leader.clientHeight;
      alignTo(follower, topAnchor(leader), span > 0 ? leader.scrollTop / span : 0);
      window.requestAnimationFrame(() => {
        if (drivenBy.current === side) drivenBy.current = null;
      });
    },
    [prefs.linked],
  );

  /** Re-align once after linking, a chapter change or a width change. */
  const realign = useCallback(() => {
    if (!prefs.linked) return;
    const leader = scriptRef.current;
    const follower = sourceRef.current;
    if (!leader || !follower) return;
    const span = leader.scrollHeight - leader.clientHeight;
    alignTo(follower, topAnchor(leader), span > 0 ? leader.scrollTop / span : 0);
  }, [prefs.linked]);

  return { ...prefs, hostRef, height, bodyHeight, sourceRef, scriptRef, setRatio, toggleLinked, onSplitterDown, onPaneScroll, realign };
}
