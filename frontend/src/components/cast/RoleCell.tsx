import { roleLabel } from "../../v2/types";
import { pluralRu, splitCastAliases, type CastRow } from "../../viewModels/booksCast";
import { InlineCell } from "./InlineCell";

type RoleCellProps = {
  row: CastRow;
  /** the aliases are open; the table owns this so it survives windowing */
  open: boolean;
  onToggle: (id: string) => void;
  saving: boolean;
  onRename: (row: CastRow, next: string) => void;
};

/** Role name with a faint «+N» alias count; the aliases open inline on click. */
export function RoleCell({ row, open, onToggle, saving, onRename }: RoleCellProps) {
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
      {show ? <div className="cast-aliases">{aliases.join(" · ")}</div> : null}
    </div>
  );
}
