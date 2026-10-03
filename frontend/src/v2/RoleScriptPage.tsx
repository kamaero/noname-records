/**
 * «Все реплики роли» — one actor's own lines across the whole book.
 *
 * The chapter reader answers «что в этой главе»; a dictor asks «где я говорю».
 * Бимькмолепус speaks in nineteen chapters of «Крыльев», and finding those places
 * meant opening sixty chapters one by one. Here they are in reading order, each with
 * the paragraphs around it, grouped by chapter, walked with the arrow keys.
 *
 * The paragraphs are drawn by the same `Paragraph` as everywhere else, so colours,
 * stress marks and role chips look exactly as they do in the chapter — an actor
 * should not have to learn a second way of reading the same script.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { SkeletonPanel } from "../components/Skeleton";
import { Paragraph } from "./Paragraph";
import { StressConstructor } from "./StressConstructor";
import { StressToggle } from "./StressToggle";
import { RoleTraps } from "./RoleTraps";
import { LorePanel } from "./LorePanel";
import { useLore } from "./loreApi";
import { useBookCast } from "./editorApi";
import { plural, useIsMobile } from "./useIsMobile";
import { useReaderPrefs } from "./useReaderPrefs";
import { useRoleCursor } from "./useRoleCursor";
import type { WordTarget } from "./useEditorState";
import type { ReaderSegment, RoleStyle } from "./types";
import { recordingHref } from "./roleRoutes";
import "./ScriptReader.css";

type RoleSegment = ReaderSegment & { mine: boolean; after_gap: boolean };

type RoleScriptResponse = {
  ok: boolean;
  book: { id: string; title: string };
  role: string;
  /** что энциклопедия автора говорит об этой роли; null — связи с каноном нет */
  canon?: { name: string; aliases: string[]; description: string; topic: string; portrait?: string } | null;
  radius: number;
  counts: { lines: number; chapters: number; shown: number; offset: number };
  has_more: boolean;
  chapters: Array<{
    chapter_id: string;
    chapter_index: number;
    chapter_title: string;
    lines: number;
    segments: RoleSegment[];
  }>;
  /** every chapter the role speaks in, across the whole book — not only the shown page */
  in_chapters?: Array<{ chapter_id: string; chapter_index: number; lines: number }>;
  /** may this reader set stress — the same right as in the chapter reader */
  can_voice?: boolean;
};

/** Titles usually already start with «Глава N.»; do not print it twice. */
function chapterHeading(index: number, title: string): string {
  const clean = (title || "").trim();
  if (!clean) return `Глава ${index}`;
  return /^глава\s*\d+/i.test(clean) ? clean : `Глава ${index}. ${clean}`;
}

const PAGE = 300;
const RADII = [0, 1, 2, 3, 5];

