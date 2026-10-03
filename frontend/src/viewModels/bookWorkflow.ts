import type { BookListItem } from "../types";

export function normalizeBookStatus(book: BookListItem | null | undefined) {
  return book?.progress?.effective_status || book?.status || "unknown";
}
