/**
 * «Результат открыт» — шаг 6 «Первых шагов». Сервер сам решает, пример ли это книга,
 * поэтому звать можно с любой книги. Ошибка молчит: это отметка, а не работа.
 *
 * Книга запоминается только после ответа сервера: успех и отказ в правах (диктору 403 —
 * незачем спрашивать на каждой главе) больше не отправляются, а сбой сети или 5xx — да,
 * при следующем открытии; иначе один сбой оставлял шаг неотмеченным до перезагрузки.
 * Пока запрос в пути, второй не уходит.
 */
import { ApiError, apiPostJson } from "../api/client";

const settled = new Set<string>();
const inFlight = new Set<string>();

export function markFirstStepsSeen(bookId: string): void {
  if (!bookId || settled.has(bookId) || inFlight.has(bookId)) return;
  inFlight.add(bookId);
  apiPostJson("/api/first-steps/seen", { book_id: bookId })
    .then(() => settled.add(bookId))
    .catch((error) => {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) settled.add(bookId);
    })
    .finally(() => inFlight.delete(bookId));
}
