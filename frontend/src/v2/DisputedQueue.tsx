import { roleLabel } from "./types";
/**
 * Side sheet with the lines the model would not settle: it either named no speaker
 * (`UNSURE`) or named one with a low confidence. Clicking a line goes to it and
 * opens the role picker there, so the queue is worked through in the reader rather
 * than in a separate editor.
 *
 * The two kinds are kept apart on purpose. «Не определил» is the model doing what it
 * was told — abstain instead of guess — and every one of those needs an answer.
 * «Сомневается» is a guess that stands until someone disagrees, so it is a shorter,
 * optional pass.
 */
import { describeApiError } from "../api/client";
import { useDisputed } from "./editorApi";
import { Sheet } from "./Sheet";
import { plural } from "./useIsMobile";
import type { DisputedItem } from "./types";

type DisputedQueueProps = {
  bookId: string;
  currentChapterId: string;
  onClose: () => void;
  onPick: (item: DisputedItem) => void;
};

function Row({ item, here, onPick }: { item: DisputedItem; here: boolean; onPick: (item: DisputedItem) => void }) {
  return (
    <button type="button" className="v2r-dispute" onClick={() => onPick(item)}>
      <span className="v2r-dispute-head">
        <span className={item.kind === "unsure" ? "v2r-dispute-tag v2r-dispute-tag--unsure" : "v2r-dispute-tag"}>
          {item.kind === "unsure" ? "unassigned" : `uncertain · ${roleLabel(item.speaker)}`}
        </span>
        <span className="v2r-dispute-where num">
          ch. {item.chapter_index}
          {here ? " · here" : ""}
        </span>
      </span>
      <span className="v2r-dispute-text">{item.excerpt || "…"}</span>
    </button>
  );
}

export function DisputedQueue({ bookId, currentChapterId, onClose, onPick }: DisputedQueueProps) {
  const query = useDisputed(bookId, true);
  const data = query.data;
  const subtitle = data
    ? `${data.counts.total} ${plural(data.counts.total, "uncertain line", "uncertain lines", "uncertain lines")} in ${data.counts.chapters} ${plural(data.counts.chapters, "chapter", "chapters", "chapters")}`
    : query.isLoading
      ? "Loading…"
      : null;

  const unsure = (data?.items ?? []).filter((item) => item.kind === "unsure");
  const low = (data?.items ?? []).filter((item) => item.kind === "low");

  return (
    <Sheet title="Uncertain lines" subtitle={subtitle} onClose={onClose}>
      {query.isError ? <p className="v2r-sheet-error">{describeApiError(query.error, "Could not load list.")}</p> : null}
      {data ? (
        <>
          <p className="v2r-sheet-note">
            When the text does not identify the speaker, the model leaves the role unassigned. Click a line to review it in context and select a role.
          </p>
          <section className="v2r-sheet-section">
            <h3 className="v2r-sheet-h">
              Unassigned <span className="v2r-legend-count num">{data.counts.unsure}</span>
            </h3>
            {unsure.length === 0 ? (
              <p className="v2r-sheet-empty">No unassigned lines; all roles have been assigned.</p>
            ) : (
              <div className="v2r-dispute-list">
                {unsure.map((item) => (
                  <Row key={`${item.segment_id}:${item.span.start}`} item={item} here={item.chapter_id === currentChapterId} onPick={onPick} />
                ))}
              </div>
            )}
          </section>
          <section className="v2r-sheet-section">
            <h3 className="v2r-sheet-h">
              Uncertain <span className="v2r-legend-count num">{data.counts.low}</span>
            </h3>
            <p className="v2r-sheet-note">
              The role is assigned, but confidence is below {Math.round(data.threshold * 100)}%. Review is optional; this is a precaution rather than a confirmed error.
            </p>
            {low.length === 0 ? (
              <p className="v2r-sheet-empty">No uncertain lines.</p>
            ) : (
              <div className="v2r-dispute-list">
                {low.map((item) => (
                  <Row key={`${item.segment_id}:${item.span.start}`} item={item} here={item.chapter_id === currentChapterId} onPick={onPick} />
                ))}
              </div>
            )}
          </section>
          {data.truncated ? (
            <p className="v2r-sheet-note">
              Showing the first {data.items.length} — more will appear after you resolve these.
            </p>
          ) : null}
        </>
      ) : null}
    </Sheet>
  );
}
