/**
 * «Переназначить роль»: a picker docked under the toolbar like the stress
 * constructor. The chapter cast as chips (most lines first), then «Рассказчик»
 * and «UNSURE», and a search over the whole book cast. Scope is the paragraph,
 * the clicked replica, or the text selected inside the paragraph. Saves via
 * `POST /api/v2/segments/{id}/attribution`; Shift keeps the picker open.
 *
 * A name the cast does not have is not a dead end: the search row doubles as «новая
 * роль», and a speaker the markup carries without a `Character` row behind it (an
 * import artefact, or a name the model invented) is offered the same way. Creating
 * and assigning are one action — a role created and then not used is litter.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/ToastProvider";
import { foldName, selectionInside, spansForRange, spansForWhole, spanAt, type TextRange } from "./attribution";
import { reassignImpactToast } from "./consilium";
import { createRole, describeReassignError, invalidateReader, reassignSegment, useBookCast } from "./editorApi";
import { segmentDomId } from "./Paragraph";
import { NARRATOR, UNSURE, type ReaderCastEntry, type ReaderSegment, type ReassignSpan, type RoleStyle } from "./types";

type Scope = "whole" | "span" | "selection";

/** One row of the picker: a chapter role, the narrator, UNSURE, a book role, or «create». */
type Candidate = {
  name: string;
  actor: string;
  lines: number;
  style: RoleStyle | null;
  /** true for names the markup carries without a `Character` row: assigning creates it first */
  foreign: boolean;
  /** speaks in this chapter */
  here: boolean;
  /** the «завести роль» row at the end of the list */
  create?: boolean;
};

type RolePickerProps = {
  bookId: string;
  segment: ReaderSegment;
  /** the clicked replica; null = the paragraph */
  span: TextRange | null;
  /** text selected inside the paragraph when the picker opened */
  initialSelection: TextRange | null;
  cast: ReaderCastEntry[];
  onClose: () => void;
  /** after a successful save; `keepOpen` when the operator held Shift */
  onSaved: (keepOpen: boolean) => void;
};

const PREVIEW_CHARS = 110;

function roleVars(style: RoleStyle | null): CSSProperties {
  return {
    "--role-color": style?.color || "transparent",
    "--role-text": style?.text_color || "inherit",
    "--role-weight": style?.weight || "600",
    "--role-style": style?.font_style || "normal",
  } as CSSProperties;
}

function preview(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > PREVIEW_CHARS ? `${flat.slice(0, PREVIEW_CHARS - 1)}…` : flat;
}

