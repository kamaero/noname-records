import { roleLabel } from "../v2/types";
import { formatDeadlineDateTime } from "../utils/serverTime";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPostJson, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { useToast } from "../components/ToastProvider";
import { Button, PageHeader, ScoreScale } from "../ui";
import type { SaveBudgetCharacterResponse } from "../types";
import type { AuditionLane, AuditionPair, AuditionsResponse } from "../v2/types";
import { createRole, moveAuditionsToRole } from "../v2/editorApi";
import { asksReason, busyConfirmText, isBusy, isRecommended, roleOpen, postScore } from "../viewModels/auditionPairs";
import { ReasonNote, useReasonPrompt } from "../components/auditions/ReasonPrompt";
import type { ScoreAnswer } from "../viewModels/auditionPairs";
import { sameActor } from "./DictorAssignDialog";
import "./AuditionsPage.css";

/* ============================================================
   Пробы — лента пар «актёр + роль» всех книг, свежие сверху
   (02.10; пары и шкалы 1–10 — с 03.10). Слушать, оценивать
   (автор — верхняя шкала, студия — нижняя) и утверждать — теми
   же запросами, что лист проб в касте.
   ============================================================ */

export const AUDITIONS_SEEN_KEY = "auditions.lastSeen";
const LANES: AuditionLane[] = ["author", "studio"];
const LANE_TITLE: Record<AuditionLane, string> = { author: "Author rating", studio: "Studio rating" };

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
  return formatDeadlineDateTime(iso);
}

function seconds(value: number): string {
  const total = Math.round(value || 0);
  return total ? `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}` : "";
}

function roleState(pair: AuditionPair): { text: string; tone: "" | "ok" | "maybe" } {
  const actor = pair.role_actor.trim();
  if (!actor) return { text: `unassigned · candidates: ${pair.role_auditions}`, tone: "" };
  if (actor.endsWith("?")) return { text: `auditioning: ${actor.replace(/\?+$/, "")} · candidates: ${pair.role_auditions}`, tone: "maybe" };
  return { text: `approved ${actor}`, tone: "ok" };
}

/** «тик-так» → «Тик-Так»: роль из имени файла приходит строчными. */
function suggestRoleName(role: string): string {
  return role.trim().replace(/(^|[\s-])(\p{L})/gu, (_, sep: string, letter: string) => sep + letter.toLocaleUpperCase("ru-RU"));
}

function auditionsWord(count: number): string {
  return `${count} ${count === 1 ? "audition" : "auditions"}`;
}

