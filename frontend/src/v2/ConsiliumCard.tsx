import { roleLabel } from "./types";
/**
 * Карточка находки консилиума — док читалки, как выбор роли. Показывает, что стоит в
 * сценарии, что сказал каждый чтец, что доказал (или не смог) арбитр, и даёт кнопки-имена.
 * Любое имя применяется ко ВСЕЙ роли в абзаце: так устроен приём на сервере.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/ToastProvider";
import {
  BACKER_LABELS,
  KIND_NOTES,
  KIND_TITLES,
  buildCandidates,
  impactNote,
  nextFinding,
  readerAnswer,
} from "./consilium";
import {
  acceptFinding,
  describeConsiliumError,
  dismissFinding,
  fetchRecordingImpact,
  invalidateReader,
  isStaleFindingError,
} from "./editorApi";
import { NARRATOR, type ConsiliumDecision, type ConsiliumItem } from "./types";

type ConsiliumCardProps = {
  bookId: string;
  item: ConsiliumItem;
  /** все роли каста книги — имена, которые можно поставить */
  castNames: readonly string[];
  items: readonly ConsiliumItem[];
  onClose: () => void;
  onNext: (item: ConsiliumItem) => void;
};

type Choice = { kind: "accept"; speaker: string } | { kind: "dismiss" };
// `acceptFinding` and `dismissFinding` return different shapes; the mutation result is
// whichever one ran. `useMutation`'s inferred `TData` needs the union spelled out, or it
// narrows to the first branch's type and rejects the other at the call site below.
type DecideResult = ConsiliumDecision | { ok: boolean; finding_id: string };

