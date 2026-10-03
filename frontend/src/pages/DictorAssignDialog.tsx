import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiGet, apiPostJson, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { useToast } from "../components/ToastProvider";
import { Button, Dialog } from "../ui";
import { plural } from "../v2/useIsMobile";
import type { BookBudgetResponse, BooksResponse, BudgetCharacterRow, SaveBudgetCharacterResponse } from "../types";

/* «Назначить на роль» из карточки диктора: послушал демо — сразу поставил.
   Запрос — тот же, что у ячейки актёра в касте (`POST /api/budget/character/{id}`):
   голос, перенос по циклу, письмо в бот и пересборка назначений делает сервер.
   Рассказчик — не персонаж (он в смете книги), поэтому здесь его нет. */

type CycleBook = { book_title: string };
type AssignResponse = SaveBudgetCharacterResponse & {
  cycle?: { changed: CycleBook[]; kept: (CycleBook & { actor: string })[]; outvoted: CycleBook[] };
};

const LAST_BOOK_KEY = "dictors.assign.book";

function fold(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е");
}

/** Тот же человек: слова без «?», без регистра и порядка («Ольга Ветрова» = «Ветрова Ольга»). */
export function sameActor(a: string, b: string): boolean {
  const norm = (s: string) => fold(s.replace(/\?+\s*$/, "")).split(/\s+/).filter(Boolean).sort().join(" ");
  return Boolean(norm(a)) && norm(a) === norm(b);
}

function readLastBook(): string {
  try {
    return window.localStorage.getItem(LAST_BOOK_KEY) || "";
  } catch {
    return "";
  }
}

function rememberBook(bookId: string) {
  try {
    window.localStorage.setItem(LAST_BOOK_KEY, bookId);
  } catch {
    /* приватное окно — просто не запомним */
  }
}

function minutes(seconds: number): string {
  const m = Math.round((seconds || 0) / 60);
  return m ? `${m} мин` : "";
}

function outcome(name: string, role: string, tentative: boolean, result: AssignResponse): { tone: "success" | "info" | "error"; title: string; detail: string } {
  const vote = result.vote;
  if (vote && !sameActor(vote.actor_name, name)) {
    return {
      tone: "error",
      title: "Голос не перевесил",
      detail: `На «${role}» остаётся ${vote.actor_name || "свободно"}${vote.overridden_by ? ` — решил голос: ${vote.overridden_by}` : ""}.`,
    };
  }
  const parts: string[] = [];
  const also = result.cycle?.changed?.map((b) => b.book_title).filter(Boolean) || [];
  if (also.length) parts.push(`ещё в: ${also.join(", ")}`);
  const kept = result.cycle?.kept || [];
  if (kept.length) parts.push(`где записано, остался прежний: ${kept.map((b) => `${b.book_title} (${b.actor})`).join(", ")}`);
  const approval = result.approval;
  if (approval) {
    if (approval.reason === "sent") parts.push("письмо в бот ушло");
    else if (approval.reason === "no_telegram" || approval.reason === "not_sent") parts.push("бот не дозвался — письмо ушло вам, сообщите сами");
    else if (approval.reason === "ambiguous") parts.push("имени подошло несколько учёток — письмо ушло вам");
  }
  return {
    tone: "success",
    title: tentative ? `Позван на пробу: ${role}` : `Утверждён на роль: ${role}`,
    detail: parts.join(" · "),
  };
}

