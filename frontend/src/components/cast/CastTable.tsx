import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type UIEvent } from "react";
import { Link } from "react-router-dom";
import { DataTable, type Column } from "../../ui";
import { Icon } from "../Icon";
import { chapterMismatch, characterSwatchStyle, describeChapterMismatch, type CastRow, type CastSortDir, type CastSortKey } from "../../viewModels/booksCast";
import type { RoleIntersectionForRole } from "../../pages/CastPage";
import type { CharacterAbout } from "../../v2/editorApi";
import { InlineCell } from "./InlineCell";
import { RoleCell } from "./RoleCell";
import { RoleDetailRow } from "./RoleDetailRow";
import { DeadlineChip } from "../DeadlineChip";
import { ActorPicker, type PickerDictor } from "./ActorPicker";

/* The window is arithmetic over a row height, so the height has to be true: spacers of
   `count × ROW_H` under rows that are actually taller make the table shorter than its
   content, and every scroll re-renders a different window with a different real height.
   That oscillation is the shaking, and the bottom of the list is never reachable.
   This is a fallback only — the real height is measured from a rendered row below. */
const ROW_H_FALLBACK = 40;
const OVERSCAN = 40;
/** below this many rows everything is rendered; windowing only pays off on long casts */
const WINDOW_FROM = 120;

const NARRATOR_HINT = "Рассказчик остаётся в нейтральной подаче";

type TableItem = { kind: "row"; row: CastRow } | { kind: "spacer"; key: string; height: number };

export type CastSort = { key: CastSortKey; dir: CastSortDir } | null;

export type ActorPickerConfig = {
  /** все роли книги, а не отфильтрованные: «уже Егбод в этой книге» смотрит на весь каст */
  allRows: CastRow[];
  dictors: PickerDictor[];
  canListen: boolean;
  proposeOnly: boolean;
  canCreate: boolean;
  canRecast: (row: CastRow) => boolean;
  onPick: (row: CastRow, value: string, chosen: PickerDictor | null) => void;
  onCreate: (row: CastRow, name: string) => void;
  onRecast: (row: CastRow) => void;
};

type CastTableProps = {
  rows: CastRow[];
  /** выпадашка дикторов вместо поля с подсказками (план 2б); нет — прежнее поле */
  actorPicker?: ActorPickerConfig;
  loading: boolean;
  actorNames: string[];
  sort: CastSort;
  onSort: (key: CastSortKey) => void;
  onSwatchClick: (row: CastRow) => void;
  onSaveActor: (row: CastRow, actor: string) => void;
  /** Может ли этот человек назначить актёра на эту роль. У агента на строке
      рассказчика — нет: рассказчик живёт в смете книги, а не в карточке персонажа. */
  canAssignActor?: (row: CastRow) => boolean;
  onSaveRate: (row: CastRow, rate: number) => void;
  /** имя правится прямо в ячейке; говорящей роли переезд реплик спрашивает согласие на странице */
  onRename: (row: CastRow, next: string) => void;
  /** who the character is and how he sounds; dictors read it in the reader */
  onSaveAbout: (row: CastRow, patch: CharacterAbout) => void;
  onSaveNarratorFixed: (fixed: number) => void;
  /** «так задумано» с причиной, снятая с непризнанной пары в развёртке строки */
  onAcknowledge: (row: CastRow, partner: string, reason: string) => void;
  /** развёртка строки: слить роль с другой или удалить её из карты */
  onMerge: (row: CastRow) => void;
  onDelete: (row: CastRow) => void;
  /** the studio's rate, shown for a role that has no rate of its own */
  defaultRate: number;
  /** the book, so a role's line count can lead to the lines themselves */
  bookId: string;
  /** сколько проб пришло на роль; пусто — проб не присылали */
  auditionCounts: ReadonlyMap<string, number>;
  /** 👍/👎 автора на пробах роли — видны только тому, кому видны сами реакции */
  auditionVotes: ReadonlyMap<string, { up: number; down: number }>;
  onAuditions: (role: string) => void;
  /** пересечения ролей одного актёра, разложенные по имени роли */
  intersectionsByRole: Map<string, RoleIntersectionForRole[]>;
  /** какие строки развёрнуты; переключатель живёт на странице, а не в таблице,
      чтобы разворот переживал пересортировку и смену фильтра */
  openDetail: Set<string>;
  onToggleDetail: (id: string) => void;
  /** character ids with a save in flight */
  savingIds: ReadonlySet<string>;
  empty: ReactNode;
  maxHeight?: string;
};

