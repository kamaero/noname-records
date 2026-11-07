import { useQuery } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../../../api/client";
import { LinkButton } from "../../../ui";
import type { BookRecordingResponse, MeResponse } from "../../../types";
import { hubKeys, useRecordingBook } from "./hubApi";

/**
 * Готовность записи по главам — и ссылка на архив готовой главы.
 *
 * Раньше карточка показывала покрытие из распознавания речи. Распознавание не
 * запускалось ни разу, поэтому она всегда показывала ноль и означала «мы не знаем»,
 * выглядя как «ничего не записано». Счёт по файлам знает меньше — «у роли есть дубль»,
 * а не «все реплики произнесены», — зато знает это на самом деле.
 */
const SHOWN = 6;

export function RecordingCard({ bookId }: { bookId: string }) {
  const query = useQuery({
    queryKey: hubKeys.recording(bookId),
    queryFn: () => apiGet<BookRecordingResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/recording`),
    enabled: Boolean(bookId),
    retry: false,
    staleTime: 30_000,
  });
  const data = query.data;
  const chapters = data?.chapters ?? [];
  // Начатые главы — то, что сейчас в работе; законченные среди них видно по кнопке.
  const live = chapters.filter((chapter) => chapter.recorded_roles > 0);
  const shown = live.slice(0, SHOWN);
  // Переключатель — решение владельца: от него зависит, чьи дубли примет бот.
  const meQuery = useQuery({ queryKey: ["me"], queryFn: () => apiGet<MeResponse>("/api/me"), staleTime: 60_000 });
  const me = meQuery.data;
  const isAdmin = Boolean(me && (me.roles.includes("admin")));
  const recordingBook = useRecordingBook();
  const recordingTo = `/recording?book_id=${encodeURIComponent(bookId)}`;

  return (
    <section className="panel hub-card" aria-labelledby="hub-rec-title">
      <header className="hub-card-head">
        <h2 id="hub-rec-title" className="hub-card-title">In progress</h2>
        {data?.total_chapters ? (
          <span className="hub-dim num">
            {data.ready_chapters} of {data.total_chapters}
          </span>
        ) : null}
      </header>

      {query.isLoading ? (
        <div className="hub-skel" aria-busy="true" aria-label="Loading recordings"><span /><span /></div>
      ) : query.isError ? (
        <p className="ui-note ui-note--error">Could not load recording progress: {describeApiError(query.error, "the server did not respond")}.</p>
      ) : live.length ? (
        <>
          <ul className="hub-chapters" aria-label="Chapters being recorded">
            {shown.map((chapter) => (
              <li key={chapter.chapter_id} className={chapter.ready ? "hub-chapter is-ready" : "hub-chapter"}>
                <span className="hub-chapter-name">
                  <span className="num">{chapter.chapter_index}.</span> {chapter.chapter_title || "Untitled"}
                </span>
                <span className="hub-chapter-count num">
                  {chapter.recorded_roles}/{chapter.total_roles}
                </span>
                {chapter.ready && data?.can_download ? (
                  <a
                    className="hub-chapter-get"
                    href={`/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/archive.zip`}
                    title="Download chapter takes in one archive"
                  >
                    Download
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
          {live.length > SHOWN ? (
            <p className="hub-dim hub-text">…and {live.length - SHOWN} in progress</p>
          ) : null}
        </>
      ) : (
        <p className="hub-dim hub-text">No recordings yet. Voice actors will receive the script after publication.</p>
      )}

      {isAdmin ? (
        <div className="hub-recording-now">
          <label className="ui-check">
            <input
              type="checkbox"
              checked={recordingBook.activeBookId === bookId}
              disabled={recordingBook.loading || recordingBook.switching}
              onChange={(event) => recordingBook.set(event.target.checked ? bookId : null)}
            />
            <span>Currently recording</span>
          </label>
          <p className="hub-dim hub-text">The bot accepts takes for this book</p>
        </div>
      ) : null}

      <div className="hub-card-actions">
        <LinkButton to={recordingTo} size="sm" variant="secondary">Open script</LinkButton>
      </div>
    </section>
  );
}
