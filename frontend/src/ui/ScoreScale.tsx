import type { KeyboardEvent } from "react";
import "./ScoreScale.css";

type ScoreScaleProps = {
  /** 1–10; null — не оценено, шкала пустая */
  value: number | null;
  /** «Оценка автора» — для читалки экрана и подсказки */
  label: string;
  /** нет — шкала только показывает (чужая дорожка) */
  onChange?: (next: number | null) => void;
  size?: "md" | "sm";
  disabled?: boolean;
};

const CELLS = Array.from({ length: 10 }, (_, index) => index + 1);

/**
 * Оценка пробы — 10 квадратиков от красного («нет») к зелёному («да»), решение
 * владельца 03.10. Закрашены от 1 до оценки, как полоска громкости: длина читается
 * быстрее цифры. Повторное нажатие на ту же оценку снимает её — отдельная кнопка
 * «сбросить» была бы ещё одним элементом в строке, где и так тесно на телефоне.
 */
export function ScoreScale({ value, label, onChange, size = "md", disabled }: ScoreScaleProps) {
  // Кнопки не заменяем на span, пока идёт сохранение: иначе фокус клавиатуры теряется после каждого нажатия.
  const interactive = Boolean(onChange);
  const title = value ? `${label}: ${value} из 10` : `${label}: нет оценки`;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!interactive || !onChange || disabled) return;
    if (event.key === "ArrowRight") {
      event.preventDefault();
      onChange(Math.min(10, (value ?? 0) + 1));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      onChange(value && value > 1 ? value - 1 : null);
    }
  };

  return (
    <div
      className={`score-scale score-scale--${size}${interactive ? " is-interactive" : ""}`}
      role={interactive ? "group" : "img"}
      aria-label={title}
      title={title}
      onKeyDown={onKeyDown}
    >
      {CELLS.map((cell) => {
        const on = value !== null && cell <= value;
        const style = { ["--i" as string]: cell };
        return interactive ? (
          <button
            key={cell}
            type="button"
            className={on ? "score-cell is-on" : "score-cell"}
            style={style}
            aria-label={`${cell} из 10`}
            aria-pressed={value === cell}
            aria-disabled={disabled || undefined}
            onClick={() => !disabled && onChange?.(value === cell ? null : cell)}
          />
        ) : (
          <span key={cell} className={on ? "score-cell is-on" : "score-cell"} style={style} aria-hidden="true" />
        );
      })}
    </div>
  );
}
