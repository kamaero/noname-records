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
          value={row.name}
          saving={saving}
          disabled={row.is_narrator}
          title={row.is_narrator ? "У рассказчика тысячи реплик по всей книге — имя не правится отсюда" : undefined}
          label={`Имя роли ${row.name}`}
          onCommit={(next) => onRename(row, next)}
        />
        {aliases.length ? (
          <button
            type="button"
            className="cast-alias-toggle num"
            aria-expanded={show}
            aria-label={`${show ? "Скрыть" : "Показать"} ${aliases.length} ${pluralRu(aliases.length, "алиас", "алиаса", "алиасов")} роли ${row.name}`}
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
