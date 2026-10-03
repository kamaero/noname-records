import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Link } from "react-router-dom";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

type BaseProps = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  /** icon slot, rendered before the label */
  icon?: ReactNode;
  /** icon-only button (square) — pass `aria-label` */
  iconOnly?: boolean;
  block?: boolean;
  className?: string;
  children?: ReactNode;
};

export type ButtonProps = BaseProps & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className" | "children">;

function classes({ variant = "secondary", size = "md", loading, iconOnly, block, className }: BaseProps): string {
  return [
    "ui-btn",
    `ui-btn--${variant}`,
    size === "sm" ? "ui-btn--sm" : "",
    iconOnly ? "ui-btn--icon" : "",
    block ? "ui-btn--block" : "",
    loading ? "is-loading" : "",
    className || "",
  ]
    .filter(Boolean)
    .join(" ");
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, loading, icon, iconOnly, block, className, children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={classes({ variant, size, loading, iconOnly, block, className })}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {icon}
      {children}
      {loading ? <span className="ui-btn-spinner" aria-hidden="true" /> : null}
    </button>
  );
});

export type LinkButtonProps = BaseProps & { to: string; title?: string; "aria-label"?: string };

/** Same look as Button, routes with react-router. */
export function LinkButton({ to, children, icon, title, "aria-label": ariaLabel, ...rest }: LinkButtonProps) {
  return (
    <Link to={to} className={classes(rest)} title={title} aria-label={ariaLabel}>
      {icon}
      {children}
    </Link>
  );
}
