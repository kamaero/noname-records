/**
 * «С кем этот цвет можно спутать» — the list under the colour pickers.
 *
 * It used to be a refusal: a toast saying the colour was too close to something, with
 * the save cancelled. The colour is the director's decision — he is looking at the two
 * roles on the same page and we are not — so the roles are named, with the chapters
 * they share, and the button below still saves.
 */
import type { PaletteConflict } from "../../viewModels/booksCast";

const SHOWN = 4;

export function PaletteWarning({ conflicts }: { conflicts: PaletteConflict[] }) {
  if (conflicts.length === 0) return null;
  const shown = conflicts.slice(0, SHOWN);
  const rest = conflicts.length - shown.length;
  return (
    <>
      <strong>Похожий цвет у ролей из тех же глав — на распечатке их можно спутать:</strong>
      <ul>
        {shown.map((item) => (
          <li key={item.rightId}>
            {item.rightName} — {item.chapters.length > 3 ? `главы ${item.chapters.slice(0, 3).join(", ")}…` : `главы ${item.chapters.join(", ")}`}
          </li>
        ))}
        {rest > 0 ? <li>…и ещё {rest}</li> : null}
      </ul>
    </>
  );
}
