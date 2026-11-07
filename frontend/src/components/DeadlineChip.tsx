import type { RoleDeadlineView } from "../types";
import "./DeadlineChip.css";
import { deadlineTimeZone, usesIanaCalendar } from "../utils/serverTime";

/** Срок пробы или роли: «проба до 3.10 18:00», «до 31.12 · 12/18 гл.», «просрочено 2 дн.». */
export function deadlineLabel(deadline: RoleDeadlineView, now: Date = new Date()): string {
  const due = deadline.due_at ? new Date(deadline.due_at) : null;
  if (!due) return "";
  const progress = deadline.kind === "role" && deadline.total ? ` · ${deadline.done}/${deadline.total} ch.` : "";
  if (deadline.overdue) {
    const days = Math.max(1, Math.floor((now.getTime() - due.getTime()) / 86_400_000));
    return `overdue by ${days} ${days === 1 ? "day" : "days"}${progress}`;
  }
  const local = new Intl.DateTimeFormat("en-US", {
    timeZone: deadlineTimeZone(),
    day: "numeric",
    month: "numeric",
    ...(deadline.kind === "audition" ? { hour: "2-digit", minute: "2-digit" } : {}),
    ...(usesIanaCalendar() && deadline.kind === "audition" ? { timeZoneName: "short" } : {}),
  }).format(due);
  return deadline.kind === "audition" ? `audition due ${local.replace(",", "")}` : `due ${local}${progress}`;
}

export function DeadlineChip({ deadline }: { deadline: RoleDeadlineView | null | undefined }) {
  if (!deadline) return null;
  const label = deadlineLabel(deadline);
  if (!label) return null;
  return (
    <span
      className={deadline.overdue ? "deadline-chip is-overdue" : "deadline-chip"}
      title={`${deadline.kind === "audition" ? "Audition deadline (48 hours)" : "Role recording deadline"} · ${deadlineTimeZone()}`}
    >
      {label}
    </span>
  );
}
