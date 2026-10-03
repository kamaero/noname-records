import { useState } from "react";
import { Icon } from "../../Icon";
import { ModelCard } from "./ModelCard";
import { TitleCard } from "./TitleCard";
import { ProfileCard } from "./ProfileCard";
import type { HubProgress } from "../../../viewModels/bookHub";

type BookSettingsCardProps = {
  bookId: string;
  progress: HubProgress;
  /** чистое название книги (без автора) и автор для витрины — правятся в `TitleCard` */
  title: string;
  author: string;
  /** the profile's bound author, already fetched by the page for `ProfileCard` — empty when unbound */
  authorName: string;
  /** a run is going: `ModelCard`'s own choice waits, same as today */
  locked: boolean;
};

function storageKey(bookId: string): string {
  return `hub-settings-open:${bookId}`;
}

function readStoredOpen(bookId: string): boolean | null {
  try {
    const raw = window.localStorage.getItem(storageKey(bookId));
    if (raw === "1") return true;
    if (raw === "0") return false;
  } catch {
    // private mode / disabled storage: fall back to the computed default
  }
  return null;
}

function writeStoredOpen(bookId: string, open: boolean): void {
  try {
    window.localStorage.setItem(storageKey(bookId), open ? "1" : "0");
  } catch {
    // nothing to do — the choice just won't survive a reload
  }
}

/**
 * «Настройки книги»: model choice and the author profile, collapsed once the book
 * is on rails. Open by default while roles are not attributed or no model is
 * chosen yet — the two things worth checking before the first real run; a manual
 * toggle after that is remembered per book, so re-opening it once doesn't force
 * the operator to fight the auto-collapse on every visit.
 *
 * `open`'s initial value is read once, at mount (`useState`'s lazy initializer,
 * not an effect) — the caller (`BookCommandCenterPage`) mounts this only after
 * `progress` has settled, so that one read already sees the real steps/model
 * instead of the pre-fetch empty progress, which would otherwise default to
 * "open" and never re-close itself once real data arrived.
 */
export function BookSettingsCard({ bookId, progress, title, author, authorName, locked }: BookSettingsCardProps) {
  const attributeDone = progress.steps.find((step) => step.key === "attribute")?.state === "done";
  const defaultOpen = !attributeDone || !progress.model;
  const [open, setOpen] = useState<boolean>(() => readStoredOpen(bookId) ?? defaultOpen);

  const summaryParts: string[] = [];
  if (progress.model?.label) summaryParts.push(`модель ${progress.model.label}`);
  if (authorName) summaryParts.push(`автор ${authorName}`);

  return (
    <details
      className="panel hub-card hub-service"
      open={open}
      onToggle={(event) => {
        const next = event.currentTarget.open;
        setOpen(next);
        writeStoredOpen(bookId, next);
      }}
    >
      <summary className="hub-service-summary">
        <Icon name="chevron" />
        <span className="hub-card-title">Настройки книги</span>
        {summaryParts.length ? <span className="hub-dim">{summaryParts.join(" · ")}</span> : null}
      </summary>
      {/* `hub-settings-body` (on top of the shared `hub-service-body`) scopes the
          inner-panel-chrome strip below to this card only — `ModelCard`/`ProfileCard`
          keep their own `panel hub-card` markup unchanged, but nested inside another
          `.panel` they read as a card stuck inside a card; here they're just sections
          separated by a rule. */}
      <div className="hub-service-body hub-settings-body">
        <TitleCard bookId={bookId} title={title} author={author} />
        {progress.model ? <ModelCard bookId={bookId} model={progress.model} locked={locked} /> : null}
        <ProfileCard bookId={bookId} />
      </div>
    </details>
  );
}
