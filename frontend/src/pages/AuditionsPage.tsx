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
const LANE_TITLE: Record<AuditionLane, string> = { author: "Оценка автора", studio: "Оценка студии" };

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

function roleState(pair: AuditionPair): { text: string; tone: "" | "ok" | "maybe" } {
  const actor = pair.role_actor.trim();
  if (!actor) return { text: `свободна · претендентов: ${pair.role_auditions}`, tone: "" };
  if (actor.endsWith("?")) return { text: `на пробе: ${actor.replace(/\?+$/, "")} · претендентов: ${pair.role_auditions}`, tone: "maybe" };
  return { text: `утверждён ${actor}`, tone: "ok" };
}

/** «тик-так» → «Тик-Так»: роль из имени файла приходит строчными. */
function suggestRoleName(role: string): string {
  return role.trim().replace(/(^|[\s-])(\p{L})/gu, (_, sep: string, letter: string) => sep + letter.toLocaleUpperCase("ru-RU"));
}

function auditionsWord(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} проба`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} пробы`;
  return `${count} проб`;
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
      if (needle && !fold(`${pair.actor_name} ${pair.role}`).includes(needle)) return false;
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
      pushToast({ tone: "error", title: "Оценка не сохранилась", detail: describeApiError(error, "") });
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
      `Роли «${pair.role}» нет в касте книги «${pair.book_title}». Похоже, это «${guess.name}» — в книге она пишется так.\n\n`
      + `ОК — утвердить ${pair.actor_name} на «${guess.name}» (проба переименуется в «${guess.name}»).\n`
      + "Отмена — завести новую роль.",
    )) {
      // 11.10 «Лйра» из имени файла стала второй ролью без реплик, а не «Эиной»:
      // диктор открыла пустую Читалку. Похожая роль предлагается до новой, и правится
      // проба, а не каст: опечатка диктора не становится вторым именем роли.
      try {
        await moveAuditionsToRole(pair.book_id, guess.character_id, pair.role, pair.actor_name);
      } catch (error) {
        pushToast({ tone: "error", title: "Роль не нашлась", detail: describeApiError(error, "") });
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
        `Роли «${pair.role}» нет в касте книги «${pair.book_title}». Добавить её и утвердить ${pair.actor_name}?\n\nКак назвать роль в касте:`,
        suggestRoleName(pair.role),
      );
      if (!name || !name.trim()) return;
      try {
        characterId = (await createRole(pair.book_id, name.trim())).character_id;
      } catch (error) {
        pushToast({ tone: "error", title: "Роль не добавилась", detail: describeApiError(error, "") });
        return;
      }
    }
    if (isBusy(pair) && !window.confirm(busyConfirmText(pair))) return;
    if (current && !current.endsWith("?") && !sameActor(current, pair.actor_name) && !window.confirm(
      `На «${roleName}» сейчас утверждён ${current}. Заменить на ${pair.actor_name}?\n\nРешение считается по голосам, как в касте.`,
    )) return;
    setBusy(pair.key);
    try {
      const result = await apiPostJson<SaveBudgetCharacterResponse>(
        `/api/budget/character/${encodeURIComponent(characterId)}`, { actor_name: pair.actor_name });
      const won = !result.vote || sameActor(result.vote.actor_name, pair.actor_name);
      // Про отказы — только когда они правда заводятся: решение сменилось и есть
      // кому отказывать. Повторное утверждение того же актёра писем не шлёт.
      const rejects = pair.role_auditions > 1 && Boolean(result.vote?.changed);
      const detail = `«${roleName}» · ${pair.book_title}` + (rejects ? ". Остальным пробовавшимся через 15 минут уйдёт вежливый отказ." : "");
      pushToast(won
        ? { tone: "success", title: `Утверждён: ${pair.actor_name}`, detail }
        : { tone: "error", title: "Голос не перевесил", detail: `На «${pair.role}» остаётся ${result.vote?.actor_name || "свободно"}.` });
      await feedQuery.refetch();
    } catch (error) {
      pushToast({ tone: "error", title: "Не удалось утвердить", detail: describeApiError(error, "") });
    } finally {
      setBusy("");
    }
  };

  /* «Отказать» — письмо через 15 минут, как после утверждения: письмо не отзовёшь,
     а нажатие по ошибке — можно («Не отказывать» до срока) */
  const reject = async (pair: AuditionPair, cancel: boolean) => {
    if (!cancel && !window.confirm(
      `Отказать ${pair.actor_name} по роли «${pair.role}»?\n\nЧерез 15 минут диктору уйдёт вежливое письмо в Telegram. До этого отказ можно отменить.`,
    )) return;
    setBusy(pair.key);
    try {
      await apiPostJson("/api/auditions/reject", { book_id: pair.book_id, role: pair.role, actor_name: pair.actor_name, cancel });
      pushToast(cancel
        ? { tone: "success", title: "Отказ отменён", detail: `${pair.actor_name} · «${pair.role}»` }
        : { tone: "success", title: `Отказ: ${pair.actor_name}`, detail: `«${pair.role}» · письмо уйдёт через 15 минут` });
      await feedQuery.refetch();
    } catch (error) {
      pushToast({ tone: "error", title: cancel ? "Отказ не отменился" : "Отказ не записался", detail: describeApiError(error, "") });
    } finally {
      setBusy("");
    }
  };

  if (feedQuery.isError) return <div className="panel panel-pad">{describeApiError(feedQuery.error, "Не удалось загрузить пробы.")}</div>;
  const canApprove = Boolean(feedQuery.data?.can_approve);
  const seesScores = pairs.some((pair) => pair.scores !== null) || canScore.length > 0;

  const playButton = (id: string, actor: string) => (
    <button
      type="button"
      className={playing === id ? "aufeed-play is-on" : "aufeed-play"}
      aria-label={playing === id ? `Остановить пробу: ${actor}` : `Слушать пробу: ${actor}`}
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
        title="Пробы"
        subtitle={`${pairs.length} претендентов · свежие сверху${fresh ? ` · новых проб с прошлого раза: ${fresh}` : ""}`}
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
        {seesScores ? (
          <>
            <button type="button" className={unscored ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={unscored} onClick={() => setUnscored(!unscored)}>
              Без оценки
            </button>
            <button type="button" className={recommended ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={recommended} onClick={() => setRecommended(!recommended)}
              title="Кто-то из двоих поставил 7 или выше, а роль ещё не утверждена">
              К утверждению (7+)
            </button>
          </>
        ) : null}
        {busyCount || hideBusy ? (
          <button type="button" className={hideBusy ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={hideBusy} onClick={() => setHideBusy(!hideBusy)}
            title="Скрыть актёров, уже утверждённых на другую роль этой книги">
            Без занятых{busyCount ? ` (${busyCount})` : ""}
          </button>
        ) : null}
        <button type="button" className={unapproved ? "aufeed-chip is-on" : "aufeed-chip"} aria-pressed={unapproved} onClick={() => setUnapproved(!unapproved)}>
          Роль не утверждена
        </button>
      </div>

      {feedQuery.isLoading ? <p className="aufeed-sub">Загружаю…</p> : null}
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
                    aria-label={expanded ? "Свернуть пробы" : `Показать ${auditionsWord(pair.auditions.length)}`}
                    onClick={() => toggleOpen(pair.key)}>
                    {expanded ? "Свернуть ▴" : `${auditionsWord(pair.auditions.length)} ▾`}
                  </button>
                ) : playButton(single.id, pair.actor_name)}
                <div className="aufeed-main" onClick={several ? () => toggleOpen(pair.key) : undefined}>
                  <div className="aufeed-line">
                    <Link className="aufeed-actor" to={`/dictors?q=${encodeURIComponent(pair.actor_name)}`} onClick={(e) => e.stopPropagation()}>
                      {pair.actor_name || "без имени"}
                    </Link>
                    <span className="aufeed-role">«{pair.role}»</span>
                    {freshPair ? <span className="aufeed-new">новая</span> : null}
                    {!pair.approved && roleOpen(pair) && isRecommended(pair) ? <span className="aufeed-recommended">к утверждению</span> : null}
                    {pair.busy_on.length ? <span className="aufeed-busy">утверждён: {pair.busy_on.map((r) => `«${r}»`).join(", ")}</span> : null}
                  </div>
                  <div className="aufeed-sub">
                    {pair.book_title} · {several ? auditionsWord(pair.auditions.length) : `${when(single.uploaded_at)}${seconds(single.duration_seconds) ? ` · ${seconds(single.duration_seconds)}` : ""}`}
                    {" · "}<span className={`aufeed-state ${state.tone ? `is-${state.tone}` : ""}`}>{state.text}</span>
                  </div>
                </div>
                <div className="aufeed-actions">
                  {pair.approved ? (
                    <span className="aufeed-state is-ok">утверждён</span>
                  ) : canApprove && pair.actor_name ? (
                    <Button size="sm" variant={roleOpen(pair) && isRecommended(pair) ? "primary" : "secondary"} loading={busy === pair.key} onClick={() => approve(pair)}>Утвердить</Button>
                  ) : null}
                  {/* отказ, который уже ушёл (или не дошёл), назван строкой ниже — второй кнопки нет */}
                  {!pair.approved && canApprove && pair.actor_name ? (
                    pair.rejection?.status === "pending" ? (
                      <Button size="sm" variant="ghost" disabled={busy === pair.key} onClick={() => reject(pair, true)}>Не отказывать</Button>
                    ) : !pair.rejection ? (
                      <Button size="sm" variant="ghost" disabled={busy === pair.key} onClick={() => reject(pair, false)}>Отказать</Button>
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
                  {pair.rejection.status === "pending" ? `Отказ уйдёт диктору в ${when(pair.rejection.due_at)}`
                    : pair.rejection.status === "sent" ? `Отказ отправлен ${when(pair.rejection.sent_at)}`
                    : pair.rejection.status === "failed" ? "Отказ не ушёл — Telegram не ответил"
                    : "Отказ не доставлен: у диктора нет учётки — сообщите сами"}
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
                      {isNew(item.uploaded_at) ? <span className="aufeed-new">новая</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
        {!feedQuery.isLoading && shown.length === 0 ? <li className="aufeed-sub">Проб не нашлось.</li> : null}
      </ul>
      {reasonPrompt.dialog}
    </section>
  );
}