export function RoleScriptPage() {
  const { bookId = "" } = useParams();
  const { pathname } = useLocation();
  const [params, setParams] = useSearchParams();
  const recordingContext = pathname.startsWith("/recording/role/");
  const role = params.get("role") || "";
  const radius = Number(params.get("radius") ?? 2);
  // «Слова-ловушки» instead of the lines — in the address, so the list can be sent and printed
  const traps = params.get("view") === "traps";
  const fresh = params.get("fresh") !== "0";
  const [limit, setLimit] = useState(PAGE);
  const [prefs, updatePrefs] = useReaderPrefs();
  // a phone keeps the arrows — they are how an actor walks his lines — and folds the rest
  const compact = useIsMobile();
  const [more, setMore] = useState(false);
  const [loreOpen, setLoreOpen] = useState(false);
  // Описание героя из энциклопедии бывает в 600 знаков — во всю ширину телефона это
  // экран текста до первой реплики. Свёрнуто до пары строк, раскрывается по кнопке.
  const [canonOpen, setCanonOpen] = useState(false);
  const [canonClamped, setCanonClamped] = useState(false);
  const canonTextRef = useRef<HTMLSpanElement>(null);
  const lore = useLore(bookId);
  const hasLore = Boolean(lore.data?.articles?.length);
  // the word the stress constructor is open on
  const [target, setTarget] = useState<WordTarget | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => setLimit(PAGE), [bookId, role, radius]);
  useEffect(() => setTarget(null), [bookId, role, radius]);

  const query = useQuery({
    queryKey: ["v2", "role-script", bookId, role, radius, limit],
    queryFn: () =>
      apiGet<RoleScriptResponse>(
        `/api/v2/books/${encodeURIComponent(bookId)}/role-script?role=${encodeURIComponent(role)}&radius=${radius}&limit=${limit}`,
      ),
    enabled: Boolean(bookId && role),
  });

  const data = query.data;
  useEffect(() => setCanonOpen(false), [bookId, role]);
  // Кнопка — только когда текст правда не влез: у коротких описаний она лишняя.
  useLayoutEffect(() => {
    const el = canonTextRef.current;
    if (!el || canonOpen) return;
    const measure = () => setCanonClamped(el.scrollHeight > el.clientHeight + 1);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [data?.canon, canonOpen]);
  const canVoice = Boolean(data?.can_voice);
  // one flat list for the arrow keys: they step through the actor's lines, not the context
  const flat = useMemo<RoleSegment[]>(() => (data?.chapters ?? []).flatMap((chapter) => chapter.segments), [data]);
  const mineOnly = useMemo(() => flat.filter((segment) => segment.mine), [flat]);
  const cursor = useRoleCursor({
    segments: mineOnly,
    selected: useMemo(() => new Set(mineOnly.flatMap((s) => s.spans.map((span) => span.speaker))), [mineOnly]),
    // the constructor keeps its own arrows: ← → walk the vowels there; the word list has no lines to walk
    busy: target !== null || traps,
    chapterId: `${bookId}:${role}:${radius}`,
  });

  // the same colours the chapter reader uses, so a role looks like itself here too
  const cast = useBookCast(bookId, Boolean(bookId));
  // the author's word on the voice, at the top of the part it describes
  const about = useMemo(() => {
    const row = (cast.data?.characters ?? []).find((entry) => entry.name === role);
    if (!row) return null;
    const facts = [row.race, row.temperament].filter(Boolean).join(" · ");
    return row.note || facts ? { facts, note: row.note } : null;
  }, [cast.data, role]);
  const styles = useMemo(() => {
    const map = new Map<string, RoleStyle>();
    for (const entry of cast.data?.characters ?? []) {
      map.set(entry.name, {
        color: entry.character_color,
        text_color: entry.character_text_color,
        weight: entry.character_font_weight,
        font_style: entry.character_font_style,
      });
    }
    return map;
  }, [cast.data]);
  const styleOf = useCallback((speaker: string) => styles.get(speaker), [styles]);
  // An actor cast in two roles picks one explicitly: Бапдиг and Хинзонме are two voices,
  // and a page that mixed their lines would ask him to switch timbre mid-list.
  const myRoles = useMemo(
    () => (cast.data?.characters ?? []).filter((entry) => entry.mine && entry.lines_count > 0),
    [cast.data],
  );
  const roleColor = styles.get(role)?.color || "";
  const switchRole = (name: string) => {
    if (name === role) return;
    params.set("role", name);
    setParams(params);
  };
  const targetSegment = target ? flat.find((segment) => segment.id === target.segmentId) : undefined;
  const closeWord = useCallback(() => setTarget(null), []);
  // a new mark may land in any paragraph of the book, not only in the clicked one
  const onStressSaved = useCallback(() => {
    setTarget(null);
    void queryClient.invalidateQueries({ queryKey: ["v2", "role-script", bookId] });
  }, [bookId, queryClient]);
  /** A click on a word opens the constructor on it — the same `.v2r-word` spans the chapter reader has. */
  const onArticleClick = (event: MouseEvent<HTMLElement>) => {
    if (!canVoice) return;
    const el = (event.target as HTMLElement | null)?.closest?.(".v2r-word") as HTMLElement | null;
    const host = el?.closest("[id^='seg-']") as HTMLElement | null;
    if (!el || !host) return;
    const segment = flat.find((item) => item.id === host.id.slice("seg-".length));
    const start = Number(el.dataset.start);
    const end = Number(el.dataset.end);
    if (!segment || !Number.isFinite(start) || !(end > start) || end > segment.text.length) return;
    setTarget({ segmentId: segment.id, start, end, word: segment.text.slice(start, end) });
  };
  const backHref = recordingContext
    ? (bookId ? recordingHref(bookId, params.toString()) : "/recording")
    : (bookId ? `/reader?book_id=${encodeURIComponent(bookId)}` : "/reader");
  const backLabel = recordingContext ? "К записи" : "К читалке";

  if (!bookId || !role) {
    return (
      <div className="panel panel-pad v2r-empty">
        <p>Не указана роль. Откройте её из каста главы или из панели записи.</p>
        <Link className="btn btn-sm" to={backHref}>{backLabel}</Link>
      </div>
    );
  }
  if (query.isLoading) return <SkeletonPanel label={`Собираю реплики роли «${role}»…`} lines={6} />;
  if (query.isError || !data) {
    return (
      <div className="panel panel-pad v2r-empty">
        <p>{describeApiError(query.error, "Не удалось собрать реплики роли.")}</p>
        <Link className="btn btn-sm" to={backHref}>{backLabel}</Link>
      </div>
    );
  }

  const { counts } = data;
  const inChapters = data.in_chapters ?? [];
  const shownChapters = new Set(data.chapters.map((chapter) => chapter.chapter_id));
  const chapterHref = (chapterId: string) => (recordingContext
    ? recordingHref(bookId, params.toString(), chapterId)
    : `/reader/${encodeURIComponent(chapterId)}`);
  return (
    <div className={`v2r v2r--font-${prefs.font} v2r--role-mode v2r--rolescript${canVoice ? " v2r--editor" : ""}`}>
      <header className="v2r-toolbar">
        <div className="v2r-toolbar-row v2r-toolbar-row--title">
          <Link className="btn btn-sm v2r-btn" to={backHref}>
            <Icon name="back" /> {backLabel}
          </Link>
          <div className="v2r-title">
            <span className="v2r-title-book">{data.book.title || "Книга"}</span>
            <h1 className="v2r-title-chapter">
              {roleColor ? <span className="v2r-title-dot" style={{ background: roleColor }} aria-hidden="true" /> : null}
              Все реплики: {data.role}
            </h1>
          </div>
          <span className="v2r-rolescript-count num">
            {counts.lines} {plural(counts.lines, "реплика", "реплики", "реплик")} · {counts.chapters}{" "}
            {plural(counts.chapters, "глава", "главы", "глав")}
          </span>
        </div>

        {data.canon ? (
          <div className="v2r-toolbar-row v2r-canon">
            {data.canon.portrait ? (
              <img className="v2r-canon-portrait" src={data.canon.portrait} alt={data.canon.name} loading="lazy" />
            ) : (
              <Icon name="doc" />
            )}
            <span className="v2r-canon-body">
              <span ref={canonTextRef} className={`v2r-canon-text${canonOpen ? "" : " is-clamped"}`}>
                <strong>{data.canon.name}</strong>
                {data.canon.aliases.length ? ` (он же ${data.canon.aliases.join(", ")})` : ""}
                {data.canon.description ? ` — ${data.canon.description}` : ""}
              </span>
              {canonClamped || canonOpen ? (
                <button type="button" className="v2r-canon-more" aria-expanded={canonOpen} onClick={() => setCanonOpen((v) => !v)}>
                  {canonOpen ? "Свернуть ▴" : "Подробнее ▾"}
                </button>
              ) : null}
            </span>
            {hasLore ? (
              <button type="button" className="btn btn-sm v2r-btn" onClick={() => setLoreOpen(true)}>
                В лор
              </button>
            ) : null}
          </div>
        ) : null}

        {myRoles.length > 1 ? (
          <div className="v2r-toolbar-row v2r-myroles" role="tablist" aria-label="Мои роли — какую озвучиваю">
            <span className="v2r-myroles-label">Мои роли:</span>
            {myRoles.map((entry) => (
              <button
                key={entry.character_id || entry.name}
                type="button"
                role="tab"
                aria-selected={entry.name === role}
                className={entry.name === role ? "v2r-myrole is-on" : "v2r-myrole"}
                style={{ "--role-color": entry.character_color } as CSSProperties}
                onClick={() => switchRole(entry.name)}
                title={entry.name === role ? `Сейчас: ${entry.name}` : `Перейти к роли «${entry.name}» — только её реплики`}
              >
                <span className="v2r-myrole-dot" aria-hidden="true" />
                {entry.name}
                <span className="v2r-myrole-count num">{entry.lines_count}</span>
              </button>
            ))}
          </div>
        ) : null}

        <div className="v2r-toolbar-row v2r-toolbar-row--tools">
          <button
            type="button"
            className={traps ? "btn btn-sm v2r-btn is-on" : "btn btn-sm v2r-btn"}
            aria-pressed={traps}
            onClick={() => {
              if (traps) params.delete("view");
              else params.set("view", "traps");
              setParams(params, { replace: true });
            }}
            title="Редкие слова из реплик роли, по главам, с ударением — посмотреть до записи"
          >
            {traps ? "К репликам" : "Слова-ловушки"}
          </button>

          {/* Шпаргалка о мире книги: актёру она нужнее, чем редактору, — он в Читалку
              редактора не ходит, а озвучивает расу, о которой впервые слышит. */}
          {hasLore ? (
            <button
              type="button"
              className="btn btn-sm v2r-btn"
              onClick={() => setLoreOpen(true)}
              title="Мир книги: расы, титулы, кто есть кто — шпаргалка от автора"
            >
              Лор
            </button>
          ) : null}

          {traps ? null : (
          <span className="v2r-cursor" role="group" aria-label="Переход по репликам">
            <button type="button" className="btn btn-sm v2r-btn" onClick={() => cursor.go(-1)} aria-label="Предыдущая реплика">↑</button>
            <button type="button" className="btn btn-sm v2r-btn" onClick={() => cursor.go(1)} aria-label="Следующая реплика">↓</button>
            <span className="v2r-cursor-count num">{cursor.position || "—"} / {cursor.total}</span>
          </span>
          )}

          {compact ? (
            <button
              type="button"
              className={more ? "btn btn-sm v2r-btn v2r-more is-on" : "btn btn-sm v2r-btn v2r-more"}
              onClick={() => setMore((open) => !open)}
              aria-expanded={more}
              title="Контекст, ударения, размер шрифта, печать"
            >
              {more ? "Свернуть" : "Ещё"}
            </button>
          ) : null}

            <div className="v2r-toolbar-more" hidden={compact && !more}>
            {traps ? null : (
            <label className="v2r-radius" title="Сколько абзацев вокруг каждой реплики показывать">
              <span className="v2r-radius-label">± абзацев</span>
              <select
                className="v2r-select"
                value={radius}
                onChange={(event) => {
                  params.set("radius", event.target.value);
                  setParams(params, { replace: true });
                }}
              >
                {RADII.map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </label>
            )}

            <StressToggle
              prefs={prefs}
              onPrefs={updatePrefs}
              hint={canVoice ? "Щёлкните по любому слову в тексте — поставите ударение" : undefined}
            />

            <div className="v2r-seg" role="group" aria-label="Размер шрифта">
              {(["s", "m", "l"] as const).map((key) => (
                <button
                  key={key}
                  type="button"
                  className={prefs.font === key ? "v2r-seg-btn is-on" : "v2r-seg-btn"}
                  onClick={() => updatePrefs({ font: key })}
                  aria-pressed={prefs.font === key}
                >
                  {key.toUpperCase()}
                </button>
              ))}
            </div>

          <button type="button" className="btn btn-sm v2r-btn v2r-print" onClick={() => window.print()}>
            <Icon name="doc" /> PDF / печать
          </button>
          </div>
        </div>
      </header>

      {canVoice && target ? (
        <StressConstructor
          key={`${target.segmentId}:${target.start}`}
          bookId={bookId}
          target={target}
          segment={targetSegment}
          onClose={closeWord}
          onSaved={onStressSaved}
        />
      ) : null}

      <div className="v2r-body">
        <div className="v2r-main">
          {about || inChapters.length > 0 ? (
            <aside className="v2r-rolecard" style={roleColor ? { borderLeftColor: roleColor } : undefined}>
              {about?.note ? <p className="v2r-rolecard-note">{about.note}</p> : null}
              {about?.facts ? <p className="v2r-rolecard-facts">{about.facts}</p> : null}
              {inChapters.length > 0 ? (
                <p className="v2r-rolecard-chapters">
                  В {inChapters.length === 1 ? "главе" : "главах"}:{" "}
                  {inChapters.map((chapter, index) => {
                    const hint = `${chapter.lines} ${plural(chapter.lines, "реплика", "реплики", "реплик")}`;
                    return (
                      <span key={chapter.chapter_id}>
                        {index > 0 ? ", " : null}
                        {/* a chapter already on this page is a jump down; a later one opens on its own */}
                        {shownChapters.has(chapter.chapter_id) ? (
                          <a
                            href={`#role-ch-${chapter.chapter_id}`}
                            title={`${hint} — перейти ниже`}
                            onClick={(event) => {
                              event.preventDefault();
                              document.getElementById(`role-ch-${chapter.chapter_id}`)?.scrollIntoView({ block: "start" });
                            }}
                          >
                            {chapter.chapter_index}
                          </a>
                        ) : (
                          <Link to={chapterHref(chapter.chapter_id)} title={`${hint} — открыть главу`}>
                            {chapter.chapter_index}
                          </Link>
                        )}
                      </span>
                    );
                  })}
                </p>
              ) : null}
            </aside>
          ) : null}
          {traps ? (
            <RoleTraps
              bookId={bookId}
              role={role}
              fresh={fresh}
              onFresh={(next) => {
                if (next) params.delete("fresh");
                else params.set("fresh", "0");
                setParams(params, { replace: true });
              }}
              chapterHref={chapterHref}
              heading={chapterHeading}
            />
          ) : counts.lines === 0 ? (
            <div className="v2r-empty">
              <Icon name="doc" />
              <p>У роли «{data.role}» в этой книге нет реплик — возможно, она называется иначе в разметке.</p>
            </div>
          ) : (
            <article className="v2r-page" lang="ru" onClick={onArticleClick}>
              {data.chapters.map((chapter) => (
                <section key={chapter.chapter_id} id={`role-ch-${chapter.chapter_id}`} className="v2r-rolescript-chapter">
                  <h2 className="v2r-rolescript-h">
                    <Link to={chapterHref(chapter.chapter_id)} title="Открыть главу целиком">
                      {chapterHeading(chapter.chapter_index, chapter.chapter_title)}
                    </Link>
                    <span className="num">
                      {chapter.lines} {plural(chapter.lines, "реплика", "реплики", "реплик")}
                    </span>
                  </h2>
                  {chapter.segments.map((segment) => (
                    <div key={segment.id} className={segment.after_gap ? "v2r-rolescript-island" : undefined}>
                      <Paragraph
                        segment={segment}
                        styleOf={styleOf}
                        showStress={prefs.stress}
                        stressAll={prefs.stressAll}
                        query=""
                        dim={!segment.mine}
                        mine={segment.mine}
                        stressable={canVoice}
                        activeWord={target && target.segmentId === segment.id ? target : null}
                        current={segment.id === cursor.currentSegmentId}
                      />
                    </div>
                  ))}
                </section>
              ))}
            </article>
          )}

          {!traps && data.has_more ? (
            <div className="v2r-nav v2r-nav--bottom">
              <button type="button" className="btn v2r-btn" onClick={() => setLimit((value) => value + PAGE)}>
                Показать ещё {PAGE} реплик (из {counts.lines})
              </button>
            </div>
          ) : null}
        </div>
      </div>
      <span className="v2r-sr">Стрелки вверх и вниз ведут по репликам роли</span>
      {loreOpen ? <LorePanel bookId={bookId} onClose={() => setLoreOpen(false)} /> : null}
    </div>
  );
}
