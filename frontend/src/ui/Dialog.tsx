import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../components/Icon";

type DialogProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** строка кнопок внизу окна */
  footer?: ReactNode;
  /** мелкий текст под заголовком */
  subtitle?: ReactNode;
  className?: string;
  /** куда вернуть фокус, если открывший окно элемент уже исчез (строка закрытого меню)
   *  или окно открылось само — без него фокус упал бы на body */
  fallbackFocus?: () => HTMLElement | null;
};

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusablesOf(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.hasAttribute("inert") && el.offsetParent !== null);
}

/** Окно по центру поверх страницы: подложка, фокус внутри окна, Esc закрывает,
 *  после закрытия фокус возвращается туда, откуда окно открыли. На узком экране
 *  (≤ 720 px) окно занимает весь экран.
 *
 *  Первым получает фокус элемент с `data-autofocus` (главная кнопка), иначе первый
 *  интерактивный элемент тела окна.
 *
 *  Клавиши не уходят странице под окном: Esc и Tab перехватываются на фазе захвата
 *  (иначе Esc закрыл бы заодно боковой лист или конструктор ударений), остальные
 *  гасятся на своём контейнере портала — ↑ ↓ не двигают курсор реплик за окном.
 *  Обработчики React внутри окна при этом работают: React слушает сам контейнер. */
export function Dialog({ title, onClose, children, footer, subtitle, className, fallbackFocus }: DialogProps) {
  const titleId = useId();
  const [container] = useState(() => document.createElement("div"));
  const boxRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const fallbackRef = useRef(fallbackFocus);
  fallbackRef.current = fallbackFocus;

  useLayoutEffect(() => {
    document.body.appendChild(container);
    const swallow = (event: KeyboardEvent) => event.stopPropagation();
    container.addEventListener("keydown", swallow);
    return () => {
      container.removeEventListener("keydown", swallow);
      container.remove();
    };
  }, [container]);

  // фокус — один раз при открытии: главная кнопка, иначе первое в теле окна, иначе крестик
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const box = boxRef.current;
    if (box) {
      const preferred = box.querySelector<HTMLElement>("[data-autofocus]");
      const inBody = bodyRef.current ? focusablesOf(bodyRef.current) : [];
      (preferred || inBody[0] || focusablesOf(box)[0] || box).focus();
    }
    return () => {
      const alive = opener && opener !== document.body && document.contains(opener);
      const target = alive ? opener : fallbackRef.current?.() || null;
      target?.focus();
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      event.stopImmediatePropagation();
      const box = boxRef.current;
      if (!box) return;
      const items = focusablesOf(box);
      if (items.length === 0) {
        event.preventDefault();
        box.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = active ? box.contains(active) : false;
      if (event.shiftKey && (active === first || !inside)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !inside)) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  // страница под окном не прокручивается
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return createPortal(
    <div className="ui-dialog-layer">
      <div className="ui-dialog-backdrop" onClick={() => onCloseRef.current()} aria-hidden="true" />
      <div
        ref={boxRef}
        className={["ui-dialog", className || ""].filter(Boolean).join(" ")}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="ui-dialog-head">
          <div className="ui-dialog-titles">
            <h2 id={titleId} className="ui-dialog-title">{title}</h2>
            {subtitle ? <div className="ui-dialog-subtitle">{subtitle}</div> : null}
          </div>
          <button type="button" className="ui-btn ui-btn--ghost ui-btn--icon" onClick={() => onCloseRef.current()} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </header>
        <div ref={bodyRef} className="ui-dialog-body">{children}</div>
        {footer ? <footer className="ui-dialog-foot">{footer}</footer> : null}
      </div>
    </div>,
    container,
  );
}
