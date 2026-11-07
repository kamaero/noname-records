import { useEffect, useRef, type RefObject } from "react";
import type { ReadingAnchor } from "../components/recording/recordingPosition";
import { alignTo, topAnchor } from "./useSplitView";

export type ReadingPosition = {
  identity: string;
  initial?: ReadingAnchor;
  onChange: (anchor: ReadingAnchor) => void;
};

/** Восстановление — один раз после разметки и фильтра роли; обычная смена главы начинает сверху. */
export function useReadingPosition(
  position: ReadingPosition | undefined,
  chapterId: string,
  article: HTMLElement | null,
  compare: boolean,
  scriptRef: RefObject<HTMLDivElement>,
  sourceRef: RefObject<HTMLDivElement>,
) {
  const visited = useRef(new Set<string>());
  const latest = useRef(position);
  latest.current = position;
  const identity = position?.identity;
  useEffect(() => {
    if (!identity || !article) return;
    let frame = 0;
    let observer: MutationObserver | undefined;
    let active = false;
    const initial = visited.current.has(identity) ? undefined : latest.current?.initial;
    const save = (event: Event) => {
      const target = event.target;
      if (compare && target !== scriptRef.current && target !== sourceRef.current) return;
      if (!compare && target !== document && target !== window) return;
      if (!active || latest.current?.identity !== identity) return;
      observer?.disconnect();
      const pane = compare ? (target === sourceRef.current ? sourceRef.current : scriptRef.current) : null;
      let anchor: ReadingAnchor | undefined;
      if (pane) {
        const top = topAnchor(pane);
        if (top) anchor = { segmentId: top.id, fraction: top.fraction };
      } else {
        for (const block of article.querySelectorAll<HTMLElement>("[data-seg]")) {
          const box = block.getBoundingClientRect();
          if (box.bottom <= 4) continue;
          anchor = { segmentId: block.dataset.seg || "", fraction: Math.min(1, Math.max(0, -box.top / (box.height || 1))) };
          break;
        }
      }
      // Сохраняем само событие, а не DOM в cleanup: при уходе там уже может быть другая глава.
      if (anchor?.segmentId) latest.current.onChange(anchor);
    };
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        if (initial) {
          const block = Array.from(article.querySelectorAll<HTMLElement>("[data-seg]"))
            .find((item) => item.dataset.seg === initial.segmentId);
          if (block) {
            if (compare && scriptRef.current) {
              const anchor = { id: initial.segmentId, fraction: initial.fraction, offset: -initial.fraction };
              alignTo(scriptRef.current, anchor, 0);
              const alignSource = () => {
                const source = sourceRef.current;
                if (!source?.querySelector(`[data-seg="${CSS.escape(initial.segmentId)}"]`)) return;
                alignTo(source, anchor, 0);
                observer?.disconnect();
              };
              if (sourceRef.current) {
                observer = new MutationObserver(alignSource);
                observer.observe(sourceRef.current, { childList: true, subtree: true });
                alignSource();
              }
            } else {
              const box = block.getBoundingClientRect();
              window.scrollBy(0, box.top + box.height * initial.fraction);
            }
          }
        }
        // Отменённый кадр ещё не восстановил чтение. Иначе смена режима между
        // кадрами навсегда теряет попытку; отсутствующий после рендера абзац — уже результат.
        visited.current.add(identity);
        // Событие от scrollTo(0, 0) при смене главы не затирает сохранённый абзац.
        frame = requestAnimationFrame(() => {
          active = true;
          document.addEventListener("scroll", save, true);
        });
      });
    });
    return () => {
      active = false;
      cancelAnimationFrame(frame);
      observer?.disconnect();
      document.removeEventListener("scroll", save, true);
    };
  }, [identity, chapterId, article, compare, scriptRef, sourceRef]);
}
