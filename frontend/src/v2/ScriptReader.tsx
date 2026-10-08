/**
 * The actor's reading view: one chapter as continuous prose with role tints,
 * a sticky cast legend, "my role" folding, stress marks and print.
 *
 * When the payload says `can_edit` (operator / author) the same view grows an
 * editor layer: clickable words → stress constructor, the stress queue, palette
 * editing from the legend, and the book profile sheet. Dictors never see it.
 *
 * `ScriptReaderPage` is the `/reader/:chapterId` route. `ScriptReader` is the
 * same view as a component, so a host page (Запись) can embed it, route chapter
 * changes itself and preselect «моя роль».
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from "react";
import { markFirstStepsSeen } from "../utils/firstStepsSeen";
import { Link, Navigate, useNavigate, useParams, useSearchParams, type NavigateFunction } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { useToast } from "../components/ToastProvider";
import { SkeletonPanel } from "../components/Skeleton";
import { selectionInside, spansForRange, type TextRange } from "./attribution";
import { reassignImpactToast } from "./consilium";
import { CastLegend, CastLegendPrint } from "./CastLegend";
import { ChapterRail } from "./ChapterRail";
import { LegendConflicts, LegendEditor } from "./LegendEditor";
import { Paragraph } from "./Paragraph";
import { ProfilePanel } from "./ProfilePanel";
import { ReaderToolbar } from "./ReaderToolbar";
import { Sheet } from "../ui";
import { SourcePane } from "./SourcePane";
import { SceneBand, TransitionLine } from "./SoundLayer";
import { bySegment, sceneNumbers } from "./sound";
import { describeSoundError, isSoundRunActive, soundKeys, useSoundBook, useSoundChapter } from "./soundApi";
import { SoundCard, type SoundPick } from "./SoundCard";
import { SoundPlaces } from "./SoundPlaces";
import { LorePanel } from "./LorePanel";
import { useLore } from "./loreApi";
import { ConsiliumCard } from "./ConsiliumCard";
import { RolePicker } from "./RolePicker";
import { StressConstructor } from "./StressConstructor";
import { ConsiliumQueue } from "./ConsiliumQueue";
import { DisputedQueue } from "./DisputedQueue";
import { StressQueue } from "./StressQueue";
import {
  approveChapter,
  describeReassignError,
  editorKeys,
  invalidateReader,
  reassignSegment,
  useBookCast,
  useConsilium,
  useDisputed,
  useStressQueue,
} from "./editorApi";
import { planVisibility } from "./plan";
import { useEditorState } from "./useEditorState";
import { plural, useIsMobile } from "./useIsMobile";
import { RoleFinder } from "./RoleFinder";
import { useReaderPrefs } from "./useReaderPrefs";
import { useRoleCursor } from "./useRoleCursor";
import { roleScriptHref, type RoleScriptBase } from "./roleRoutes";
import { useSplitView } from "./useSplitView";
import { NARRATOR, type ReaderChapterPayload, type ReaderSegment, type RoleStyle, type SoundMarker, type SoundPlace } from "./types";
import "./ScriptReader.css";

export { ReaderEntryPage } from "./ReaderEntryPage";

const NO_CHAPTERS: ReaderChapterPayload["chapters"] = [];
const NO_SEGMENTS: ReaderSegment[] = [];
const NO_ROLES: readonly string[] = [];
const NO_SOUND = new Map<string, SoundMarker[]>();
/** сколько места справа от страницы нужно метке звука с описанием (160px + отступ) */
const SOUND_MARGIN_PX = 184;

function GapRow({ count, onExpand }: { count: number; onExpand: () => void }) {
  return (
    <button type="button" className="v2r-gap" onClick={onExpand}>
      … пропущено {count} {plural(count, "абзац", "абзаца", "абзацев")} — показать …
    </button>
  );
}

const REMARK_PREVIEW_CHARS = 110;

/** Flattened, capped preview of a reassigned fragment — same shape as the role picker's toast detail. */
function remarkPreview(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > REMARK_PREVIEW_CHARS ? `${flat.slice(0, REMARK_PREVIEW_CHARS - 1)}…` : flat;
}

/**
 * Floating «→ Рассказчику» button over a text selection inside an editable
 * paragraph — for remarks the underline heuristic missed (see the remark
 * picker brief, §5). Positioned from the live selection's own bounding box, so
 * it tracks whatever the operator actually highlighted.
 */
function RemarkSelectionButton({ rect, pending, onPick }: { rect: DOMRect; pending: boolean; onPick: () => void }) {
  const style: CSSProperties = {
    position: "fixed",
    top: Math.max(8, rect.top - 38),
    left: Math.min(Math.max(rect.left + rect.width / 2, 60), window.innerWidth - 60),
    transform: "translateX(-50%)",
  };
  return (
    <button
      type="button"
      className="btn btn-sm v2r-btn v2r-remark-btn"
      style={style}
      disabled={pending}
      // keep the selection alive across the click — a mousedown on anything else collapses it first
      onMouseDown={(event) => event.preventDefault()}
      onClick={onPick}
    >
      → Рассказчику
    </button>
  );
}

export type ScriptReaderProps = {
  chapterId: string;
  /**
   * Embedded hosts route chapter changes themselves: the rail, prev/next and the
   * queue's cross-chapter jumps all go through here instead of `/reader/:id`.
   */
  onChapterChange?: (chapterId: string) => void;
  /**
   * No route-level chrome assumptions (no «К списку книг» links); adds `v2r--embedded`
   * so the host's stylesheet can trim the toolbar to search / stress / font.
   */
  embedded?: boolean;
  /** roles to preselect in the legend («моя роль») once the chapter loads; re-applied when the list changes */
  initialRoles?: readonly string[];
  /** only these chapters stay reachable from the rail and prev/next (e.g. the published ones) */
  allowedChapterIds?: ReadonlySet<string>;
  /** the loaded payload, for hosts that mirror the chapter title / cast */
  onLoaded?: (payload: ReaderChapterPayload) => void;
  /** open with the split view on or off, whatever the reader's own preference says */
  forceCompare?: boolean;
  /** one-shot route request to open the stress queue after permissions load */
  openStressQueue?: boolean;
  /** called after the one-shot stress request has been applied */
  onStressQueueOpened?: () => void;
  /** one-shot route request to open the consilium panel after permissions load */
  openConsilium?: boolean;
  /** called after the one-shot consilium request has been applied */
  onConsiliumOpened?: () => void;
  /** one-shot route request (`?sound=1` from the hub): switch the sound layer on and open «Места книги» */
  openSound?: boolean;
  /** called after the one-shot sound request has been applied */
  onSoundOpened?: () => void;
  /** role-wide links stay inside the host surface (Preparation by default, Recording when embedded there) */
  roleScriptBase?: RoleScriptBase;
  /** useful host query context to carry into a role-wide link */
  roleScriptSearch?: string;
};

