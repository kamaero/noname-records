import { useId, type ReactNode } from "react";

type FieldProps = {
  label: ReactNode;
  /** the control; receives `id` and aria attributes through the render callback */
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  className?: string;
};

/** Label + control + hint/error. Errors are words under the field, never alerts. */
export function Field({ label, children, hint, error, required, className }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [error ? errorId : "", hint ? hintId : ""].filter(Boolean).join(" ") || undefined;
  return (
    <div className={["ui-field", error ? "has-error" : "", className || ""].filter(Boolean).join(" ")}>
      <label className="ui-field-label" htmlFor={id}>
        {label}
        {required ? <span className="ui-field-req" aria-hidden="true">*</span> : null}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {error ? (
        <p className="ui-field-error" id={errorId} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="ui-field-hint" id={hintId}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}
