import { Link } from "react-router-dom";
import { roleScriptHref } from "../../v2/roleRoutes";
import { roleLabel } from "../../v2/types";
import { pluralRu, splitCastAliases, type CastRow } from "../../viewModels/booksCast";
import { InlineCell } from "./InlineCell";

type RoleCellProps = {
  row: CastRow;
  bookId: string;
  /** the aliases are open; the table owns this so it survives windowing */
  open: boolean;
  onToggle: (id: string) => void;
  saving: boolean;
  onRename: (row: CastRow, next: string) => void;
};

/** Role name with a faint «+N» alias count; the aliases open inline on click. */
export function RoleCell({ row, bookId, open, onToggle, saving, onRename }: RoleCellProps) {
  const aliases = splitCastAliases(row);
  const show = open && aliases.length > 0;
  return (
    <div className="cast-role">
      <div className="cast-role-line">
        <InlineCell
          value={roleLabel(row.name)}
          saving={saving}
          disabled={row.is_narrator}
          title={row.is_narrator ? "The narrator spans the entire book; its name cannot be edited here" : undefined}
          label={`Role name ${row.name}`}
          onCommit={(next) => onRename(row, next)}
        />
        {aliases.length ? (
          <button
            type="button"
            className="cast-alias-toggle num"
            aria-expanded={show}
            aria-label={`${show ? "Hide" : "Show"} ${aliases.length} ${pluralRu(aliases.length, "alias", "aliases", "aliases")} for ${row.name}`}
            onClick={() => onToggle(row.character_id)}
          >
            +{aliases.length}
          </button>
        ) : null}
      </div>
      {row.lines_count > 0 && bookId ? (
        <Link className="cast-lines cast-lines-mobile" to={roleScriptHref(bookId, row.name)}>
          {row.lines_count.toLocaleString("en-US")} {row.lines_count === 1 ? "line" : "lines"}
        </Link>
      ) : null}
      {show ? <div className="cast-aliases">{aliases.join(" · ")}</div> : null}
    </div>
  );
}
