import { Fragment, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";

export type Column<Row> = {
  key: string;
  header: ReactNode;
  render: (row: Row) => ReactNode;
  align?: "left" | "right" | "center";
  width?: string | number;
  nowrap?: boolean;
  /** applied to the th and every td of the column (e.g. to hide it on narrow screens) */
  className?: string;
};

type DataTableProps<Row> = {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  onRowClick?: (row: Row) => void;
  /** accessible name for a clickable row */
  rowLabel?: (row: Row) => string;
  selectedKey?: string | null;
  /** shown inside the table body when rows are empty and not loading */
  empty?: ReactNode;
  loading?: boolean;
  skeletonRows?: number;
  className?: string;
  /** e.g. `calc(100vh - 220px)` — the wrapper scrolls, the header sticks */
  maxHeight?: string;
  /** a full-width row drawn right after its own, when it returns non-null (e.g. a
      disclosure triangle's expanded detail); a row with nothing to show returns null */
  renderDetail?: (row: Row) => ReactNode;
  "aria-label"?: string;
};

function alignClass(align?: "left" | "right" | "center"): string {
  if (align === "right") return "is-right";
  if (align === "center") return "is-center";
  return "";
}

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  onRowClick,
  rowLabel,
  selectedKey,
  empty,
  loading,
  skeletonRows = 4,
  className,
  maxHeight,
  renderDetail,
  "aria-label": ariaLabel,
}: DataTableProps<Row>) {
  const clickable = Boolean(onRowClick);
  const onKey = (row: Row) => (event: KeyboardEvent<HTMLTableRowElement>) => {
    if (!onRowClick) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onRowClick(row);
    }
  };

  const wrapStyle: CSSProperties | undefined = maxHeight ? { maxHeight } : undefined;

  return (
    <div className={["ui-table-wrap", className || ""].filter(Boolean).join(" ")} style={wrapStyle}>
      <table className="ui-table" aria-label={ariaLabel} aria-busy={loading || undefined}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={[alignClass(col.align), col.nowrap ? "is-nowrap" : "", col.className || ""].filter(Boolean).join(" ") || undefined}
                style={col.width ? { width: col.width } : undefined}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: skeletonRows }).map((_, i) => (
                <tr key={`skel-${i}`} aria-hidden="true">
                  {columns.map((col, j) => (
                    <td key={col.key} className={[alignClass(col.align), col.className || ""].filter(Boolean).join(" ") || undefined}>
                      <div className={`ui-table-skel ${j === 0 ? "ui-table-skel--wide" : j % 2 ? "ui-table-skel--short" : "ui-table-skel--mid"}`} />
                    </td>
                  ))}
                </tr>
              ))
            : rows.map((row) => {
                const key = rowKey(row);
                const selected = selectedKey != null && selectedKey === key;
                const detail = renderDetail?.(row);
                return (
                  <Fragment key={key}>
                    <tr
                      className={[clickable ? "is-clickable" : "", selected ? "is-selected" : ""].filter(Boolean).join(" ") || undefined}
                      onClick={clickable ? () => onRowClick?.(row) : undefined}
                      onKeyDown={clickable ? onKey(row) : undefined}
                      tabIndex={clickable ? 0 : undefined}
                      role={clickable ? "link" : undefined}
                      aria-label={clickable && rowLabel ? rowLabel(row) : undefined}
                      aria-selected={selected || undefined}
                    >
                      {columns.map((col) => (
                        <td key={col.key} className={[alignClass(col.align), col.nowrap ? "is-nowrap" : "", col.className || ""].filter(Boolean).join(" ") || undefined}>
                          {col.render(row)}
                        </td>
                      ))}
                    </tr>
                    {detail != null ? (
                      <tr className="ui-table-detail-row">
                        <td colSpan={columns.length}>{detail}</td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
          {!loading && rows.length === 0 && empty ? (
            <tr className="ui-table-empty">
              <td colSpan={columns.length}>{empty}</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
