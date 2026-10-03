import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPostJson, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { useToast } from "../components/ToastProvider";
import { Button, PageHeader } from "../ui";
import type { SaveBudgetCharacterResponse } from "../types";
import type { AuditionItem, AuditionReactionResponse } from "../v2/types";
import { sameActor } from "./DictorAssignDialog";
import "./AuditionsPage.css";

/* ============================================================
   Пробы — лента проб всех книг, свежие сверху (решение 02.10).
   Слушать, реагировать (автор) и утверждать (админ, автор) —
   теми же запросами, что лист проб в касте.
   ============================================================ */

export const AUDITIONS_SEEN_KEY = "auditions.lastSeen";

type FeedItem = AuditionItem & {
  book_id: string;
  book_title: string;
  character_id: string;
  role_actor: string;
  role_auditions: number;
};
type FeedResponse = { ok: boolean; items: FeedItem[]; can_approve: boolean; can_react: boolean };

function readSeen(): string {
  try {
    return window.localStorage.getItem(AUDITIONS_SEEN_KEY) || "";
  } catch {
    return "";
  }
}

function fold(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е");
}

function when(iso: string): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    .format(new Date(iso));
}

function seconds(value: number): string {
  const total = Math.round(value || 0);
  return total ? `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}` : "";
}

function roleState(item: FeedItem): { text: string; tone: "" | "ok" | "maybe" } {
  const actor = item.role_actor.trim();
  if (!actor) return { text: `свободна · проб: ${item.role_auditions}`, tone: "" };
  if (actor.endsWith("?")) return { text: `на пробе: ${actor.replace(/\?+$/, "")} · проб: ${item.role_auditions}`, tone: "maybe" };
  return { text: `утверждён ${actor}`, tone: "ok" };
}

