/**
 * «Развести цвета» — what will change, before it changes.
 *
 * The auto-fix used to be a button that repainted an unknown number of roles and
 * reported the count afterwards. What a conflict *is* was equally unclear: the old
 * line quoted two different numbers (a filtered one and a raw one) without saying
 * what either meant. Here the rule is stated in words, every pair is listed, and
 * every role about to change shows its colour now and after.
 */
import "./PaletteFixSheet.css";

import { useMemo } from "react";
import { Button, Sheet } from "../../ui";
import { plural } from "../../viewModels/bookHub";
import { buildPaletteResolution, paletteFromRow, type MeaningfulPaletteConflict, type PaletteDraft } from "../../viewModels/booksCast";
import type { BudgetCharacterRow } from "../../types";

type PaletteFixSheetProps = {
  rows: BudgetCharacterRow[];
  conflicts: MeaningfulPaletteConflict[];
  applying: boolean;
  onApply: (resolution: ReturnType<typeof buildPaletteResolution>) => void;
  onClose: () => void;
};

function Swatch({ palette, name }: { palette: PaletteDraft; name: string }) {
  return (
    <span
      className="pal-swatch"
      style={{ background: palette.background, color: palette.text, fontWeight: palette.weight, fontStyle: palette.style }}
    >
      {name}
    </span>
  );
}

export function PaletteFixSheet({ rows, conflicts, applying, onApply, onClose }: PaletteFixSheetProps) {
  const resolution = useMemo(() => buildPaletteResolution(rows, conflicts), [rows, conflicts]);
  const byId = useMemo(() => new Map(rows.map((row) => [row.character_id, row] as const)), [rows]);

  return (
    <Sheet
      title="Развести цвета"
      subtitle={`${conflicts.length} ${plural(conflicts.length, "пара", "пары", "пар")} · перекрасим ${plural(resolution.length, "роль", "роли", "ролей")}: ${resolution.length}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="primary" loading={applying} disabled={resolution.length === 0} onClick={() => onApply(resolution)}>
            Перекрасить {resolution.length}
          </Button>
          <Button variant="ghost" disabled={applying} onClick={onClose}>
            Отмена
          </Button>
        </>
      }
    >
      <p className="pal-note">
        Конфликт — это две роли, у которых почти одинаковый цвет <em>и</em> которые встречаются в одной главе:
        диктор с распечаткой их перепутает. Роли, что нигде не пересекаются, могут быть одного цвета — это не конфликт.
      </p>

      <section className="v2r-sheet-section">
        <h3 className="v2r-sheet-h">Похожие пары</h3>
        <ul className="pal-pairs">
          {conflicts.slice(0, 24).map((item) => (
            <li key={`${item.leftId}:${item.rightId}`}>
              <span className="pal-pair-names">{item.leftName} ↔ {item.rightName}</span>
              <span className="pal-pair-meta num">
                {item.sharedChapters} {plural(item.sharedChapters, "общая глава", "общие главы", "общих глав")}
              </span>
            </li>
          ))}
          {conflicts.length > 24 ? <li className="pal-pair-more num">…ещё {conflicts.length - 24}</li> : null}
        </ul>
      </section>

      <section className="v2r-sheet-section">
        <h3 className="v2r-sheet-h">Что изменится</h3>
        {resolution.length === 0 ? (
          <p className="v2r-sheet-empty">
            Свободных различимых сочетаний не осталось — задайте цвет вручную через свотч роли.
          </p>
        ) : (
          <ul className="pal-changes">
            {resolution.map((entry) => {
              const row = byId.get(entry.row.character_id);
              return (
                <li key={entry.row.character_id}>
                  {row ? <Swatch palette={paletteFromRow(row)} name={entry.row.name} /> : null}
                  <span className="pal-arrow" aria-hidden="true">→</span>
                  <Swatch palette={entry.palette} name={entry.row.name} />
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </Sheet>
  );
}