export function RolePicker({ bookId, segment, span, initialSelection, cast, onClose, onSaved }: RolePickerProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const rootRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [selection, setSelection] = useState<TextRange | null>(initialSelection);
  const [scope, setScope] = useState<Scope>(initialSelection ? "selection" : "whole");

  // a new selection inside the paragraph replaces the stored one; losing it (focus in the input) does not
  useEffect(() => {
    const onChange = () => {
      const host = document.getElementById(segmentDomId(segment.id));
      if (!host) return;
      const next = selectionInside(host, segment.text);
      if (next) setSelection(next);
    };
    document.addEventListener("selectionchange", onChange);
    return () => document.removeEventListener("selectionchange", onChange);
  }, [segment.id, segment.text]);

  // focus lives in the dock while it is open; when it closes, the chip (or «роль» tab) that opened it takes it back
  useEffect(() => {
    // the search field takes focus so typing filters at once; on a touch device the dock itself,
    // so the on-screen keyboard does not cover the chips (arrows / Enter / Esc bubble up either way)
    const coarse = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
    (coarse ? rootRef.current : inputRef.current ?? rootRef.current)?.focus({ preventScroll: true });
    const segmentId = segment.id;
    const startAt = span?.start;
    return () => {
      const host = document.getElementById(segmentDomId(segmentId));
      if (!host) return;
      const trigger =
        startAt === undefined
          ? host.querySelector<HTMLElement>(".v2r-role-hint, .v2r-chip--btn")
          : host.querySelector<HTMLElement>(`.v2r-chip--btn[data-start="${startAt}"]`) ?? host.querySelector<HTMLElement>(".v2r-chip--btn");
      const active = document.activeElement;
      if (trigger && (active === null || active === document.body || !document.contains(active))) trigger.focus({ preventScroll: true });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const bookCast = useBookCast(bookId, true);

  const current = useMemo(() => {
    const at = scope === "selection" && selection ? selection.start : span ? span.start : 0;
    return spanAt(segment, at)?.speaker || NARRATOR;
  }, [segment, span, scope, selection]);

  const candidates = useMemo<Candidate[]>(() => {
    const here = new Map<string, ReaderCastEntry>();
    for (const entry of cast) here.set(entry.name, entry);
    const styleOf = (entry: ReaderCastEntry | undefined): RoleStyle | null =>
      entry ? { color: entry.color, text_color: entry.text_color, weight: entry.weight, font_style: entry.font_style } : null;
    const chapterRoles: Candidate[] = cast
      .filter((entry) => entry.name !== NARRATOR && entry.name !== UNSURE)
      .map((entry) => ({
        name: entry.name,
        actor: entry.actor,
        lines: entry.lines,
        style: styleOf(entry),
        foreign: !entry.character_id,
        here: true,
      }));
    const narrator = here.get(NARRATOR);
    const specials: Candidate[] = [
      { name: NARRATOR, actor: narrator?.actor || "", lines: narrator?.lines || 0, style: styleOf(narrator), foreign: false, here: true },
      { name: UNSURE, actor: "не определено", lines: here.get(UNSURE)?.lines || 0, style: null, foreign: false, here: true },
    ];
    const seen = new Set([...chapterRoles.map((item) => item.name), NARRATOR, UNSURE]);
    const bookRoles: Candidate[] = (bookCast.data?.characters ?? [])
      .filter((row) => !row.is_narrator && !seen.has(row.name))
      .map((row) => ({
        name: row.name,
        actor: row.actor_name || "",
        lines: Number(row.lines_count || 0),
        style: {
          color: row.character_color,
          text_color: row.character_text_color,
          weight: row.character_font_weight,
          font_style: row.character_font_style,
        },
        foreign: false,
        here: false,
      }))
      .sort((a, b) => b.lines - a.lines || a.name.localeCompare(b.name, "ru"));
    return [...chapterRoles, ...specials, ...bookRoles];
  }, [cast, bookCast.data]);

  const needle = foldName(query);
  const typed = query.trim();
  const shown = useMemo(() => {
    if (!needle) return candidates.filter((item) => item.here);
    const found = candidates.filter(
      (item) => foldName(item.name).includes(needle) || (item.actor && foldName(item.actor).includes(needle)),
    );
    // «Завести роль» goes last, and only when nothing in the cast is already that name
    const exists = candidates.some((item) => foldName(item.name) === needle);
    if (!exists && typed.length >= 2) {
      found.push({ name: typed, actor: "новая роль", lines: 0, style: null, foreign: false, here: false, create: true });
    }
    return found;
  }, [candidates, needle, typed]);

  useEffect(() => setActive(0), [needle]);
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    el?.scrollIntoView?.({ block: "nearest" });
  }, [active]);

  const spanPartial = span !== null && !(span.start <= 0 && span.end >= segment.text.length);
  const effectiveScope: Scope = scope === "selection" && !selection ? "whole" : scope === "span" && !spanPartial ? "whole" : scope;
  const range: TextRange | null = effectiveScope === "selection" ? selection : effectiveScope === "span" ? span : null;
  const rangeText = range ? segment.text.slice(range.start, range.end) : segment.text;

  const save = useMutation({
    mutationFn: async ({ speaker, create }: { speaker: string; keepOpen: boolean; create?: boolean }) => {
      let name = speaker;
      if (create) {
        const made = await createRole(bookId, speaker);
        if (!made.ok) throw new Error(made.error || "Роль не создалась.");
        name = made.name;
      }
      const spans: ReassignSpan[] = range ? spansForRange(segment, range, name) : spansForWhole(segment, name);
      return reassignSegment(segment.id, spans);
    },
    onSuccess: async (result, { speaker, keepOpen, create }) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Роль не изменена", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      const label = speaker === UNSURE ? "не определено" : speaker;
      pushToast({
        tone: "success",
        title: create ? `Новая роль: ${label}` : `Роль изменена: ${label}`,
        detail: effectiveScope === "whole" ? "Весь абзац." : `«${preview(rangeText)}»`,
      });
      const impactToast = reassignImpactToast(result.recording_impact);
      if (impactToast) pushToast({ ...impactToast, durationMs: 9000 });
      // Close first: refetching a 150-segment chapter takes a second or two, and a
      // picker that lingers over «Сохраняю…» reads as a save that did not happen.
      onSaved(keepOpen);
      await invalidateReader(queryClient, bookId, ["chapters", "cast", "disputed"]);
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Роль не изменена", detail: describeReassignError(error) });
    },
  });

  const pick = (candidate: Candidate, keepOpen: boolean) => {
    if (save.isPending) return;
    // Both «завести роль» and a name the markup has without a cast row create first.
    save.mutate({ speaker: candidate.name, keepOpen, create: candidate.create || candidate.foreign });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (shown.length === 0) return;
      event.preventDefault();
      setActive((index) => (index + (event.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length);
      return;
    }
    if (event.key === "Enter") {
      const control = (event.target as HTMLElement | null)?.closest?.("[data-action]");
      if (control) return; // scope / close buttons handle their own Enter
      event.preventDefault();
      const candidate = shown[active];
      if (candidate) pick(candidate, event.shiftKey);
      return;
    }
    // type anywhere in the dock: the search field takes the character
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.target !== inputRef.current) {
      inputRef.current?.focus();
    }
  };

  const onPickClick = (event: MouseEvent<HTMLButtonElement>, candidate: Candidate) => {
    pick(candidate, event.shiftKey);
  };

  const scopeButton = (value: Scope, label: string, enabled: boolean, title?: string) => (
    <button
      type="button"
      className={effectiveScope === value ? "v2r-seg-btn is-on" : "v2r-seg-btn"}
      onClick={() => setScope(value)}
      disabled={!enabled}
      aria-pressed={effectiveScope === value}
      title={title}
      data-action="scope"
    >
      {label}
    </button>
  );

  return (
    <section
      ref={rootRef}
      className="v2r-dock v2r-dock--role"
      role="region"
      aria-label="Переназначить роль"
      tabIndex={-1}
      onKeyDown={onKeyDown}
    >
      <div className="v2r-dock-head">
        <span className="v2r-dock-title">Роль</span>
        <span className="v2r-dock-source">
          сейчас: <strong>{current === UNSURE ? "не определено" : current}</strong>
        </span>
        <div className="v2r-seg" role="group" aria-label="Что переназначить">
          {scopeButton("whole", "Весь абзац", true)}
          {spanPartial ? scopeButton("span", "Эта реплика", true) : null}
          {scopeButton(
            "selection",
            "Только выделенное",
            selection !== null,
            selection ? undefined : "Выделите текст внутри абзаца",
          )}
        </div>
        <button type="button" className="btn btn-sm btn-ghost v2r-btn v2r-dock-close" onClick={onClose} aria-label="Закрыть выбор роли" data-action="close">
          ✕
        </button>
      </div>

      <p className="v2r-dock-quote" title={rangeText}>
        «{preview(rangeText)}»
      </p>

      <div className="v2r-dock-row">
        <label className="v2r-search v2r-search--dock">
          <input
            ref={inputRef}
            className="v2r-search-input"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Найти роль в касте книги…"
            aria-label="Поиск роли по касту книги"
            autoComplete="off"
          />
        </label>
        {needle && bookCast.isLoading ? <span className="v2r-dock-hint">Загружаю каст книги…</span> : null}
        {needle && bookCast.isError ? <span className="v2r-dock-hint">Каст книги недоступен, ищу только по главе.</span> : null}
      </div>

      <div ref={listRef} className="v2r-pick-list" role="listbox" aria-label="Роли">
        {shown.length === 0 ? (
          <span className="v2r-dock-hint">Впишите имя роли — её можно завести прямо отсюда.</span>
        ) : null}
        {shown.map((candidate, index) => {
          const unsure = candidate.name === UNSURE;
          const className = [
            "v2r-pick",
            index === active ? "is-active" : "",
            candidate.name === current ? "is-current" : "",
            unsure ? "v2r-role--unsure" : "",
            candidate.create ? "v2r-pick--create" : "",
            candidate.foreign ? "v2r-role--foreign" : "",
            candidate.here ? "" : "v2r-pick--book",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <button
              key={candidate.name}
              type="button"
              className={className}
              style={roleVars(candidate.style)}
              role="option"
              aria-selected={index === active}
              data-index={index}
              tabIndex={-1}
              disabled={save.isPending}
              onMouseEnter={() => setActive(index)}
              onClick={(event) => onPickClick(event, candidate)}
              title={
                candidate.create
                  ? "Завести роль в касте книги и назначить её этой реплике"
                  : candidate.foreign
                    ? "Этого имени нет в касте — оно будет заведено и назначено"
                    : candidate.here
                      ? undefined
                      : "Роль из других глав"
              }
            >
              <span className="v2r-role-chip">{candidate.create ? `+ ${candidate.name}` : unsure ? "?" : candidate.name}</span>
              <span className="v2r-role-meta">
                <span className="v2r-role-actor">
                  {candidate.create
                    ? "завести новую роль"
                    : candidate.foreign
                      ? "нет в касте — заведём"
                      : candidate.actor || (unsure ? "" : "— актёр не назначен")}
                </span>
                {candidate.create ? null : <span className="v2r-role-n num">{candidate.lines}</span>}
              </span>
            </button>
          );
        })}
      </div>

      <div className="v2r-dock-actions">
        <span className="v2r-dock-hint">
          {save.isPending
            ? "Сохраняю…"
            : "↑ ↓ роль · Enter — назначить · Shift — назначить и оставить открытым · Esc — закрыть · имени нет в касте — впишите его и выберите «+ имя»"}
        </span>
      </div>
    </section>
  );
}
