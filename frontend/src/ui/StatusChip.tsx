export type ChipTone = "accent" | "blue" | "amber" | "danger" | "neutral";

type StatusMeta = { label: string; tone: ChipTone };

/** Book status codes → display label + token colour. Unknown codes fall back to neutral + raw code. */
export const BOOK_STATUS: Record<string, StatusMeta> = {
  uploaded: { label: "Uploaded", tone: "neutral" },
  queued: { label: "Queued", tone: "neutral" },
  processing: { label: "In progress", tone: "blue" },
  char_extracting: { label: "In progress", tone: "blue" },
  stopping: { label: "Stopping", tone: "amber" },
  stopped: { label: "Stopped", tone: "amber" },
  author_review: { label: "Review", tone: "blue" },
  needs_review: { label: "Review", tone: "blue" },
  pending_review: { label: "Review", tone: "blue" },
  partially_approved: { label: "Partly approved", tone: "amber" },
  approved: { label: "Approved", tone: "accent" },
  published: { label: "Published", tone: "accent" },
  published_to_dictor: { label: "With voice actors", tone: "accent" },
  ready_for_mix: { label: "Ready to mix", tone: "accent" },
  done: { label: "Done", tone: "accent" },
  failed: { label: "Error", tone: "danger" },
  stalled: { label: "Stalled", tone: "danger" },
};

export function describeStatus(code: string): StatusMeta & { known: boolean } {
  const key = String(code || "").trim().toLowerCase();
  const meta = BOOK_STATUS[key];
  if (meta) return { ...meta, known: true };
  return { label: key || "—", tone: "neutral", known: false };
}

type StatusChipProps = {
  status: string;
  /** override the derived label (e.g. server-provided) */
  label?: string;
  tone?: ChipTone;
  className?: string;
  title?: string;
};

export function StatusChip({ status, label, tone, className, title }: StatusChipProps) {
  const meta = describeStatus(status);
  const finalTone = tone ?? meta.tone;
  return (
    <span
      className={["ui-chip", `ui-chip--${finalTone}`, meta.known || label ? "" : "ui-chip--raw", className || ""].filter(Boolean).join(" ")}
      title={title ?? (meta.known ? status : undefined)}
    >
      <span className="ui-chip-dot" aria-hidden="true" />
      {label ?? meta.label}
    </span>
  );
}
