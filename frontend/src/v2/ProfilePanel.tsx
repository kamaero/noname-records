/**
 * Side sheet with the book ↔ author profile: who the author is, the book's
 * mode/profile, counts, last sync, and the two actions
 * «Обновить профиль автора из книги» (sync) / «Применить профиль к книге» (apply).
 */
import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPostJson, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { formatWhen, invalidateReader, useBookProfile } from "./editorApi";
import { Sheet } from "./Sheet";
import type { BookProfileResponse, ProfileActionResponse } from "./types";

type ProfilePanelProps = {
  bookId: string;
  onClose: () => void;
};

const COUNT_LABELS: Array<[keyof BookProfileResponse["counts"], string]> = [
  ["author_characters", "Author's characters"],
  ["author_characters_confirmed", "— confirmed"],
  ["linked_characters", "Book characters linked to author"],
  ["author_pronunciations", "Author dictionary stress entries"],
  ["book_pronunciation_terms", "Book dictionary stress entries"],
  ["v2_segments", "Annotated segments"],
  ["v2_attributed_segments", "— with roles"],
  ["v2_stress_marks", "Stress marks in annotated text"],
];

/** The sync/apply summaries are free-form on the backend; show whatever shape arrives as a compact list. */
export function SummaryList({ summary }: { summary: unknown }): ReactNode {
  if (summary === null || summary === undefined || summary === "") return <p className="v2r-sheet-empty">No details.</p>;
  if (typeof summary === "string" || typeof summary === "number" || typeof summary === "boolean") {
    return <p className="v2r-summary-text">{String(summary)}</p>;
  }
  if (Array.isArray(summary)) {
    return (
      <ul className="v2r-summary">
        {summary.map((item, index) => (
          <li key={index}>{typeof item === "object" && item !== null ? JSON.stringify(item) : String(item)}</li>
        ))}
      </ul>
    );
  }
  const entries = Object.entries(summary as Record<string, unknown>);
  if (entries.length === 0) return <p className="v2r-sheet-empty">No details.</p>;
  return (
    <dl className="v2r-kv">
      {entries.map(([key, value]) => (
        <div key={key} className="v2r-kv-row">
          <dt>{key}</dt>
          <dd className="num">{typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function summaryToText(summary: unknown): string | undefined {
  if (summary === null || summary === undefined || summary === "") return undefined;
  if (typeof summary !== "object") return String(summary);
  const entries = Array.isArray(summary) ? summary.map(String) : Object.entries(summary as Record<string, unknown>).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  return entries.slice(0, 6).join(" · ") || undefined;
}

export function ProfilePanel({ bookId, onClose }: ProfilePanelProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const profile = useBookProfile(bookId, true);
  const [overwrite, setOverwrite] = useState(false);
  const [lastResult, setLastResult] = useState<{ label: string; summary: unknown } | null>(null);

  const base = `/api/v2/books/${encodeURIComponent(bookId)}/profile`;

  const afterAction = async (label: string, result: ProfileActionResponse) => {
    if (!result.ok) {
      pushToast({ tone: "error", title: `${label}: failed`, detail: result.error || "The server returned no details." });
      return;
    }
    setLastResult({ label, summary: result.summary });
    pushToast({ tone: "success", title: `${label}: done`, detail: summaryToText(result.summary) });
    await invalidateReader(queryClient, bookId, ["profile", "chapters", "cast", "queue"]);
  };

  const sync = useMutation({
    mutationFn: () => apiPostJson<ProfileActionResponse>(`${base}/sync`, {}),
    onSuccess: (result) => afterAction("Author profile updated", result),
    onError: (error) => pushToast({ tone: "error", title: "Could not update author profile", detail: describeApiError(error, "Synchronization failed.") }),
  });

  const apply = useMutation({
    mutationFn: () => apiPostJson<ProfileActionResponse>(`${base}/apply`, { overwrite }),
    onSuccess: (result) => afterAction("Profile applied to book", result),
    onError: (error) => pushToast({ tone: "error", title: "Could not apply profile", detail: describeApiError(error, "Profile application failed.") }),
  });

  const data = profile.data;
  const busy = sync.isPending || apply.isPending;

  return (
    <Sheet
      title="Book profile"
      subtitle={data ? data.book.title : profile.isLoading ? "Loading…" : null}
      onClose={onClose}
      footer={
        <div className="v2r-profile-actions">
          <button type="button" className="btn btn-sm v2r-btn" disabled={busy || !data} onClick={() => sync.mutate()}>
            {sync.isPending ? "Updating…" : "Update author profile from book"}
          </button>
          <button type="button" className="btn btn-sm btn-primary v2r-btn" disabled={busy || !data} onClick={() => apply.mutate()}>
            {apply.isPending ? "Applying…" : "Apply profile to book"}
          </button>
          <label className="v2r-check">
            <input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} disabled={busy} />
            <span>overwrite manual colors and voice actors</span>
          </label>
        </div>
      }
    >
      {profile.isError ? <p className="v2r-sheet-error">{describeApiError(profile.error, "Could not load profile.")}</p> : null}
      {data ? (
        <>
          <section className="v2r-sheet-section">
            <dl className="v2r-kv">
              <div className="v2r-kv-row">
                <dt>Author</dt>
                <dd>{data.author ? data.author.name : <span className="v2r-sheet-empty">author unassigned</span>}</dd>
              </div>
              <div className="v2r-kv-row">
                <dt>Pipeline mode</dt>
                <dd><span className="v2r-tag">{data.book.pipeline_mode || "—"}</span></dd>
              </div>
              <div className="v2r-kv-row">
                <dt>Validation profile</dt>
                <dd><span className="v2r-tag">{data.book.validation_profile || "—"}</span></dd>
              </div>
            </dl>
          </section>

          <section className="v2r-sheet-section">
            <h3 className="v2r-sheet-h">Counts</h3>
            <dl className="v2r-kv">
              {COUNT_LABELS.map(([key, label]) => (
                <div key={key} className="v2r-kv-row">
                  <dt>{label}</dt>
                  <dd className="num">{Number(data.counts?.[key] ?? 0)}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="v2r-sheet-section">
            <h3 className="v2r-sheet-h">Last synchronization</h3>
            {data.last_sync ? (
              <>
                <p className="v2r-sheet-note">{formatWhen(data.last_sync.at)}</p>
                <SummaryList summary={data.last_sync.summary} />
              </>
            ) : (
              <p className="v2r-sheet-empty">Profile has not been synchronized yet.</p>
            )}
          </section>

          {lastResult ? (
            <section className="v2r-sheet-section">
              <h3 className="v2r-sheet-h">{lastResult.label}</h3>
              <SummaryList summary={lastResult.summary} />
            </section>
          ) : null}
        </>
      ) : null}
    </Sheet>
  );
}
