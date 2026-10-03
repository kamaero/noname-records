/**
 * Editor-only UI state for the reader: which word the stress constructor is
 * open on, which paragraph the role picker is open on, which side sheet is
 * open, which legend chip is being recoloured, and the "jump to a sample from
 * the queue" flow that may cross chapters.
 */
import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import type { NavigateFunction } from "react-router-dom";
import { selectionInside, type TextRange } from "./attribution";
import { segmentDomId } from "./Paragraph";
import { findWordOccurrence } from "./render";
import type { ConsiliumItem, DisputedItem, ReaderCastEntry, ReaderChapterRef, ReaderSegment, StressQueueItem } from "./types";

export type WordTarget = { segmentId: string; start: number; end: number; word: string };
export type EditorSheet = "queue" | "disputed" | "profile" | "consilium" | "sound" | "places" | null;

/** The paragraph the role picker is open on; `span` is the clicked replica (null from the narration affordance). */
export type RoleTarget = {
  segmentId: string;
  span: TextRange | null;
  /** text selected inside the paragraph when the picker opened */
  selection: TextRange | null;
};

/** A jump asked for from a side sheet: to a word (stress) or to a span (role). */
type PendingJump =
  | { kind: "word"; chapterId: string; segmentId: string; word: string }
  | { kind: "span"; chapterId: string; segmentId: string; span: TextRange }
  | { kind: "finding"; chapterId: string; segmentId: string; findingId: string };

type EditorStateArgs = {
  chapterId: string;
  chapters: ReaderChapterRef[];
  segments: ReaderSegment[];
  navigate: NavigateFunction;
  /** clear role filter / search so the target segment is guaranteed visible */
  onReveal: () => void;
};

const ROLE_TRIGGER = ".v2r-chip--btn, .v2r-role-hint";

