import { useQuery } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../../../api/client";
import { LinkButton } from "../../../ui";
import type { BookRecordingResponse } from "../../../types";
import { hubKeys } from "./hubApi";

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
  const recordingTo = `/recording?book_id=${encodeURIComponent(bookId)}`;

  return (
    <section className="panel hub-card" aria-labelledby="hub-rec-title">
      <header className="hub-card-head">
        <h2 id="hub-rec-title" className="hub-card-title">Запись</h2>
        {data?.total_chapters ? (
          <span className="hub-dim num">
            {data.ready_chapters} из {data.total_chapters}
          </span>
        ) : null}
      </header>

      {query.isLoading ? (
        <div className="hub-skel" aria-busy="true" aria-label="Загрузка записи"><span /><span /></div>
      ) : query.isError ? (
        <p className="ui-note ui-note--error">Не удалось загрузить готовность: {describeApiError(query.error, "сервер не ответил")}.</p>
      ) : live.length ? (
        <>
          <ul className="hub-chapters" aria-label="Главы в записи">
            {shown.map((chapter) => (
              <li key={chapter.chapter_id} className={chapter.ready ? "hub-chapter is-ready" : "hub-chapter"}>
                <span className="hub-chapter-name">
                  <span className="num">{chapter.chapter_index}.</span> {chapter.chapter_title || "Без названия"}
                </span>
                <span className="hub-chapter-count num">
                  {chapter.recorded_roles}/{chapter.total_roles}
                </span>
                {chapter.ready && data?.can_download ? (
                  <a
                    className="hub-chapter-get"
                    href={`/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/archive.zip`}
                    title="Скачать дубли главы одним архивом"
                  >
                    Забрать
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
          {live.length > SHOWN ? (
            <p className="hub-dim hub-text">…и ещё {live.length - SHOWN} в работе</p>
          ) : null}
        </>
      ) : (
        <p className="hub-dim hub-text">Записей пока нет. Дикторы получат сценарий после публикации.</p>
      )}

      <div className="hub-card-actions">
        <LinkButton to={recordingTo} size="sm" variant="secondary">Открыть запись</LinkButton>
      </div>
    </section>
  );
}
