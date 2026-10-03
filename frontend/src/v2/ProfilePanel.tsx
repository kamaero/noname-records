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
  ["author_characters", "Персонажей у автора"],
  ["author_characters_confirmed", "— из них подтверждённых"],
  ["linked_characters", "Персонажей книги, связанных с автором"],
  ["author_pronunciations", "Ударений в словаре автора"],
  ["book_pronunciation_terms", "Ударений в словаре книги"],
  ["v2_segments", "Сегментов v2"],
  ["v2_attributed_segments", "— из них с ролями"],
  ["v2_stress_marks", "Ударений в тексте v2"],
];

/** The sync/apply summaries are free-form on the backend; show whatever shape arrives as a compact list. */
export function SummaryList({ summary }: { summary: unknown }): ReactNode {
  if (summary === null || summary === undefined || summary === "") return <p className="v2r-sheet-empty">Без подробностей.</p>;
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
  if (entries.length === 0) return <p className="v2r-sheet-empty">Без подробностей.</p>;
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
      pushToast({ tone: "error", title: `${label}: не выполнено`, detail: result.error || "Сервер ответил без подробностей." });
      return;
    }
    setLastResult({ label, summary: result.summary });
    pushToast({ tone: "success", title: `${label}: готово`, detail: summaryToText(result.summary) });
    await invalidateReader(queryClient, bookId, ["profile", "chapters", "cast", "queue"]);
  };

  const sync = useMutation({
    mutationFn: () => apiPostJson<ProfileActionResponse>(`${base}/sync`, {}),
    onSuccess: (result) => afterAction("Профиль автора обновлён", result),
    onError: (error) => pushToast({ tone: "error", title: "Не удалось обновить профиль автора", detail: describeApiError(error, "Ошибка синхронизации.") }),
  });

  const apply = useMutation({
    mutationFn: () => apiPostJson<ProfileActionResponse>(`${base}/apply`, { overwrite }),
    onSuccess: (result) => afterAction("Профиль применён к книге", result),
    onError: (error) => pushToast({ tone: "error", title: "Не удалось применить профиль", detail: describeApiError(error, "Ошибка применения профиля.") }),
  });

  const data = profile.data;
  const busy = sync.isPending || apply.isPending;

  return (
    <Sheet
      title="Профиль книги"
      subtitle={data ? data.book.title : profile.isLoading ? "Загружаю…" : null}
      onClose={onClose}
      footer={
        <div className="v2r-profile-actions">
          <button type="button" className="btn btn-sm v2r-btn" disabled={busy || !data} onClick={() => sync.mutate()}>
            {sync.isPending ? "Обновляю…" : "Обновить профиль автора из книги"}
          </button>
          <button type="button" className="btn btn-sm btn-primary v2r-btn" disabled={busy || !data} onClick={() => apply.mutate()}>
            {apply.isPending ? "Применяю…" : "Применить профиль к книге"}
          </button>
          <label className="v2r-check">
            <input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} disabled={busy} />
            <span>перезаписать ручные цвета/актёров</span>
          </label>
        </div>
      }
    >
      {profile.isError ? <p className="v2r-sheet-error">{describeApiError(profile.error, "Не удалось загрузить профиль.")}</p> : null}
      {data ? (
        <>
          <section className="v2r-sheet-section">
            <dl className="v2r-kv">
              <div className="v2r-kv-row">
                <dt>Автор</dt>
                <dd>{data.author ? data.author.name : <span className="v2r-sheet-empty">автор не определён</span>}</dd>
              </div>
              <div className="v2r-kv-row">
                <dt>Режим пайплайна</dt>
                <dd><span className="v2r-tag">{data.book.pipeline_mode || "—"}</span></dd>
              </div>
              <div className="v2r-kv-row">
                <dt>Профиль валидации</dt>
                <dd><span className="v2r-tag">{data.book.validation_profile || "—"}</span></dd>
              </div>
            </dl>
          </section>

          <section className="v2r-sheet-section">
            <h3 className="v2r-sheet-h">Счётчики</h3>
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
            <h3 className="v2r-sheet-h">Последняя синхронизация</h3>
            {data.last_sync ? (
              <>
                <p className="v2r-sheet-note">{formatWhen(data.last_sync.at)}</p>
                <SummaryList summary={data.last_sync.summary} />
              </>
            ) : (
              <p className="v2r-sheet-empty">Профиль ещё не синхронизировали.</p>
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