export function AuditionsPage() {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const seenAtOpen = useRef(readSeen());
  const [book, setBook] = useState("");
  const [query, setQuery] = useState("");
  const [unscored, setUnscored] = useState(false);
  const [unapproved, setUnapproved] = useState(false);
  const [recommended, setRecommended] = useState(false);
  const [hideBusy, setHideBusy] = useState(false);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [playing, setPlaying] = useState("");
  const [busy, setBusy] = useState("");
  const audio = useRef<HTMLAudioElement | null>(null);

  const feedQuery = useQuery({ queryKey: ["auditions-feed"], queryFn: () => apiGet<AuditionsResponse>("/api/auditions/feed") });
  const pairs = feedQuery.data?.items || [];
  const canScore = feedQuery.data?.can_score || [];

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
    for (const pair of pairs) seen.set(pair.book_id, pair.book_title);
    return [...seen.entries()];
  }, [pairs]);

  const unapprovedRole = (pair: AuditionPair) => !pair.role_actor.trim() || pair.role_actor.trim().endsWith("?");

  const matching = useMemo(() => {
    const needle = fold(query.trim());
    return pairs.filter((pair) => {
      if (book && pair.book_id !== book) return false;
      if (needle && !fold(`${pair.actor_name} ${roleLabel(pair.role)}`).includes(needle)) return false;
      if (unscored && (pair.scores?.author || pair.scores?.studio)) return false;
      if (unapproved && !unapprovedRole(pair)) return false;
      if (recommended && !(isRecommended(pair) && unapprovedRole(pair))) return false;
      return true;
    });
  }, [pairs, book, query, unscored, unapproved, recommended]);
  // Лента — «что нового пришло», поэтому занятые (уже утверждены на другую роль)
  // видны по умолчанию, с жёлтой меткой. 03.10 их скрывали и здесь — в «Карте»
  // почти все дикторы уже где-то утверждены, и лента опустела: свежие пробы
  // выглядели как непришедшие. Скрыты занятые только в листе проб роли в касте,
  // где решают, кого утверждать.
  const busyCount = matching.filter(isBusy).length;
  const shown = hideBusy ? matching.filter((pair) => !isBusy(pair)) : matching;

  const isNew = (iso: string) => Boolean(seenAtOpen.current) && iso > seenAtOpen.current;
  const fresh = pairs.reduce((sum, pair) => sum + pair.auditions.filter((item) => isNew(item.uploaded_at)).length, 0);

  const toggleOpen = (key: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const play = (id: string) => {
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

  const reasonPrompt = useReasonPrompt();

  const score = async (pair: AuditionPair, lane: AuditionLane, value: number | null, given?: ScoreAnswer | null) => {
    // Ниже 7 у автора — спросить, чем не этот герой (решение владельца 04.10, порог с 11.10).
    const answer = given !== undefined ? given : asksReason(lane, value) ? await reasonPrompt.ask(pair, lane, true) : null;
    setBusy(pair.key);
    try {
      await postScore(pair, lane, value, answer);
      await feedQuery.refetch();
    } catch (error) {
      pushToast({ tone: "error", title: "Rating not saved", detail: describeApiError(error, "") });
    } finally {
      setBusy("");
    }
  };

  const approve = async (pair: AuditionPair) => {
    let characterId = pair.character_id;
    let current = pair.role_actor.trim();
    let roleName = pair.role;
    const guess = pair.role_guess;
    if (!characterId && guess && window.confirm(
      `Role «${roleLabel(pair.role)}» is not in the cast for book «${pair.book_title}». It may refer to «${guess.name}», as spelled in the book.

`
      + `OK — approve ${pair.actor_name} to «${guess.name}» (audition will be renamed to «${guess.name}»).\n`
      + "Cancel — create a new role.",
    )) {
      // 11.10 «Ийли» из имени файла стала второй ролью без реплик, а не «Эиной»:
      // диктор открыла пустую Читалку. Похожая роль предлагается до новой, и правится
      // проба, а не каст: опечатка диктора не становится вторым именем роли.
      try {
        await moveAuditionsToRole(pair.book_id, guess.character_id, pair.role, pair.actor_name);
      } catch (error) {
        pushToast({ tone: "error", title: "Role not found", detail: describeApiError(error, "") });
        return;
      }
      characterId = guess.character_id;
      current = guess.actor_name.trim();
      roleName = guess.name;
    }
    if (!characterId) {
      // Роль пробы берётся из имени файла, а разметка книги могла этого персонажа не
      // завести (Тик-Так в «Карте»). Раньше кнопка отвечала «роли нет в касте» и
      // отправляла в Читалку; теперь роль заводится тут же — той же ручкой, что
      // «новая роль» в Читалке: она узнаёт уже существующую по регистру и псевдонимам.
      const name = window.prompt(
        `Role «${roleLabel(pair.role)}» is not in the cast for book «${pair.book_title}». Add it and approve ${pair.actor_name}? Enter the role name for the cast:`,
        suggestRoleName(pair.role),
      );
      if (!name || !name.trim()) return;
      try {
        characterId = (await createRole(pair.book_id, name.trim())).character_id;
      } catch (error) {
        pushToast({ tone: "error", title: "Role not added", detail: describeApiError(error, "") });
        return;
      }
    }
    if (isBusy(pair) && !window.confirm(busyConfirmText(pair))) return;
    if (current && !current.endsWith("?") && !sameActor(current, pair.actor_name) && !window.confirm(
      `«${roleName}» is currently assigned to ${current}. Replace with ${pair.actor_name}? Decisions follow voting priority, as in the cast.`,
    )) return;
    setBusy(pair.key);
    try {
      const result = await apiPostJson<SaveBudgetCharacterResponse>(
        `/api/budget/character/${encodeURIComponent(characterId)}`, { actor_name: pair.actor_name });
      const won = !result.vote || sameActor(result.vote.actor_name, pair.actor_name);
      // Про отказы — только когда они правда заводятся: решение сменилось и есть
      // кому отказывать. Повторное утверждение того же актёра писем не шлёт.
      const rejects = pair.role_auditions > 1 && Boolean(result.vote?.changed);
      const detail = `«${roleName}» · ${pair.book_title}` + (rejects ? ". Other candidates will receive a polite rejection in 15 minutes." : "");
      pushToast(won
        ? { tone: "success", title: `Approved: ${pair.actor_name}`, detail }
        : { tone: "error", title: "Decision did not take priority", detail: `«${roleLabel(pair.role)}» remains ${result.vote?.actor_name || "unassigned"}.` });
      await feedQuery.refetch();
    } catch (error) {
      pushToast({ tone: "error", title: "Could not approve", detail: describeApiError(error, "") });
    } finally {
      setBusy("");
    }
  };

  /* «Отказать» — письмо через 15 минут, как после утверждения: письмо не отзовёшь,
     а нажатие по ошибке — можно («Не отказывать» до срока) */
  const reject = async (pair: AuditionPair, cancel: boolean) => {
    if (!cancel && !window.confirm(
      `Reject ${pair.actor_name} for role «${roleLabel(pair.role)}»? A polite rejection will be sent through Telegram in 15 minutes. You can cancel it before then.`,
    )) return;
    setBusy(pair.key);
    try {
      await apiPostJson("/api/auditions/reject", { book_id: pair.book_id, role: pair.role, actor_name: pair.actor_name, cancel });
      pushToast(cancel
        ? { tone: "success", title: "Rejection cancelled", detail: `${pair.actor_name} · «${roleLabel(pair.role)}»` }
        : { tone: "success", title: `Rejection: ${pair.actor_name}`, detail: `«${roleLabel(pair.role)}» · notification will be sent in 15 minutes` });
      await feedQuery.refetch();
    } catch (error) {
      pushToast({ tone: "error", title: cancel ? "Could not cancel rejection" : "Could not save rejection", detail: describeApiError(error, "") });
    } finally {
      setBusy("");
    }
  };

  if (feedQuery.isError) return <div className="panel panel-pad">{describeApiError(feedQuery.error, "Could not load auditions.")}</div>;
  const canApprove = Boolean(feedQuery.data?.can_approve);
  const seesScores = pairs.some((pair) => pair.scores !== null) || canScore.length > 0;

  const playButton = (id: string, actor: string) => (
    <button
      type="button"
      className={playing === id ? "aufeed-play is-on" : "aufeed-play"}
      aria-label={playing === id ? `Stop audition: ${actor}` : `Listen to audition: ${actor}`}
      aria-pressed={playing === id}
      onClick={(event) => {
        event.stopPropagation();
        play(id);
      }}
    >
      {playing === id ? <span className="aufeed-stop" aria-hidden="true" /> : <Icon name="play" />}
    </button>
  );

  return (
    <section className="aufeed">
      <PageHeader
        title="Auditions"
        subtitle={`${pairs.length} candidates · newest first${fresh ? ` · new auditions since last visit: ${fresh}` : ""}`}
      />
      <div className="aufeed-head">
        <select value={book} onChange={(e) => setBook(e.target.value)} aria-label="Book">
          <option value="">All books</option>
          {books.map(([id, title]) => (
            <option key={id} value={id}>{title}</option>
          ))}
        </select>
        <label className="aufeed-search">
          <Icon name="search" />
          <input type="search" value={query} placeholder="Voice actor or role" aria-label="Find audition" onChange={(e) => setQuery(e.target.value)} />
        </label>
        {seesScores ? (
          <>
            <button type="button" className={unscored ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={unscored} onClick={() => setUnscored(!unscored)}>
              Unrated
            </button>
            <button type="button" className={recommended ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={recommended} onClick={() => setRecommended(!recommended)}
              title="At least one reviewer rated this 7 or higher; the role is not assigned yet">
              Ready for approval (7+)
            </button>
          </>
        ) : null}
        {busyCount || hideBusy ? (
          <button type="button" className={hideBusy ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={hideBusy} onClick={() => setHideBusy(!hideBusy)}
            title="Hide voice actors already approved for another role in this book">
            Available actors only{busyCount ? ` (${busyCount})` : ""}
          </button>
        ) : null}
        <button type="button" className={unapproved ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={unapproved} onClick={() => setUnapproved(!unapproved)}>
          Unassigned role
        </button>
      </div>

      {feedQuery.isLoading ? <p className="aufeed-sub">Loading…</p> : null}
      <ul className="aufeed-list">
        {shown.map((pair) => {
          const state = roleState(pair);
          const several = pair.auditions.length > 1;
          const expanded = open.has(pair.key);
          const freshPair = pair.auditions.some((item) => isNew(item.uploaded_at));
          const single = pair.auditions[0];
          return (
            <li key={pair.key} className={freshPair ? "aufeed-item is-new" : "aufeed-item"}>
              <div className="aufeed-row">
                {several ? (
                  <button type="button" className="aufeed-expand" aria-expanded={expanded}
                    aria-label={expanded ? "Collapse auditions" : `Show ${auditionsWord(pair.auditions.length)}`}
                    onClick={() => toggleOpen(pair.key)}>
                    {expanded ? "Collapse ▴" : `${auditionsWord(pair.auditions.length)} ▾`}
                  </button>
                ) : playButton(single.id, pair.actor_name)}
                <div className="aufeed-main" onClick={several ? () => toggleOpen(pair.key) : undefined}>
                  <div className="aufeed-line">
                    <Link className="aufeed-actor" to={`/dictors?q=${encodeURIComponent(pair.actor_name)}`} onClick={(e) => e.stopPropagation()}>
                      {pair.actor_name || "unnamed"}
                    </Link>
                    <span className="aufeed-role">«{roleLabel(pair.role)}»</span>
                    {freshPair ? <span className="aufeed-new">new</span> : null}
                    {!pair.approved && roleOpen(pair) && isRecommended(pair) ? <span className="aufeed-recommended">pending approval</span> : null}
                    {pair.busy_on.length ? <span className="aufeed-busy">approved: {pair.busy_on.map((r) => `«${r}»`).join(", ")}</span> : null}
                  </div>
                  <div className="aufeed-sub">
                    {pair.book_title} · {several ? auditionsWord(pair.auditions.length) : `${when(single.uploaded_at)}${seconds(single.duration_seconds) ? ` · ${seconds(single.duration_seconds)}` : ""}`}
                    {" · "}<span className={`aufeed-state ${state.tone ? `is-${state.tone}` : ""}`}>{state.text}</span>
                  </div>
                </div>
                <div className="aufeed-actions">
                  {pair.approved ? (
                    <span className="aufeed-state is-ok">approved</span>
                  ) : canApprove && pair.actor_name ? (
                    <Button size="sm" variant={roleOpen(pair) && isRecommended(pair) ? "primary" : "secondary"} loading={busy === pair.key} onClick={() => approve(pair)}>Approve</Button>
                  ) : null}
                  {/* отказ, который уже ушёл (или не дошёл), назван строкой ниже — второй кнопки нет */}
                  {!pair.approved && canApprove && pair.actor_name ? (
                    pair.rejection?.status === "pending" ? (
                      <Button size="sm" variant="ghost" disabled={busy === pair.key} onClick={() => reject(pair, true)}>Cancel rejection</Button>
                    ) : !pair.rejection ? (
                      <Button size="sm" variant="ghost" disabled={busy === pair.key} onClick={() => reject(pair, false)}>Reject</Button>
                    ) : null
                  ) : null}
                </div>
              </div>
              {pair.scores && pair.lane_labels ? (
                <div className="aufeed-scores">
                  {LANES.map((lane) => {
                    const mine = canScore.includes(lane);
                    const value = pair.scores?.[lane]?.score ?? null;
                    return (
                      <div key={lane} className="aufeed-lane">
                        <span className="aufeed-lane-name" title={pair.scores?.[lane]?.name || undefined}>{pair.lane_labels?.[lane]}</span>
                        <ScoreScale
                          value={value}
                          label={LANE_TITLE[lane]}
                          size={mine ? "md" : "sm"}
                          disabled={busy === pair.key}
                          onChange={mine ? (next) => score(pair, lane, next) : undefined}
                        />
                        <span className="aufeed-lane-value num">{value ?? ""}</span>
                        <ReasonNote pair={pair} lane={lane} canAsk={mine}
                          onAsk={async (reasons) => {
                            const answer = await reasonPrompt.ask(pair, lane, reasons);
                            if (answer) await score(pair, lane, value, answer);
                          }} />
                      </div>
                    );
                  })}
                </div>
              ) : null}
              {/* Диктор оценок не видит, но свой отправленный отказ — видит, как в листе проб */}
              {pair.rejection && (pair.scores || pair.rejection.status === "sent") ? (
                <p className="aufeed-rejection">
                  {pair.rejection.status === "pending" ? `The rejection will be sent via ${when(pair.rejection.due_at)}`
                    : pair.rejection.status === "sent" ? `Rejection sent ${when(pair.rejection.sent_at)}`
                    : pair.rejection.status === "failed" ? "Rejection not delivered: Telegram did not respond"
                    : "Rejection not delivered: no voice actor account. Contact them directly"}
                </p>
              ) : null}
              {several && expanded ? (
                <ul className="aufeed-files">
                  {pair.auditions.map((item) => (
                    <li key={item.id} className="aufeed-file">
                      {playButton(item.id, pair.actor_name)}
                      <span className="aufeed-sub">
                        {when(item.uploaded_at)}{seconds(item.duration_seconds) ? ` · ${seconds(item.duration_seconds)}` : ""}
                        {item.chapter ? ` · ${item.chapter}` : ""}
                      </span>
                      {isNew(item.uploaded_at) ? <span className="aufeed-new">new</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
        {!feedQuery.isLoading && shown.length === 0 ? <li className="aufeed-sub">No auditions found.</li> : null}
      </ul>
      {reasonPrompt.dialog}
    </section>
  );
}
