import { Fragment, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiGet } from "../../api/client";
import { Button } from "../../ui";
import type { CastRow } from "../../viewModels/booksCast";
import { splitScriptNote } from "../../v2/scriptNote";
import type { ReaderBookChapters } from "../../v2/types";

type Song = { title: string; href: string; performer: string };

export function useCastSongs(bookId: string, rows: CastRow[]) {
  // Одна роль может быть показана во многих главах, а ссылка на песню у неё одна.
  const refs = useMemo(() => Array.from(new Map(rows.map((row) => [row.character_id, row])).values()).flatMap((row) =>
    splitScriptNote(row.note || "").links.map(({ label, href }) => ({
      title: label.replace(/\s+\((?:гл\.|ch\.)\s*\d+\)$/iu, ""),
      href,
      performer: row.name,
    })),
  ), [rows]);
  const { data, isError, refetch } = useQuery({
    queryKey: ["v2", "book-chapters", bookId],
    queryFn: () => apiGet<ReaderBookChapters>("/api/v2/books/" + encodeURIComponent(bookId) + "/chapters"),
    enabled: Boolean(bookId && refs.length),
  });
  // Глава берётся из цели ссылки. Номер в подписи может устареть после правки.
  const chapterById = new Map(data?.chapters.map(({ id, index }) => [id, index]));
  const byChapter = new Map<number, Song[]>();
  for (const song of refs) {
    const chapter = chapterById.get(song.href.slice("/reader/".length, song.href.indexOf("?")));
    if (chapter === undefined) continue;
    const existing = byChapter.get(chapter) ?? [];
    if (!existing.some((item) => item.href === song.href && item.performer === song.performer)) existing.push(song);
    byChapter.set(chapter, existing);
  }
  return {
    groups: [...byChapter].sort(([left], [right]) => left - right),
    retry: refs.length > 0 && !data && isError ? () => { void refetch(); } : undefined,
  };
}

export function CastChapterHeading({ chapter, columnCount }: { chapter: number; columnCount: number }) {
  return (
    <tr className="ui-table-section-row">
      <th scope="row" colSpan={columnCount}>{"Chapter " + chapter}</th>
    </tr>
  );
}

export function CastSongGroup({ chapter, songs, bookId, columnCount, heading = true }: {
  chapter: number;
  songs: Song[];
  bookId: string;
  columnCount: number;
  heading?: boolean;
}) {
  const book = new URLSearchParams({ book_id: bookId }).toString();
  return (
    <Fragment>
      {heading ? <CastChapterHeading chapter={chapter} columnCount={columnCount} /> : null}
      {songs.map(({ title, performer, href }) => (
        <tr className="cast-song-row" key={href + performer}>
          <td colSpan={columnCount}>
            <span>{"Song "}</span>
            <Link to={href + "&" + book}>«{title}»</Link>
            {title !== performer ? <span className="cast-muted"> · {"Performed by " + performer}</span> : null}
          </td>
        </tr>
      ))}
    </Fragment>
  );
}

export function CastSongLoadError({ retry, columnCount }: { retry: () => void; columnCount: number }) {
  return (
    <tr className="cast-song-row">
      <td colSpan={columnCount}>
        <span role="alert">Could not load the song chapters.</span>{" "}
        <Button size="sm" variant="secondary" onClick={retry}>Retry</Button>
      </td>
    </tr>
  );
}
