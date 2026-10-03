import { memo, type CSSProperties } from "react";
import { hasOperatorSpans } from "./attribution";
import { renderSegment, type WordRange } from "./render";
import { SoundMarks } from "./SoundLayer";
import { visibleStress } from "./StressToggle";
import { NARRATOR, UNSURE, type ReaderSegment, type RoleStyle, type SoundMarker } from "./types";

type ParagraphProps = {
  segment: ReaderSegment;
  styleOf: (speaker: string) => RoleStyle | undefined;
  showStress: boolean;
  /** every mark; false keeps only rare words and marks a person set («только редкие») */
  stressAll?: boolean;
  query: string;
  dim: boolean;
  mine: boolean;
  /** editor mode: words become clickable `.v2r-word` spans (never for dictors) */
  editable?: boolean;
  /**
   * Only the words, for the stress constructor — no role chips, no «роль» affordance.
   * «Все реплики роли» gives an actor his stress marks and nothing that reassigns lines.
   */
  stressable?: boolean;
  /**
   * Remark candidates get a clickable dashed underline — author/operator only
   * (`can_edit`), not every voicing dictor: a wrong reassignment here is the same
   * database write the role picker makes, so a plain dictor must see nothing new.
   * Absent/empty leaves the paragraph pixel-identical to before this feature.
   */
  remarkCandidates?: ReaderSegment["remark_candidates"];
  /** the word whose stress constructor is open — passed only to the segment that owns it */
  activeWord?: WordRange | null;
  /** the span whose role picker is open — passed only to the segment that owns it */
  activeSpan?: WordRange | null;
  /** editor mode: this paragraph's role picker is open (any scope) */
  rolePicking?: boolean;
  /** the line the arrow keys are standing on, in «моя роль» */
  current?: boolean;
  /** editor: у абзаца нерешённая находка консилиума; "active" — её карточка открыта */
  consilium?: "open" | "active" | null;
  /** editor: этот абзац — источник цитаты арбитра для открытой карточки */
  evidence?: boolean;
  /**
   * editor: значимые звуки абзаца (звуковой слой). Массив должен быть стабильным между
   * рендерами — абзац под `memo`, иначе перерисовывается вся глава.
   */
  soundMarks?: SoundMarker[];
  /** editor: открыть маркер звука; стабильная функция по той же причине */
  onSoundOpen?: (marker: SoundMarker) => void;
};

/** DOM id of a segment's paragraph/heading — used by the stress queue to scroll to a sample. */
export const segmentDomId = (segmentId: string) => `seg-${segmentId}`;

function leadColor(segment: ReaderSegment, styleOf: ParagraphProps["styleOf"]): string {
  const lead = segment.spans.find((span) => span.speaker !== NARRATOR && span.speaker !== UNSURE);
  return lead ? styleOf(lead.speaker)?.color || "transparent" : "transparent";
}

const OPERATOR_MARK = "изменено оператором";

export const Paragraph = memo(function Paragraph({
  segment,
  styleOf,
  showStress,
  stressAll = true,
  query,
  dim,
  mine,
  editable = false,
  stressable = false,
  remarkCandidates,
  activeWord = null,
  activeSpan = null,
  rolePicking = false,
  current = false,
  consilium = null,
  evidence = false,
  soundMarks,
  onSoundOpen,
}: ParagraphProps) {
  const nodes = renderSegment(segment.text, segment.spans, visibleStress(segment.stress, stressAll), {
    showStress,
    query,
    styleOf,
    words: editable || stressable,
    activeWord,
    roles: editable,
    activeSpan,
    remarkCandidates,
  });
  const operator = editable && hasOperatorSpans(segment.spans);
  const mark = operator ? (
    <span className="v2r-op" title={OPERATOR_MARK} aria-label={OPERATOR_MARK} role="img" />
  ) : null;
  // в конце абзаца: на телефоне это значок после текста, на широком экране — метка на поле
  const sound = soundMarks && onSoundOpen ? <SoundMarks markers={soundMarks} onOpen={onSoundOpen} /> : null;
  if (segment.kind === "heading") {
    return (
      <h2 id={segmentDomId(segment.id)} data-seg={segment.id} className={dim ? "v2r-h v2r-h--dim" : "v2r-h"}>
        {mark}
        {nodes}
        {sound}
      </h2>
    );
  }
  // narration only: no chip to click, so a small «роль» affordance at the paragraph start
  const narration = editable && !segment.spans.some((span) => span.speaker !== NARRATOR && span.end > span.start);
  const className = [
    "v2r-p",
    dim ? "v2r-p--dim" : "",
    mine ? "v2r-p--mine" : "",
    rolePicking ? "v2r-p--picking" : "",
    current ? "v2r-p--current" : "",
    consilium ? "v2r-p--cons" : "",
    consilium === "active" ? "v2r-p--cons-active" : "",
    evidence ? "v2r-p--evidence" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <p id={segmentDomId(segment.id)} data-seg={segment.id} className={className} style={{ "--p-color": leadColor(segment, styleOf) } as CSSProperties}>
      {mark}
      {consilium ? (
        <button type="button" className="v2r-cons-mark" data-seg={segment.id} title="Находка консилиума — открыть" aria-label="Открыть находку консилиума">
          ИИ
        </button>
      ) : null}
      {narration ? (
        <button
          type="button"
          className={rolePicking ? "v2r-role-hint is-active" : "v2r-role-hint"}
          data-start={0}
          data-end={segment.text.length}
          title="Переназначить роль"
          aria-label="Переназначить роль абзаца"
        >
          роль
        </button>
      ) : null}
      {nodes}
      {sound}
    </p>
  );
});
