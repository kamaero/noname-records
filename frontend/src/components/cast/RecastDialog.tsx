import { useEffect, useMemo, useState } from "react";

import { apiGet, apiPostJson, describeApiError } from "../../api/client";
import { Button, Dialog, Field } from "../../ui";
import type { CastRow } from "../../viewModels/booksCast";
import type { PickerDictor } from "./ActorPicker";

/* «Переназначить по циклу» (план 2б): новый актёр встаёт в эту книгу и во все книги
   цикла, где роль ещё не записана; где записана — остаётся прежний. Предпросмотр
   показывает это до решения; итог пишет журнал рекастов и письмо новому актёру. */

const REASONS: [string, string][] = [
  ["overlap", "Занят в соседней роли"],
  ["audition_failed", "Проба не подошла"],
  ["left", "Ушёл из проекта"],
  ["removed", "Удалён из базы"],
  ["other", "Другое"],
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
      .catch((e) => alive && setError(describeApiError(e, "Не удалось посчитать предпросмотр.")));
    return () => {
      alive = false;
    };
  }, [actor, row.character_id]);

  const run = async () => {
    setBusy(true);
    try {
      const result = await apiPostJson<Result>(`/api/v2/characters/${encodeURIComponent(row.character_id)}/recast`,
        { to_actor: actor, reason, comment });
      const parts = [`встал в: ${result.changed.map((b) => b.book_title).join(", ") || "—"}`];
      if (result.kept.length) parts.push(`остался прежний (записано): ${result.kept.map((b) => b.book_title).join(", ")}`);
      if (result.outvoted.length) parts.push(`голос не перевесил: ${result.outvoted.map((b) => b.book_title).join(", ")}`);
      onDone(parts.join(" · "));
    } catch (e) {
      setError(describeApiError(e, "Рекаст не прошёл."));
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={`Переназначить: «${row.name}»`}
      subtitle={current ? `Сейчас: ${current}. Новый актёр встанет во все книги цикла, где роль ещё не записана.` : undefined}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
          <Button variant="primary" loading={busy} disabled={!actor || !preview} onClick={run}>Переназначить</Button>
        </>
      }
    >
      <div className="form-grid">
        <Field label="Новый актёр" required>
          {(props) => (
            <input {...props} data-autofocus value={actor || query} placeholder="Найти диктора"
              onChange={(e) => { setActor(""); setQuery(e.target.value); }} />
          )}
        </Field>
        {!actor && query.trim() ? (
          <ul className="recast-pick">
            {found.map((d) => (
              <li key={d.user_id}><button type="button" onClick={() => setActor(d.name)}>{d.name}</button></li>
            ))}
            {found.length === 0 ? <li className="recast-none">Дикторов не нашлось</li> : null}
          </ul>
        ) : null}
        <Field label="Причина">
          {(props) => (
            <select {...props} value={reason} onChange={(e) => setReason(e.target.value)}>
              {REASONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          )}
        </Field>
        <Field label="Комментарий" hint="Попадёт в журнал рекастов">
          {(props) => <input {...props} value={comment} onChange={(e) => setComment(e.target.value)} />}
        </Field>
        {preview ? (
          <div className="recast-preview">
            <p><strong>Сменится</strong> (если голос пройдёт): {preview.changed.map((b) => b.book_title).join(", ") || "нигде"}</p>
            {preview.kept.length ? (
              <p><strong>Останется прежний</strong> — роль там уже записана: {preview.kept.map((b) => `${b.book_title} (${b.actor})`).join(", ")}</p>
            ) : null}
          </div>
        ) : null}
        {error ? <p className="recast-error">{error}</p> : null}
      </div>
    </Dialog>
  );
}