function SortHeader({ label, column, sort, onSort }: { label: string; column: CastSortKey; sort: CastSort; onSort: (key: CastSortKey) => void }) {
  const active = sort?.key === column;
  return (
    <button
      type="button"
      className={["cast-sort", active ? "is-active" : "", active && sort?.dir === "asc" ? "is-asc" : ""].filter(Boolean).join(" ")}
      aria-pressed={active}
      aria-label={`Сортировать по столбцу «${label}»`}
      onClick={() => onSort(column)}
    >
      {label}
      <Icon name="chevron" className="cast-sort-icon" />
    </button>
  );
}

function chaptersTitle(row: CastRow): string {
  const list = row.appears_in_chapters || [];
  if (!list.length) return "Ни в одной главе";
  return `Главы: ${list.join(", ")}`;
}

export function CastTable({
  rows,
  actorPicker,
  bookId,
  auditionCounts,
  auditionVotes,
  onAuditions,
  intersectionsByRole,
  openDetail,
  onToggleDetail,
  loading,
  actorNames,
  sort,
  onSort,
  onSwatchClick,
  onSaveActor,
  canAssignActor,
  onSaveRate,
  onRename,
  onSaveAbout,
  defaultRate,
  onAcknowledge,
  onMerge,
  onDelete,
  savingIds,
  empty,
  maxHeight,
}: CastTableProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const frame = useRef<number | null>(null);
  const [viewport, setViewport] = useState({ top: 0, height: 800 });
  const [openAliases, setOpenAliases] = useState<ReadonlySet<string>>(() => new Set());
  const toggleAliases = useCallback((id: string) => {
    setOpenAliases((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const windowed = rows.length > WINDOW_FROM;
  /* Measured from a row on screen rather than assumed: a column added later (the
     «Персонаж» one, three lines tall) silently broke the arithmetic before. */
  const [rowHeight, setRowHeight] = useState(ROW_H_FALLBACK);

  const measure = useCallback((el: HTMLElement) => {
    const top = el.scrollTop;
    const height = el.clientHeight || 800;
    setViewport((current) => {
      // re-render only when the window moved by a handful of rows
      if (Math.abs(current.top - top) < rowHeight * 8 && current.height === height) return current;
      return { top, height };
    });
  }, [rowHeight]);

  useEffect(() => {
    const wrap = hostRef.current?.querySelector<HTMLElement>(".ui-table-wrap");
    if (wrap) measure(wrap);
  }, [measure, rows.length, loading]);

  // one real row decides the arithmetic for all of them
  useEffect(() => {
    // a spacer is a `tr` too, and it is thousands of pixels tall — measure a real one
    const row = [...(hostRef.current?.querySelectorAll<HTMLElement>("tbody tr") ?? [])].find(
      (candidate) => !candidate.querySelector(".cast-spacer"),
    );
    const height = Math.round(row?.getBoundingClientRect().height || 0);
    if (height > 8 && height !== rowHeight) setRowHeight(height);
  }, [rows.length, loading, rowHeight]);

  const onScrollCapture = (event: UIEvent<HTMLDivElement>) => {
    if (!windowed) return;
    const target = event.target as HTMLElement;
    if (!target.classList.contains("ui-table-wrap")) return;
    if (frame.current != null) return;
    frame.current = window.requestAnimationFrame(() => {
      frame.current = null;
      measure(target);
    });
  };

  useEffect(() => () => {
    if (frame.current != null) window.cancelAnimationFrame(frame.current);
  }, []);

  const items = useMemo<TableItem[]>(() => {
    if (!windowed) return rows.map((row) => ({ kind: "row", row }));
    const first = Math.max(0, Math.floor(viewport.top / rowHeight) - OVERSCAN);
    const last = Math.min(rows.length, Math.ceil((viewport.top + viewport.height) / rowHeight) + OVERSCAN);
    const out: TableItem[] = [];
    if (first > 0) out.push({ kind: "spacer", key: "spacer-top", height: first * rowHeight });
    for (let index = first; index < last; index += 1) out.push({ kind: "row", row: rows[index] });
    if (last < rows.length) out.push({ kind: "spacer", key: "spacer-bottom", height: (rows.length - last) * rowHeight });
    return out;
  }, [rows, windowed, viewport, rowHeight]);

  const columns = useMemo<Column<TableItem>[]>(() => {
    const cell = (render: (row: CastRow) => ReactNode, spacer?: boolean) => (item: TableItem) => {
      if (item.kind === "spacer") return spacer ? <div className="cast-spacer" style={{ height: item.height }} aria-hidden="true" /> : null;
      return render(item.row);
    };
    return [
      {
        key: "swatch",
        header: <span className="cast-sr">Цвет</span>,
        className: "cast-col-swatch",
        nowrap: true,
        render: cell(
          (row) => (
            <button
              type="button"
              className="cast-swatch"
              style={characterSwatchStyle(row)}
              disabled={row.is_narrator}
              title={row.is_narrator ? NARRATOR_HINT : `Изменить цвет роли ${row.name}`}
              aria-label={row.is_narrator ? NARRATOR_HINT : `Изменить цвет роли ${row.name}`}
              onClick={() => !row.is_narrator && onSwatchClick(row)}
            >
              <span aria-hidden="true">{row.is_narrator ? "Р" : row.name.slice(0, 1).toUpperCase()}</span>
            </button>
          ),
          true,
        ),
      },
      {
        key: "role",
        header: <SortHeader label="Роль" column="role" sort={sort} onSort={onSort} />,
        className: "cast-col-role",
        render: cell((row) => (
          <span className="cast-role-cell">
            <button
              type="button"
              className="cast-disc"
              aria-expanded={openDetail.has(row.character_id)}
              aria-label={`${openDetail.has(row.character_id) ? "Свернуть" : "Развернуть"} роль ${row.name}`}
              onClick={() => onToggleDetail(row.character_id)}
            >
              <Icon name="chevron" className="cast-disc-icon" />
            </button>
            <RoleCell
              row={row}
              open={openAliases.has(row.character_id)}
              onToggle={toggleAliases}
              saving={savingIds.has(row.character_id)}
              onRename={onRename}
            />
          </span>
        )),
      },
      {
        key: "actor",
        header: <SortHeader label="Актёр" column="actor" sort={sort} onSort={onSort} />,
        className: "cast-col-actor",
        render: cell((row) =>
          <>
            {canAssignActor && !canAssignActor(row) ? (
              <span className="cast-actor-static">{row.actor_name || "—"}</span>
            ) : actorPicker ? (
              <ActorPicker
                row={row}
                rows={actorPicker.allRows}
                dictors={actorPicker.dictors}
                canListen={actorPicker.canListen}
                proposeOnly={actorPicker.proposeOnly}
                canCreate={actorPicker.canCreate}
                canRecast={actorPicker.canRecast(row)}
                saving={savingIds.has(row.character_id)}
                onCommit={(value, chosen) => actorPicker.onPick(row, value, chosen)}
                onCreate={(name) => actorPicker.onCreate(row, name)}
                onRecast={() => actorPicker.onRecast(row)}
              />
            ) : (
              <InlineCell
                value={row.actor_name || ""}
                placeholder="назначить"
                suggestions={actorNames}
                listId="cast-actor-names"
                saving={savingIds.has(row.character_id)}
                label={`Актёр роли ${row.name}`}
                onCommit={(next) => onSaveActor(row, next)}
              />
            )}
            <DeadlineChip deadline={row.deadline} />
          </>
        ),
      },
      {
        key: "who",
        header: "Персонаж",
        className: "cast-col-who",
        render: cell((row) => (
          <span className="cast-who">
            {/* what the pipeline guessed is a draft: all three lines edit in place */}
            <InlineCell
              value={row.race || ""}
              placeholder="+ раса, вид"
              saving={savingIds.has(row.character_id)}
              label={`Раса роли ${row.name}`}
              onCommit={(next) => onSaveAbout(row, { race: next })}
            />
            <InlineCell
              value={row.temperament || ""}
              placeholder="+ характер, манера речи"
              multiline
              saving={savingIds.has(row.character_id)}
              label={`Характер роли ${row.name}`}
              onCommit={(next) => onSaveAbout(row, { temperament: next })}
            />
            <InlineCell
              value={row.note || ""}
              placeholder="+ заметка о голосе"
              multiline
              saving={savingIds.has(row.character_id)}
              label={`Заметка о роли ${row.name}`}
              display={row.note ? <span className="cast-who-note">{row.note}</span> : undefined}
              onCommit={(next) => onSaveAbout(row, { note: next })}
            />
          </span>
        )),
      },
      {
        key: "lines",
        header: <SortHeader label="Реплик" column="lines" sort={sort} onSort={onSort} />,
        align: "right",
        className: "cast-col-lines",
        nowrap: true,
        render: cell((row) =>
          row.lines_count > 0 && bookId ? (
            <Link
              className="cast-lines num"
              to={`/reader/role/${encodeURIComponent(bookId)}?role=${encodeURIComponent(row.name)}`}
              title={`Все реплики роли «${row.name}» по всей книге, с контекстом`}
            >
              {row.lines_count.toLocaleString("ru-RU")}
            </Link>
          ) : (
            <span className="num">{row.lines_count.toLocaleString("ru-RU")}</span>
          ),
        ),
      },
      {
        key: "auditions",
        header: "Пробы",
        align: "right",
        className: "cast-col-auditions",
        nowrap: true,
        render: cell((row) => {
          const count = auditionCounts.get(row.name) || 0;
          if (!count) return <span className="cast-muted">—</span>;
          return (
            <button
              type="button"
              className="cast-auditions num"
              title={`Послушать пробы на роль «${row.name}»`}
              onClick={() => onAuditions(row.name)}
            >
              {count}
              {(() => {
                const votes = auditionVotes.get(row.name);
                if (!votes || (!votes.up && !votes.down)) return null;
                return (
                  <span className="cast-aud-votes" aria-label={`Автор: подходит ${votes.up}, не подходит ${votes.down}`}>
                    {votes.up ? `👍${votes.up}` : ""}{votes.up && votes.down ? " " : ""}{votes.down ? `👎${votes.down}` : ""}
                  </span>
                );
              })()}
            </button>
          );
        }),
      },
      {
        key: "chapters",
        header: "Глав",
        align: "right",
        className: "cast-col-chapters",
        nowrap: true,
        render: cell((row) => {
          const got = (row.appears_in_chapters || []).length;
          // Янтарь — только когда извлечение сделало заявку: пустой `claimed_chapters`
          // значит «про роль не говорили», а не «назвали ноль глав» (см. `chapterMismatch`).
          const mismatch = chapterMismatch(row);
          if (!mismatch) return <span className="num cast-chapters" title={chaptersTitle(row)}>{got}</span>;
          return (
            <span
              className="num cast-chapters is-mismatch"
              title={`${describeChapterMismatch(mismatch)}. Расхождение — сигнал о тексте или разметке, а не о роли.`}
            >
              {got}<span className="cast-chapters-claimed">/{mismatch.claimed}</span>
            </span>
          );
        }),
      },
      {
        key: "intersections",
        header: "Пересечения",
        className: "cast-col-cross",
        nowrap: true,
        render: cell((row) => {
          const pending = (intersectionsByRole.get(row.name) || []).filter((item) => !item.acknowledged);
          if (!pending.length) return <span className="cast-muted">—</span>;
          // Порядок задаёт сервер: `role_intersections` сортирует непризнанные пары
          // вперёд и худшую тяжесть — первой (см. app/v2/role_intersections.py).
          // Пересортировка здесь молча сломает выбор худшей пары — `pending[0]`
          // держится на этом инварианте, а не на случайном порядке ответа.
          const worst = pending[0];
          const label = worst.severity === "dialogue" ? "диалог" : worst.severity === "scene" ? "сцена" : "глава";
          const tone = worst.severity === "dialogue" ? "is-bad" : worst.severity === "scene" ? "is-warn" : "is-info";
          return (
            <button
              type="button"
              className={`cast-cross ${tone}`}
              title={`${worst.partner} · ${worst.distance} абз.${worst.same_race ? " · одна раса" : ""}`}
              aria-label={`Пересечения роли ${row.name}: ${pending.length}`}
              onClick={() => onToggleDetail(row.character_id)}
            >
              {label}{pending.length > 1 ? ` +${pending.length - 1}` : ""}
            </button>
          );
        }),
      },
    ];
  }, [sort, onSort, onSwatchClick, onSaveActor, canAssignActor, onRename, savingIds, actorNames, openAliases, toggleAliases, auditionCounts, auditionVotes, onAuditions, bookId, onSaveAbout, intersectionsByRole, onToggleDetail, openDetail]);

  // The disclosure triangle in the role column drives this: a row not in
  // `openDetail` costs nothing (it renders as `null`, which `DataTable` skips).
  const renderDetail = useCallback(
    (item: TableItem) => {
      if (item.kind !== "row") return null;
      const row = item.row;
      if (!openDetail.has(row.character_id)) return null;
      const intersections = (intersectionsByRole.get(row.name) || []).filter((entry) => !entry.acknowledged);
      return (
        <RoleDetailRow
          row={row}
          intersections={intersections}
          defaultRate={defaultRate}
          saving={savingIds.has(row.character_id)}
          onSaveAbout={onSaveAbout}
          onSaveRate={onSaveRate}
          onAcknowledge={onAcknowledge}
          onMerge={onMerge}
          onDelete={onDelete}
        />
      );
    },
    [openDetail, intersectionsByRole, defaultRate, savingIds, onSaveAbout, onSaveRate, onAcknowledge, onMerge, onDelete],
  );

  return (
    <div ref={hostRef} className="cast-table" onScrollCapture={onScrollCapture}>
      <DataTable
        aria-label="Каст книги"
        columns={columns}
        rows={items}
        rowKey={(item) => (item.kind === "spacer" ? item.key : item.row.character_id)}
        loading={loading}
        skeletonRows={8}
        empty={empty}
        maxHeight={maxHeight}
        renderDetail={renderDetail}
      />
    </div>
  );
}
