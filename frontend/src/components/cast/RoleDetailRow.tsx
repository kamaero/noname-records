import "./RoleDetailRow.css";

import { useState } from "react";
import { InlineCell } from "./InlineCell";
import { Button } from "../../ui";
import { chapterMismatch, describeChapterMismatch, pluralRu, splitCastAliases, type CastRow } from "../../viewModels/booksCast";
import type { CharacterAbout } from "../../v2/editorApi";
import type { RoleIntersection } from "../../types";

const SEVERITY: Record<string, string> = { dialogue: "dialogue", scene: "scene", chapter: "chapter" };

const CANON: Record<string, string> = {
  known: "name found in canon",
  new: "name not found in canon",
  "": "canon not checked",
};

type RoleDetailRowProps = {
  row: CastRow;
  intersections: (RoleIntersection & { partner: string })[];
  defaultRate: number;
  saving: boolean;
  onSaveAbout: (row: CastRow, patch: CharacterAbout) => void;
  onSaveRate: (row: CastRow, rate: number) => void;
  onAcknowledge: (row: CastRow, partner: string, reason: string) => void;
  onMerge: (row: CastRow) => void;
  onDelete: (row: CastRow) => void;
};

/**
 * Всё о роли, что строка специально прячет: возраст, алиасы, канон, деньги и
 * пересечения. Строка держит шесть колонок, по которым скользит глаз; здесь —
 * то, ради чего в неё заходят.
 */
export function RoleDetailRow({
  row, intersections, defaultRate, saving,
  onSaveAbout, onSaveRate, onAcknowledge, onMerge, onDelete,
}: RoleDetailRowProps) {
  const aliases = splitCastAliases(row);
  const got = (row.appears_in_chapters || []).length;
  // Та же проверка, что и у колонки «Глав»: расхождение считается только против
  // непустой заявки извлечения, иначе «извлечение назвало 0» стояло бы у каждой
  // роли, про которую извлечение просто промолчало.
  const mismatch = chapterMismatch(row);
  const manual = Number(row.manual_rate_rub_per_min || 0);
  const rate = manual > 0 ? manual : defaultRate;

  return (
    <div className="rdr">
      <div className="rdr-grid">
        <section>
          <h3 className="rdr-lbl">Role</h3>
          <dl className="rdr-kv">
            <dt>Species</dt>
            <dd>
              <InlineCell
                value={row.race || ""}
                placeholder="+ species"
                saving={saving}
                label={`Role species: ${row.name}`}
                onCommit={(next) => onSaveAbout(row, { race: next })}
              />
            </dd>
            <dt>Age</dt>
            <dd>
              <InlineCell
                value={row.age || ""}
                placeholder="+ age"
                saving={saving}
                label={`Role age: ${row.name}`}
                onCommit={(next) => onSaveAbout(row, { age: next })}
              />
            </dd>
            <dt>Aliases</dt>
            <dd>{aliases.length ? aliases.join(" · ") : <span className="cast-muted">none</span>}</dd>
            <dt>Chapters</dt>
            <dd>
              {mismatch
                ? describeChapterMismatch(mismatch)
                : (row.claimed_chapters || []).length
                  ? `${got} — annotation and extraction agree`
                  : `${got} from annotation; extraction supplied no chapter data for this role`}
            </dd>
            <dt>Canon</dt>
            <dd className="cast-muted">{CANON[row.canon_status ?? ""]}</dd>
          </dl>
        </section>

        <section>
          <h3 className="rdr-lbl">Budget</h3>
          <dl className="rdr-kv">
            <dt>Rate</dt>
            <dd>
              <InlineCell
                value={String(rate)}
                display={<span className={manual > 0 ? undefined : "cast-muted"}>{rate} RUB/min</span>}
                type="number"
                min={0}
                mono
                saving={saving}
                label={`Role rate: ${row.name}, RUB per minute`}
                onCommit={(next) => {
                  const value = Number(next);
                  if (Number.isFinite(value) && value >= 0) onSaveRate(row, Math.round(value));
                }}
              />
            </dd>
            <dt>Lines</dt>
            <dd><span className="num">{row.lines_count.toLocaleString("en-US")}</span></dd>
            <dt>Amount</dt>
            <dd className="rdr-strong"><span className="num">{Math.round(row.total_rub).toLocaleString("en-US")}</span> ₽</dd>
          </dl>
        </section>

        <section className="rdr-wide">
          <h3 className="rdr-lbl">Overlapping roles assigned to the same voice actor</h3>
          {intersections.length ? (
            intersections.map((item) => (
              <IntersectionCard key={`${item.role_a}-${item.role_b}`} row={row} item={item} onAcknowledge={onAcknowledge} />
            ))
          ) : (
            // Список уже отфильтрован по «не признано» — пустота здесь не значит, что
            // актёр вообще ни с кем не встречается, только что решать больше не о чём.
            <p className="cast-muted">No unapproved overlaps. Any overlaps have been accepted as intentional.</p>
          )}
        </section>
      </div>

      <footer className="rdr-foot">
        <span className="cast-muted">
          {row.name}
          {aliases.length ? ` · ${aliases.length} ${pluralRu(aliases.length, "alias", "aliases", "aliases")}` : ""}
        </span>
        {/* Рассказчик — не роль ни для слияния, ни для удаления: у него тысячи реплик,
            и ручка `merge_speaker` отдельно отбивает его же кодом. Молчащую роль без
            дублей можно удалить; молчащую с дублями — только предупредить, что нельзя,
            а не дать кнопку без последствий. Тот же порядок, что был на карте персонажей. */}
        {row.is_narrator ? null : (
          <span className="rdr-acts">
            <Button size="sm" variant="secondary" onClick={() => onMerge(row)}>Merge into another role…</Button>
            {row.lines_count === 0 ? (
              row.has_audio ? (
                <span className="cast-muted">Takes exist under this name; deletion is unavailable.</span>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => onDelete(row)}>Delete role</Button>
              )
            ) : null}
          </span>
        )}
      </footer>
    </div>
  );
}

/** Одно пересечение: чем оно плохо и кнопка сказать «так задумано» с причиной. */
function IntersectionCard({
  row, item, onAcknowledge,
}: {
  row: CastRow;
  item: RoleIntersection & { partner: string };
  onAcknowledge: (row: CastRow, partner: string, reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [asking, setAsking] = useState(false);
  const tone = item.severity === "dialogue" ? "is-bad" : item.severity === "scene" ? "is-warn" : "is-info";
  return (
    <div className={`rdr-ix ${tone}`}>
      <div className="rdr-ix-head">
        <b>{item.partner}</b>
        <span className="cast-muted">
          {SEVERITY[item.severity]} · {item.distance} paragraphs · {item.same_race ? "same species" : "different species"}
        </span>
      </div>
      {asking ? (
        <div className="rdr-ix-ask">
          <input
            className="rdr-ix-reason"
            value={reason}
            autoFocus
            placeholder="twins · different registers · heavily processed voices"
            aria-label={`Why is the overlap of role ${row.name} with ${item.partner} acceptable`}
            onChange={(event) => setReason(event.target.value)}
          />
          <Button
            size="sm"
            variant="secondary"
            disabled={!reason.trim()}
            onClick={() => { onAcknowledge(row, item.partner, reason.trim()); setAsking(false); }}
          >
            Save reason
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button>
        </div>
      ) : (
        <Button size="sm" variant="secondary" onClick={() => setAsking(true)}>Intentional overlap…</Button>
      )}
    </div>
  );
}
