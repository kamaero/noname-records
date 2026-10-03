/**
 * Лор: чтение шпаргалки по миру книги.
 *
 * Оглавление и карточки приходят одним запросом и живут долго — энциклопедия не
 * меняется между прогонами. Тело статьи запрашивается по клику: «Магия» весит 131 КБ,
 * и списком такие тела не возят.
 */
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../api/client";

export type LoreArticleRef = {
  id: string;
  topic: string;
  title: string;
  chars: number;
  /** сколько карт и схем в статье */
  images: number;
};

export type LoreEntity = {
  id: string;
  name: string;
  aliases: string[];
  topic: string;
  description: string;
  /** авторская иллюстрация, привязанная человеком; "" — портрета нет */
  portrait?: string;
};

export type LoreResponse = {
  /** null — у книги нет автора или у автора нет лора; по этому кнопка и прячется */
  author: { id: string; name: string } | null;
  articles: LoreArticleRef[];
  entities: LoreEntity[];
};

export type LoreArticle = {
  id: string;
  topic: string;
  title: string;
  body: string;
  images: string[];
};

export type LoreHit = { id: string; title: string; snippet: string; count: number };

export const loreKeys = {
  book: (bookId: string) => ["lore", bookId] as const,
  article: (id: string) => ["lore-article", id] as const,
  search: (bookId: string, query: string) => ["lore-search", bookId, query] as const,
};

const book = (bookId: string) => `/api/v2/books/${encodeURIComponent(bookId)}`;

export function useLore(bookId: string, enabled = true) {
  return useQuery({
    queryKey: loreKeys.book(bookId),
    queryFn: () => apiGet<LoreResponse>(`${book(bookId)}/lore`),
    enabled: enabled && Boolean(bookId),
    // энциклопедия не меняется, пока её не перезальют вручную
    staleTime: 60 * 60_000,
    retry: false,
  });
}

export function useLoreArticle(id: string) {
  return useQuery({
    queryKey: loreKeys.article(id),
    queryFn: () => apiGet<LoreArticle>(`/api/v2/lore/articles/${encodeURIComponent(id)}`),
    enabled: Boolean(id),
    staleTime: 60 * 60_000,
  });
}

/** Поиск по телам статей — на сервере: в браузере их нет. */
export function useLoreSearch(bookId: string, query: string) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: loreKeys.search(bookId, trimmed),
    queryFn: () => apiGet<{ query: string; hits: LoreHit[] }>(
      `${book(bookId)}/lore/search?q=${encodeURIComponent(trimmed)}`,
    ),
    enabled: Boolean(bookId) && trimmed.length >= 2,
    staleTime: 5 * 60_000,
  });
}

/** Карточки, совпавшие с запросом: имя, алиас или описание. */
export function matchEntities(entities: readonly LoreEntity[], query: string): LoreEntity[] {
  const needle = query.trim().toLowerCase();
  if (needle.length < 2) return [];
  return entities.filter((item) =>
    item.name.toLowerCase().includes(needle)
    || item.aliases.some((alias) => alias.toLowerCase().includes(needle))
    || item.description.toLowerCase().includes(needle));
}