export function DictorAssignDialog({ name, onClose, onAssigned }: { name: string; onClose: () => void; onAssigned: () => void }) {
  const { pushToast } = useToast();
  const [bookId, setBookId] = useState<string>(readLastBook);
  const [query, setQuery] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [picked, setPicked] = useState<BudgetCharacterRow | null>(null);
  const [busy, setBusy] = useState<"" | "approve" | "audition">("");

  const booksQuery = useQuery({ queryKey: ["books"], queryFn: () => apiGet<BooksResponse>("/api/books") });
  const books = booksQuery.data?.items || [];
  const currentBook = books.find((b) => b.id === bookId) ? bookId : books[0]?.id || "";

  const castQuery = useQuery({
    queryKey: ["budget", currentBook],
    queryFn: () => apiGet<BookBudgetResponse>(`/api/budget/${encodeURIComponent(currentBook)}`),
    enabled: Boolean(currentBook),
  });
  const roles = useMemo(
    () => (castQuery.data?.characters || []).filter((row) => !row.is_narrator),
    [castQuery.data],
  );
  const mine = roles.filter((row) => sameActor(row.actor_name, name));
  const shown = useMemo(() => {
    const needle = fold(query.trim());
    return roles
      .filter((row) => !needle || fold(row.name).includes(needle))
      .filter((row) => !freeOnly || !row.actor_name.trim())
      .sort((a, b) => (b.lines_count || 0) - (a.lines_count || 0));
  }, [roles, query, freeOnly]);

  const assign = async (row: BudgetCharacterRow, tentative: boolean) => {
    const others = mine.filter((r) => r.character_id !== row.character_id);
    if (others.length && !window.confirm(
      `${name} уже на роли «${others.map((r) => r.name).join("», «")}» в этой книге.\n\nОдин актёр — одна роль. Всё равно назначить на «${row.name}»?`,
    )) return;
    const current = row.actor_name.trim();
    if (current && !sameActor(current, name) && !current.endsWith("?") && !window.confirm(
      `На «${row.name}» сейчас утверждён ${current}. Заменить на ${name}?\n\nРешение считается по голосам: если автор голосовал за ${current}, замена не пройдёт.`,
    )) return;
    setBusy(tentative ? "audition" : "approve");
    try {
      const result = await apiPostJson<AssignResponse>(`/api/budget/character/${encodeURIComponent(row.character_id)}`, {
        actor_name: tentative ? `${name}?` : name,
      });
      const toast = outcome(name, row.name, tentative, result);
      pushToast({ ...toast, durationMs: 9000 });
      rememberBook(currentBook);
      void castQuery.refetch();
      setPicked(null);
      onAssigned();
    } catch (error) {
      pushToast({ tone: "error", title: "Не удалось назначить", detail: describeApiError(error, "Сервер не принял назначение.") });
    } finally {
      setBusy("");
    }
  };

  return (
    <Dialog title={`Назначить: ${name}`} subtitle="Утвердить сразу или позвать на пробу — как выбор актёра в касте" onClose={onClose} className="dic-assign">
      <div className="dic-assign-head">
        <label className="dic-assign-book">
          <span>Книга</span>
          <select
            value={currentBook}
            onChange={(event) => {
              setBookId(event.target.value);
              setPicked(null);
            }}
            disabled={booksQuery.isLoading}
          >
            {books.map((book) => (
              <option key={book.id} value={book.id}>
                {book.display_title || book.title}
              </option>
            ))}
          </select>
        </label>
        <label className="dic-search dic-assign-search">
          <Icon name="search" />
          <input type="search" value={query} placeholder="Роль" aria-label="Найти роль" data-autofocus onChange={(e) => setQuery(e.target.value)} />
        </label>
        <label className="dic-assign-free">
          <input type="checkbox" checked={freeOnly} onChange={(e) => setFreeOnly(e.target.checked)} /> только свободные
        </label>
      </div>
      {mine.length ? (
        <p className="dic-assign-mine">
          Уже в этой книге: {mine.map((r) => `${r.name}${r.actor_name.trim().endsWith("?") ? " (проба)" : ""}`).join(", ")}
        </p>
      ) : null}

      {castQuery.isError ? (
        <p className="dic-sub">{describeApiError(castQuery.error, "Не удалось загрузить роли книги.")}</p>
      ) : castQuery.isLoading ? (
        <p className="dic-sub">Загружаю роли…</p>
      ) : (
        <ul className="dic-assign-roles" aria-label="Роли книги">
          {shown.map((row) => {
            const actor = row.actor_name.trim();
            const isHim = sameActor(actor, name);
            const open = picked?.character_id === row.character_id;
            return (
              <li key={row.character_id} className={open ? "is-open" : ""}>
                <button type="button" className="dic-assign-role" aria-expanded={open} onClick={() => setPicked(open ? null : row)}>
                  <strong>{row.name}</strong>
                  <span className={actor ? (isHim ? "dic-assign-actor is-him" : "dic-assign-actor") : "dic-assign-actor is-free"}>
                    {actor || "свободна"}
                  </span>
                  <span className="dic-sub">
                    {row.lines_count} {plural(row.lines_count, "реплика", "реплики", "реплик")}
                    {minutes(row.approx_seconds) ? ` · ${minutes(row.approx_seconds)}` : ""}
                  </span>
                </button>
                {open ? (
                  <div className="dic-assign-actions">
                    <Button size="sm" variant="secondary" loading={busy === "audition"} disabled={Boolean(busy)} onClick={() => assign(row, true)}>
                      Позвать на пробу
                    </Button>
                    <Button size="sm" variant="primary" loading={busy === "approve"} disabled={Boolean(busy)} onClick={() => assign(row, false)}>
                      Утвердить на роль
                    </Button>
                  </div>
                ) : null}
              </li>
            );
          })}
          {shown.length === 0 ? <li className="dic-sub">Ролей не нашлось.</li> : null}
        </ul>
      )}
    </Dialog>
  );
}
