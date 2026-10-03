import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "../../Icon";
import { DeleteBookConfirm } from "./DeleteBookConfirm";

export type HubMenuItem = {
  key: string;
  label: ReactNode;
  onSelect?: () => void;
  disabled?: boolean;
  /** shown under a disabled item — why it does nothing */
  hint?: string;
  /** shown under the item regardless of disabled state — consequences the operator must know before clicking */
  note?: string;
};

type HubMenuProps = {
  items: HubMenuItem[];
  bookTitle: string;
  onDelete: () => void;
  deletePending: boolean;
};

/** The «⋯» menu: a native details popover, Escape / outside click close it. */
export function HubMenu({ items, bookTitle, onDelete, deletePending }: HubMenuProps) {
  const ref = useRef<HTMLDetailsElement>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && node.open) {
        node.open = false;
        setConfirming(false);
        node.querySelector("summary")?.focus();
      }
    };
    const onClick = (event: MouseEvent) => {
      if (node.open && !node.contains(event.target as Node)) {
        node.open = false;
        setConfirming(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, []);

  const close = () => {
    if (ref.current) ref.current.open = false;
    setConfirming(false);
  };

  return (
    <details ref={ref} className="hub-menu" onToggle={(event) => !(event.currentTarget as HTMLDetailsElement).open && setConfirming(false)}>
      <summary className="ui-btn ui-btn--secondary ui-btn--icon hub-menu-summary" aria-label="Ещё действия" title="Ещё действия">
        <Icon name="dots" />
      </summary>
      <div className="hub-menu-pop" role="menu" aria-label="Действия с книгой">
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            role="menuitem"
            className="hub-menu-item"
            disabled={item.disabled}
            title={item.disabled ? item.hint : item.note}
            onClick={() => {
              item.onSelect?.();
              close();
            }}
          >
            <span>{item.label}</span>
            {item.note ? <small>{item.note}</small> : item.disabled && item.hint ? <small>{item.hint}</small> : null}
          </button>
        ))}
        <div className="hub-menu-sep" role="separator" />
        {confirming ? (
          <DeleteBookConfirm title={bookTitle} pending={deletePending} onConfirm={onDelete} onCancel={() => setConfirming(false)} />
        ) : (
          <button type="button" role="menuitem" className="hub-menu-item is-danger" onClick={() => setConfirming(true)}>
            <span>Удалить книгу</span>
          </button>
        )}
      </div>
    </details>
  );
}
