import { Button } from "../../ui";
import type { MeaningfulPaletteConflict } from "../../viewModels/booksCast";

type CastConflictsNoteProps = {
  conflicts: MeaningfulPaletteConflict[];
  onFix: () => void;
};

/**
 * One compact line above the table: what a conflict is, how many there are, the
 * worst pairs, and the way in. The fix itself happens behind a preview, so this
 * line never repaints anything on its own.
 */
export function CastConflictsNote({ conflicts, onFix }: CastConflictsNoteProps) {
  if (!conflicts.length) return null;
  const shown = conflicts.slice(0, 6);
  const rest = conflicts.length - shown.length;
  return (
    <div className="cast-conflicts" role="status">
      <span className="cast-conflicts-title" title="Две роли одного цвета, которые говорят в одной главе: на распечатке их не различить">
        Похожие цвета у ролей из одной главы: <strong className="num">{conflicts.length}</strong>
      </span>
      <span className="cast-conflicts-list">
        {shown.map((item) => (
          <span key={`${item.leftId}:${item.rightId}`} className="cast-conflicts-pair" title={`главы ${item.chapters.join(", ")}`}>
            {item.leftName} ↔ {item.rightName}
          </span>
        ))}
        {rest > 0 ? <span className="cast-conflicts-more num">ещё {rest}</span> : null}
      </span>
      <Button size="sm" variant="secondary" onClick={onFix}>
        Развести цвета
      </Button>
    </div>
  );
}
