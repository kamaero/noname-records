import { useEffect, useRef, type ReactNode } from "react";
import { Icon } from "../components/Icon";

type SheetProps = {
  title: string;
  /** small text under the title */
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** slide from the left instead of the right (mobile menus) */
  side?: "right" | "left";
};

/** A side sheet with backdrop: Escape closes, focus lands on the close button. */
export function Sheet({ title, subtitle, onClose, children, footer, side = "right" }: SheetProps) {
  const closeRef = useRef<HTMLButtonElement>(null);

  // focus once on open — not on every parent re-render (onClose is usually an inline arrow)
  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="ui-sheet-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className={side === "left" ? "ui-sheet ui-sheet--left" : "ui-sheet"} role="dialog" aria-modal="true" aria-label={title}>
        <header className="ui-sheet-head">
          <div className="ui-sheet-titles">
            <h2 className="ui-sheet-title">{title}</h2>
            {subtitle ? <div className="ui-sheet-subtitle">{subtitle}</div> : null}
          </div>
          <button ref={closeRef} type="button" className="ui-btn ui-btn--ghost ui-btn--icon" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </header>
        <div className="ui-sheet-body">{children}</div>
        {footer ? <footer className="ui-sheet-foot">{footer}</footer> : null}
      </aside>
    </>
  );
}
