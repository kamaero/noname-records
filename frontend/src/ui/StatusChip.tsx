export type ChipTone = "accent" | "blue" | "amber" | "danger" | "neutral";

type StatusMeta = { label: string; tone: ChipTone };

/** Book status codes → Russian label + token colour. Unknown codes fall back to neutral + raw code. */
export const BOOK_STATUS: Record<string, StatusMeta> = {
  uploaded: { label: "Загружена", tone: "neutral" },
  queued: { label: "В очереди", tone: "neutral" },
  processing: { label: "В работе", tone: "blue" },
  char_extracting: { label: "В работе", tone: "blue" },
  stopping: { label: "Останавливается", tone: "amber" },
  stopped: { label: "Остановлена", tone: "amber" },
  author_review: { label: "Проверка", tone: "blue" },
  needs_review: { label: "Проверка", tone: "blue" },
  pending_review: { label: "Проверка", tone: "blue" },
  partially_approved: { label: "Частично утверждена", tone: "amber" },
  approved: { label: "Утверждена", tone: "accent" },
  published: { label: "Опубликована", tone: "accent" },
  published_to_dictor: { label: "У дикторов", tone: "accent" },
  ready_for_mix: { label: "К сведению", tone: "accent" },
  done: { label: "Готово", tone: "accent" },
  failed: { label: "Ошибка", tone: "danger" },
  stalled: { label: "Зависла", tone: "danger" },
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