export function ConsiliumCard({ bookId, item, castNames, items, onClose, onNext }: ConsiliumCardProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const cast = useMemo(() => new Set(castNames), [castNames]);
  const candidates = useMemo(() => buildCandidates(item, cast), [item, cast]);
  const [stale, setStale] = useState(false);
  const [other, setOther] = useState(false);
  const [filter, setFilter] = useState("");
  const [decided, setDecided] = useState<string | null>(null);

  // Предупреждение считается на сервере для всех имён сразу: имя применяется по клику,
  // и примечание должно стоять у кнопки ДО него. Пока ответа нет — кнопки работают.
  const impact = useQuery({
    queryKey: ["v2", "consilium-impact", item.id],
    queryFn: () => fetchRecordingImpact(item.id),
    enabled: item.status === "new",
    staleTime: 30_000,
  });

  const decide = useMutation<DecideResult, unknown, Choice>({
    mutationFn: (choice: Choice) => (choice.kind === "dismiss" ? dismissFinding(item.id) : acceptFinding(item.id, choice.speaker)),
    onSuccess: async (result, choice) => {
      // «оставить» приходит либо от dismiss, либо от accept с именем из сценария (status: dismissed)
      const speaker = "speaker" in result ? result.speaker : "";
      const applied = choice.kind === "accept" && "status" in result && result.status === "accepted";
      setDecided(applied ? `Paragraph role: ${roleLabel(speaker)}` : "Script assignment kept");
      pushToast({ tone: "success", title: applied ? `Role changed: ${roleLabel(speaker)}` : "Kept unchanged" });
      await invalidateReader(queryClient, bookId, applied ? ["chapters", "disputed", "consilium"] : ["consilium"]);
      // чтобы следующая находка той же главы видела новую сверку позже
      await queryClient.invalidateQueries({ queryKey: ["v2", "consilium-impact"] });
    },
    onError: async (error) => {
      if (isStaleFindingError(error)) setStale(true);
      pushToast({ tone: "error", title: "Decision not saved", detail: describeConsiliumError(error) });
      await invalidateReader(queryClient, bookId, ["consilium"]);
    },
  });

  const next = nextFinding(items, item.id);
  const provenChange = item.evidence_proven && item.arbiter_verdict === "change";
  const provenKeep = item.evidence_proven && item.arbiter_verdict === "keep_current";
  const allNames = useMemo(
    () => [NARRATOR, ...castNames.filter((name) => name !== NARRATOR)].filter((name) => name.toLowerCase().includes(filter.trim().toLowerCase())),
    [castNames, filter],
  );
  const busy = decide.isPending;
  const alreadyDecided = item.status !== "new";

  return (
    <section className="v2r-dock v2r-dock--cons" role="region" aria-label="AI review finding">
      <div className="v2r-dock-head">
        <span className="v2r-dock-title">AI review · {KIND_TITLES[item.kind]}</span>
        <span className="v2r-dock-source">
          ch. {item.chapter_index}, paragraph {item.ordinal}
        </span>
        <button type="button" className="btn btn-sm v2r-btn v2r-dock-close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <p className="v2r-cons-note">{KIND_NOTES[item.kind]}</p>

      <dl className="v2r-cons-facts">
        <dt>current script</dt>
        <dd><strong>{roleLabel(item.current_speaker)}</strong></dd>
        <dt>independent reviewers</dt>
        <dd>opus: {readerAnswer(item.reader_opus, cast)} · sol: {readerAnswer(item.reader_sol, cast)}</dd>
        <dt>arbiter</dt>
        <dd>
          {provenChange || provenKeep ? (
            <>
              <strong>{provenChange ? `change to ${roleLabel(item.arbiter_speaker)}` : "script confirmed"}</strong>
              <blockquote className="v2r-cons-quote">«{item.evidence_quote}» <span className="num">— paragraph {item.evidence_para}</span></blockquote>
              {item.reason ? <span className="v2r-cons-reason">{item.reason}</span> : null}
            </>
          ) : (
            <>
              <strong>The arbiter found insufficient evidence in the text. You decide.</strong>
              {item.reason ? <span className="v2r-cons-reason v2r-cons-reason--weak">{item.reason}</span> : null}
            </>
          )}
        </dd>
      </dl>

      {decided || alreadyDecided ? (
        <div className="v2r-dock-actions">
          <span className="v2r-cons-done">{decided ?? "This finding is already resolved."}</span>
          {next ? (
            <button type="button" className="btn btn-sm btn-primary" onClick={() => onNext(next)}>
              next finding →
            </button>
          ) : (
            <span className="v2r-dock-hint">No unresolved findings remain.</span>
          )}
        </div>
      ) : (
        <>
          {stale ? <p className="v2r-sheet-error">This passage was edited after the run. Review it again.</p> : null}
          <div className="v2r-dock-actions" role="group" aria-label="Who is speaking">
            {!stale
              ? candidates.map((candidate) => (
                  <button
                    key={candidate.name}
                    type="button"
                    disabled={busy}
                    className={candidate.primary ? "btn btn-sm btn-primary v2r-cons-pick" : "btn btn-sm v2r-btn v2r-cons-pick"}
                    onClick={() => decide.mutate(candidate.keep ? { kind: "dismiss" } : { kind: "accept", speaker: candidate.name })}
                  >
                    {candidate.keep ? `${roleLabel(candidate.name)} — keep script assignment` : roleLabel(candidate.name)}
                    <span className="v2r-cons-backers">{candidate.backers.filter((b) => b !== "script" || !candidate.keep).map((b) => BACKER_LABELS[b]).join(" and ")}</span>
                    {(() => {
                      const note = candidate.keep ? null : impactNote(impact.data, candidate.name);
                      return note ? <span className={`v2r-cons-impact v2r-cons-impact--${note.level}`}>{note.text}</span> : null;
                    })()}
                  </button>
                ))
              : (
                  <button type="button" disabled={busy} className="btn btn-sm v2r-btn" onClick={() => decide.mutate({ kind: "dismiss" })}>
                    keep unchanged
                  </button>
                )}
            <button type="button" disabled={busy} className={other ? "btn btn-sm v2r-btn is-on" : "btn btn-sm v2r-btn"} onClick={() => setOther((open) => !open)} aria-expanded={other}>
              another role…
            </button>
          </div>
          {other ? (
            <div className="v2r-cons-other">
              <label className="v2r-search v2r-search--dock">
                <input
                  className="v2r-search-input v2r-cons-filter"
                  type="search"
                  placeholder="Find a role in the cast…"
                  aria-label="Search book cast"
                  autoComplete="off"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  autoFocus
                />
              </label>
              <div className="v2r-cons-names">
                {allNames.map((name) => {
                  const note = impactNote(impact.data, name);
                  return (
                    <button key={name} type="button" disabled={busy} title={note?.text}
                            className={note?.level === "warn" ? "btn btn-sm v2r-btn v2r-cons-name--warn" : "btn btn-sm v2r-btn"}
                            onClick={() => decide.mutate({ kind: "accept", speaker: name })}>
                      {note?.level === "warn" ? "⚠ " : ""}{roleLabel(name)}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
