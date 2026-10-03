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

const KIND_WORD: Record<SoundMarker["kind"], string> = { scene: "Сцена", transition: "Переход", sound: "Звук" };

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
        title: merge ? `Склеено: «${pair.a.name}»` : "Оставлены разными",
        detail: merge ? `Сцены «${pair.b.name}» теперь в «${pair.a.name}».` : "Следующий прогон эту пару не предложит.",
      });
      await refresh();
    },
    onError: async (error) => {
      pushToast({ tone: "error", title: "Решение не сохранилось", detail: describeSoundError(error) });
      await refresh();
    },
  });
  return (
    <li className="snd-pair">
      <div className="snd-pair-names">
        <strong>{pair.a.name}</strong> <span aria-label="или">↔</span> <strong>{pair.b.name}</strong>
      </div>
      {pair.reason ? <p className="snd-pair-reason">{pair.reason}</p> : null}
      <div className="snd-card-actions">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={decide.isPending}
          title={`Одно место: остаётся имя «${pair.a.name}», сцены второго переходят к нему`}
          onClick={() => decide.mutate(true)}
        >
          склеить
        </button>
        <button type="button" className="btn btn-sm v2r-btn" disabled={decide.isPending} onClick={() => decide.mutate(false)}>
          разные
        </button>
        <span className="snd-card-hint">склеить — останется «{pair.a.name}»</span>
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
      pushToast({ tone: "success", title: `Место сохранено: «${draft.name.trim()}»` });
      setEditing(false);
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Место не сохранилось", detail: describeSoundError(error) }),
  });
  const byIndex = new Map(chapters.map((chapter) => [chapter.index, chapter]));

  return (
    <li className="snd-place">
      <div className="snd-place-head">
        <strong className="snd-place-name">{place.name}</strong>
        {!editing ? (
          <button type="button" className="snd-edit" aria-label={`Править место «${place.name}»`} title="Править" onClick={startEdit}>
            ✎
          </button>
        ) : null}
      </div>
      {place.chapters.length > 0 ? (
        <div className="snd-place-chapters" aria-label="Главы, где встречается место">
          {place.chapters.map((index) => {
            const chapter = byIndex.get(index);
            const here = chapter?.id === chapterId;
            return chapter && !here ? (
              <Link
                key={index}
                to={`/reader/${encodeURIComponent(chapter.id)}`}
                className="snd-place-ch"
                title={chapter.title ? `Глава ${index}: ${chapter.title}` : `Глава ${index}`}
                onClick={(event) => {
                  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
                  event.preventDefault();
                  onGoChapter(chapter.id);
                }}
              >
                гл. {index}
              </Link>
            ) : (
              <span key={index} className={here ? "snd-place-ch is-here" : "snd-place-ch"} title={here ? "Эта глава" : undefined}>
                гл. {index}
              </span>
            );
          })}
        </div>
      ) : (
        <span className="snd-card-hint">сейчас ни в одной сцене</span>
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
            <span className="snd-card-hint">запросов фона нет</span>
          )}
        </>
      ) : (
        <div className="snd-card-subform">
          <label className="snd-card-field">
            <span className="snd-card-label">Имя</span>
            <input className="ui-input" value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} disabled={save.isPending} autoFocus />
          </label>
          <label className="snd-card-field">
            <span className="snd-card-label">Описание</span>
            <textarea
              className="ui-input snd-card-text"
              rows={2}
              value={draft.description}
              onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
              disabled={save.isPending}
            />
          </label>
          <label className="snd-card-field">
            <span className="snd-card-label">Фон — запросы</span>
            <input className="ui-input" value={draft.queries} onChange={(e) => setDraft((d) => ({ ...d, queries: e.target.value }))} disabled={save.isPending} />
            <span className="snd-card-hint">До трёх, через запятую, по-английски. Встают во все сцены этого места.</span>
          </label>
          <div className="snd-card-actions">
            <button type="button" className="btn btn-sm btn-primary" disabled={save.isPending || !draft.name.trim()} onClick={() => save.mutate()}>
              {save.isPending ? "Сохраняю…" : "Сохранить"}
            </button>
            <button type="button" className="btn btn-sm btn-ghost" disabled={save.isPending} onClick={() => setEditing(false)}>
              Отмена
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
    ? `${places.length} ${plural(places.length, "место", "места", "мест")}${pairs.length ? ` · на решение ${pairs.length}` : ""}`
    : book.isLoading
      ? "Загружаю…"
      : null;

  return (
    <Sheet title="Места книги" subtitle={subtitle} onClose={onClose}>
      {book.isError ? <p className="v2r-sheet-error">{describeApiError(book.error, "Не удалось загрузить места.")}</p> : null}
      {pairs.length > 0 ? (
        <section className="v2r-sheet-section">
          <h3 className="v2r-sheet-h">
            Одно место или разные? <span className="v2r-legend-count num">{pairs.length}</span>
          </h3>
          <p className="v2r-sheet-note">ИИ подозревает, что это одно и то же место под разными именами. Решаете вы.</p>
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
            Потерялись в этой главе <span className="v2r-legend-count num">{lost.length}</span>
          </h3>
          <p className="v2r-sheet-note">Текст главы поменялся, и эти маркеры не нашли свой абзац — откройте и поставьте руками.</p>
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
            Все места <span className="v2r-legend-count num">{places.length}</span>
          </h3>
          {places.length === 0 ? (
            <p className="v2r-sheet-empty">Мест пока нет — они появятся после звуковой разметки.</p>
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