const readerPath = (id: string) => `/reader/${encodeURIComponent(id)}`;

/** `/reader/:chapterId`, with `?compare=1` opening straight into the split view */
export function ScriptReaderPage() {
  const { chapterId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const asked = searchParams.get("compare");
  const openStressQueue = searchParams.get("stress") === "1";
  const clearStressRequest = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete("stress");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);
  const openConsilium = searchParams.get("consilium") === "1";
  const clearConsiliumRequest = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete("consilium");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);
  const openSound = searchParams.get("sound") === "1";
  const clearSoundRequest = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete("sound");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);
  if (!chapterId) return <Navigate to="/reader" replace />;
  return (
    <ScriptReader
      chapterId={chapterId}
      forceCompare={asked === null ? undefined : asked !== "0"}
      openStressQueue={openStressQueue}
      onStressQueueOpened={clearStressRequest}
      openConsilium={openConsilium}
      onConsiliumOpened={clearConsiliumRequest}
      openSound={openSound}
      onSoundOpened={clearSoundRequest}
    />
  );
}

export function ScriptReader({
  chapterId,
  onChapterChange,
  embedded = false,
  initialRoles = NO_ROLES,
  allowedChapterIds,
  onLoaded,
  forceCompare,
  openStressQueue = false,
  onStressQueueOpened,
  openConsilium = false,
  onConsiliumOpened,
  openSound = false,
  onSoundOpened,
  roleScriptBase = "/reader/role",
  roleScriptSearch = "",
}: ScriptReaderProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const isMobile = useIsMobile();
  const [prefs, updatePrefs] = useReaderPrefs();
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(() => new Set());
  const [mobileRail, setMobileRail] = useState(false);
  const [findRole, setFindRole] = useState(false);
  // on a phone the cast is a sheet opened on demand; on a wide screen it is a column
  // that stays where the reader left it
  const [castSheet, setCastSheet] = useState(false);
  // free-selection «→ Рассказчику» (remark picker §5) — declared early so the
  // chapter-change reset below can clear it; the effect that fills it lives
  // further down, next to the editor state it depends on
  const [remarkSelection, setRemarkSelection] = useState<{ segmentId: string; range: TextRange; rect: DOMRect } | null>(null);
  // звуковой слой: открытый маркер (его лист — `editor.sheet === "sound"`) и поле справа от страницы
  const [soundMarkerId, setSoundMarkerId] = useState<string | null>(null);
  const [pageEl, setPageEl] = useState<HTMLElement | null>(null);
  const [soundRoomy, setSoundRoomy] = useState(false);
  // режим «выберите абзац»: перенос сцены, постановка потерянного маркера, звук к абзацу
  const [soundPick, setSoundPick] = useState<SoundPick | null>(null);

  // The split view is desktop work: on a phone there is no room for two columns,
  // so the toggle is hidden and the reader falls back to the script alone.
  // A link may ask for the split view («Сверить с оригиналом» in the hub); from then
  // on it is the reader's own preference again.
  useEffect(() => {
    if (forceCompare !== undefined) updatePrefs({ compare: forceCompare });
  }, [forceCompare, updatePrefs]);

  const chapterQuery = useQuery({
    queryKey: ["v2", "chapter-script", chapterId],
    queryFn: () => apiGet<ReaderChapterPayload>(`/api/v2/chapters/${encodeURIComponent(chapterId)}/script`),
    enabled: Boolean(chapterId),
  });

  useEffect(() => {
    window.scrollTo(0, 0);
    setExpanded(new Set());
    setQuery("");
    setMobileRail(false);
    setCastSheet(false);
    setRemarkSelection(null);
    setSoundMarkerId(null);
    setSoundPick(null);
  }, [chapterId]);

  const data = chapterQuery.data;
  const segments = data?.segments ?? NO_SEGMENTS;
  const allChapters = data?.chapters ?? NO_CHAPTERS;
  const chapters = useMemo(
    () => (allowedChapterIds ? allChapters.filter((chapter) => allowedChapterIds.has(chapter.id)) : allChapters),
    [allChapters, allowedChapterIds],
  );
  const bookId = data?.book.id ?? "";
  useEffect(() => { markFirstStepsSeen(bookId); }, [bookId]);
  // Лор — шпаргалка о мире книги: видят все, включая диктора. Кнопки нет, если у
  // книги нет автора или у автора не загружена энциклопедия (ручка отвечает пустым).
  const [loreOpen, setLoreOpen] = useState(false);
  const lore = useLore(bookId);
  const hasLore = Boolean(lore.data?.articles?.length);
  const canEdit = Boolean(data?.can_edit);
  /* A dictor sets stress and picks colours; he does not reassign roles, approve
     chapters or start runs. One flag used to gate all of it together. */
  const canVoice = Boolean(data?.can_voice ?? data?.can_edit);
  const compare = canEdit && prefs.compare && !isMobile;

  // Звуковой слой — только редактору: у диктора запроса нет вовсе (ручка ему и не ответит).
  const soundOn = canEdit && prefs.sound;
  const soundQuery = useSoundChapter(chapterId, soundOn);
  const soundData = soundOn ? soundQuery.data : undefined;
  // Места книги (для выбора места сцены) и ход прогона: пока идёт — опрос раз в 5 с.
  const soundBook = useSoundBook(bookId, soundOn);
  const soundRunActive = isSoundRunActive(soundBook.data?.run ?? null);
  const soundWasRunning = useRef(false);
  // «Переразметить главу» обещает, что результат появится сам: прогон кончился — перечитать главу.
  useEffect(() => {
    if (soundWasRunning.current && !soundRunActive) void queryClient.invalidateQueries({ queryKey: soundKeys.chapter(chapterId) });
    soundWasRunning.current = soundRunActive;
  }, [soundRunActive, chapterId, queryClient]);
  // Стабильные массивы по абзацам: `Paragraph` под memo и не должен перерисовываться зря.
  const soundLayer = useMemo(() => {
    const markers = soundData?.markers ?? [];
    return {
      sounds: soundData ? bySegment(markers.filter((marker) => marker.kind === "sound")) : NO_SOUND,
      bands: soundData ? bySegment(markers.filter((marker) => marker.kind !== "sound")) : NO_SOUND,
      numbers: sceneNumbers(markers),
      places: new Map<string, SoundPlace>((soundData?.places ?? []).map((place) => [place.id, place])),
    };
  }, [soundData]);

  // Everything above the panes that can change their height or their line breaks:
  // the split measures itself again whenever one of these moves.
  const layoutKey = [chapterId, data ? "1" : "0", prefs.font, prefs.rail, prefs.cast, canEdit, canVoice].join("|");
  const split = useSplitView(compare, layoutKey);

  // A new chapter, a fresh link, a moved divider: both panes start from the top of
  // the same paragraph instead of wherever the previous chapter left them.
  useEffect(() => {
    if (!compare) return undefined;
    const timer = window.setTimeout(() => split.realign(), 80);
    return () => window.clearTimeout(timer);
  }, [compare, layoutKey, split.linked, split.ratio, split.realign]);

  useEffect(() => {
    if (data) onLoaded?.(data);
  }, [data, onLoaded]);

  // «Моя роль» from the host: applied when the list changes and the chapter's cast
  // knows the names. The actor can still deselect — we do not re-apply on every render.
  const rolesKey = initialRoles.join("\u0001");
  const appliedRolesKey = useRef<string | null>(null);
  useEffect(() => {
    if (!data || appliedRolesKey.current === rolesKey) return;
    appliedRolesKey.current = rolesKey;
    const known = new Set(data.cast.map((entry) => entry.name));
    setSelected(new Set(rolesKey.split("\u0001").filter((name) => name && known.has(name))));
    setExpanded(new Set());
  }, [data, rolesKey]);

  const styleMap = useMemo(() => {
    const map = new Map<string, RoleStyle>();
    for (const entry of data?.cast ?? []) {
      map.set(entry.name, { color: entry.color, text_color: entry.text_color, weight: entry.weight, font_style: entry.font_style });
    }
    return map;
  }, [data?.cast]);
  const styleOf = useCallback((speaker: string) => styleMap.get(speaker), [styleMap]);

  const plan = useMemo(() => planVisibility(segments, selected, prefs.radius, query), [segments, selected, prefs.radius, query]);
  // «слова автора к проверке: N» in the toolbar (§6 of the brief) — a navigation
  // cue, so the operator opening a chapter sees at a glance whether it has any
  // manual work left. Only meaningful in editor mode; a dictor never sees it.
  const remarkCount = useMemo(
    () => segments.reduce((sum, segment) => sum + segment.remark_candidates.length, 0),
    [segments],
  );

  const goTo = useCallback(
    (id: string) => {
      if (onChapterChange) onChapterChange(id);
      else navigate(readerPath(id));
    },
    [navigate, onChapterChange],
  );

  const marked = useMemo(() => chapters.filter((chapter) => chapter.has_v2), [chapters]);
  const position = marked.findIndex((chapter) => chapter.id === chapterId);
  const prevId = position > 0 ? marked[position - 1].id : "";
  const nextId = position >= 0 && position + 1 < marked.length ? marked[position + 1].id : "";
  const goPrev = prevId ? () => goTo(prevId) : null;
  const goNext = nextId ? () => goTo(nextId) : null;

  // The rail renders `/reader/:id` links. An embedded host owns routing, so the click
  // is caught on the way down and handed to it; the Link sees defaultPrevented and stays put.
  const onRailClickCapture = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (!onChapterChange) return;
      const anchor = (event.target as HTMLElement | null)?.closest?.("a.v2r-rail-item") as HTMLAnchorElement | null;
      if (!anchor) return;
      const href = anchor.getAttribute("href") || "";
      const id = decodeURIComponent(href.slice(href.lastIndexOf("/") + 1));
      if (!id) return;
      event.preventDefault();
      onChapterChange(id);
    },
    [onChapterChange],
  );

  const toggleRole = useCallback((name: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
    setExpanded(new Set());
  }, []);

  const toggleRail = useCallback(() => {
    if (isMobile) setMobileRail((open) => !open);
    else updatePrefs({ rail: !prefs.rail });
  }, [isMobile, prefs.rail, updatePrefs]);

  const toggleCast = useCallback(() => {
    if (isMobile) setCastSheet((open) => !open);
    else updatePrefs({ cast: !prefs.cast });
  }, [isMobile, prefs.cast, updatePrefs]);

  // ---- editor layer (state is inert for dictors: nothing renders without can_edit) ----
  const reveal = useCallback(() => {
    setSelected(new Set());
    setQuery("");
    setExpanded(new Set());
  }, []);
  // The editor's cross-chapter jump navigates by path; an embedded host gets the id instead.
  const editorNavigate = useMemo<NavigateFunction>(() => {
    if (!onChapterChange) return navigate;
    return ((to: unknown) => {
      if (typeof to !== "string") return;
      onChapterChange(decodeURIComponent(to.slice(to.lastIndexOf("/") + 1)));
    }) as unknown as NavigateFunction;
  }, [navigate, onChapterChange]);
  const editor = useEditorState({ chapterId, chapters: allChapters, segments, navigate: editorNavigate, onReveal: reveal });
  // Нажатие на маркер слоя: запомнить его и открыть карточку. Как и карточка консилиума,
  // она взаимоисключающая с выбором роли, конструктором ударений и открытой находкой.
  const { setSheet: setEditorSheet, closeWord, closeRole, closeFinding } = editor;
  const onSoundOpen = useCallback(
    (marker: SoundMarker) => {
      closeWord();
      closeRole();
      closeFinding();
      setRemarkSelection(null);
      setSoundPick(null);
      setSoundMarkerId(marker.id);
      setEditorSheet("sound");
    },
    [closeWord, closeRole, closeFinding, setEditorSheet],
  );
  // Выбор абзаца нажатием: лист закрывается (текст под ним), после выбора — снова открыт.
  const pickSegment = useCallback(
    (pick: SoundPick) => {
      setSoundPick(pick);
      setEditorSheet(null);
    },
    [setEditorSheet],
  );
  const cancelPick = useCallback(() => {
    setSoundPick(null);
    setEditorSheet("sound");
  }, [setEditorSheet]);
  // Слой выключили — выбор абзаца и карточка маркера уходят вместе с ним, иначе плашка и
  // перехват нажатий оставались бы, а пустой лист «sound» запирал бы стрелки. Лист «places»
  // от слоя не зависит — список мест книги работает и с выключенным слоем.
  const editorSheet = editor.sheet;
  useEffect(() => {
    if (soundOn) return;
    setSoundPick(null);
    if (editorSheet === "sound") setEditorSheet(null);
  }, [soundOn, editorSheet, setEditorSheet]);
  // Любой лист, открытый во время выбора абзаца (например, с панели), отменяет выбор.
  // Сам выбор снимает `soundPick` раньше, чем снова открывает карточку.
  useEffect(() => {
    if (editorSheet !== null) setSoundPick(null);
  }, [editorSheet]);
  // Глава на момент выбора: ответ, пришедший уже на другой главе, лист не открывает.
  const chapterNow = useRef(chapterId);
  chapterNow.current = chapterId;
  useEffect(() => {
    if (!soundPick) return undefined;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      cancelPick();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [soundPick, cancelPick]);
  // Метка звука с описанием встаёт на правое поле, только если оно там есть: страница
  // отцентрована в колонке, и при открытых списке глав и касте поля может не остаться.
  // Нет поля — метка сворачивается в значок в конце абзаца, страница не уезжает вбок.
  useEffect(() => {
    const host = pageEl?.parentElement;
    if (!soundOn || !pageEl || !host) {
      setSoundRoomy(false);
      return undefined;
    }
    const measure = () => {
      const right = Math.min(host.getBoundingClientRect().right, document.documentElement.clientWidth);
      setSoundRoomy(right - pageEl.getBoundingClientRect().right >= SOUND_MARGIN_PX);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    observer.observe(pageEl);
    return () => observer.disconnect();
  }, [pageEl, soundOn]);
  useEffect(() => {
    if (!openStressQueue || !canVoice || !bookId) return;
    editor.setSheet("queue");
    onStressQueueOpened?.();
  }, [bookId, canVoice, editor.setSheet, onStressQueueOpened, openStressQueue]);
  useEffect(() => {
    if (!openConsilium || !canEdit || !bookId) return;
    editor.setSheet("consilium");
    onConsiliumOpened?.();
  }, [bookId, canEdit, editor.setSheet, onConsiliumOpened, openConsilium]);
  useEffect(() => {
    if (!openSound || !canEdit || !bookId) return;
    updatePrefs({ sound: true });
    editor.setSheet("places");
    onSoundOpened?.();
  }, [bookId, canEdit, editor.setSheet, onSoundOpened, openSound, updatePrefs]);
  const queue = useStressQueue(bookId, canVoice);
  const disputed = useDisputed(bookId, canEdit);
  const consilium = useConsilium(bookId, canEdit);
  const consiliumLabel = consilium.data ? `Консилиум: ${consilium.data.counts.new}` : "Консилиум: …";
  const hasConsilium = Boolean(consilium.data && consilium.data.items.length > 0);
  // Только находка ЭТОЙ главы: если человек ушёл по рейлу с открытой карточкой, чужая
  // карточка над другим текстом предлагала бы переписать абзац, которого на экране нет.
  // Считается здесь, ДО `useRoleCursor`: `busy` ниже опирается на `openItem`, а не на
  // сырой `editor.findingId` — тот переживает уход по рейлу, хотя карточка уже не видна,
  // и раньше молча запирал стрелки в режиме «моя роль».
  const openItem = editor.findingId
    ? consilium.data?.items.find((item) => item.id === editor.findingId && item.chapter_id === chapterId) ?? null
    : null;
  const queueLabel = queue.data
    ? `Ударения: ${queue.data.counts.unresolved} без ответа`
    : queue.isError
      ? "Ударения: очередь недоступна"
      : "Ударения: …";
  // «Проверено»: the author's own mark, the thing publishing waits for. It lives in
  // the reader because that is where the chapter is actually read.
  const approved = Boolean(data?.chapter.approved);
  const approve = useMutation({
    mutationFn: (next: boolean) => approveChapter(chapterId, next),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Не сохранилось", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      pushToast({
        tone: "success",
        title: result.approved ? `Глава ${result.chapter_index} проверена` : `Отметка с главы ${result.chapter_index} снята`,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: editorKeys.chapters }),
        queryClient.invalidateQueries({ queryKey: ["v2", "book-chapters", bookId] }),
        queryClient.invalidateQueries({ queryKey: ["v2-progress", bookId] }),
      ]);
    },
    onError: (error) => pushToast({ tone: "error", title: "Не сохранилось", detail: describeApiError(error, "Попробуйте ещё раз.") }),
  });

  /**
   * Remark candidates and the free-selection button both do the SAME thing a role
   * picker's «Рассказчик» would: no picker in between, because a human already
   * confirmed the fragment with a click (see the remark picker brief, §4-5). Same
   * endpoint, same span builder (`spansForRange`), same error vocabulary.
   */
  const remarkReassign = useMutation({
    mutationFn: async ({ segmentId, range }: { segmentId: string; range: TextRange }) => {
      const segment = segments.find((item) => item.id === segmentId);
      if (!segment) throw new Error("Абзац не найден. Обновите главу.");
      return reassignSegment(segmentId, spansForRange(segment, range, NARRATOR));
    },
    onSuccess: async (result, variables) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Роль не изменена", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      const segment = segments.find((item) => item.id === variables.segmentId);
      const fragment = segment ? segment.text.slice(variables.range.start, variables.range.end) : "";
      pushToast({
        tone: "success",
        title: `Роль изменена: ${NARRATOR}`,
        detail: fragment ? `«${remarkPreview(fragment)}»` : undefined,
      });
      const impactToast = reassignImpactToast(result.recording_impact);
      if (impactToast) pushToast({ ...impactToast, durationMs: 9000 });
      await invalidateReader(queryClient, bookId, ["chapters", "cast", "disputed"]);
    },
    onError: (error) => pushToast({ tone: "error", title: "Роль не изменена", detail: describeReassignError(error) }),
  });

  // Free-selection «→ Рассказчику» (§5 of the brief): for remarks the underline
  // heuristic missed. Tracks the live selection inside an editable paragraph;
  // gone as soon as it collapses, moves elsewhere, or a picker/sheet takes over.
  // (`remarkSelection` itself is declared near the top, with the other state, so
  // the chapter-change reset above can clear it.)
  useEffect(() => {
    if (!canEdit) return undefined;
    const onSelectionChange = () => {
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
        setRemarkSelection(null);
        return;
      }
      const domRange = selection.getRangeAt(0);
      const anchor = domRange.commonAncestorContainer;
      const anchorEl = anchor.nodeType === Node.ELEMENT_NODE ? (anchor as Element) : anchor.parentElement;
      const host = anchorEl?.closest("[id^='seg-']") as HTMLElement | null;
      if (!host) {
        setRemarkSelection(null);
        return;
      }
      const segmentId = host.id.slice("seg-".length);
      const segment = segments.find((item) => item.id === segmentId);
      const range = segment ? selectionInside(host, segment.text) : null;
      if (!range) {
        setRemarkSelection(null);
        return;
      }
      setRemarkSelection({ segmentId, range, rect: domRange.getBoundingClientRect() });
    };
    document.addEventListener("selectionchange", onSelectionChange);
    return () => document.removeEventListener("selectionchange", onSelectionChange);
  }, [canEdit, segments]);

  // Arrow keys walk the actor's own lines while «моя роль» is on. Modal things —
  // the stress constructor, the role picker, any sheet — keep their own arrows.
  const roleCursor = useRoleCursor({
    segments,
    selected,
    busy:
      editor.target !== null ||
      editor.roleTarget !== null ||
      editor.sheet !== null ||
      editor.editingCast !== null ||
      castSheet ||
      openItem !== null ||
      soundPick !== null,
    chapterId,
  });

  const disputedLabel = disputed.data
    ? `Спорные: ${disputed.data.counts.unsure}`
    : disputed.isError
      ? "Спорные: недоступно"
      : "Спорные: …";
  const targetSegment = editor.target ? segments.find((segment) => segment.id === editor.target?.segmentId) : undefined;
  const roleSegment = editor.roleTarget ? segments.find((segment) => segment.id === editor.roleTarget?.segmentId) : undefined;

  // The book's whole cast, not this chapter's: the roles a dictor is looking for are
  // usually the ones absent from the chapter he happens to have open.
  const bookCast = useBookCast(bookId, Boolean(bookId));
  const myRoles = useMemo(
    () => (bookCast.data?.characters ?? []).filter((entry) => entry.mine && entry.lines_count > 0),
    [bookCast.data],
  );
  const findingsHere = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of consilium.data?.items ?? []) {
      if (item.status === "new" && item.chapter_id === chapterId) map.set(item.segment_id, item.id);
    }
    return map;
  }, [consilium.data, chapterId]);
  // Подсветка «источник» имеет смысл только в доказанной ветке карточки (см.
  // `provenChange`/`provenKeep` в ConsiliumCard): арбитр либо сменил роль, либо
  // подтвердил сценарий цитатой. На недоказанном вердикте (`undecidable`,
  // `keep_current` без цитаты) `evidence_para` в проде всё равно заполнен у всех
  // 138 находок — подсветка без этой проверки помечала бы «источник» рядом с
  // карточкой, которая сама говорит «не смог доказать».
  const evidenceProven = Boolean(
    openItem?.evidence_proven &&
      (openItem.arbiter_verdict === "change" || openItem.arbiter_verdict === "keep_current"),
  );
  const evidenceSegmentId = openItem && evidenceProven && openItem.evidence_para
    ? segments.find((segment) => segment.ordinal === openItem.evidence_para)?.id ?? ""
    : "";
  const castNames = useMemo(() => (bookCast.data?.characters ?? []).filter((c) => !c.is_narrator).map((c) => c.name), [bookCast.data]);
  // One own role is not a choice — go straight to it; the finder stays a click away.
  const openRoleScript = useCallback(() => {
    if (myRoles.length === 1) {
      navigate(roleScriptHref(bookId, myRoles[0].name, roleScriptBase, roleScriptSearch));
      return;
    }
    setFindRole(true);
  }, [myRoles, bookId, navigate, roleScriptBase, roleScriptSearch]);

  if (!chapterId) return embedded ? null : <Navigate to="/reader" replace />;
  if (chapterQuery.isLoading) return <SkeletonPanel label="Загружаю главу…" lines={6} />;
  if (chapterQuery.isError || !data) {
    return (
      <div className="panel panel-pad v2r-empty">
        <p>{describeApiError(chapterQuery.error, "Не удалось загрузить главу.")}</p>
        {embedded ? null : (
          <Link className="btn btn-sm" to="/reader">
            К списку книг
          </Link>
        )}
      </div>
    );
  }

  const editing = (canVoice && editor.target !== null) || (canEdit && editor.roleTarget !== null && roleSegment !== undefined);
  const rootClass = [
    "v2r",
    `v2r--font-${prefs.font}`,
    prefs.rail && !isMobile ? "v2r--rail" : "",
    prefs.cast && !isMobile ? "v2r--cast" : "",
    selected.size > 0 ? "v2r--role-mode" : "",
    canVoice ? "v2r--editor" : "",
    editing ? "v2r--editing" : "",
    embedded ? "v2r--embedded" : "",
    compare ? "v2r--compare" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const railOpen = isMobile ? mobileRail : prefs.rail;

  const onSplitterKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowLeft") split.setRatio(split.ratio - 0.02);
    else if (event.key === "ArrowRight") split.setRatio(split.ratio + 0.02);
    else if (event.key === "Home") split.setRatio(0.5);
    else return;
    event.preventDefault();
  };


  const castRoles = data.cast.filter((entry) => entry.name !== NARRATOR).length;
  const castSubtitle = `${castRoles} ${plural(castRoles, "роль", "роли", "ролей")} · рассказчик отдельно`;
  const castLegend = (
    <CastLegend
      cast={data.cast}
      selected={selected}
      onToggle={toggleRole}
      onClear={() => setSelected(new Set())}
      onEdit={canVoice ? editor.setEditingCast : undefined}
      bookId={bookId}
      roleScriptBase={roleScriptBase}
      roleScriptSearch={roleScriptSearch}
    >
      {canEdit ? <LegendConflicts bookId={bookId} chapterIndex={data.chapter.index} enabled /> : null}
    </CastLegend>
  );

  const soundActive = editor.sheet === "sound" || soundPick ? soundMarkerId : null;
  const soundMarker = soundMarkerId
    ? [...(soundData?.markers ?? []), ...(soundData?.lost ?? [])].find((marker) => marker.id === soundMarkerId) ?? null
    : null;
  // места книги — для выбора места сцены; пока книга не ответила — места главы
  const soundPlaces = soundBook.data?.places ?? soundData?.places ?? [];
  const paragraph = (segment: ReaderSegment, dim: boolean, mine: boolean) => (
    <Fragment key={segment.id}>
      {(soundLayer.bands.get(segment.id) ?? []).map((marker) =>
        marker.kind === "scene" ? (
          <SceneBand
            key={marker.id}
            marker={marker}
            place={soundLayer.places.get(marker.place_id)}
            number={soundLayer.numbers.get(marker.id) ?? 0}
            active={soundActive === marker.id}
            onOpen={onSoundOpen}
          />
        ) : (
          <TransitionLine key={marker.id} marker={marker} active={soundActive === marker.id} onOpen={onSoundOpen} />
        ),
      )}
      <Paragraph
        current={segment.id === roleCursor.currentSegmentId}
        segment={segment}
        styleOf={styleOf}
        showStress={prefs.stress}
        stressAll={prefs.stressAll}
        query={query}
        dim={dim}
        mine={mine}
        editable={canVoice}
        // author/operator only — see `remarkReassign` above: a dictor who can only
        // voice gets no underline and no click, not one pixel different from today
        remarkCandidates={canEdit ? segment.remark_candidates : undefined}
        activeWord={editor.target && editor.target.segmentId === segment.id ? editor.target : null}
        activeSpan={editor.roleTarget && editor.roleTarget.segmentId === segment.id ? editor.roleTarget.span : null}
        rolePicking={editor.roleTarget?.segmentId === segment.id}
        consilium={canEdit && findingsHere.has(segment.id) ? (openItem?.segment_id === segment.id ? "active" : "open") : null}
        evidence={canEdit && evidenceSegmentId === segment.id && openItem?.segment_id !== segment.id}
        soundMarks={soundLayer.sounds.get(segment.id)}
        onSoundOpen={soundOn ? onSoundOpen : undefined}
      />
    </Fragment>
  );

  // A click on a remark's dashed underline reassigns straight away (§4 of the
  // brief); everything else falls through to the existing role-chip / word
  // handler. Composed here (not inside `useEditorState`) because it needs the
  // network mutation and toast, which are UI-layer concerns the pure editor
  // state hook does not own.
  const onArticleClick = (event: MouseEvent<HTMLElement>) => {
    const markEl = canEdit ? ((event.target as HTMLElement | null)?.closest?.(".v2r-cons-mark") as HTMLElement | null) : null;
    if (markEl) {
      const found = consilium.data?.items.find((item) => item.id === findingsHere.get(markEl.dataset.seg || ""));
      if (found) editor.openFinding(found);
      return;
    }
    const remarkEl = canEdit ? ((event.target as HTMLElement | null)?.closest?.(".v2r-remark") as HTMLElement | null) : null;
    const host = remarkEl?.closest("[id^='seg-']") as HTMLElement | null;
    const start = remarkEl ? Number(remarkEl.dataset.start) : NaN;
    const end = remarkEl ? Number(remarkEl.dataset.end) : NaN;
    if (host && Number.isFinite(start) && Number.isFinite(end)) {
      if (!remarkReassign.isPending) {
        remarkReassign.mutate({ segmentId: host.id.slice("seg-".length), range: { start, end } });
      }
      return;
    }
    editor.onArticleClick(event);
  };

  // Режим выбора абзаца перехватывает нажатие раньше всех: ни выбор роли, ни ударения,
  // ни «→ Рассказчику», ни метки звука в это время не срабатывают.
  const onPickCapture = (event: MouseEvent<HTMLElement>) => {
    if (!soundPick) return;
    event.preventDefault();
    event.stopPropagation();
    const host = (event.target as HTMLElement | null)?.closest?.("[data-seg]") as HTMLElement | null;
    const segmentId = host?.dataset.seg;
    if (!segmentId) return;
    const pick = soundPick;
    const pickedIn = chapterId;
    setSoundPick(null);
    window.getSelection()?.removeAllRanges();
    pick
      .onPick(segmentId)
      .then((openId) => {
        if (openId && chapterNow.current === pickedIn) setSoundMarkerId(openId);
      })
      .catch((error: unknown) => pushToast({ tone: "error", title: "Не получилось", detail: describeSoundError(error) }))
      .finally(() => {
        if (chapterNow.current === pickedIn) setEditorSheet("sound");
      });
  };

  const article = (
    <article
      ref={setPageEl}
      className={["v2r-page", soundRoomy ? "snd-roomy" : "", soundPick ? "snd-picking" : ""].filter(Boolean).join(" ")}
      lang="ru"
      onClick={canVoice ? onArticleClick : undefined}
      onClickCapture={soundPick ? onPickCapture : undefined}
      onMouseDownCapture={canEdit && !soundPick ? editor.onArticleMouseDown : undefined}  /* role picker: markup */
    >
      {plan.map((item) => {
        if (item.type === "segment") return paragraph(segments[item.index], item.dim, item.mine);
        if (expanded.has(item.from)) {
          return segments.slice(item.from, item.to).map((segment) => paragraph(segment, true, false));
        }
        return (
          <GapRow
            key={`gap-${item.from}`}
            count={item.count}
            onExpand={() => setExpanded((current) => new Set(current).add(item.from))}
          />
        );
      })}
    </article>
  );

  // «Сверка с оригиналом»: the author's text on the left, the script on the right,
  // a divider that can be dragged, and panes that scroll together until unlinked.
  const script = !compare ? (
    article
  ) : (
    <div className="v2r-split" ref={split.hostRef} style={split.height ? { height: split.height } : undefined}>
      <section className="v2r-pane" style={{ flexBasis: `calc(${(split.ratio * 100).toFixed(2)}% - 9px)` }}>
        <div className="v2r-pane-label">Оригинал</div>
        <div className="v2r-pane-scroll" ref={split.sourceRef} onScroll={() => split.onPaneScroll("source")}>
          <SourcePane chapterId={chapterId} query={query} />
        </div>
      </section>
      <div
        className="v2r-splitter"
        role="separator"
        aria-orientation="vertical"
        aria-label="Ширина панелей"
        aria-valuenow={Math.round(split.ratio * 100)}
        aria-valuemin={25}
        aria-valuemax={75}
        tabIndex={0}
        onPointerDown={split.onSplitterDown}
        onKeyDown={onSplitterKey}
        onDoubleClick={() => split.setRatio(0.5)}
      >
        <span />
      </div>
      <section className="v2r-pane" style={{ flexBasis: `calc(${((1 - split.ratio) * 100).toFixed(2)}% - 9px)` }}>
        <div className="v2r-pane-label">Сценарий</div>
        <div className="v2r-pane-scroll" ref={split.scriptRef} onScroll={() => split.onPaneScroll("script")}>
          {article}
        </div>
      </section>
    </div>
  );

  return (
    <div
      className={rootClass}
      style={compare && split.bodyHeight ? ({ "--v2r-avail": `${split.bodyHeight}px` } as CSSProperties) : undefined}
    >
      <ReaderToolbar
        compact={isMobile}
        onFindRole={openRoleScript}
        myRoleCount={myRoles.length}
        bookTitle={data.book.title}
        chapterIndex={data.chapter.index}
        chapterTitle={data.chapter.title}
        remarkCount={canEdit ? remarkCount : 0}
        onPrev={goPrev}
        onNext={goNext}
        prefs={prefs}
        onPrefs={updatePrefs}
        query={query}
        onQuery={setQuery}
        onPrint={() => window.print()}
        onToggleRail={toggleRail}
        railOpen={railOpen}
        cast={{
          // the narrator is not among the chips; the count must not promise him
          count: castRoles,
          selected: [...selected],
          open: isMobile ? castSheet : prefs.cast,
          onToggle: toggleCast,
          onClear: () => setSelected(new Set()),
          cursor: roleCursor.total > 0 ? { position: roleCursor.position, total: roleCursor.total, onStep: roleCursor.go } : undefined,
        }}
        sound={canEdit ? { on: prefs.sound, onToggle: () => updatePrefs({ sound: !prefs.sound }) } : undefined}
        compare={
          !canEdit || isMobile
            ? undefined
            : {
                on: compare,
                onToggle: () => updatePrefs({ compare: !prefs.compare }),
                linked: split.linked,
                onToggleLinked: split.toggleLinked,
              }
        }
        lore={hasLore ? { onOpen: () => setLoreOpen(true) } : undefined}
        approval={
          canEdit && data.chapter.attributed
            ? {
                approved,
                pending: approve.isPending,
                onToggle: () => approve.mutate(!approved),
              }
            : undefined
        }
        editor={
          canVoice
            ? {
                queueLabel,
                queueOpen: editor.sheet === "queue",
                onQueue: () => editor.toggleSheet("queue"),
                markup: canEdit
                  ? {
                      disputedLabel,
                      disputedOpen: editor.sheet === "disputed",
                      onDisputed: () => editor.toggleSheet("disputed"),
                      profileOpen: editor.sheet === "profile",
                      onProfile: () => editor.toggleSheet("profile"),
                      consilium: hasConsilium
                        ? { label: consiliumLabel, open: editor.sheet === "consilium", onToggle: () => editor.toggleSheet("consilium") }
                        : undefined,
                      places: { open: editor.sheet === "places", onToggle: () => editor.toggleSheet("places") },
                    }
                  : undefined,
              }
            : undefined
        }
      />

      {editing && editor.target ? (
        <StressConstructor
          key={`${editor.target.segmentId}:${editor.target.start}`}
          bookId={bookId}
          target={editor.target}
          segment={targetSegment}
          onClose={editor.closeWord}
          onSaved={editor.closeWord}
        />
      ) : null}
      {canEdit && editor.roleTarget && roleSegment ? (
        <RolePicker
          key={`${editor.roleTarget.segmentId}:${editor.roleTarget.span?.start ?? "p"}`}
          bookId={bookId}
          segment={roleSegment}
          span={editor.roleTarget.span}
          initialSelection={editor.roleTarget.selection}
          cast={data.cast}
          onClose={editor.closeRole}
          onSaved={(keepOpen) => {
            if (!keepOpen) editor.closeRole();
          }}
        />
      ) : null}
      {canEdit && openItem ? (
        <ConsiliumCard
          key={openItem.id}
          bookId={bookId}
          item={openItem}
          castNames={castNames}
          items={consilium.data?.items ?? []}
          onClose={editor.closeFinding}
          onNext={editor.openFinding}
        />
      ) : null}
      {/* §5 of the brief: a remark the underline heuristic missed. Hidden while a
          picker/constructor already covers the same paragraph, so the operator is
          never offered two competing ways to do the same thing at once. An open
          consilium card is the same kind of competing tool — checked directly here
          rather than folded into `editing`, which also drives `v2r--editing` on the
          root and the word-role-picker below; a card being open must not flip either
          of those. */}
      {canEdit && !editing && openItem === null && !soundPick && remarkSelection ? (
        <RemarkSelectionButton
          rect={remarkSelection.rect}
          pending={remarkReassign.isPending}
          onPick={() => {
            remarkReassign.mutate({ segmentId: remarkSelection.segmentId, range: remarkSelection.range });
            setRemarkSelection(null);
          }}
        />
      ) : null}

      <div className="v2r-body">
        <div className="v2r-rail-host" style={{ display: "contents" }} onClickCapture={onRailClickCapture}>
          <ChapterRail
            bookTitle={data.book.title}
            chapters={chapters}
            currentId={chapterId}
            open={prefs.rail && !isMobile}
            mobileOpen={isMobile && mobileRail}
            onClose={() => setMobileRail(false)}
          />
        </div>

        <div className="v2r-main">
          <CastLegendPrint cast={data.cast} />

          {segments.length === 0 ? (
            <div className="v2r-empty">
              <Icon name="doc" />
              <p>Глава ещё не размечена в v2.</p>
              {marked.length > 0 ? (
                onChapterChange ? (
                  <button type="button" className="btn btn-sm" onClick={() => goTo(marked[0].id)}>
                    Открыть первую размеченную главу
                  </button>
                ) : (
                  <Link className="btn btn-sm" to={readerPath(marked[0].id)}>
                    Открыть первую размеченную главу
                  </Link>
                )
              ) : null}
            </div>
          ) : (
            <>
              {!data.chapter.attributed ? (
                <p className="v2r-notice">Реплики в этой главе ещё не распределены по ролям — показан текст без разметки.</p>
              ) : null}
              {query.trim() && plan.every((item) => item.type === "gap") ? (
                <p className="v2r-notice">Ничего не найдено по запросу «{query.trim()}».</p>
              ) : null}

              {script}
            </>
          )}

          <nav className="v2r-nav v2r-nav--bottom" aria-label="Соседние главы">
            <button type="button" className="btn v2r-btn" onClick={goPrev ?? undefined} disabled={!goPrev}>
              <Icon name="back" /> Предыдущая глава
            </button>
            <button type="button" className="btn v2r-btn" onClick={goNext ?? undefined} disabled={!goNext}>
              Следующая глава <Icon name="arrow" />
            </button>
          </nav>
        </div>

        {prefs.cast && !isMobile ? (
          <aside className="v2r-cast" aria-label="Каст главы">
            <div className="v2r-cast-head">
              <div className="v2r-cast-titles">
                <strong>Каст главы</strong>
                <span className="faint">{castSubtitle}</span>
              </div>
              <button
                type="button"
                className="btn btn-sm btn-ghost v2r-btn v2r-cast-close"
                onClick={() => updatePrefs({ cast: false })}
                aria-label="Скрыть каст главы"
                title="Скрыть — вернуть можно кнопкой «Каст главы»"
              >
                ✕
              </button>
            </div>
            <div className="v2r-cast-body">{castLegend}</div>
          </aside>
        ) : null}
      </div>

      {isMobile && castSheet ? (
        <Sheet title="Каст главы" subtitle={castSubtitle} onClose={() => setCastSheet(false)}>
          {castLegend}
        </Sheet>
      ) : null}
      {findRole ? (
        <RoleFinder
          bookId={bookId}
          cast={bookCast.data?.characters ?? []}
          loading={bookCast.isLoading}
          onClose={() => setFindRole(false)}
          roleScriptBase={roleScriptBase}
          roleScriptSearch={roleScriptSearch}
        />
      ) : null}
      {canVoice && editor.sheet === "queue" ? (
        <StressQueue bookId={bookId} onClose={() => editor.setSheet(null)} onPick={editor.jumpTo} />
      ) : null}
      {canEdit && editor.sheet === "disputed" ? (
        <DisputedQueue
          bookId={bookId}
          currentChapterId={chapterId}
          onClose={() => editor.setSheet(null)}
          onPick={editor.jumpToSpan}
        />
      ) : null}
      {canEdit && editor.sheet === "consilium" ? (
        <ConsiliumQueue bookId={bookId} currentChapterId={chapterId} onClose={() => editor.setSheet(null)} onPick={editor.openFinding} />
      ) : null}
      {canEdit && editor.sheet === "profile" ? <ProfilePanel bookId={bookId} onClose={() => editor.setSheet(null)} /> : null}
      {soundOn && editor.sheet === "sound" && soundMarker ? (
        <SoundCard
          key={soundMarker.id}
          chapterId={chapterId}
          bookId={bookId}
          marker={soundMarker}
          place={soundPlaces.find((place) => place.id === soundMarker.place_id)}
          places={soundPlaces}
          sceneNumber={soundLayer.numbers.get(soundMarker.id) ?? 0}
          lost={soundData?.lost ?? []}
          pickSegment={pickSegment}
          onOpenMarker={onSoundOpen}
          onClose={() => editor.setSheet(null)}
        />
      ) : null}
      {loreOpen ? <LorePanel bookId={bookId} onClose={() => setLoreOpen(false)} /> : null}
      {canEdit && editor.sheet === "places" ? (
        <SoundPlaces
          bookId={bookId}
          chapterId={chapterId}
          chapters={allChapters}
          lost={soundData?.lost ?? []}
          onOpenMarker={onSoundOpen}
          onGoChapter={(id) => {
            editor.setSheet(null);
            goTo(id);
          }}
          onClose={() => editor.setSheet(null)}
        />
      ) : null}
      {soundPick ? (
        <div className="snd-pick-banner" role="status" aria-live="polite">
          <span>{soundPick.hint}</span>
          <span className="snd-pick-esc">Esc — отмена</span>
          <button type="button" className="btn btn-sm v2r-btn" onClick={cancelPick}>
            Отмена
          </button>
        </div>
      ) : null}
      {canVoice && editor.editingCast ? (
        <LegendEditor
          key={editor.editingCast.character_id}
          bookId={bookId}
          entry={editor.editingCast}
          onClose={() => editor.setEditingCast(null)}
        />
      ) : null}
    </div>
  );
}
