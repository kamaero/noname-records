export type RoleScriptBase = "/reader/role" | "/recording/role";

function queryParams(search: string): URLSearchParams {
  return new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
}

export function roleScriptHref(
  bookId: string,
  role: string,
  base: RoleScriptBase = "/reader/role",
  search = "",
): string {
  const params = queryParams(search);
  params.set("role", role);
  return `${base}/${encodeURIComponent(bookId)}?${params.toString()}`;
}

export function recordingHref(bookId: string, search = "", chapterId?: string): string {
  const params = queryParams(search);
  params.set("book_id", bookId);
  if (chapterId) params.set("chapter_id", chapterId);
  const query = params.toString();
  return `/recording${query ? `?${query}` : ""}`;
}
