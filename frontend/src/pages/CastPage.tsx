import "./CastPage.css";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiPostJson, describeApiError } from "../api/client";
import { saveCharacterAbout, type CharacterAbout } from "../v2/editorApi";
import { Icon } from "../components/Icon";
import { useToast } from "../components/ToastProvider";
import { markFirstStepsSeen } from "../utils/firstStepsSeen";
import { hubKeys, useCharacterMapActions } from "../components/book/v2/hubApi";
import { CastBudgetLine } from "../components/cast/CastBudgetLine";
import { CastConflictsNote } from "../components/cast/CastConflictsNote";
import { CastPaletteHost } from "../components/cast/CastPaletteHost";
import { PaletteFixSheet } from "../components/cast/PaletteFixSheet";
import { CastTable, type ActorPickerConfig, type CastSort } from "../components/cast/CastTable";
import type { PickerDictor } from "../components/cast/ActorPicker";
import { RecastDialog } from "../components/cast/RecastDialog";
import "../components/cast/ActorPicker.css";
import { inviteText } from "../utils/invite";
import { AuditionsSheet } from "../components/cast/AuditionsSheet";
import "../components/cast/AuditionsSheet.css";
import { isAgentRoles } from "../layout/AppShell";
import { Button, EmptyState, Field, PageHeader, Sheet } from "../ui";
import { editorKeys, formatWhen, invalidateReader, toBudgetRow, useBookAuditions, useBookCast } from "../v2/editorApi";
import type { AuditionLane, AuditionPair } from "../v2/types";
import { bestAuthorScore, postScore, type ScoreAnswer } from "../viewModels/auditionPairs";
import type { BookBudgetResponse, BooksResponse, CharacterMapAcknowledgeResponse, CharacterMapCheckedResponse, CharacterMapForgetResponse, CharacterMapResponse, MeResponse, RoleApproval, RoleIntersection, RoleVoteResult, PublicAuthConfigResponse, SaveBookBudgetPayload, SaveBookBudgetResponse, SaveBudgetCharacterPayload, SaveBudgetCharacterResponse } from "../types";
import {
  chapterMismatch,
  collectActorNames,
  filterCastRows,
  findMeaningfulPaletteConflicts,
  formatCastSubtitle,
  mergeCastCounts,
  mergeRoleFacts,
  normalizeHexColor,
  pluralRu,
  sortCastRowsBy,
  summarizeCast,
  type CastRow,
  type CastSortKey,
  type PaletteResolutionEntry,
} from "../viewModels/booksCast";

const DEFAULT_DIR: Record<CastSortKey, "asc" | "desc"> = { role: "asc", actor: "asc", lines: "desc", sum: "desc" };

/**
 * Одна формулировка на оба места: лист слияния и вопрос при переименовании.
 * Признак один и тот же (`has_audio`), и цена одна и та же — `AudioFile.role`
 * хранится строкой, ни слияние, ни переименование её не трогают, и записи
 * остаются под прежним именем. Двум местам врозь сочинять третий текст нельзя:
 * это одно и то же обещание владельцу.
 */
function audioStaysBehindNote(name: string, operation: "Merge" | "Rename"): string {
  return `«${name}» has voice recordings. ${operation} does not move them: takes will remain under «${name}».`;
}

/** `RoleIntersection` знает пару целиком (`role_a`/`role_b`); колонка каста смотрит на
    одну роль и её партнёра — отсюда узкое расширение, а не отдельный тип с нуля. */
export type RoleIntersectionForRole = RoleIntersection & { partner: string };

/** Что вышло из голоса. Голос автора весит два, владельца — один: книгу написал автор,
    и последнее слово о том, как персонаж звучит, за ним. Отказ надо назвать вслух —
    иначе кнопка нажата, ничего не изменилось, и непонятно почему. */
function describeVote(vote: RoleVoteResult | undefined): string {
  if (!vote || !vote.overridden_by) return "";
  return vote.actor_name
    ? `Role remains assigned to «${vote.actor_name}» as chosen by ${vote.overridden_by}, whose decision takes priority.`
    : `Role remains unassigned as chosen by ${vote.overridden_by}, whose decision takes priority.`;
}

/** Дошло ли до диктора «ты утверждён на роль» или «вас зовут на пробу». Молчание тут
    хуже отказа: режиссёр решит, что человек предупреждён, а тот будет ждать. Имя со
    знаком вопроса (`kind: "audition"`) — предложение, а не решение, и текст об этом
    отдельный: диктора зовут на пробу, а не сообщают о назначении. */
function describeApproval(approval: RoleApproval | undefined): string {
  if (!approval) return "";
  // Неоднозначное имя — общий случай для назначения и для пробы: `names_match`
  // нечёткий, и писать напрямую нельзя, не разобравшись, кто из подошедших имелся в виду.
  if (approval.reason === "ambiguous") return `${approval.actor_name}: this name matches several people; forwarded to you and the agents`;
  if (approval.kind === "audition") {
    if (approval.notified) return `Invited to audition: ${approval.actor_name}`;
    if (approval.reason === "no_telegram") return `${approval.actor_name}: no account found; forwarded to you and the agents`;
    if (approval.reason === "not_sent") return `${approval.actor_name}: the bot cannot reach them; ask them to open it and press Start`;
    return "";
  }
  if (approval.notified) return `${approval.actor_name} was notified in Telegram.`;
  if (approval.reason === "no_telegram") return `${approval.actor_name} was not notified: no Telegram account linked.`;
  if (approval.reason === "not_sent") return `${approval.actor_name} was not notified: Telegram rejected the message.`;
  return "";
}

/** `title` предполагает назначение; при `kind: "audition"` актёра не назначили, а только предложили. */
function resolveActorToastTitle(title: string, approval: RoleApproval | undefined): string {
  return approval?.kind === "audition" ? "Voice actor suggested" : title;
}

/**
 * `rename_role` (`app/v2/character_map.py`) отклоняет по коду — сырой код
 * показывать нельзя, сервер прислал его не для человека. `needs_consent` сюда
 * не входит: это не отказ, а вопрос про цену переезда реплик, и его разбирает
 * `onRename` отдельно.
 */
const RENAME_ERRORS: Record<string, string> = {
  placeholder: "This is an annotation marker. Its name cannot be edited.",
  empty_name: "Name cannot be empty.",
  same_name: "Name has not changed.",
  name_taken: "This role already exists. Use Merge to combine roles and review its impact first.",
  narrator_is_not_a_role: "This is an alias of the narrator. Only one narrator is allowed.",
  not_found: "This role no longer exists. Refresh the page.",
};

