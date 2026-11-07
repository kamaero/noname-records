/**
 * The book's stress queue: words without a decision, and homographs.
 *
 * The author asked for «слова, которых нет в словарях», and that turned out not to be
 * the filter he wanted. The base dictionary holds words whose stress is *not* obvious,
 * so «даже» and «сказал» are missing from it too: on «Карте» 29 784 of 30 304 words
 * are «unknown», which sorts nothing. What separates «Бимькмолепус» from «даже» is the
 * capital letter in the middle of a sentence — 2 136 words — and that is what «Имена и
 * выдумки» shows.
 *
 * The other half of his complaint was the cap: the list was cut at the 200 most
 * frequent, and an invented name is by definition rare, so his own words were never on
 * screen at all. There is a search box and a «показать ещё» now, and a word he waves
 * off leaves the queue for good — that is what makes thirty thousand shrink as he works.
 */
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { editorKeys, skipStressWord, useStressQueue } from "./editorApi";
import { Sheet } from "./Sheet";
import { HomographReview } from "./HomographReview";
import type { StressQueueItem, StressQueueScope } from "./types";

type StressQueueProps = {
  bookId: string;
  onClose: () => void;
  onPick: (item: StressQueueItem) => void;
};

/** «Спорные» is a third view of the same queue, not a screen of its own. */
type Tab = StressQueueScope | "disputed";

const PAGE = 200;

function fold(value: string): string {
  return (value || "").toLowerCase().replace(/ё/g, "е").trim();
}

function ChipList({
  items,
  empty,
  onPick,
  onSkip,
}: {
  items: StressQueueItem[];
  empty: string;
  onPick: (item: StressQueueItem) => void;
  onSkip?: (word: string) => void;
}) {
  if (items.length === 0) return <p className="v2r-sheet-empty">{empty}</p>;
  return (
    <div className="v2r-chips">
      {items.map((item) => (
        <span key={`${item.word}:${item.sample_segment_id}`} className="v2r-chip-pair">
          <button
            type="button"
            className="v2r-chip-btn"
            onClick={() => onPick(item)}
            title={`Chapter ${item.sample_chapter_index} — open example`}
          >
            <span className="v2r-chip-word">{item.word}</span>
            <span className="v2r-chip-n num">×{item.count}</span>
          </button>
          {onSkip ? (
            <button
              type="button"
              className="v2r-chip-skip"
              onClick={() => onSkip(item.word)}
              title="No stress mark needed; remove word from queue"
              aria-label={`Remove «${item.word}» from queue`}
            >
              ✕
            </button>
          ) : null}
        </span>
      ))}
    </div>
  );
}

export function StressQueue({ bookId, onClose, onPick }: StressQueueProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [scope, setScope] = useState<Tab>("names");
  const [limit, setLimit] = useState(PAGE);
  const [query, setQuery] = useState("");

  const queue = useStressQueue(bookId, true, scope === "disputed" ? "names" : scope, limit);
  const data = queue.data;

  const skip = useMutation({
    mutationFn: (word: string) => skipStressWord(bookId, word),
    onSuccess: (_result, word) => {
      pushToast({ tone: "success", title: `«${word}» — no stress mark needed`, detail: "Word removed from this book's queue." });
      void queryClient.invalidateQueries({ queryKey: editorKeys.queue(bookId) });
    },
    onError: (error) => pushToast({ tone: "error", title: "Not removed", detail: describeApiError(error, "") }),
  });

  const shown = useMemo(() => {
    const needle = fold(query);
    const rows = data?.unresolved ?? [];
    return needle ? rows.filter((item) => fold(item.word).includes(needle)) : rows;
  }, [data, query]);

  const counts = data?.counts;
  const subtitle = counts
    ? `marked ${counts.marked.toLocaleString("en-US")} · unresolved ${counts.unresolved_words.toLocaleString("en-US")} words` +
      (counts.skipped_words ? ` · deferred ${counts.skipped_words}` : "")
    : queue.isLoading
      ? "Loading…"
      : null;

  const tab = (value: Tab, label: string, n?: number) => (
    <button
      type="button"
      className={scope === value ? "v2r-seg-btn is-on" : "v2r-seg-btn"}
      onClick={() => {
        setScope(value);
        setLimit(PAGE);
      }}
      aria-pressed={scope === value}
    >
      {label}
      {typeof n === "number" ? <span className="v2r-legend-count num">{n.toLocaleString("en-US")}</span> : null}
    </button>
  );

  return (
    <Sheet title="Word stress queue" subtitle={subtitle} onClose={onClose}>
      {queue.isError ? <p className="v2r-sheet-error">{describeApiError(queue.error, "Could not load queue.")}</p> : null}

      {/* the tabs stand outside the data guard: they are how you leave a tab whose
          own query has not answered yet */}
      <div className="v2r-seg v2r-queue-tabs" role="group" aria-label="Show">
        {tab("names", "Author terms", counts?.author_words ?? counts?.name_words)}
        {tab("common", counts && counts.common_words === undefined ? "All words" : "Common Russian", counts?.common_words ?? counts?.unresolved_words)}
        {tab("disputed", "Ambiguous", counts?.homograph_words)}
      </div>

      {scope === "disputed" ? (
        <HomographReview
          bookId={bookId}
          onOpenPlace={(place) =>
            onPick({ word: place.word, count: 1, sample_segment_id: place.segment_id, sample_chapter_index: place.chapter_index })
          }
        />
      ) : data ? (
        <>
          <p className="v2r-sheet-note">
            {scope === "names"
              ? "Names, locations and invented words: capitalized terms not found in the dictionary."
              : counts?.common_words === undefined
                ? "All words whose stress was not resolved by dictionaries or context."
                : "Common Russian words whose stress is not yet resolved by dictionary or context."}
          </p>

          <label className="v2r-search v2r-search--dock">
            <input
              className="v2r-search-input"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find a word…"
              aria-label="Search word stress queue"
            />
          </label>

          <section className="v2r-sheet-section">
            <ChipList
              items={shown}
              empty={query ? "Nothing found." : "Queue is empty."}
              onPick={onPick}
              onSkip={(word) => skip.mutate(word)}
            />
            {data.has_more.unresolved && !query ? (
              <button type="button" className="btn btn-sm v2r-btn v2r-queue-more" onClick={() => setLimit((value) => value + PAGE)}>
                Show more {PAGE}
              </button>
            ) : null}
          </section>

        </>
      ) : queue.isLoading ? (
        <p className="v2r-sheet-note">Loading queue…</p>
      ) : null}
    </Sheet>
  );
}
