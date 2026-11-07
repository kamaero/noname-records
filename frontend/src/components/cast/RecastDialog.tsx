import { useEffect, useMemo, useState } from "react";

import { apiGet, apiPostJson, describeApiError } from "../../api/client";
import { Button, Dialog, Field } from "../../ui";
import type { CastRow } from "../../viewModels/booksCast";
import type { PickerDictor } from "./ActorPicker";

/* «Переназначить по циклу» (план 2б): новый актёр встаёт в эту книгу и во все книги
   цикла, где роль ещё не записана; где записана — остаётся прежний. Предпросмотр
   показывает это до решения; итог пишет журнал рекастов и письмо новому актёру. */

const REASONS: [string, string][] = [
  ["overlap", "Busy with another role"],
  ["audition_failed", "Audition was not suitable"],
  ["left", "Left the project"],
  ["removed", "Removed from database"],
  ["other", "Other"],
];

type Book = { book_id: string; book_title: string; actor?: string };
type Preview = { from_actor: string; changed: Book[]; kept: Book[] };
type Result = Preview & { outvoted: (Book & { by?: string })[] };

function fold(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е");
}

export function RecastDialog({ row, dictors, onClose, onDone }: {
  row: CastRow; dictors: PickerDictor[]; onClose: () => void; onDone: (summary: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("overlap");
  const [comment, setComment] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = (row.actor_name || "").replace(/\?+$/, "").trim();

  const found = useMemo(() => {
    const needle = fold(query.trim());
    return dictors.filter((d) => fold(d.name) !== fold(current) && (!needle || fold(d.name).includes(needle))).slice(0, 8);
  }, [dictors, query, current]);

  useEffect(() => {
    setPreview(null);
    setError("");
    if (!actor) return;
    let alive = true;
    apiGet<Preview>(`/api/v2/characters/${encodeURIComponent(row.character_id)}/recast-preview?to_actor=${encodeURIComponent(actor)}`)
      .then((p) => alive && setPreview(p))
      .catch((e) => alive && setError(describeApiError(e, "Could not calculate preview.")));
    return () => {
      alive = false;
    };
  }, [actor, row.character_id]);

  const run = async () => {
    setBusy(true);
    try {
      const result = await apiPostJson<Result>(`/api/v2/characters/${encodeURIComponent(row.character_id)}/recast`,
        { to_actor: actor, reason, comment });
      const parts = [`reassigned in: ${result.changed.map((b) => b.book_title).join(", ") || "—"}`];
      if (result.kept.length) parts.push(`unchanged (already recorded): ${result.kept.map((b) => b.book_title).join(", ")}`);
      if (result.outvoted.length) parts.push(`decision did not take priority: ${result.outvoted.map((b) => b.book_title).join(", ")}`);
      onDone(parts.join(" · "));
    } catch (e) {
      setError(describeApiError(e, "Reassignment failed."));
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={`Reassign: «${row.name}»`}
      subtitle={current ? `Current: ${current}. The new voice actor will be assigned across the series where the role is not yet recorded.` : undefined}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!actor || !preview} onClick={run}>Reassign</Button>
        </>
      }
    >
      <div className="form-grid">
        <Field label="New voice actor" required>
          {(props) => (
            <input {...props} data-autofocus value={actor || query} placeholder="Find voice actor"
              onChange={(e) => { setActor(""); setQuery(e.target.value); }} />
          )}
        </Field>
        {!actor && query.trim() ? (
          <ul className="recast-pick">
            {found.map((d) => (
              <li key={d.user_id}><button type="button" onClick={() => setActor(d.name)}>{d.name}</button></li>
            ))}
            {found.length === 0 ? <li className="recast-none">No voice actors found</li> : null}
          </ul>
        ) : null}
        <Field label="Reason">
          {(props) => (
            <select {...props} value={reason} onChange={(e) => setReason(e.target.value)}>
              {REASONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          )}
        </Field>
        <Field label="Comment" hint="Recorded in reassignment history">
          {(props) => <input {...props} value={comment} onChange={(e) => setComment(e.target.value)} />}
        </Field>
        {preview ? (
          <div className="recast-preview">
            <p><strong>Will change</strong> (if your decision takes priority): {preview.changed.map((b) => b.book_title).join(", ") || "nowhere"}</p>
            {preview.kept.length ? (
              <p><strong>Will stay unchanged</strong> — already recorded: {preview.kept.map((b) => `${b.book_title} (${b.actor})`).join(", ")}</p>
            ) : null}
          </div>
        ) : null}
        {error ? <p className="recast-error">{error}</p> : null}
      </div>
    </Dialog>
  );
}
