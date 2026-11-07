import { useCallback, useRef, useState, type ReactNode } from "react";

import { Button, Dialog, Field } from "../../ui";
import type { AuditionPair, AuditionReason, AuditionLane } from "../../v2/types";
import { COMMENT_MAX_CHARS, REASON_LABEL, REASON_MAX_SCORE } from "../../viewModels/auditionPairs";
import "./ReasonPrompt.css";

const CHOICES = Object.entries(REASON_LABEL) as [AuditionReason, string][];

/** Ответ окна: причина (только при низкой оценке автора) и слова для студии. */
export type ReasonAnswer = { reason: AuditionReason | null; comment: string };

type Asked = { pair: AuditionPair; lane: AuditionLane; reasons: boolean };

/**
 * «Чем не этот герой?» — когда автор ставит пробе ниже 7 (до 11.10 — 5 и ниже), и
 * «пара слов для студии» — по ссылке под любой оценкой.
 *
 * Кнопки причины относятся к герою: общее «Голос» воспринималось как приговор качеству
 * голоса, даже когда он просто не подходил персонажу. Причина
 * одной фразой уходит диктору в вежливом отказе; слова своими словами — только студии,
 * иначе автор писал бы осторожно. Закрыли окно — оценка сохраняется как есть.
 */
export function useReasonPrompt(): {
  ask: (pair: AuditionPair, lane: AuditionLane, reasons: boolean) => Promise<ReasonAnswer | null>;
  dialog: ReactNode;
} {
  const [asked, setAsked] = useState<Asked | null>(null);
  const [reason, setReason] = useState<AuditionReason | null>(null);
  const [comment, setComment] = useState("");
  const resolver = useRef<((value: ReasonAnswer | null) => void) | null>(null);

  const finish = useCallback((value: ReasonAnswer | null) => {
    resolver.current?.(value);
    resolver.current = null;
    setAsked(null);
  }, []);

  const ask = useCallback((pair: AuditionPair, lane: AuditionLane, reasons: boolean) => new Promise<ReasonAnswer | null>((resolve) => {
    resolver.current?.(null);
    resolver.current = resolve;
    const entry = pair.scores?.[lane];
    setReason(reasons ? ((entry?.reason || null) as AuditionReason | null) : null);
    setComment(entry?.comment || "");
    setAsked({ pair, lane, reasons });
  }), []);

  const tooLong = comment.trim().length > COMMENT_MAX_CHARS;
  const dialog = asked ? (
    <Dialog
      title={asked.reasons ? "What does not fit this character?" : "A note for the studio"}
      subtitle={`${asked.pair.actor_name} · «${asked.pair.role}». `
        + (asked.reasons ? "The voice may be excellent; explain how this character should sound." : "How should this character sound?")}
      onClose={() => finish(null)}
      footer={
        <>
          <Button variant="ghost" onClick={() => finish(null)}>Skip</Button>
          <Button variant="primary" disabled={tooLong} onClick={() => finish({ reason, comment: comment.trim() })}>
            Save
          </Button>
        </>
      }
    >
      {asked.reasons ? (
        <>
          <div className="reason-choices" role="group" aria-label="Reason">
            {CHOICES.map(([value, label], index) => (
              <Button key={value} variant={reason === value ? "primary" : "secondary"} aria-pressed={reason === value}
                data-autofocus={index === 0 ? true : undefined}
                onClick={() => setReason(reason === value ? null : value)}>
                {label}
              </Button>
            ))}
          </div>
          <p className="reason-hint">This short reason will be included in the voice actor's rejection message.</p>
        </>
      ) : null}
      <Field label="Describe how the character should sound"
        hint={tooLong ? undefined : "Visible to the studio only; not sent to the voice actor."}
        error={tooLong ? `Too long; use at most ${COMMENT_MAX_CHARS} characters.` : undefined}>
        {(props) => (
          <textarea {...props} className="ui-input reason-comment" rows={3} value={comment}
            data-autofocus={asked.reasons ? undefined : true}
            placeholder="For example: the character needs a bright, youthful voice"
            onChange={(event) => setComment(event.target.value)} />
        )}
      </Field>
    </Dialog>
  ) : null;

  return { ask, dialog };
}

type ReasonNoteProps = {
  pair: AuditionPair;
  lane: AuditionLane;
  /** может ли смотрящий нажимать эту дорожку — тогда видны ссылки «причина?» и «комментарий» */
  canAsk: boolean;
  onAsk: (reasons: boolean) => void;
};

/** Под шкалой: причина автора и слова для студии — или ссылка, чтобы их добавить. */
export function ReasonNote({ pair, lane, canAsk, onAsk }: ReasonNoteProps) {
  const entry = pair.scores?.[lane];
  if (!entry) return null;
  const low = lane === "author" && entry.score <= REASON_MAX_SCORE;
  const reason = entry.reason as AuditionReason | "" | undefined;
  const label = low && reason && REASON_LABEL[reason] ? REASON_LABEL[reason] : "";
  const comment = entry.comment || "";
  if (!label && !comment && !canAsk) return null;
  return (
    <span className="reason-note">
      {label ? <span>{label}</span> : null}
      {comment ? <q className="reason-comment-text" title="Studio only">{comment}</q> : null}
      {canAsk ? (
        <button type="button" className="reason-ask" onClick={() => onAsk(low)}>
          {low && !label ? "reason?" : label || comment ? "edit" : "comment"}
        </button>
      ) : null}
    </span>
  );
}
