export type StudioCalendar = {
  mode: "legacy" | "iana";
  timezone: string | null;
  display_timezone: string;
  deadline_timezone: string;
  spending_timezone: string;
};

let calendar: StudioCalendar = { mode: "legacy", timezone: null, display_timezone: "Asia/Yekaterinburg",
  deadline_timezone: "Europe/Moscow", spending_timezone: "Etc/GMT-3" };

export function configureStudioCalendar(value: StudioCalendar): void {
  if (!value || !["legacy", "iana"].includes(value.mode)) throw new Error("Invalid studio calendar profile");
  if (value.mode === "iana" && (!value.timezone || [value.display_timezone, value.deadline_timezone, value.spending_timezone]
    .some((zone) => zone !== value.timezone))) throw new Error("Inconsistent studio timezone");
  for (const zone of [value.display_timezone, value.deadline_timezone, value.spending_timezone]) {
    if (typeof zone !== "string" || !zone) throw new Error("Missing studio timezone");
    new Intl.DateTimeFormat("en-US", { timeZone: zone }).format();
  }
  calendar = value;
}

export function deadlineTimeZone(): string { return calendar.deadline_timezone; }
export function usesIanaCalendar(): boolean { return calendar.mode === "iana"; }

export function formatDeadlineDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { timeZone: calendar.deadline_timezone, day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit", ...(usesIanaCalendar() ? { timeZoneName: "short" } : {}) }).format(date);
}

export function formatServerDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("en-US", {
    timeZone: calendar.display_timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    ...(usesIanaCalendar() ? { timeZoneName: "short" } : {}),
  }).format(date);
}
