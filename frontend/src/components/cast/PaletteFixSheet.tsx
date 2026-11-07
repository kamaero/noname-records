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
      title="Adjust colors"
      subtitle={`${conflicts.length} ${plural(conflicts.length, "pair", "pairs", "pairs")} · roles to recolor: ${plural(resolution.length, "role", "roles", "roles")}: ${resolution.length}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="primary" loading={applying} disabled={resolution.length === 0} onClick={() => onApply(resolution)}>
            Recolor {resolution.length}
          </Button>
          <Button variant="ghost" disabled={applying} onClick={onClose}>
            Cancel
          </Button>
        </>
      }
    >
      <p className="pal-note">
        A conflict is a pair of roles with nearly identical colors <em>and</em> appearing in the same chapter, making them hard to distinguish in print. Roles that never overlap can share a color.
      </p>

      <section className="v2r-sheet-section">
        <h3 className="v2r-sheet-h">Similar pairs</h3>
        <ul className="pal-pairs">
          {conflicts.slice(0, 24).map((item) => (
            <li key={`${item.leftId}:${item.rightId}`}>
              <span className="pal-pair-names">{item.leftName} ↔ {item.rightName}</span>
              <span className="pal-pair-meta num">
                {item.sharedChapters} {plural(item.sharedChapters, "shared chapter", "shared chapters", "shared chapters")}
              </span>
            </li>
          ))}
          {conflicts.length > 24 ? <li className="pal-pair-more num">…more {conflicts.length - 24}</li> : null}
        </ul>
      </section>

      <section className="v2r-sheet-section">
        <h3 className="v2r-sheet-h">What will change</h3>
        {resolution.length === 0 ? (
          <p className="v2r-sheet-empty">
            No distinct color combinations remain. Set a color manually using the role swatch.
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
