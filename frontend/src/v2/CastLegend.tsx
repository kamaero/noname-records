import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import { NARRATOR, UNSURE, type ReaderCastEntry } from "./types";
import { roleScriptHref, type RoleScriptBase } from "./roleRoutes";

type CastLegendProps = {
  cast: ReaderCastEntry[];
  selected: ReadonlySet<string>;
  onToggle: (name: string) => void;
  onClear: () => void;
  /** editors only: opens the palette editor for a chip (dictors get the plain legend) */
  onEdit?: (entry: ReaderCastEntry) => void;
  /** editors only: rendered under the chips (palette conflicts line) */
  children?: ReactNode;
  /** the book, for the link to a role's lines across all of it */
  bookId?: string;
  roleScriptBase?: RoleScriptBase;
  roleScriptSearch?: string;
};

function roleVars(entry: ReaderCastEntry): CSSProperties {
  return {
    "--role-color": entry.color,
    "--role-text": entry.text_color,
    "--role-weight": entry.weight,
    "--role-style": entry.font_style,
  } as CSSProperties;
}

const NARRATOR_HINT = "Рассказчик остаётся в нейтральной подаче";
const FOREIGN_HINT = "нет в касте — переназначьте";

/**
 * The chapter's cast, as the roles it can be filtered by.
 *
 * It used to sit above the text, where it took the top of every screen and pushed the
 * script down; it lives in the right-hand sheet now and the toolbar says what is
 * selected. The chips are the filter: click a role and only its lines stay.
 */
export function CastLegend({
  cast,
  selected,
  onToggle,
  onClear,
  onEdit,
  children,
  bookId,
  roleScriptBase = "/reader/role",
  roleScriptSearch = "",
}: CastLegendProps) {
  const roleMode = selected.size > 0;
  // The narrator is not a casting decision — one voice for the whole book, set once in
  // the cast screen — so he is not a chip here. He comes back when he is the selected
  // role, or the narrator's own dictor could not turn his filter off again.
  const shown = cast.filter((entry) => entry.name !== NARRATOR || selected.has(entry.name));
  return (
    <section className={roleMode ? "v2r-legend v2r-legend--role-mode" : "v2r-legend"} aria-label="Каст главы">
      <div className="v2r-legend-head">
        <span className="v2r-legend-hint">
          {roleMode
            ? `Показаны реплики: ${[...selected].join(", ")}`
            : bookId
              ? "Нажмите на роль — в главе останутся только её реплики. Кнопка «все» — её реплики по всей книге, с контекстом."
              : "Нажмите на роль — останутся только её реплики"}
        </span>
        {roleMode ? (
          <button type="button" className="btn btn-sm btn-ghost v2r-legend-clear" onClick={onClear}>
            Показать всё
          </button>
        ) : null}
      </div>
      {shown.length > 0 ? (
        <div className="v2r-roles">
          {shown.map((entry) => {
            const on = selected.has(entry.name);
            const unsure = entry.name === UNSURE;
            // a speaker the cast table does not know (an import artefact): grey, until its lines are reassigned
            const foreign = !unsure && entry.name !== NARRATOR && !entry.character_id;
            const chip = (
              <button
                key={entry.name}
                type="button"
                className={["v2r-role", on ? "is-on" : "", unsure ? "v2r-role--unsure" : "", foreign ? "v2r-role--foreign" : ""]
                  .filter(Boolean)
                  .join(" ")}
                style={roleVars(entry)}
                onClick={() => onToggle(entry.name)}
                aria-pressed={on}
                title={foreign ? FOREIGN_HINT : undefined}
              >
                <span className="v2r-role-chip">{unsure ? "?" : entry.name}</span>
                <span className="v2r-role-meta">
                  <span className="v2r-role-actor">{unsure ? "не определено" : entry.actor || "— актёр не назначен"}</span>
                  <span className="v2r-role-n num">{entry.lines}</span>
                </span>
                {/* who the character is, and how the author wants him voiced */}
                {entry.note || entry.about ? (
                  <span className="v2r-role-about" title={[entry.about, entry.note].filter(Boolean).join("\n")}>
                    {entry.note || entry.about}
                  </span>
                ) : null}
              </button>
            );
            const allLines = bookId && !unsure && !foreign ? (
              <Link
                key={`all-${entry.name}`}
                className="v2r-role-all"
                to={roleScriptHref(bookId, entry.name, roleScriptBase, roleScriptSearch)}
                title={`Все реплики роли «${entry.name}» по всей книге, с контекстом`}
                aria-label={`Все реплики роли ${entry.name} по всей книге`}
              >
                все
              </Link>
            ) : null;
            const narrator = entry.name === NARRATOR || !entry.character_id;
            if (!onEdit || unsure) {
              return allLines ? (
                <div key={entry.name} className={on ? "v2r-role-wrap is-on" : "v2r-role-wrap"} style={roleVars(entry)}>
                  {chip}
                  {allLines}
                </div>
              ) : chip;
            }
            const editHint = foreign ? FOREIGN_HINT : narrator ? NARRATOR_HINT : `Палитра: ${entry.name}`;
            return (
              <div key={entry.name} className={on ? "v2r-role-wrap is-on" : "v2r-role-wrap"} style={roleVars(entry)}>
                {chip}
                {allLines}
                <button
                  type="button"
                  className="v2r-role-edit"
                  onClick={() => onEdit(entry)}
                  disabled={narrator}
                  title={editHint}
                  aria-label={narrator ? editHint : `Изменить палитру роли ${entry.name}`}
                >
                  <Icon name="dots" />
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
      {bookId && selected.size === 1 ? (
        <Link
          className="btn btn-sm v2r-btn v2r-legend-all"
          to={roleScriptHref(bookId, [...selected][0], roleScriptBase, roleScriptSearch)}
        >
          Все реплики роли в книге →
        </Link>
      ) : null}
      {children}
    </section>
  );
}


/**
 * The same cast as a static block, for paper: the sheet is a screen thing, and a
 * dictor printing the chapter needs the colours and the actors on page one.
 */
export function CastLegendPrint({ cast }: { cast: ReaderCastEntry[] }) {
  const roles = cast.filter((entry) => entry.name !== NARRATOR);
  if (roles.length === 0) return null;
  return (
    <section className="v2r-legend v2r-legend--print" aria-hidden="true">
      <div className="v2r-roles">
        {roles.map((entry) => (
          <span key={entry.name} className="v2r-role" style={roleVars(entry)}>
            <span className="v2r-role-chip">{entry.name === UNSURE ? "?" : entry.name}</span>
            <span className="v2r-role-meta">
              <span className="v2r-role-actor">{entry.name === UNSURE ? "не определено" : entry.actor || "— актёр не назначен"}</span>
              <span className="v2r-role-n num">{entry.lines}</span>
            </span>
          </span>
        ))}
      </div>
    </section>
  );
}