function describeRenameError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && RENAME_ERRORS[error.errorCode]) {
    return RENAME_ERRORS[error.errorCode];
  }
  return describeApiError(error, fallback);
}

/** Shown only until the first budget response lands; the server is the authority. */
const DEFAULT_RATE_FALLBACK = 1000;

/* Книгу меняют выбором в заголовке, и маршрут остаётся тем же — React оставил бы
   страницу жить с состоянием прежней книги: поиском, раскрытыми ролями, отметкой
   «Карта не проверена». Ключ по книге начинает каст каждой книги с чистого листа. */
export function CastPage() {
  const { bookId = "" } = useParams();
  return <CastBookPage key={bookId} />;
}

function CastBookPage() {
  const { bookId = "" } = useParams();
  const navigate = useNavigate();
  const booksQuery = useQuery({ queryKey: ["books"], queryFn: () => apiGet<BooksResponse>("/api/books") });
  useEffect(() => { markFirstStepsSeen(bookId); }, [bookId]);
  const queryClient = useQueryClient();
  const { pushToast } = useToast();

  const budgetQuery = useQuery({
    queryKey: ["book-budget", bookId],
    queryFn: () => apiGet<BookBudgetResponse>(`/api/budget/${encodeURIComponent(bookId)}`),
    enabled: Boolean(bookId),
    staleTime: 15_000,
  });
  const castQuery = useBookCast(bookId, true);
  // Карта знает то, чего смета не считает: возраст, алиасы, канон и заявленные
  // разметкой главы. Пересечения ролей приходят тем же ответом — эта задача
  // раскладывает их по роли и рисует развёртку строки.
  // Ключ берётся из `hubKeys`, а не набирается здесь второй раз: тем же ключом
  // читает карту карточка хаба и инвалидирует её `useCharacterMapActions`.
  const mapQueryKey = useMemo(() => hubKeys.characterMap(bookId), [bookId]);
  const mapQuery = useQuery({
    queryKey: mapQueryKey,
    queryFn: () => apiGet<CharacterMapResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/character-map`),
    enabled: Boolean(bookId),
  });
  // Слить / удалить роль — та же логика (тосты, коды отказа), что была на
  // отдельном экране карты персонажей; здесь она приезжает в развёртку строки,
  // а не остаётся дублем на исчезающем экране.
  const cardActions = useCharacterMapActions(bookId);
  const invalidateMap = useCallback(
    () => queryClient.invalidateQueries({ queryKey: mapQueryKey }),
    [queryClient, mapQueryKey],
  );
  // Только для имени слушающего: удалить свою пробу диктор вправе и не будучи автором
  // или владельцем — `/api/me` уже лежит в кэше (его читает `Layout` в `App.tsx`),
  // второго запроса это не стоит.
  const meQuery = useQuery({ queryKey: ["me"], queryFn: () => apiGet<MeResponse>("/api/me") });
  const isAgent = isAgentRoles(meQuery.data?.roles ?? []);
  // Пробы приходят на роль, а решение принимают в касте — значит и слушать их здесь.
  const auditionsQuery = useBookAuditions(bookId, true);
  const auditionsQueryKey = useMemo(() => editorKeys.auditions(bookId), [bookId]);
  // Лист проб и слушает, и от них избавляется. Отказ приходит человеческим текстом
  // («Отказы приходят с человеческим текстом, не кодом») — `describeApiError` берёт
  // его как есть, а не сочиняет свой.
  const deleteAuditionMutation = useMutation({
    mutationFn: (audioId: string) =>
      apiPostJson<{ ok: boolean }>(`/api/recording/files/${encodeURIComponent(audioId)}/delete`, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: auditionsQueryKey });
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Not deleted", detail: describeApiError(error, "Could not delete the audition.") });
    },
  });
  // Оценку 1–10 ставят автор (верхняя шкала) и студия (нижняя); после неё лист
  // перечитывается — порядок претендентов зависит от оценки автора.
  const scoreMutation = useMutation({
    mutationFn: ({ pair, lane, value, answer }: { pair: AuditionPair; lane: AuditionLane; value: number | null; answer?: ScoreAnswer | null }) =>
      postScore(pair, lane, value, answer),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: auditionsQueryKey });
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Reaction not saved", detail: describeApiError(error, "Could not save the reaction.") });
    },
  });
  const rows = useMemo<CastRow[]>(
    () => mergeRoleFacts(mergeCastCounts((budgetQuery.data?.characters || []) as CastRow[], castQuery.data?.characters), mapQuery.data?.rows),
    [budgetQuery.data, castQuery.data, mapQuery.data],
  );
  const auditionsByRole = useMemo(() => {
    const items = auditionsQuery.data?.items ?? [];
    // Ключ — имя роли в касте, а не то, как роль записана в пробе: сервер узнаёт
    // персонажа по регистру и псевдонимам, и проба попадает в строку своего персонажа.
    const castName = new Map(rows.map((row) => [row.character_id, row.name]));
    const map = new Map<string, AuditionPair[]>();
    for (const pair of items) {
      const role = (castName.get(pair.character_id) ?? pair.role).trim();
      if (!role) continue;
      const list = map.get(role);
      if (list) list.push(pair);
      else map.set(role, [pair]);
    }
    return map;
  }, [auditionsQuery.data, rows]);
  const auditionCounts = useMemo(
    () => new Map([...auditionsByRole].map(([role, list]) => [role, list.length])),
    [auditionsByRole],
  );
  const auditionBest = useMemo(
    () => new Map([...auditionsByRole].map(([role, list]) => [role, bestAuthorScore(list)])),
    [auditionsByRole],
  );
  const [auditionsRole, setAuditionsRole] = useState("");
  const crossingCount = useMemo(
    () => (mapQuery.data?.intersections ?? []).filter((item) => !item.acknowledged).length,
    [mapQuery.data],
  );
  /** Пересечения по имени роли: строка показывает худшее из непризнанных. */
  const intersectionsByRole = useMemo(() => {
    const out = new Map<string, RoleIntersectionForRole[]>();
    for (const item of mapQuery.data?.intersections ?? []) {
      for (const [role, partner] of [[item.role_a, item.role_b], [item.role_b, item.role_a]] as const) {
        const list = out.get(role) ?? [];
        list.push({ ...item, partner });
        out.set(role, list);
      }
    }
    return out;
  }, [mapQuery.data]);
  // Conflicts are computed from the v2 cast — the same rows the reader's legend uses —
  // so the two screens never quote different numbers for the same book. (The budget
  // rows collapse duplicate identities, which is right for money and wrong for colours.)
  const voicedRows = useMemo(
    () => (castQuery.data?.characters ?? []).map(toBudgetRow).filter((row) => !row.is_narrator),
    [castQuery.data],
  );
  // Similar colours *and* a shared chapter. The raw pair count (580 on «Карта»)
  // counted roles that never meet, which is not a problem anybody has.
  const conflicts = useMemo(() => findMeaningfulPaletteConflicts(voicedRows), [voicedRows]);
  const actorNames = useMemo(() => collectActorNames(rows), [rows]);
  const summary = useMemo(() => summarizeCast(rows), [rows]);

  const [query, setQuery] = useState("");
  const [onlyUnassigned, setOnlyUnassigned] = useState(false);
  const [onlyAuditions, setOnlyAuditions] = useState(false);
  const [onlyCrossing, setOnlyCrossing] = useState(false);
  const [onlyMismatch, setOnlyMismatch] = useState(false);
  const [paletteFix, setPaletteFix] = useState(false);
  const [sort, setSort] = useState<CastSort>(null);
  const [paletteRow, setPaletteRow] = useState<CastRow | null>(null);
  const [savingIds, setSavingIds] = useState<ReadonlySet<string>>(() => new Set());
  const [acknowledgedOpen, setAcknowledgedOpen] = useState(false);
  const acknowledgedTriggerRef = useRef<HTMLButtonElement>(null);
  const toolbarFallbackRef = useRef<HTMLButtonElement>(null);
  const closeAcknowledged = useCallback(() => {
    setAcknowledgedOpen(false);
    window.requestAnimationFrame(() => {
      const trigger = acknowledgedTriggerRef.current;
      if (trigger?.isConnected) trigger.focus();
      else toolbarFallbackRef.current?.focus();
    });
  }, []);
  /** Какие строки развёрнуты. Живёт на странице, а не в таблице: разворот
      переживает пересортировку и смену фильтра. */
  const [openDetail, setOpenDetail] = useState<Set<string>>(new Set());
  const toggleDetail = useCallback((id: string) => {
    setOpenDetail((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const visible = useMemo(() => {
    const found = filterCastRows(rows, query, onlyUnassigned);
    // Пробы приходят на роль, поэтому и фильтр — по роли: показать те, на которые уже пробовались.
    const withAuditions = onlyAuditions ? found.filter((row) => auditionCounts.has(row.name)) : found;
    const withCrossing = onlyCrossing
      ? withAuditions.filter((row) => (intersectionsByRole.get(row.name) || []).some((item) => !item.acknowledged))
      : withAuditions;
    // Тот же `chapterMismatch`, что зажигает янтарь в колонке «Глав»: посчитай
    // здесь по-своему — и фильтр покажет не те строки, что подсвечены.
    const withMismatch = onlyMismatch ? withCrossing.filter((row) => chapterMismatch(row) !== null) : withCrossing;
    return sortCastRowsBy(withMismatch, sort?.key ?? null, sort?.dir ?? "asc");
  }, [rows, query, onlyUnassigned, onlyAuditions, auditionCounts, onlyCrossing, onlyMismatch, intersectionsByRole, sort]);
  const filtered = Boolean(query.trim()) || onlyUnassigned || onlyAuditions || onlyCrossing || onlyMismatch;

  /** Сброс всех фильтров разом. Тулбар и пустое состояние обязаны сбрасывать
      одно и то же: кнопка, снимающая половину фильтров, оставляет таблицу
      пустой ровно там, где её нажимают, чтобы увидеть строки. */
  const resetFilters = useCallback(() => {
    setQuery("");
    setOnlyUnassigned(false);
    setOnlyAuditions(false);
    setOnlyCrossing(false);
    setOnlyMismatch(false);
  }, []);

  const onSort = useCallback((key: CastSortKey) => {
    setSort((current) => {
      if (current?.key === key) return { key, dir: current.dir === "asc" ? "desc" : "asc" };
      return { key, dir: DEFAULT_DIR[key] };
    });
  }, []);

  // the table takes the rest of the viewport; the page itself does not scroll 30 screens
  const tableAnchor = useRef<HTMLDivElement>(null);
  const [tableTop, setTableTop] = useState(0);
  useLayoutEffect(() => {
    const el = tableAnchor.current;
    if (!el) return;
    const update = () => setTableTop(Math.round(el.getBoundingClientRect().top + window.scrollY));
    update();
    window.addEventListener("resize", update);
    // anything above the table (budget form, conflicts note, wrapped header) can change height
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    observer?.observe(el.parentElement ?? el);
    return () => {
      window.removeEventListener("resize", update);
      observer?.disconnect();
    };
  }, [budgetQuery.isLoading]);
  const maxHeight = tableTop ? `max(320px, calc(100vh - ${tableTop + 22}px))` : undefined;

  const invalidate = useCallback(
    () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["book-budget", bookId] }),
        queryClient.invalidateQueries({ queryKey: ["v2", "book-cast", bookId] }),
      ]),
    [queryClient, bookId],
  );

  const markSaving = (id: string, on: boolean) => {
    setSavingIds((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const characterMutation = useMutation({
    mutationFn: async ({ row, payload }: { row: CastRow; payload: SaveBudgetCharacterPayload; title: string }) => {
      markSaving(row.character_id, true);
      try {
        const result = await apiPostJson<SaveBudgetCharacterResponse>(`/api/budget/character/${encodeURIComponent(row.character_id)}`, payload);
        if (!result.ok) throw new Error(result.error || "The server returned no details.");
        return result;
      } finally {
        markSaving(row.character_id, false);
      }
    },
    onSuccess: async (result, { row, title }) => {
      const outvoted = describeVote(result.vote);
      const notice = [describeApproval(result.approval), outvoted].filter(Boolean).join(" ");
      pushToast({
        tone: outvoted || (result.approval && !result.approval.notified) ? "info" : "success",
        title: outvoted ? "Your vote was saved" : resolveActorToastTitle(title, result.approval),
        detail: notice ? `${row.name}. ${notice}` : row.name,
      });
      await invalidate();
    },
    onError: (error, { row }) => {
      pushToast({ tone: "error", title: `${row.name}: not saved`, detail: describeApiError(error, "Could not save the row.") });
    },
  });

  const bookBudgetMutation = useMutation({
    mutationFn: async ({ patch }: { patch: Partial<SaveBookBudgetPayload>; title: string; savingId?: string }) => {
      const current = budgetQuery.data?.budget;
      const payload: SaveBookBudgetPayload = {
        narrative_cost_rub: current?.narrative_cost_rub ?? 0,
        sound_engineer_cost_rub: current?.sound_engineer_cost_rub ?? 0,
        extra_cost_rub: current?.extra_cost_rub ?? 0,
        notes: current?.notes ?? "",
        narrator_actor_name: current?.narrator_actor_name ?? "",
        ...patch,
      };
      const result = await apiPostJson<SaveBookBudgetResponse>(`/api/budget/${encodeURIComponent(bookId)}`, payload);
      if (!result.ok) throw new Error(result.error || "The server returned no details.");
      return result;
    },
    onMutate: ({ savingId }) => {
      if (savingId) markSaving(savingId, true);
    },
    onSettled: (_result, _error, { savingId }) => {
      if (savingId) markSaving(savingId, false);
    },
    onSuccess: async (result, { title }) => {
      const notice = describeApproval(result.approval);
      pushToast({
        tone: result.approval && !result.approval.notified ? "info" : "success",
        title: resolveActorToastTitle(title, result.approval),
        detail: notice || undefined,
      });
      await invalidate();
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Budget not saved", detail: describeApiError(error, "Could not save the budget.") });
    },
  });

  const rateMutation = useMutation({
    mutationFn: async ({ rate, usdRate }: { rate: number; usdRate: number }) => {
      const result = await apiPostJson<{ ok: boolean; error?: string }>("/api/settings/rate", {
        default_rate_rub_per_min: rate,
        usd_rub_rate: usdRate,
      });
      if (!result.ok) throw new Error(result.error || "The server returned no details.");
      return result;
    },
    onSuccess: async () => {
      pushToast({ tone: "success", title: "Studio settings saved", detail: "Roles without a custom rate were recalculated." });
      await invalidate();
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Rate not saved", detail: describeApiError(error, "Could not save the rate.") });
    },
  });

  const autoFixMutation = useMutation({
    mutationFn: async (resolution: PaletteResolutionEntry[]) => {
      for (const entry of resolution) {
        await apiPostJson<SaveBudgetCharacterResponse>(`/api/budget/character/${encodeURIComponent(entry.row.character_id)}`, {
          character_color: normalizeHexColor(entry.palette.background, entry.row.character_color || "#13333B"),
          character_text_color: normalizeHexColor(entry.palette.text, entry.row.character_text_color || "#D7FFE9"),
          character_font_weight: entry.palette.weight,
          character_font_style: entry.palette.style,
        });
      }
      return resolution.length;
    },
    onSuccess: async (changed) => {
      setPaletteFix(false);
      if (changed > 0) {
        pushToast({ tone: "success", title: "Palette updated", detail: `Roles recolored: ${changed}.` });
      } else {
        pushToast({ tone: "info", title: "No alternatives found", detail: "No available contrasting colors for these roles. Choose a color manually using the swatch." });
      }
      await invalidate();
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Conflicts not resolved", detail: describeApiError(error, "Could not recolor the roles.") });
    },
  });

  const narratorRow = rows.find((row) => row.is_narrator);

  const onSaveActor = useCallback(
    (row: CastRow, actor: string) => {
      const title = actor ? "Voice actor assigned" : "Voice actor removed";
      if (row.is_narrator) {
        bookBudgetMutation.mutate({ patch: { narrator_actor_name: actor }, title, savingId: row.character_id });
        return;
      }
      characterMutation.mutate({ row, payload: { actor_name: actor }, title });
    },
    [bookBudgetMutation, characterMutation],
  );

  // План 2б: выбор актёра из списка дикторов с ▶ демо; сохранение — тем же onSaveActor.
  const myRoles = meQuery.data?.roles ?? [];
  const meAdmin = myRoles.includes("admin") || Boolean(meQuery.data?.is_owner_telegram);
  const meEditor = meAdmin || myRoles.includes("author");
  const pickerQuery = useQuery({
    queryKey: ["dictors-picker"],
    queryFn: () => apiGet<{ items: PickerDictor[]; can_listen: boolean }>("/api/dictors/picker"),
    enabled: meEditor || isAgent,
    staleTime: 60_000,
  });
  const authConfig = useQuery({
    queryKey: ["public-auth-config"],
    queryFn: () => apiGet<PublicAuthConfigResponse>("/api/public/auth-config"),
    retry: false,
  });
  const [recastRow, setRecastRow] = useState<CastRow | null>(null);
  const bookTitle = budgetQuery.data?.book_title || "";

  const offerInvite = useCallback(
    async (row: CastRow, chosen: PickerDictor) => {
      if (chosen.reachable === true) return;
      // Бота в этой установке нет — звать некуда, письма о ролях дикторы не получат.
      if (!authConfig.data?.telegram_bot_username) return;
      if (!chosen.has_telegram) {
        pushToast({ tone: "info", title: `${chosen.name}: Telegram is not linked`, detail: "The bot cannot deliver the message. Contact them directly.", durationMs: 9000 });
        return;
      }
      try {
        await navigator.clipboard.writeText(inviteText(authConfig.data?.telegram_bot_username || "", authConfig.data?.studio_name || "", { role: row.name, book: bookTitle }));
        pushToast({ tone: "info", title: `${chosen.name} cannot be reached by the bot yet`, detail: "Role invitation copied. Send it in a private chat.", durationMs: 9000 });
      } catch {
        pushToast({ tone: "info", title: `${chosen.name} cannot be reached by the bot yet`, detail: "Invite them from Voice actors → Copy invitation.", durationMs: 9000 });
      }
    },
    [authConfig.data, bookTitle, pushToast],
  );

  const actorPicker: ActorPickerConfig | undefined = pickerQuery.data
    ? {
        allRows: rows,
        dictors: pickerQuery.data.items,
        canListen: pickerQuery.data.can_listen,
        proposeOnly: isAgent,
        canCreate: meAdmin,
        canRecast: (row) => meEditor && !row.is_narrator && Boolean(row.actor_name) && !row.actor_name.trim().endsWith("?"),
        onPick: (row, value, chosen) => {
          onSaveActor(row, value);
          // Приглашение «зовём на роль» — только при утверждении; проба и предложение агента
          // ещё не решение (ревью 02.10).
          if (value && chosen && !value.trim().endsWith("?")) void offerInvite(row, chosen);
        },
        onCreate: async (row, name) => {
          try {
            await apiPostJson("/api/dictors", { name });
            await queryClient.invalidateQueries({ queryKey: ["dictors-picker"] });
            onSaveActor(row, name);
            pushToast({ tone: "success", title: `Voice actor created: ${name}`, detail: "Manage their password and Telegram in Voice actors." });
          } catch (error) {
            pushToast({ tone: "error", title: "Voice actor not created", detail: describeApiError(error, "") });
          }
        },
        onRecast: (row) => setRecastRow(row),
      }
    : undefined;

  const onSaveRate = useCallback(
    (row: CastRow, rate: number) => {
      characterMutation.mutate({ row, payload: { manual_rate_rub_per_min: rate }, title: "Rate saved" });
    },
    [characterMutation],
  );

  const onSaveNarratorFixed = useCallback(
    (fixed: number) => {
      bookBudgetMutation.mutate({ patch: { narrative_cost_rub: fixed }, title: "Narrator fee saved", savingId: narratorRow?.character_id });
    },
    [bookBudgetMutation, narratorRow?.character_id],
  );

  const renameMutation = useMutation({
    mutationFn: async ({ row, next, moveLines }: { row: CastRow; next: string; moveLines: boolean }) => {
      markSaving(row.character_id, true);
      try {
        return await apiPostJson<{ ok: boolean; lines: number; segments: number }>(
          `/api/v2/books/${encodeURIComponent(bookId)}/character-map/rename`,
          { name: row.name, new_name: next, move_lines: moveLines },
        );
      } finally {
        markSaving(row.character_id, false);
      }
    },
    onSuccess: async (result, { row, next }) => {
      pushToast({
        tone: "success",
        title: "Role renamed",
        detail: result.lines
          ? `«${row.name}» → «${next}». Lines moved: ${result.lines}.`
          : `«${row.name}» → «${next}».`,
      });
      // Переименование переписывает разметку по всем главам — того же класса
      // операция, что слияние (`useCharacterMapActions.refresh(true)`), и кэш
      // читалки обязана сбрасывать так же: иначе открытая глава продолжит
      // показывать старого спикера.
      await Promise.all([invalidate(), invalidateMap(), invalidateReader(queryClient, bookId, ["chapters", "cast"])]);
    },
  });

  /**
   * Переименование говорящей роли спрашивает согласие: первый заход идёт без
   * него и у говорящей роли получает `needs_consent` (400, `ApiError.errorCode`
   * — так `apiPostJson`/`throwApiError` сообщают код отказа, см. `api/client.ts`).
   * Только тогда экран называет цену — сколько реплик и в скольких главах
   * переедет — и спрашивает. Молчаливое переименование говорящей роли и
   * развело карту с разметкой в самом начале, поэтому обойти этот путь нельзя.
   *
   * Записи диктора требуют согласия САМИ ПО СЕБЕ, отдельно от реплик. Согласие у
   * сервера просит только говорящая роль, а осиротеть записи могут и у молчащей:
   * на «Карте» такая строка есть — «Дракон», реплик ноль, запись есть. После
   * переименования `AudioFile.role` остаётся строкой со старым именем, строка
   * приходит с пустым `character_id`, и объединённая таблица её не показывает
   * вовсе — проба исчезает из интерфейса. Поэтому у молчащей роли с записями
   * вопрос задаёт сам экран, до всякого запроса.
   */
  const onRename = useCallback(
    async (row: CastRow, next: string) => {
      if (!row.lines_count && row.has_audio) {
        const ok = window.confirm(
          `Rename «${row.name}» to «${next}»?\n\n${audioStaysBehindNote(row.name, "Rename")}`,
        );
        if (!ok) return;
      }
      try {
        await renameMutation.mutateAsync({ row, next, moveLines: false });
      } catch (error) {
        if (!(error instanceof ApiError) || error.errorCode !== "needs_consent") {
          pushToast({ tone: "error", title: "Not renamed", detail: describeRenameError(error, "The server rejected the request.") });
          return;
        }
        const chapters = (row.appears_in_chapters || []).length;
        // Про записи диктора спрашивающий обязан знать до «да», а не после:
        // `AudioFile.role` — строка, переименование её не трогает, и дубли
        // остаются на старом имени.
        const audioWarning = row.has_audio ? `\n\n${audioStaysBehindNote(row.name, "Rename")}` : "";
        const ok = window.confirm(
          `Role «${row.name}» ${row.lines_count} ${pluralRu(row.lines_count, "line", "lines", "lines")}`
            + ` in ${chapters} ${pluralRu(chapters, "chapter", "chapters", "chapters")}.\n\n`
            + `Move them to «${next}»? A new annotation version will be created; you can undo it.`
            + audioWarning,
        );
        if (!ok) return;
        try {
          await renameMutation.mutateAsync({ row, next, moveLines: true });
        } catch (secondError) {
          pushToast({ tone: "error", title: "Not renamed", detail: describeRenameError(secondError, "The server rejected the request.") });
        }
      }
    },
    [renameMutation, pushToast],
  );

  // The note is a v2 field on the character, not a budget number: it goes through
  // its own endpoint and refreshes the same cast query the table reads.
  const aboutMutation = useMutation({
    mutationFn: ({ row, patch }: { row: CastRow; patch: CharacterAbout }) => saveCharacterAbout(row.character_id, patch),
    onSuccess: () => {
      pushToast({ tone: "success", title: "Saved", detail: "The voice actor sees this in the chapter cast and their lines." });
      void queryClient.invalidateQueries({ queryKey: ["book-budget", bookId] });
      void queryClient.invalidateQueries({ queryKey: ["v2", "book-cast", bookId] });
      // Возраст живёт только в ответе карты (`mergeRoleFacts`) — без этой строки
      // сохранённое значение не попадёт в строку до следующей полной перезагрузки.
      void invalidateMap();
    },
    onError: (error) => pushToast({ tone: "error", title: "Not saved", detail: describeApiError(error, "") }),
  });
  const onSaveAbout = useCallback(
    (row: CastRow, patch: CharacterAbout) => {
      const [key, next] = Object.entries(patch)[0] as [keyof CharacterAbout, string];
      const current = key === "note" ? row.note || "" : String(row[key] || "");
      if (current === next) return;
      aboutMutation.mutate({ row, patch });
    },
    [aboutMutation],
  );

  const onSwatchClick = useCallback((row: CastRow) => setPaletteRow(row), []);
  const onAuditions = useCallback((role: string) => setAuditionsRole(role), []);

  const acknowledgeMutation = useMutation({
    mutationFn: ({ row, partner, reason }: { row: CastRow; partner: string; reason: string }) =>
      apiPostJson<CharacterMapAcknowledgeResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/character-map/acknowledge`,
                  { role_a: row.name, role_b: partner, reason }),
    onSuccess: async () => {
      pushToast({ tone: "success", title: "Overlap accepted", detail: "This overlap will no longer be flagged." });
      await invalidateMap();
    },
    onError: (error) => pushToast({ tone: "error", title: "Not saved", detail: describeApiError(error, "Could not save the reason.") }),
  });
  const onAcknowledge = useCallback(
    (row: CastRow, partner: string, reason: string) => acknowledgeMutation.mutate({ row, partner, reason }),
    [acknowledgeMutation],
  );

  // Признанные пары не попадают в развёртку строки (там только непризнанные,
  // см. `CastTable`) — без отдельного списка причина «так задумано» терялась
  // бы совсем, а отменить решение было бы негде. Перенесено с отдельного
  // экрана карты персонажей без изменений в логике.
  const acknowledgedPairs = useMemo(
    () => (mapQuery.data?.intersections ?? []).filter((item) => item.acknowledged),
    [mapQuery.data],
  );
  const forgetMutation = useMutation({
    mutationFn: async (pair: { role_a: string; role_b: string }) => {
      const result = await apiPostJson<CharacterMapForgetResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/character-map/forget`, pair);
      if (!result.ok) throw new Error(result.error || "The server returned no details.");
      return result;
    },
    onSuccess: async () => {
      pushToast({ tone: "info", title: "Decision reverted", detail: "The overlap warning will return to the list." });
      await invalidateMap();
    },
    onError: (error) => pushToast({ tone: "error", title: "Not reverted", detail: describeApiError(error, "The server did not respond.") }),
  });
  const onForget = useCallback(
    (pair: { role_a: string; role_b: string }) => forgetMutation.mutate(pair),
    [forgetMutation],
  );
  useEffect(() => {
    if (acknowledgedOpen && acknowledgedPairs.length === 0) closeAcknowledged();
  }, [acknowledgedOpen, acknowledgedPairs.length, closeAcknowledged]);

  // Отметка «автор проверил карту» — про книгу целиком, не про отдельную роль:
  // правка любого поля карты её не снимает, это то же поведение, что было на
  // отдельном экране карты персонажей (см. `api_v2_character_map_checked`).
  const markChecked = useMutation({
    mutationFn: async () => {
      const result = await apiPostJson<CharacterMapCheckedResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/character-map/checked`, {});
      if (!result.ok) throw new Error(result.error || "The server returned no details.");
      return result;
    },
    onSuccess: async () => {
      pushToast({ tone: "success", title: "Cast marked as reviewed" });
      await invalidateMap();
    },
    onError: (error) => pushToast({ tone: "error", title: "Not marked", detail: describeApiError(error, "The server did not respond.") }),
  });

  // Слить / удалить — те же коды отказа, что были на отдельном экране карты
  // персонажей (`useCharacterMapActions`); развёртка строки — их новый дом.
  const [mergeRow, setMergeRow] = useState<CastRow | null>(null);
  const onMerge = useCallback((row: CastRow) => setMergeRow(row), []);
  const onDelete = useCallback(
    (row: CastRow) => {
      // Снимок утраченной строки `delete_character` пишет в журнал вмешательств —
      // значит «отката не будет» было неправдой. Из таблицы отката и вправду нет,
      // так это и формулирует лист слияния рядом.
      if (!window.confirm(`Delete role «${row.name}» from the cast? This cannot be undone from the table.`)) return;
      cardActions.deleteRow.mutate(row.name, { onSuccess: () => void invalidateMap() });
    },
    [cardActions, invalidateMap],
  );

  const title = budgetQuery.data?.book_title || "";
  const loading = budgetQuery.isLoading;

  return (
    <div className="cast">
      <PageHeader
        back={{ to: `/books/${encodeURIComponent(bookId)}`, label: "Back to book" }}
        title={loading ? "Cast" : `Cast · ${title}`}
        actions={
          <>
            <CastCheckedControl
              checkedAt={mapQuery.data?.checked_at || ""}
              checkedBy={mapQuery.data?.checked_by || ""}
              onCheck={() => markChecked.mutate()}
              pending={markChecked.isPending}
            />
            {conflicts.length ? (
              <Button variant="secondary" onClick={() => setPaletteFix(true)}>
                Adjust colors
              </Button>
            ) : null}
            <label className="cast-search">
              <Icon name="search" />
              <input
                type="search"
                value={query}
                placeholder="Role, alias or voice actor"
                aria-label="Find role, alias or voice actor"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          </>
        }
      />

      <CastBudgetLine
        budget={budgetQuery.data}
        summaryText={formatCastSubtitle(summary)}
        saving={bookBudgetMutation.isPending}
        onSave={(next) => bookBudgetMutation.mutateAsync({ patch: next, title: "Profiles saved" })}
        rate={budgetQuery.data?.default_rate_rub_per_min || DEFAULT_RATE_FALLBACK}
        usdRate={budgetQuery.data?.usd_rub_rate || 0}
        savingRate={rateMutation.isPending}
        onSaveRate={(next) => rateMutation.mutateAsync(next)}
      />

      <div className="cast-toolbar">
        {/* Каст у каждой книги свой: книгу меняют здесь, рядом с фильтрами, а адрес
            остаётся адресом книги — ссылку на каст можно переслать. */}
        <label className="cast-book-pick">
          <Icon name="books" />
          <select
            value={bookId}
            aria-label="Book shown in the cast"
            onChange={(event) => navigate(`/books/${encodeURIComponent(event.target.value)}/cast`)}
          >
            {(booksQuery.data?.items ?? []).some((book) => book.id === bookId) ? null : (
              <option value={bookId}>{loading ? "…" : title}</option>
            )}
            {(booksQuery.data?.items ?? []).map((book) => (
              <option key={book.id} value={book.id}>{book.display_title || book.title}</option>
            ))}
          </select>
        </label>
        <Button
          ref={toolbarFallbackRef}
          size="sm"
          variant="secondary"
          className="cast-toggle"
          aria-pressed={onlyUnassigned}
          icon={<Icon name="filter" />}
          onClick={() => setOnlyUnassigned((v) => !v)}
        >
          Unassigned only
        </Button>
        <Button
          size="sm"
          variant="secondary"
          className="cast-toggle"
          aria-pressed={onlyAuditions}
          disabled={auditionCounts.size === 0}
          icon={<Icon name="mic" />}
          title={auditionCounts.size ? undefined : "No auditions submitted for this book yet"}
          onClick={() => setOnlyAuditions((v) => !v)}
        >
          {auditionCounts.size ? `Auditions · ${auditionCounts.size}` : "Auditions"}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          className="cast-toggle"
          aria-pressed={onlyCrossing}
          disabled={crossingCount === 0}
          icon={<Icon name="flag" />}
          title={crossingCount ? undefined : "No overlapping roles assigned to the same voice actor"}
          onClick={() => setOnlyCrossing((v) => !v)}
        >
          {crossingCount ? `Overlaps · ${crossingCount}` : "Overlaps"}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          className="cast-toggle"
          aria-pressed={onlyMismatch}
          icon={<Icon name="filter" />}
          title="Roles whose chapter counts differ between annotation and extraction"
          onClick={() => setOnlyMismatch((v) => !v)}
        >
          Chapter mismatches
        </Button>
        {acknowledgedPairs.length > 0 ? (
          <Button
            ref={acknowledgedTriggerRef}
            size="sm"
            variant="secondary"
            className="cast-toggle"
            aria-haspopup="dialog"
            aria-expanded={acknowledgedOpen}
            icon={<Icon name="check" />}
            onClick={() => setAcknowledgedOpen(true)}
          >
            Accepted · {acknowledgedPairs.length}
          </Button>
        ) : null}
        {!loading && filtered ? (
          <span className="cast-toolbar-count num" aria-live="polite">
            {visible.length} of {rows.length}
          </span>
        ) : null}
        {filtered ? (
          <Button size="sm" variant="ghost" onClick={resetFilters}>
            Clear
          </Button>
        ) : null}
      </div>

      <CastConflictsNote conflicts={conflicts} onFix={() => setPaletteFix(true)} />
      {paletteFix ? (
        <PaletteFixSheet
          rows={voicedRows}
          conflicts={conflicts}
          applying={autoFixMutation.isPending}
          onApply={(resolution) => autoFixMutation.mutate(resolution)}
          onClose={() => setPaletteFix(false)}
        />
      ) : null}

      <div ref={tableAnchor} />
      {budgetQuery.isError ? (
        <div className="ui-note ui-note--error cast-error" role="alert">
          <span>Could not load cast: {describeApiError(budgetQuery.error, "the server did not respond")}.</span>
          <Button size="sm" variant="secondary" onClick={() => budgetQuery.refetch()} loading={budgetQuery.isFetching}>
            Retry
          </Button>
        </div>
      ) : (
        <CastTable
          rows={visible}
          actorPicker={actorPicker}
          bookId={bookId}
          auditionCounts={auditionCounts}
          auditionBest={auditionBest}
          onAuditions={onAuditions}
          intersectionsByRole={intersectionsByRole}
          openDetail={openDetail}
          onToggleDetail={toggleDetail}
          loading={loading}
          actorNames={actorNames}
          sort={sort}
          onSort={onSort}
          onSwatchClick={onSwatchClick}
          onSaveActor={onSaveActor}
          /* Рассказчик живёт не в карточке персонажа, а в смете книги, и голосов у него нет:
             предварительному назначению там не на чём держаться, а запись агента затёрла бы
             решение владельца молча. Одна роль из двухсот трёх — и та, которую владелец
             выбирает сам. */
          canAssignActor={(row) => !isAgent || !row.is_narrator}
          onSaveRate={onSaveRate}
          onRename={onRename}
          onSaveAbout={onSaveAbout}
          defaultRate={budgetQuery.data?.default_rate_rub_per_min || DEFAULT_RATE_FALLBACK}
        onSaveNarratorFixed={onSaveNarratorFixed}
          onAcknowledge={onAcknowledge}
          onMerge={onMerge}
          onDelete={onDelete}
          savingIds={savingIds}
          maxHeight={maxHeight}
          empty={
            filtered ? (
              <EmptyState
                icon="search"
                text="No roles match your search."
                action={
                  <Button variant="secondary" onClick={resetFilters}>
                    Show all roles
                  </Button>
                }
              />
            ) : (
              <EmptyState icon="cast" text="No cast yet. Prepare the book to extract its characters." />
            )
          }
        />
      )}

      {recastRow && actorPicker ? (
        <RecastDialog
          row={recastRow}
          dictors={actorPicker.dictors}
          onClose={() => setRecastRow(null)}
          onDone={(summary) => {
            setRecastRow(null);
            pushToast({ tone: "success", title: `Reassigned: «${recastRow.name}»`, detail: summary, durationMs: 9000 });
            void invalidate();
            void queryClient.invalidateQueries({ queryKey: ["dictors-picker"] });
          }}
        />
      ) : null}
      {auditionsRole ? (
        <AuditionsSheet
          role={auditionsRole}
          pairs={auditionsByRole.get(auditionsRole) ?? []}
          onAssign={
            auditionsQuery.data?.can_approve
              ? (actor) => {
                  const row = rows.find((item) => item.name === auditionsRole);
                  if (row) onSaveActor(row, actor);
                  setAuditionsRole("");
                }
              : undefined
          }
          canDeleteAny={auditionsQuery.data?.can_approve}
          canDeleteOwn={!isAgent}
          myDisplayName={meQuery.data?.display_name || ""}
          deletingId={deleteAuditionMutation.isPending ? deleteAuditionMutation.variables : undefined}
          /* `mutate`, а не `void mutateAsync`: отказ разобран в `onError`, а
             отброшенный промис давал бы unhandled rejection. */
          onDelete={(item) => deleteAuditionMutation.mutate(item.id)}
          canScore={auditionsQuery.data?.can_score ?? []}
          showRejection={auditionsQuery.data?.can_approve}
          scoringKey={scoreMutation.isPending ? scoreMutation.variables?.pair.key : undefined}
          onScore={(pair, lane, value, answer) => scoreMutation.mutate({ pair, lane, value, answer })}
          onClose={() => setAuditionsRole("")}
        />
      ) : null}

      {paletteRow ? <CastPaletteHost bookId={bookId} row={paletteRow} allRows={rows} onClose={() => setPaletteRow(null)} /> : null}

      {acknowledgedOpen ? (
        <Sheet
          title={`Accepted overlaps — ${acknowledgedPairs.length}`}
          subtitle="Role pairs approved by the author for the same voice actor."
          onClose={closeAcknowledged}
        >
          <AcknowledgedPairsList
            pairs={acknowledgedPairs}
            onForget={onForget}
            forgetPending={forgetMutation.isPending}
            forgetVariables={forgetMutation.variables}
          />
        </Sheet>
      ) : null}

      {mergeRow ? (
        <RoleMergeSheet
          source={mergeRow}
          rows={rows}
          pending={cardActions.merge.isPending}
          onMerge={(target) =>
            cardActions.merge.mutate(
              { source: mergeRow.name, target },
              { onSuccess: (result) => { void invalidateMap(); if (result.ok) setMergeRow(null); } },
            )
          }
          onClose={() => setMergeRow(null)}
        />
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------------- */

type CastCheckedControlProps = { checkedAt: string; checkedBy: string; onCheck: () => void; pending: boolean };

/** «Автор проверил карту целиком» — отметка книги, а не строки: правка любого
    поля её не снимает. Перенесена без изменений с отдельного экрана карты
    персонажей — он исчезает, отметка остаётся тут. */
function CastCheckedControl({ checkedAt, checkedBy, onCheck, pending }: CastCheckedControlProps) {
  const title = checkedAt
    ? `Cast reviewed by author: ${checkedBy || "unsigned"}, ${formatWhen(checkedAt)}`
    : "The author has not reviewed the full cast yet.";
  return (
    <div className="cast-check-control">
      <span
        className={["cast-check-status", checkedAt ? "is-checked" : "is-unchecked"].join(" ")}
        title={title}
        aria-live="polite"
      >
        <Icon name={checkedAt ? "check" : "flag"} />
        {checkedAt ? "Cast reviewed" : "Cast not reviewed"}
      </span>
      <Button size="sm" variant={checkedAt ? "ghost" : "primary"} loading={pending} onClick={onCheck}>
        {checkedAt ? "Refresh" : "Mark as reviewed"}
      </Button>
    </div>
  );
}

type AcknowledgedPairsListProps = {
  pairs: RoleIntersection[];
  onForget: (pair: { role_a: string; role_b: string }) => void;
  forgetPending: boolean;
  forgetVariables: { role_a: string; role_b: string } | undefined;
};

/** Признанные пересечения живут в листе по компактной кнопке тулбара: причина
    «так задумано» и возможность отменить решение остаются доступны, но не
    забирают высоту у двухсот строк каста. */
function AcknowledgedPairsList({ pairs, onForget, forgetPending, forgetVariables }: AcknowledgedPairsListProps) {
  return (
    <ul className="cast-ack-list">
      {pairs.map((item) => {
        const isPending = forgetPending && forgetVariables?.role_a === item.role_a && forgetVariables?.role_b === item.role_b;
        return (
          <li key={`${item.role_a}::${item.role_b}`} className="cast-ack-item">
            <div className="cast-ack-pair">«{item.role_a}» and «{item.role_b}» <span className="cast-muted">— {item.actor}</span></div>
            <div className="cast-ack-reason">{item.reason || "no reason given"}</div>
            <span className="cast-muted cast-ack-source">
              {item.acknowledged_by || "unsigned"}{item.acknowledged_at ? `, ${formatWhen(item.acknowledged_at)}` : ""}
            </span>
            <Button size="sm" variant="ghost" loading={isPending} onClick={() => onForget({ role_a: item.role_a, role_b: item.role_b })}>
              Remove decision
            </Button>
          </li>
        );
      })}
    </ul>
  );
}

type RoleMergeSheetProps = {
  source: CastRow;
  rows: CastRow[];
  pending: boolean;
  onMerge: (target: string) => void;
  onClose: () => void;
};

/**
 * Слияние переписывает разметку по всем главам и убирает исходную роль из
 * карты безвозвратно — отката из таблицы нет. Выбор цели сам слияние не
 * запускает: сначала цена («сколько реплик и из скольких глав переедет»), и
 * только вторым явным нажатием на опасную кнопку слияние происходит. Тот же
 * арм-и-подтверди шаг, что у удаления роли рядом.
 */
function RoleMergeSheet({ source, rows, pending, onMerge, onClose }: RoleMergeSheetProps) {
  const targets = useMemo(
    () => rows.filter((row) => row.character_id !== source.character_id && !row.is_narrator),
    [rows, source.character_id],
  );
  const [targetName, setTargetName] = useState("");
  const [confirmMerge, setConfirmMerge] = useState(false);
  const chapters = (source.appears_in_chapters || []).length;

  const chooseTarget = (value: string) => {
    setTargetName(value);
    setConfirmMerge(false);
  };

  return (
    <Sheet title={`Merge «${source.name}»`} subtitle="Lines will move to the selected role; the original role will be removed" onClose={onClose}>
      <p className="cast-merge-cost">
        Lines to move: <strong>{source.lines_count.toLocaleString("en-US")}</strong> {pluralRu(source.lines_count, "line", "lines", "lines")}{" "}
        of <strong>{chapters.toLocaleString("en-US")}</strong> {pluralRu(chapters, "chapter", "chapters", "chapters")}.
      </p>
      {source.actor_name ? (
        <p className="ui-note ui-note--error">
          «{source.name}» is assigned to voice actor «{source.actor_name}». Merging will remove this role and its assignment.
        </p>
      ) : null}
      {source.has_audio ? (
        <p className="ui-note ui-note--error">{audioStaysBehindNote(source.name, "Merge")}</p>
      ) : null}
      {targets.length === 0 ? (
        <p className="cast-muted">No eligible roles to merge into.</p>
      ) : (
        <Field label="Merge into">
          {(props) => (
            <select {...props} className="ui-input" value={targetName} onChange={(event) => chooseTarget(event.target.value)}>
              <option value="">choose a role…</option>
              {targets.map((row) => <option key={row.character_id} value={row.name}>{row.name}</option>)}
            </select>
          )}
        </Field>
      )}
      {targetName && confirmMerge ? (
        <p className="ui-note ui-note--error">
          Moving {source.lines_count.toLocaleString("en-US")} {pluralRu(source.lines_count, "line", "lines", "lines")}{" "}
          of {chapters.toLocaleString("en-US")} {pluralRu(chapters, "chapter", "chapters", "chapters")} to «{targetName}», role «{source.name}» will be removed. This cannot be undone from the table.
        </p>
      ) : null}
      <div className="cast-merge-actions">
        {targetName && confirmMerge ? (
          <>
            <Button variant="danger" loading={pending} onClick={() => onMerge(targetName)}>
              Yes, merge into «{targetName}»
            </Button>
            <Button variant="ghost" disabled={pending} onClick={() => setConfirmMerge(false)}>Cancel</Button>
          </>
        ) : (
          <>
            <Button variant="primary" disabled={!targetName} onClick={() => setConfirmMerge(true)}>
              {targetName ? `Merge into «${targetName}»` : "Merge"}
            </Button>
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
          </>
        )}
      </div>
    </Sheet>
  );
}
