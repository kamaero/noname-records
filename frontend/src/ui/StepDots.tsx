export type StepState = "done" | "running" | "pending" | "failed" | "skipped";

export type StepDot = { key?: string; label?: string; state: StepState };

/** The six v2 steps, in pipeline order. Used as labels when the server sends none. */
export const V2_STEP_LABELS = ["Segmentation", "Characters", "Roles", "Word stress", "Review", "Publication"];

const STATE_WORD: Record<StepState, string> = {
  done: "done",
  running: "running",
  pending: "pending",
  failed: "error",
  skipped: "skipped",
};

type StepDotsProps = {
  steps: StepDot[];
  /** pad to this many squares with `pending` (default 6) */
  count?: number;
  size?: "sm" | "lg";
  className?: string;
};

export function StepDots({ steps, count = 6, size = "sm", className }: StepDotsProps) {
  const padded: StepDot[] = Array.from({ length: Math.max(count, steps.length) }, (_, i) => steps[i] ?? { state: "pending" });
  const summary = padded.map((s, i) => `${s.label || V2_STEP_LABELS[i] || `Step ${i + 1}`}: ${STATE_WORD[s.state]}`).join(", ");
  return (
    <span
      className={["ui-steps", size === "lg" ? "ui-steps--lg" : "", className || ""].filter(Boolean).join(" ")}
      role="img"
      aria-label={summary}
      title={summary}
    >
      {padded.map((s, i) => (
        <span key={s.key || i} className={`ui-step ui-step--${s.state}`} />
      ))}
    </span>
  );
}
