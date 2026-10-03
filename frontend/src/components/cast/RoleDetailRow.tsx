import "./RoleDetailRow.css";

import { useState } from "react";
import { InlineCell } from "./InlineCell";
import { Button } from "../../ui";
import { chapterMismatch, describeChapterMismatch, pluralRu, splitCastAliases, type CastRow } from "../../viewModels/booksCast";
import type { CharacterAbout } from "../../v2/editorApi";
import type { RoleIntersection } from "../../types";

const SEVERITY: Record<string, string> = { dialogue: "диалог", scene: "сцена", chapter: "глава" };

const CANON: Record<string, string> = {
  known: "канон знает это имя",
  new: "в каноне такого имени нет",
  "": "канон не сверялся",
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
          <h3 className="rdr-lbl">Роль</h3>
          <dl className="rdr-kv">
            <dt>Раса</dt>
            <dd>
              <InlineCell
                value={row.race || ""}
                placeholder="+ раса, вид"
                saving={saving}
                label={`Раса роли ${row.name}`}
                onCommit={(next) => onSaveAbout(row, { race: next })}
              />
            </dd>
            <dt>Возраст</dt>
            <dd>
              <InlineCell
                value={row.age || ""}
                placeholder="+ возраст"
                saving={saving}
                label={`Возраст роли ${row.name}`}
                onCommit={(next) => onSaveAbout(row, { age: next })}
              />
            </dd>
            <dt>Алиасы</dt>
            <dd>{aliases.length ? aliases.join(" · ") : <span className="cast-muted">нет</span>}</dd>
            <dt>Главы</dt>
            <dd>
              {mismatch
                ? describeChapterMismatch(mismatch)
                : (row.claimed_chapters || []).length
                  ? `${got} — разметка и извлечение сходятся`
                  : `${got} по разметке; извлечение про эту роль ничего не заявляло`}
            </dd>
            <dt>Канон</dt>
            <dd className="cast-muted">{CANON[row.canon_status ?? ""]}</dd>
          </dl>
        </section>

        <section>
          <h3 className="rdr-lbl">Смета</h3>
          <dl className="rdr-kv">
            <dt>Ставка</dt>
            <dd>
              <InlineCell
                value={String(rate)}
                display={<span className={manual > 0 ? undefined : "cast-muted"}>{rate} ₽/мин</span>}
                type="number"
                min={0}
                mono
                saving={saving}
                label={`Ставка роли ${row.name}, рублей в минуту`}
                onCommit={(next) => {
                  const value = Number(next);
                  if (Number.isFinite(value) && value >= 0) onSaveRate(row, Math.round(value));
                }}
              />
            </dd>
            <dt>Реплик</dt>
            <dd><span className="num">{row.lines_count.toLocaleString("ru-RU")}</span></dd>
            <dt>Сумма</dt>
            <dd className="rdr-strong"><span className="num">{Math.round(row.total_rub).toLocaleString("ru-RU")}</span> ₽</dd>
          </dl>
        </section>

        <section className="rdr-wide">
          <h3 className="rdr-lbl">Пересечения ролей одного актёра</h3>
          {intersections.length ? (
            intersections.map((item) => (
              <IntersectionCard key={`${item.role_a}-${item.role_b}`} row={row} item={item} onAcknowledge={onAcknowledge} />
            ))
          ) : (
            // Список уже отфильтрован по «не признано» — пустота здесь не значит, что
            // актёр вообще ни с кем не встречается, только что решать больше не о чём.
            <p className="cast-muted">Непризнанных пересечений нет: либо их не было, либо все признаны «так задумано».</p>
          )}
        </section>
      </div>

      <footer className="rdr-foot">
        <span className="cast-muted">
          {row.name}
          {aliases.length ? ` · ${aliases.length} ${pluralRu(aliases.length, "алиас", "алиаса", "алиасов")}` : ""}
        </span>
        {/* Рассказчик — не роль ни для слияния, ни для удаления: у него тысячи реплик,
            и ручка `merge_speaker` отдельно отбивает его же кодом. Молчащую роль без
            дублей можно удалить; молчащую с дублями — только предупредить, что нельзя,
            а не дать кнопку без последствий. Тот же порядок, что был на карте персонажей. */}
        {row.is_narrator ? null : (
          <span className="rdr-acts">
            <Button size="sm" variant="secondary" onClick={() => onMerge(row)}>Слить с другой ролью…</Button>
            {row.lines_count === 0 ? (
              row.has_audio ? (
                <span className="cast-muted">На это имя записаны дубли — удалить нельзя.</span>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => onDelete(row)}>Удалить роль</Button>
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
          {SEVERITY[item.severity]} · {item.distance} абз. · {item.same_race ? "одна раса" : "разные расы"}
        </span>
      </div>
      {asking ? (
        <div className="rdr-ix-ask">
          <input
            className="rdr-ix-reason"
            value={reason}
            autoFocus
            placeholder="близнецы · разный регистр · демоны, тяжёлая обработка"
            aria-label={`Почему пересечение роли ${row.name} с ${item.partner} допустимо`}
            onChange={(event) => setReason(event.target.value)}
          />
          <Button
            size="sm"
            variant="secondary"
            disabled={!reason.trim()}
            onClick={() => { onAcknowledge(row, item.partner, reason.trim()); setAsking(false); }}
          >
            Запомнить
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>Отмена</Button>
        </div>
      ) : (
        <Button size="sm" variant="secondary" onClick={() => setAsking(true)}>Так задумано…</Button>
      )}
    </div>
  );
}
