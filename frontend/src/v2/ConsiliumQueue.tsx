/**
 * Находки консилиума по всей книге: разделы по роду, внутри — сначала то, что арбитр
 * доказал. Клик ведёт к абзацу и открывает там карточку решения: решают в тексте, где
 * видны соседние реплики, а не в списке.
 */
import { useState } from "react";
import { describeApiError } from "../api/client";
import { CONSILIUM_KINDS, KIND_TITLES, decisionLine, findingTag, sortFindings } from "./consilium";
import { useConsilium } from "./editorApi";
import { Sheet } from "./Sheet";
import { plural } from "./useIsMobile";
import type { ConsiliumItem } from "./types";

type ConsiliumQueueProps = {
  bookId: string;
  currentChapterId: string;
  onClose: () => void;
  onPick: (item: ConsiliumItem) => void;
};

function Row({ item, here, onPick }: { item: ConsiliumItem; here: boolean; onPick: (item: ConsiliumItem) => void }) {
  const decided = item.status !== "new";
  return (
    <button type="button" className={decided ? "v2r-dispute v2r-cons-row is-decided" : "v2r-dispute v2r-cons-row"} onClick={() => onPick(item)}>
      <span className="v2r-dispute-head">
        <span className={item.evidence_proven && item.arbiter_verdict === "change" ? "v2r-dispute-tag v2r-cons-tag--proven" : "v2r-dispute-tag"}>
          {decided ? decisionLine(item) : findingTag(item)}
        </span>
        <span className="v2r-dispute-where num">
          гл. {item.chapter_index}
          {here ? " · здесь" : ""}
        </span>
      </span>
      <span className="v2r-dispute-text">{item.excerpt || "…"}</span>
    </button>
  );
}

export function ConsiliumQueue({ bookId, currentChapterId, onClose, onPick }: ConsiliumQueueProps) {
  const query = useConsilium(bookId, true);
  const [showDecided, setShowDecided] = useState(false);
  const data = query.data;
  const open = data?.counts.new ?? 0;
  const subtitle = data
    ? `${open} ${plural(open, "нерешённая находка", "нерешённые находки", "нерешённых находок")} из ${data.items.length}`
    : query.isLoading
      ? "Загружаю…"
      : null;
  const items = sortFindings(
    (data?.items ?? []).filter((item) => (showDecided ? item.status !== "gone" : item.status === "new")),
  );

  return (
    <Sheet title="Консилиум ИИ" subtitle={subtitle} onClose={onClose}>
      {query.isError ? <p className="v2r-sheet-error">{describeApiError(query.error, "Не удалось загрузить находки.")}</p> : null}
      {data ? (
        <>
          <p className="v2r-sheet-note">
            Два независимых чтеца прочли книгу вслепую и не согласились со сценарием. Это кандидаты, а не приговоры:
            клик открывает место в тексте, решение — ваше.
          </p>
          <label className="v2r-cons-toggle">
            <input type="checkbox" checked={showDecided} onChange={(event) => setShowDecided(event.target.checked)} />
            показать решённые ({(data.counts.accepted ?? 0) + (data.counts.dismissed ?? 0)})
          </label>
          {CONSILIUM_KINDS.map((kind) => {
            const rows = items.filter((item) => item.kind === kind);
            return (
              <section key={kind} className="v2r-sheet-section">
                <h3 className="v2r-sheet-h">
                  {KIND_TITLES[kind]} <span className="v2r-legend-count num">{data.counts.new_by_kind[kind] ?? 0}</span>
                </h3>
                {rows.length === 0 ? (
                  <p className="v2r-sheet-empty">Нерешённых нет.</p>
                ) : (
                  <div className="v2r-dispute-list">
                    {rows.map((item) => (
                      <Row key={item.id} item={item} here={item.chapter_id === currentChapterId} onPick={onPick} />
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </>
      ) : null}
    </Sheet>
  );
}
