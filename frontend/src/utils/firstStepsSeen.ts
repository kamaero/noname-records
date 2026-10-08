/**
 * «Результат открыт» — шаг 6 «Первых шагов». Сервер сам решает, пример ли это книга,
 * поэтому звать можно с любой книги. Ошибка молчит: это отметка, а не работа, и диктору
 * (403) незачем видеть ошибку. Одна отметка на книгу за сессию — не дёргать сервер на
 * каждом переходе по главам.
 */
import { apiPostJson } from "../api/client";

const sent = new Set<string>();

export function markFirstStepsSeen(bookId: string): void {
  if (!bookId || sent.has(bookId)) return;
  sent.add(bookId);
  apiPostJson("/api/first-steps/seen", { book_id: bookId }).catch(() => undefined);
}
