/**
 * Лист «Места книги»: спорные склейки мест (решает человек), потерявшиеся маркеры этой
 * главы и все места книги — имя, описание, запросы фона правятся на месте, главы, где
 * место встречается, ведут в Читалку.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { Sheet } from "./Sheet";
import { markerCaption } from "./SoundCard";
import { decideSoundPair, describeSoundError, editSoundPlace, soundKeys, splitQueries, useSoundBook } from "./soundApi";
import { plural } from "./useIsMobile";
import type { ReaderChapterRef, SoundMarker, SoundPair, SoundPlace } from "./types";

type SoundPlacesProps = {
  bookId: string;
  chapterId: string;
  /** главы книги — номер главы места превращается в ссылку */
  chapters: readonly ReaderChapterRef[];
  lost: readonly SoundMarker[];
  onOpenMarker: (marker: SoundMarker) => void;
  onGoChapter: (chapterId: string) => void;
  onClose: () => void;
};

const KIND_WORD: Record<SoundMarker["kind"], string> = { scene: "Scene", transition: "Transition", sound: "Sound" };

function useRefresh(bookId: string, chapterId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: soundKeys.book(bookId) }),
      queryClient.invalidateQueries({ queryKey: soundKeys.chapter(chapterId) }),
    ]);
}

function PairRow({ pair, bookId, chapterId }: { pair: SoundPair; bookId: string; chapterId: string }) {
  const { pushToast } = useToast();
  const refresh = useRefresh(bookId, chapterId);
  const decide = useMutation({
    mutationFn: (merge: boolean) => decideSoundPair(pair.id, merge),
    onSuccess: async (_, merge) => {
      pushToast({
        tone: "success",
        title: merge ? `Merged: «${pair.a.name}»` : "Kept separate",
        detail: merge ? `Scenes from «${pair.b.name}» now belong to «${pair.a.name}».` : "Future runs will not suggest this pair again.",
      });
      await refresh();
    },
    onError: async (error) => {
      pushToast({ tone: "error", title: "Decision not saved", detail: describeSoundError(error) });
      await refresh();
    },
  });
  return (
    <li className="snd-pair">
      <div className="snd-pair-names">
        <strong>{pair.a.name}</strong> <span aria-label="or">↔</span> <strong>{pair.b.name}</strong>
      </div>
      {pair.reason ? <p className="snd-pair-reason">{pair.reason}</p> : null}
      <div className="snd-card-actions">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={decide.isPending}
          title={`Same location: keep name «${pair.a.name}» and move the other location's scenes to it`}
          onClick={() => decide.mutate(true)}
        >
          merge
        </button>
        <button type="button" className="btn btn-sm v2r-btn" disabled={decide.isPending} onClick={() => decide.mutate(false)}>
          separate
        </button>
        <span className="snd-card-hint">merge; keep «{pair.a.name}»</span>
      </div>
    </li>
  );
}

