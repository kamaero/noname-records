import { Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../api/client";
import { SkeletonPanel } from "../components/Skeleton";
import { EmptyState, PageHeader } from "../ui";
import type { BooksResponse } from "../types";

/** Книга, чью карту автор отметил проверенной последней, — над ней студия работает
    сейчас. Ни одной отмеченной — первая в библиотеке. Даты в ISO UTC сравниваются как строки. */
export function currentCastBook<T extends { id: string; cast_checked_at: string }>(books: T[]): T | undefined {
  const checked = books.filter((book) => book.cast_checked_at);
  if (!checked.length) return books[0];
  return checked.reduce((best, book) => (book.cast_checked_at > best.cast_checked_at ? book : best));
}

/**
 * `/cast` — пункт «Каст» в навбаре. Каст у каждой книги свой, поэтому раздел ведёт
 * в каст конкретной книги — текущей (`currentCastBook`). Другую книгу выбирают
 * селектором рядом с фильтрами каста.
 *
 * Последнюю просмотренную книгу не запоминаем: владелец ждёт от навбара текущую
 * книгу студии, а не ту, в которую заглянул вчера (10.10).
 */
export function CastEntryPage() {
  const booksQuery = useQuery({ queryKey: ["books"], queryFn: () => apiGet<BooksResponse>("/api/books") });

  if (booksQuery.isLoading) return <SkeletonPanel label="Загружаю книги…" lines={3} />;
  if (booksQuery.isError) {
    return (
      <div className="lib">
        <PageHeader title="Каст" />
        <EmptyState text={describeApiError(booksQuery.error, "Не удалось загрузить книги.")} />
      </div>
    );
  }
  const books = booksQuery.data?.items ?? [];
  if (!books.length) {
    return (
      <div className="lib">
        <PageHeader title="Каст" />
        <EmptyState text="Книг пока нет. Каст появится, когда в библиотеке будет книга." />
      </div>
    );
  }
  const target = currentCastBook(books) ?? books[0];
  return <Navigate to={`/books/${encodeURIComponent(target.id)}/cast`} replace />;
}
