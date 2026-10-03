import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";

type InlineCellProps = {
  /** current stored value, shown at rest and used as the draft's start */
  value: string;
  /** what to show at rest; defaults to the value, or `placeholder` faint when empty */
  display?: ReactNode;
  placeholder?: string;
  type?: "text" | "number";
  /** datalist options (text type) */
  suggestions?: string[];
  /** id for the datalist element; must be unique per list, not per row */
  listId?: string;
  align?: "left" | "right";
  mono?: boolean;
  saving?: boolean;
  disabled?: boolean;
  /** accessible name of the edit trigger */
  label: string;
  /** hover text — the whole value when the cell shows a clipped line of it */
  title?: string;
  /** a sentence rather than a name: edits in a textarea, Enter still commits */
  multiline?: boolean;
  /** called with the trimmed draft when it differs from `value` (Enter or blur) */
  onCommit: (next: string) => void;
  min?: number;
};

/**
 * One table cell that edits in place: a quiet button at rest, a 30px input
 * while editing. Enter commits, Esc cancels, blur commits when changed.
 */
export function InlineCell({
  value,
  display,
  placeholder,
  type = "text",
  suggestions,
  listId,
  align = "left",
  mono,
  saving,
  disabled,
  label,
  title,
  multiline,
  onCommit,
  min,
}: InlineCellProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);
  /** set once the edit is over (Enter / Esc) so the blur the unmount fires cannot commit twice */
  const closing = useRef(false);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  useEffect(() => {
    if (editing) {
      closing.current = false;
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const commit = () => {
    if (closing.current) return;
    closing.current = true;
    setEditing(false);
    const next = draft.trim();
    if (next !== String(value).trim()) onCommit(next);
  };

  const cancel = () => {
    closing.current = true;
    setDraft(value);
    setEditing(false);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    // Shift+Enter is a line break in a textarea; plain Enter still means «готово»
    if (event.key === "Enter" && !(multiline && event.shiftKey)) {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancel();
    }
  };

  const onBlur = () => {
    if (editing) commit();
  };

  const classes = ["cast-cell", align === "right" ? "is-right" : "", mono ? "num" : ""].filter(Boolean).join(" ");

  if (editing && multiline) {
    return (
      <textarea
        ref={inputRef as RefObject<HTMLTextAreaElement>}
        className={`cast-cell-input cast-cell-area ${classes}`}
        rows={3}
        value={draft}
        placeholder={placeholder}
        aria-label={label}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
      />
    );
  }

  if (editing) {
    return (
      <>
        <input
          ref={inputRef as RefObject<HTMLInputElement>}
          className={`cast-cell-input ${classes}`}
          type={type}
          inputMode={type === "number" ? "numeric" : undefined}
          min={min}
          value={draft}
          list={suggestions && listId ? listId : undefined}
          placeholder={placeholder}
          aria-label={label}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={onBlur}
        />
        {suggestions && listId ? (
          <datalist id={listId}>
            {suggestions.map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
        ) : null}
      </>
    );
  }

  const empty = !String(value).trim();
  return (
    <button
      type="button"
      className={`cast-cell-btn ${classes} ${empty ? "is-empty" : ""}`}
      aria-label={label}
      title={title || (empty ? undefined : String(value))}
      aria-busy={saving || undefined}
      disabled={disabled || saving}
      onClick={() => setEditing(true)}
    >
      {display ?? (empty ? placeholder || "—" : value)}
    </button>
  );
}