export function AuditionsPage() {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const seenAtOpen = useRef(readSeen());
  const [book, setBook] = useState("");
  const [query, setQuery] = useState("");
  const [noReaction, setNoReaction] = useState(false);
  const [unapproved, setUnapproved] = useState(false);
  const [playing, setPlaying] = useState("");
  const [busy, setBusy] = useState("");
  const audio = useRef<HTMLAudioElement | null>(null);

  const feedQuery = useQuery({ queryKey: ["auditions-feed"], queryFn: () => apiGet<FeedResponse>("/api/auditions/feed") });
  const items = feedQuery.data?.items || [];

  // Визит засчитан — счётчик «новых» в меню обнуляется; подсветка на этой странице
  // держится от момента открытия, а не пропадает сразу.
  useEffect(() => {
    if (!feedQuery.data) return;
    try {
      window.localStorage.setItem(AUDITIONS_SEEN_KEY, new Date().toISOString());
    } catch {
      /* приватное окно — счётчик просто не запомнится */
    }
    void queryClient.invalidateQueries({ queryKey: ["auditions-new"] });
  }, [feedQuery.data, queryClient]);
  useEffect(() => () => audio.current?.pause(), []);

  const books = useMemo(() => {
    const seen = new Map<string, string>();
    for (const item of items) seen.set(item.book_id, item.book_title);
    return [...seen.entries()];
  }, [items]);

  const shown = useMemo(() => {
    const needle = fold(query.trim());
    return items.filter((item) => {
      if (book && item.book_id !== book) return false;
      if (needle && !fold(`${item.actor_name} ${item.role}`).includes(needle)) return false;
      if (noReaction && item.author_reaction !== null) return false;
      if (unapproved && item.role_actor.trim() && !item.role_actor.trim().endsWith("?")) return false;
      return true;
    });
  }, [items, book, query, noReaction, unapproved]);

  const fresh = items.filter((item) => seenAtOpen.current && item.uploaded_at > seenAtOpen.current).length;

  const toggle = (id: string) => {
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.onended = () => setPlaying("");
      audio.current.onerror = () => setPlaying("");
    }
    if (playing === id) {
      audio.current.pause();
      setPlaying("");
      return;
    }
    audio.current.src = `/api/v2/auditions/${encodeURIComponent(id)}/audio`;
    void audio.current.play().catch(() => setPlaying(""));
    setPlaying(id);
  };

  const react = async (item: FeedItem, value: number) => {
    setBusy(item.id);
    try {
      await apiPostJson<AuditionReactionResponse>(`/api/v2/auditions/${encodeURIComponent(item.id)}/reaction`, { value });
      await feedQuery.refetch();
    } catch (error) {
      pushToast({ tone: "error", title: "Реакция не сохранилась", detail: describeApiError(error, "") });
    } finally {
      setBusy("");
    }
  };

  const approve = async (item: FeedItem) => {
    if (!item.character_id) {
      pushToast({ tone: "error", title: "Роли нет в касте", detail: `«${item.role}» не нашлась в касте книги — назначьте в касте.` });
      return;
    }
    const current = item.role_actor.trim();
    if (current && !current.endsWith("?") && !sameActor(current, item.actor_name) && !window.confirm(
      `На «${item.role}» сейчас утверждён ${current}. Заменить на ${item.actor_name}?\n\nРешение считается по голосам, как в касте.`,
    )) return;
    setBusy(item.id);
    try {
      const result = await apiPostJson<SaveBudgetCharacterResponse>(
        `/api/budget/character/${encodeURIComponent(item.character_id)}`, { actor_name: item.actor_name });
      const won = !result.vote || sameActor(result.vote.actor_name, item.actor_name);
      pushToast(won
        ? { tone: "success", title: `Утверждён: ${item.actor_name}`, detail: `«${item.role}» · ${item.book_title}` }
        : { tone: "error", title: "Голос не перевесил", detail: `На «${item.role}» остаётся ${result.vote?.actor_name || "свободно"}.` });
      await feedQuery.refetch();
    } catch (error) {
      pushToast({ tone: "error", title: "Не удалось утвердить", detail: describeApiError(error, "") });
    } finally {
      setBusy("");
    }
  };

  if (feedQuery.isError) return <div className="panel panel-pad">{describeApiError(feedQuery.error, "Не удалось загрузить пробы.")}</div>;
  const canApprove = Boolean(feedQuery.data?.can_approve);
  const canReact = Boolean(feedQuery.data?.can_react);

  return (
    <section className="aufeed">
      <PageHeader
        title="Пробы"
        subtitle={`${items.length} проб · свежие сверху${fresh ? ` · новых с прошлого раза: ${fresh}` : ""}`}
      />
      <div className="aufeed-head">
        <select value={book} onChange={(e) => setBook(e.target.value)} aria-label="Книга">
          <option value="">Все книги</option>
          {books.map(([id, title]) => (
            <option key={id} value={id}>{title}</option>
          ))}
        </select>
        <label className="aufeed-search">
          <Icon name="search" />
          <input type="search" value={query} placeholder="Диктор или роль" aria-label="Найти пробу" onChange={(e) => setQuery(e.target.value)} />
        </label>
        <button type="button" className={noReaction ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={noReaction} onClick={() => setNoReaction(!noReaction)}>
          Без оценки
        </button>
        <button type="button" className={unapproved ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={unapproved} onClick={() => setUnapproved(!unapproved)}>
          Роль не утверждена
        </button>
      </div>

      {feedQuery.isLoading ? <p className="aufeed-sub">Загружаю…</p> : null}
      <ul className="aufeed-list">
        {shown.map((item) => {
          const state = roleState(item);
          const isNew = Boolean(seenAtOpen.current) && item.uploaded_at > seenAtOpen.current;
          const approved = item.role_actor.trim() && !item.role_actor.trim().endsWith("?") && sameActor(item.role_actor, item.actor_name);
          return (
            <li key={item.id} className={isNew ? "aufeed-item is-new" : "aufeed-item"}>
              <button
                type="button"
                className={playing === item.id ? "aufeed-play is-on" : "aufeed-play"}
                aria-label={playing === item.id ? `Остановить пробу: ${item.actor_name}` : `Слушать пробу: ${item.actor_name}`}
                aria-pressed={playing === item.id}
                onClick={() => toggle(item.id)}
              >
                {playing === item.id ? <span className="aufeed-stop" aria-hidden="true" /> : <Icon name="play" />}
              </button>
              <div className="aufeed-main">
                <div className="aufeed-line">
                  <Link className="aufeed-actor" to={`/dictors?q=${encodeURIComponent(item.actor_name)}`}>{item.actor_name || "без имени"}</Link>
                  <span className="aufeed-role">«{item.role}»</span>
                  {isNew ? <span className="aufeed-new">новая</span> : null}
                </div>
                <div className="aufeed-sub">
                  {item.book_title} · {when(item.uploaded_at)}{seconds(item.duration_seconds) ? ` · ${seconds(item.duration_seconds)}` : ""}
                  {" · "}<span className={`aufeed-state ${state.tone ? `is-${state.tone}` : ""}`}>{state.text}</span>
                </div>
              </div>
              <div className="aufeed-actions">
                {canReact ? (
                  <span className="aufeed-react" role="group" aria-label="Оценка пробы">
                    {([1, -1] as const).map((value) => {
                      const pressed = item.author_reaction === value;
                      return (
                        <button key={value} type="button" aria-pressed={pressed} disabled={busy === item.id}
                          title={value === 1 ? "Подходит" : "Не подходит — через 15 минут диктору уйдёт вежливый отказ"}
                          onClick={() => react(item, pressed ? 0 : value)}>
                          {value === 1 ? "👍" : "👎"}
                        </button>
                      );
                    })}
                  </span>
                ) : item.author_reaction ? (
                  <span className="aufeed-reaction">Оценка: {item.author_reaction === 1 ? "👍" : "👎"}</span>
                ) : null}
                {approved ? (
                  <span className="aufeed-state is-ok">утверждён</span>
                ) : canApprove && item.actor_name ? (
                  <Button size="sm" variant="secondary" loading={busy === item.id} onClick={() => approve(item)}>Утвердить</Button>
                ) : null}
              </div>
            </li>
          );
        })}
        {!feedQuery.isLoading && shown.length === 0 ? <li className="aufeed-sub">Проб не нашлось.</li> : null}
      </ul>
    </section>
  );
}
