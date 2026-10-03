import { Button, LinkButton } from "../../../ui";
import { Icon } from "../../Icon";
import { ConsiliumHubBlock } from "./ConsiliumHubBlock";
import { SoundHubBlock } from "./SoundHubBlock";
import { STEP_STATE_WORD, runFraction, stepAction, type HubProgress, type HubStepKey } from "../../../viewModels/bookHub";

type PipelineStepperProps = {
  bookId: string;
  progress: HubProgress;
  /** «Дальше: …» — one line in the card header */
  next?: { text: string; tone?: "danger" | "amber" | "blue" };
  readerTo: string;
  /** the same chapter, opened next to the author's original */
  compareTo: string;
  /** the first readable chapter, with its stress queue requested */
  stressTo: string;
  /** how many chapters the author has marked, out of the marked-up ones */
  review: { approved: number; attributed: number };
  onApproveAll: () => void;
  approvePending: boolean;
  /** approved chapters open for recording by themselves */
  autoPublish: boolean;
  onAutoPublish: (enabled: boolean) => void;
  autoPublishPending: boolean;
  hasReader: boolean;
  stressAvailable: boolean;
  onPublish: () => void;
  publishPending: boolean;
  publishArmed: boolean;
  consiliumTo?: string;
  /** Читалка первой главы с листом «Места книги» — только тому, кто правит разметку */
  soundTo?: string;
};

function Glyph({ state, index }: { state: string; index: number }) {
  if (state === "done") return <Icon name="check" />;
  if (state === "failed") return <Icon name="close" />;
  if (state === "running") return <span className="hub-step-pulse" />;
  return <span className="num">{index + 1}</span>;
}

/** The six v2 steps in a row: state, one detail line, the step's own action. */
export function PipelineStepper({ bookId, progress, next, readerTo, compareTo, stressTo, review, onApproveAll, approvePending, autoPublish, onAutoPublish, autoPublishPending, hasReader, stressAvailable, onPublish, publishPending, publishArmed, consiliumTo, soundTo }: PipelineStepperProps) {
  const castTo = `/books/${encodeURIComponent(bookId)}/cast`;

  return (
    <section className="panel hub-card hub-steps" aria-labelledby="hub-steps-title">
      <header className="hub-card-head">
        <h2 id="hub-steps-title" className="hub-card-title">Ход работы</h2>
        {next ? (
          <span className={["hub-next", next.tone ? `hub-next--${next.tone}` : ""].filter(Boolean).join(" ")}>
            Дальше: {next.text}
          </span>
        ) : null}
      </header>
      <ol className="hub-stepper">
        {progress.steps.map((step, index) => {
          const action = stepAction(step.key);
          const running = step.state === "running" && progress.run?.step === step.key;
          const key = step.key as HubStepKey;
          return (
            <li key={step.key} className={`hub-step is-${step.state}`}>
              <div className="hub-step-top">
                <span className="hub-step-glyph" aria-hidden="true">
                  <Glyph state={step.state} index={index} />
                </span>
                <strong className="hub-step-label">{step.label}</strong>
                <span className="hub-sr">, {STEP_STATE_WORD[step.state]}</span>
              </div>
              <div className="hub-bar-slot">
                {running && progress.run ? (
                  <div
                    className="hub-bar"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={progress.run.chapters_total || 0}
                    aria-valuenow={progress.run.chapters_done || 0}
                    aria-label={`${step.label}: ${progress.run.chapters_done} из ${progress.run.chapters_total} глав`}
                  >
                    <span style={{ width: `${Math.round(runFraction(progress.run) * 100)}%` }} />
                  </div>
                ) : null}
              </div>
              <span className="hub-step-detail" title={step.detail || undefined}>
                {step.detail || STEP_STATE_WORD[step.state]}
              </span>
              <div className="hub-step-foot">
              {key === "attribute" && step.state !== "pending" ? (
                <LinkButton to={castTo} size="sm" variant="secondary" title="Таблица ролей: актёр, реплики, главы — и переход к репликам роли">
                  Каст
                </LinkButton>
              ) : null}
              {action?.kind === "stress" ? (
                hasReader && stressAvailable ? (
                  <LinkButton to={stressTo} size="sm" variant="secondary">{action.label}</LinkButton>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled
                    title={
                      !hasReader
                        ? "Нет доступной для чтения размеченной главы"
                        : "Недостаточно прав: очередь ударений доступна автору, диктору и администратору"
                    }
                  >
                    {action.label}
                  </Button>
                )
              ) : null}
              {action?.kind === "reader" ? (
                hasReader ? (
                  <>
                    <LinkButton to={readerTo} size="sm" variant="secondary">{action.label}</LinkButton>
                    <LinkButton to={compareTo} size="sm" variant="secondary">Сверить с оригиналом</LinkButton>
                    {review.attributed > review.approved ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        loading={approvePending}
                        onClick={onApproveAll}
                        title="Отметить все размеченные главы проверенными — например, когда автор прочитал книгу целиком"
                      >
                        Проверены все ({review.attributed - review.approved})
                      </Button>
                    ) : null}
                    {consiliumTo ? <ConsiliumHubBlock bookId={bookId} consiliumTo={consiliumTo} /> : null}
                    {soundTo ? <SoundHubBlock bookId={bookId} soundTo={soundTo} /> : null}
                  </>
                ) : (
                  <Button size="sm" variant="secondary" disabled title="Появится после шага «Роли»">{action.label}</Button>
                )
              ) : null}
              {action?.kind === "publish" ? (
                <label className="hub-check hub-autopublish" title="Как только автор отмечает главу проверенной, она открывается дикторам">
                  <input
                    type="checkbox"
                    checked={autoPublish}
                    disabled={autoPublishPending}
                    onChange={(event) => onAutoPublish(event.target.checked)}
                  />
                  <span>Открывать дикторам после одобрения</span>
                </label>
              ) : null}
              {action?.kind === "publish" ? (
                <Button
                  size="sm"
                  variant={publishArmed ? "primary" : "secondary"}
                  loading={publishPending}
                  onClick={onPublish}
                  disabled={review.approved === 0 && review.attributed > 0}
                  title={
                    review.approved === 0 && review.attributed > 0
                      ? "Сначала отметьте проверенные главы — публикуются только они"
                      : autoPublish
                        ? "Открыть уже одобренные главы, которые ещё не ушли дикторам"
                        : undefined
                  }
                >
                  {publishArmed
                    ? "Подтвердить публикацию"
                    : review.approved > 0
                      ? `Опубликовать (${review.approved})`
                      : action.label}
                </Button>
              ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