export function useEditorState({ chapterId, chapters, segments, navigate, onReveal }: EditorStateArgs) {
  const [target, setTarget] = useState<WordTarget | null>(null);
  const [roleTarget, setRoleTarget] = useState<RoleTarget | null>(null);
  const [sheet, setSheet] = useState<EditorSheet>(null);
  const [editingCast, setEditingCast] = useState<ReaderCastEntry | null>(null);
  const [pending, setPending] = useState<PendingJump | null>(null);
  const [findingId, setFindingId] = useState<string | null>(null);
  // the selection read on mousedown, before the click could collapse it
  const stashedSelection = useRef<TextRange | null>(null);

  const openWordAt = useCallback(
    (segmentId: string, start: number, end: number) => {
      const segment = segments.find((item) => item.id === segmentId);
      if (!segment || !(end > start) || end > segment.text.length) return;
      setFindingId(null);
      setRoleTarget(null);
      setTarget({ segmentId, start, end, word: segment.text.slice(start, end) });
    },
    [segments],
  );

  const closeWord = useCallback(() => setTarget(null), []);

  /** Open the role picker on a paragraph; `span` is the clicked replica, null for the whole paragraph. */
  const openRoleAt = useCallback(
    (segmentId: string, span: TextRange | null, selection: TextRange | null) => {
      const segment = segments.find((item) => item.id === segmentId);
      if (!segment) return;
      setFindingId(null);
      const whole = span !== null && span.start <= 0 && span.end >= segment.text.length;
      setTarget(null);
      setRoleTarget({ segmentId, span: whole ? null : span, selection });
    },
    [segments],
  );

  const closeRole = useCallback(() => setRoleTarget(null), []);

  /**
   * Карточка находки консилиума. Док один на читалку: карточка, выбор роли и конструктор
   * ударений взаимоисключающие — иначе человеку предложены два способа сделать одно.
   */
  const openFinding = useCallback(
    (item: ConsiliumItem) => {
      setSheet(null);
      setPending({ kind: "finding", chapterId: item.chapter_id || chapterId, segmentId: item.segment_id, findingId: item.id });
      if (item.chapter_id && item.chapter_id !== chapterId) navigate(`/reader/${encodeURIComponent(item.chapter_id)}`);
    },
    [chapterId, navigate],
  );

  const closeFinding = useCallback(() => setFindingId(null), []);

  // `selectionInside` trims by the segment text; the DOM host knows only its id
  const hostText = useCallback(
    (host: HTMLElement): string => segments.find((item) => item.id === host.id.slice("seg-".length))?.text ?? "",
    [segments],
  );

  /**
   * Mousedown on a role chip: read the text selection now and keep the browser
   * from collapsing it, so «только выделенное» is still on offer after the click.
   */
  const onArticleMouseDown = useCallback(
    (event: MouseEvent<HTMLElement>) => {
      const el = (event.target as HTMLElement | null)?.closest?.(ROLE_TRIGGER) as HTMLElement | null;
      if (!el) return;
      const host = el.closest("[id^='seg-']") as HTMLElement | null;
      stashedSelection.current = host ? selectionInside(host, hostText(host)) : null;
      event.preventDefault();
    },
    [hostText],
  );

  /** One delegated handler on the article: role chips open the picker, `.v2r-word` opens the constructor. */
  const onArticleClick = useCallback(
    (event: MouseEvent<HTMLElement>) => {
      const origin = event.target as HTMLElement | null;
      const roleEl = origin?.closest?.(ROLE_TRIGGER) as HTMLElement | null;
      if (roleEl) {
        const host = roleEl.closest("[id^='seg-']") as HTMLElement | null;
        if (!host) return;
        const segmentId = host.id.slice("seg-".length);
        const start = Number(roleEl.dataset.start);
        const end = Number(roleEl.dataset.end);
        const span = roleEl.classList.contains("v2r-role-hint") || !Number.isFinite(start) || !Number.isFinite(end) ? null : { start, end };
        const selection = stashedSelection.current ?? selectionInside(host, hostText(host));
        stashedSelection.current = null;
        openRoleAt(segmentId, span, selection);
        return;
      }
      const el = origin?.closest?.(".v2r-word") as HTMLElement | null;
      if (!el) return;
      const host = el.closest("[id^='seg-']") as HTMLElement | null;
      if (!host) return;
      const start = Number(el.dataset.start);
      const end = Number(el.dataset.end);
      if (!Number.isFinite(start) || !Number.isFinite(end)) return;
      openWordAt(host.id.slice("seg-".length), start, end);
    },
    [openWordAt, openRoleAt, hostText],
  );

  const toggleSheet = useCallback((next: Exclude<EditorSheet, null>) => {
    setSheet((current) => (current === next ? null : next));
  }, []);

  /** From the queue: go to the sample's chapter, scroll to the segment, open the constructor on the word. */
  const jumpTo = useCallback(
    (item: StressQueueItem) => {
      const chapter = chapters.find((ref) => ref.index === item.sample_chapter_index);
      const targetChapterId = chapter?.id || chapterId;
      setSheet(null);
      setPending({ kind: "word", chapterId: targetChapterId, segmentId: item.sample_segment_id, word: item.word });
      if (targetChapterId !== chapterId) navigate(`/reader/${encodeURIComponent(targetChapterId)}`);
    },
    [chapters, chapterId, navigate],
  );

  /** From the disputed queue: go to the line and open the role picker on it. */
  const jumpToSpan = useCallback(
    (item: DisputedItem) => {
      setSheet(null);
      setPending({
        kind: "span",
        chapterId: item.chapter_id || chapterId,
        segmentId: item.segment_id,
        span: { start: item.span.start, end: item.span.end },
      });
      if (item.chapter_id && item.chapter_id !== chapterId) navigate(`/reader/${encodeURIComponent(item.chapter_id)}`);
    },
    [chapterId, navigate],
  );

  useEffect(() => {
    if (!pending || pending.chapterId !== chapterId || segments.length === 0) return;
    setPending(null);
    const segment = segments.find((item) => item.id === pending.segmentId);
    if (!segment) return;
    onReveal();
    if (pending.kind === "word") {
      setFindingId(null);
      const range = findWordOccurrence(segment.text, pending.word);
      if (range) {
        setRoleTarget(null);
        setTarget({ segmentId: segment.id, start: range.start, end: range.end, word: segment.text.slice(range.start, range.end) });
      }
    } else if (pending.kind === "span") {
      // the span may cover the whole paragraph, which the picker calls «весь абзац»
      const whole = pending.span.start <= 0 && pending.span.end >= segment.text.length;
      setTarget(null);
      setFindingId(null);
      setRoleTarget({ segmentId: segment.id, span: whole ? null : pending.span, selection: null });
    } else {
      setTarget(null);
      setRoleTarget(null);
      setFindingId(pending.findingId);
    }
    // two frames: let React commit the un-filtered plan before we look the element up
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        document.getElementById(segmentDomId(segment.id))?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    });
  }, [pending, chapterId, segments, onReveal]);

  // a chapter change invalidates the targets' offsets
  useEffect(() => {
    setTarget(null);
    setRoleTarget(null);
    setEditingCast(null);
  }, [chapterId]);

  return {
    target,
    openWordAt,
    closeWord,
    roleTarget,
    openRoleAt,
    closeRole,
    onArticleClick,
    onArticleMouseDown,
    sheet,
    setSheet,
    toggleSheet,
    editingCast,
    setEditingCast,
    jumpTo,
    jumpToSpan,
    findingId,
    openFinding,
    closeFinding,
  };
}
