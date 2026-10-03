/**
 * «Мои реплики» — the way from any chapter to a role's lines across the whole book.
 *
 * The chapter's cast could not be this door. It lists who speaks *here*, and the
 * dictors who asked for this play exactly the characters who do not: a dozen lines
 * scattered over sixty chapters. In nine chapters out of ten their role was not in
 * the panel, so neither was the way to it. This list belongs to the book.
 *
 * «N реплик · M глав» is the part that answers the question actually being asked —
 * «где я вообще говорю» — before the actor has opened anything.
 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Sheet } from "../ui/Sheet";
import { plural } from "./useIsMobile";
import type { BookCastCharacter } from "./types";
import { roleScriptHref, type RoleScriptBase } from "./roleRoutes";

type RoleFinderProps = {
  bookId: string;
  cast: BookCastCharacter[];
  loading: boolean;
  onClose: () => void;
  roleScriptBase?: RoleScriptBase;
  roleScriptSearch?: string;
};

/** Case and ё — the two ways the same name gets typed differently. */
function fold(value: string): string {
  return (value || "").toLowerCase().replace(/ё/g, "е").trim();
}

function countsLabel(entry: BookCastCharacter): string {
  const chapters = entry.appears_in_chapters.length;
  return (
    `${entry.lines_count} ${plural(entry.lines_count, "реплика", "реплики", "реплик")}` +
    ` · ${chapters} ${plural(chapters, "глава", "главы", "глав")}`
  );
}

export function RoleFinder({
  bookId,
  cast,
  loading,
  onClose,
  roleScriptBase = "/reader/role",
  roleScriptSearch = "",
}: RoleFinderProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

  // A role nobody speaks is not a part anyone records; it would only pad the list.
  const speaking = useMemo(() => cast.filter((entry) => entry.lines_count > 0), [cast]);
  const mine = useMemo(() => speaking.filter((entry) => entry.mine), [speaking]);
  const found = useMemo(() => {
    const needle = fold(query);
    if (!needle) return speaking;
    return speaking.filter(
      (entry) => fold(entry.name).includes(needle) || fold(entry.actor_name).includes(needle),
    );
  }, [speaking, query]);

  const open = (role: string) => {
    onClose();
    navigate(roleScriptHref(bookId, role, roleScriptBase, roleScriptSearch));
  };

  const row = (entry: BookCastCharacter) => (
    <button
      key={entry.character_id || entry.name}
      type="button"
      className="v2r-findrole"
      onClick={() => open(entry.name)}
      title={`Все реплики роли «${entry.name}» по всей книге, с контекстом`}
    >
      <span className="v2r-findrole-name">
        <span className="v2r-findrole-dot" style={{ background: entry.character_color }} aria-hidden="true" />
        {entry.name}
      </span>
      <span className="v2r-findrole-meta num">{countsLabel(entry)}</span>
      {entry.actor_name ? <span className="v2r-findrole-actor">{entry.actor_name}</span> : null}
    </button>
  );

  return (
    <Sheet
      title="Все реплики роли"
      subtitle="По всей книге, в порядке чтения, с контекстом вокруг каждой"
      onClose={onClose}
    >
      {loading ? <p className="v2r-find-note">Собираю роли книги…</p> : null}

      {!loading && mine.length > 0 && !query ? (
        <section className="v2r-findgroup">
          <h3 className="v2r-findgroup-title">Мои роли</h3>
          {mine.map(row)}
        </section>
      ) : null}

      {!loading && speaking.length > 0 ? (
        <section className="v2r-findgroup">
          <h3 className="v2r-findgroup-title">{query ? "Найдено" : "Все роли книги"}</h3>
          <label className="v2r-search v2r-search--dock">
            <Icon name="search" />
            <input
              className="v2r-search-input"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Роль или актёр…"
              aria-label="Поиск роли по всей книге"
            />
          </label>
          {found.length > 0 ? (
            found.map(row)
          ) : (
            <p className="v2r-find-note">Никто с таким именем в книге не говорит.</p>
          )}
        </section>
      ) : null}

      {!loading && speaking.length === 0 ? (
        <p className="v2r-find-note">В книге ещё нет размеченных реплик.</p>
      ) : null}
    </Sheet>
  );
}