function PlaceRow({
  place, bookId, chapterId, chapters, onGoChapter,
}: {
  place: SoundPlace; bookId: string; chapterId: string; chapters: readonly ReaderChapterRef[]; onGoChapter: (id: string) => void;
}) {
  const { pushToast } = useToast();
  const refresh = useRefresh(bookId, chapterId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ name: "", description: "", queries: "" });
  const startEdit = () => {
    setDraft({ name: place.name, description: place.description, queries: place.ambience_queries.join(", ") });
    setEditing(true);
  };
  const save = useMutation({
    mutationFn: () =>
      editSoundPlace(place.id, { name: draft.name.trim(), description: draft.description.trim(), ambience_queries: splitQueries(draft.queries) }),
    onSuccess: async () => {
      pushToast({ tone: "success", title: `Location saved: «${draft.name.trim()}»` });
      setEditing(false);
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Location not saved", detail: describeSoundError(error) }),
  });
  const byIndex = new Map(chapters.map((chapter) => [chapter.index, chapter]));

  return (
    <li className="snd-place">
      <div className="snd-place-head">
        <strong className="snd-place-name">{place.name}</strong>
        {!editing ? (
          <button type="button" className="snd-edit" aria-label={`Edit location «${place.name}»`} title="Edit" onClick={startEdit}>
            ✎
          </button>
        ) : null}
      </div>
      {place.chapters.length > 0 ? (
        <div className="snd-place-chapters" aria-label="Chapters featuring this location">
          {place.chapters.map((index) => {
            const chapter = byIndex.get(index);
            const here = chapter?.id === chapterId;
            return chapter && !here ? (
              <Link
                key={index}
                to={`/reader/${encodeURIComponent(chapter.id)}`}
                className="snd-place-ch"
                title={chapter.title ? `Chapter ${index}: ${chapter.title}` : `Chapter ${index}`}
                onClick={(event) => {
                  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
                  event.preventDefault();
                  onGoChapter(chapter.id);
                }}
              >
                ch. {index}
              </Link>
            ) : (
              <span key={index} className={here ? "snd-place-ch is-here" : "snd-place-ch"} title={here ? "This chapter" : undefined}>
                ch. {index}
              </span>
            );
          })}
        </div>
      ) : (
        <span className="snd-card-hint">not currently used in any scene</span>
      )}
      {!editing ? (
        <>
          {place.description ? <p className="snd-place-desc">{place.description}</p> : null}
          {place.ambience_queries.length > 0 ? (
            <div className="snd-queries">
              {place.ambience_queries.map((query) => (
                <span key={query} className="snd-query snd-query--static">
                  🔎 {query}
                </span>
              ))}
            </div>
          ) : (
            <span className="snd-card-hint">no ambience queries</span>
          )}
        </>
      ) : (
        <div className="snd-card-subform">
          <label className="snd-card-field">
            <span className="snd-card-label">Name</span>
            <input className="ui-input" value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} disabled={save.isPending} autoFocus />
          </label>
          <label className="snd-card-field">
            <span className="snd-card-label">Description</span>
            <textarea
              className="ui-input snd-card-text"
              rows={2}
              value={draft.description}
              onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
              disabled={save.isPending}
            />
          </label>
          <label className="snd-card-field">
            <span className="snd-card-label">Ambience queries</span>
            <input className="ui-input" value={draft.queries} onChange={(e) => setDraft((d) => ({ ...d, queries: e.target.value }))} disabled={save.isPending} />
            <span className="snd-card-hint">Up to three comma-separated queries in English. Used in all scenes at this location.</span>
          </label>
          <div className="snd-card-actions">
            <button type="button" className="btn btn-sm btn-primary" disabled={save.isPending || !draft.name.trim()} onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn btn-sm btn-ghost" disabled={save.isPending} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

export function SoundPlaces({ bookId, chapterId, chapters, lost, onOpenMarker, onGoChapter, onClose }: SoundPlacesProps) {
  const book = useSoundBook(bookId, true);
  const data = book.data;
  const places = data?.places ?? [];
  const pairs = data?.pairs ?? [];
  const subtitle = data
    ? `${places.length} ${plural(places.length, "location", "locations", "locations")}${pairs.length ? ` · to review ${pairs.length}` : ""}`
    : book.isLoading
      ? "Loading…"
      : null;

  return (
    <Sheet title="Book locations" subtitle={subtitle} onClose={onClose}>
      {book.isError ? <p className="v2r-sheet-error">{describeApiError(book.error, "Could not load locations.")}</p> : null}
      {pairs.length > 0 ? (
        <section className="v2r-sheet-section">
          <h3 className="v2r-sheet-h">
            Same location or different? <span className="v2r-legend-count num">{pairs.length}</span>
          </h3>
          <p className="v2r-sheet-note">AI suggests these names may refer to the same location. You decide.</p>
          <ul className="snd-list">
            {pairs.map((pair) => (
              <PairRow key={pair.id} pair={pair} bookId={bookId} chapterId={chapterId} />
            ))}
          </ul>
        </section>
      ) : null}
      {lost.length > 0 ? (
        <section className="v2r-sheet-section">
          <h3 className="v2r-sheet-h">
            Unplaced in this chapter <span className="v2r-legend-count num">{lost.length}</span>
          </h3>
          <p className="v2r-sheet-note">The chapter text changed and these markers lost their paragraphs. Open them to place them manually.</p>
          <ul className="snd-lost-list">
            {lost.map((item) => (
              <li key={item.id} className="snd-lost">
                <button type="button" className="snd-lost-open" onClick={() => onOpenMarker(item)}>
                  <span className="snd-lost-kind">{KIND_WORD[item.kind]}</span> {markerCaption(item, places.find((p) => p.id === item.place_id))}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {data ? (
        <section className="v2r-sheet-section">
          <h3 className="v2r-sheet-h">
            All locations <span className="v2r-legend-count num">{places.length}</span>
          </h3>
          {places.length === 0 ? (
            <p className="v2r-sheet-empty">No locations yet. They will appear after sound annotation.</p>
          ) : (
            <ul className="snd-list">
              {places.map((place) => (
                <PlaceRow key={place.id} place={place} bookId={bookId} chapterId={chapterId} chapters={chapters} onGoChapter={onGoChapter} />
              ))}
            </ul>
          )}
        </section>
      ) : null}
    </Sheet>
  );
}
