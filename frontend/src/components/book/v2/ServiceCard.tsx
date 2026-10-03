import { useState } from "react";
import { Button } from "../../../ui";
import { Icon } from "../../Icon";
import { formatWhen } from "../../../v2/editorApi";
import { formatTokens, isRunActive, type HubProgress } from "../../../viewModels/bookHub";
import { DeleteBookConfirm } from "./DeleteBookConfirm";
import type { HubActions } from "./hubApi";

type ServiceCardProps = {
  bookTitle: string;
  bookStatus: string;
  stopRequested: boolean;
  progress: HubProgress;
  actions: HubActions;
};

/** Collapsed by default: mode, run internals, last error, v1 stop/continue, delete. */
export function ServiceCard({ bookTitle, bookStatus, stopRequested, progress, actions }: ServiceCardProps) {
  const [confirming, setConfirming] = useState(false);
  const run = progress.run;
  const legacy = progress.mode !== "v2";
  const active = isRunActive(run);

  return (
    <details className="panel hub-card hub-service">
      <summary className="hub-service-summary">
        <Icon name="chevron" />
        <span className="hub-card-title">Служебное</span>
        <span className="hub-dim">режим {progress.mode}{run ? ` · прогон ${run.status}` : ""}</span>
      </summary>
      <div className="hub-service-body">
        <dl className="hub-kv">
          <div><dt>Режим</dt><dd className="num">{progress.mode}</dd></div>
          <div><dt>Статус</dt><dd className="num">{bookStatus}{stopRequested ? " · stop_requested" : ""}</dd></div>
          {run ? (
            <>
              <div><dt>Прогон</dt><dd className="num">{run.status}{run.step ? ` · ${run.step}` : ""} · {run.chapters_done}/{run.chapters_total} глав</dd></div>
              <div><dt>Токены</dt><dd className="num">{formatTokens(run.tokens)}{run.calls ? ` · вызовов ${run.calls}` : ""}</dd></div>
              <div><dt>Начат</dt><dd className="num">{formatWhen(run.started_at)}{run.updated_at ? ` · обновлён ${formatWhen(run.updated_at)}` : ""}</dd></div>
              {run.error ? <div><dt>Ошибка</dt><dd className="hub-danger hub-wrap">{run.error}</dd></div> : null}
            </>
          ) : (
            <div><dt>Прогон</dt><dd className="hub-dim">разметку v2 ещё не запускали</dd></div>
          )}
        </dl>
        <div className="hub-card-actions">
          {active ? (
            <Button size="sm" variant="secondary" loading={actions.stop.isPending} onClick={() => actions.stop.mutate()}>Остановить прогон</Button>
          ) : null}
          {legacy ? (
            <>
              <Button size="sm" variant="secondary" loading={actions.repair.isPending} onClick={() => actions.repair.mutate("stop_pipeline")}>
                Остановить v1
              </Button>
              <Button size="sm" variant="secondary" loading={actions.repair.isPending} onClick={() => actions.repair.mutate("continue_pipeline")}>
                Продолжить v1
              </Button>
            </>
          ) : null}
          {!confirming ? (
            <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>Удалить книгу</Button>
          ) : null}
        </div>
        {confirming ? (
          <DeleteBookConfirm title={bookTitle} pending={actions.remove.isPending} onConfirm={() => actions.remove.mutate()} onCancel={() => setConfirming(false)} />
        ) : null}
      </div>
    </details>
  );
}
