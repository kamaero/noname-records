import { Link, Navigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { SkeletonPanel } from "../components/Skeleton";
import type { BooksResponse } from "../types";
import type { ReaderBookChapters } from "./types";
import "./ScriptReader.css";

/** `/reader` — pick a book, or jump to the first v2-marked chapter of `?book_id=`. */
export function ReaderEntryPage() {
  const [searchParams] = useSearchParams();
  const bookId = searchParams.get("book_id") || "";

  const chaptersQuery = useQuery({
    queryKey: ["v2", "book-chapters", bookId],
    queryFn: () => apiGet<ReaderBookChapters>(`/api/v2/books/${encodeURIComponent(bookId)}/chapters`),
    enabled: Boolean(bookId),
  });
  const booksQuery = useQuery({
    queryKey: ["books"],
    queryFn: () => apiGet<BooksResponse>("/api/books"),
    enabled: !bookId,
  });

  if (bookId) {
    if (chaptersQuery.isLoading) return <SkeletonPanel label="Ищу размеченные главы…" lines={3} />;
    if (chaptersQuery.isError || !chaptersQuery.data) {
      return (
        <div className="panel panel-pad v2r-empty">
          <p>{describeApiError(chaptersQuery.error, "Не удалось загрузить главы книги.")}</p>
          <Link className="btn btn-sm" to="/reader">
            К списку книг
          </Link>
        </div>
      );
    }
    const first = chaptersQuery.data.chapters.find((chapter) => chapter.has_v2);
    if (first) return <Navigate to={`/reader/${encodeURIComponent(first.id)}`} replace />;
    return (
      <div className="v2r v2r--font-m">
        <div className="v2r-empty">
          <Icon name="doc" />
          <p>
            В книге «{chaptersQuery.data.book.title}» пока нет глав, размеченных в v2.
          </p>
          <Link className="btn btn-sm" to="/reader">
            Выбрать другую книгу
          </Link>
        </div>
      </div>
    );
  }

  if (booksQuery.isLoading) return <SkeletonPanel label="Загружаю книги…" lines={4} />;
  const books = booksQuery.data?.items ?? [];
  return (
    <div className="v2r v2r--font-m">
      <div className="v2r-books">
        <h1 className="v2r-books-title">Подготовка</h1>
        <p className="muted">Сверка сценария, ударения и подготовка книги к записи.</p>
        {books.length === 0 ? <p className="faint">Книг пока нет.</p> : null}
        <ul className="v2r-books-list">
          {books.map((book) => (
            <li key={book.id}>
              <Link className="v2r-book" to={`/reader?book_id=${encodeURIComponent(book.id)}`}>
                <strong>{book.display_title || book.title}</strong>
                <span className="faint num">{book.chapter_count} гл.</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
