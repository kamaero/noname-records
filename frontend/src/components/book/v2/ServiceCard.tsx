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
        <span className="hub-card-title">Technical details</span>
        <span className="hub-dim">mode {progress.mode}{run ? ` · run ${run.status}` : ""}</span>
      </summary>
      <div className="hub-service-body">
        <dl className="hub-kv">
          <div><dt>Mode</dt><dd className="num">{progress.mode}</dd></div>
          <div><dt>Status</dt><dd className="num">{bookStatus}{stopRequested ? " · stop_requested" : ""}</dd></div>
          {run ? (
            <>
              <div><dt>Run</dt><dd className="num">{run.status}{run.step ? ` · ${run.step}` : ""} · {run.chapters_done}/{run.chapters_total} chapters</dd></div>
              <div><dt>Tokens</dt><dd className="num">{formatTokens(run.tokens)}{run.calls ? ` · calls ${run.calls}` : ""}</dd></div>
              <div><dt>Started</dt><dd className="num">{formatWhen(run.started_at)}{run.updated_at ? ` · updated ${formatWhen(run.updated_at)}` : ""}</dd></div>
              {run.error ? <div><dt>Error</dt><dd className="hub-danger hub-wrap">{run.error}</dd></div> : null}
            </>
          ) : (
            <div><dt>Run</dt><dd className="hub-dim">annotation has not been run yet</dd></div>
          )}
        </dl>
        <div className="hub-card-actions">
          {active ? (
            <Button size="sm" variant="secondary" loading={actions.stop.isPending} onClick={() => actions.stop.mutate()}>Stop run</Button>
          ) : null}
          {legacy ? (
            <>
              <Button size="sm" variant="secondary" loading={actions.repair.isPending} onClick={() => actions.repair.mutate("stop_pipeline")}>
                Stop v1
              </Button>
              <Button size="sm" variant="secondary" loading={actions.repair.isPending} onClick={() => actions.repair.mutate("continue_pipeline")}>
                Resume v1
              </Button>
            </>
          ) : null}
          {!confirming ? (
            <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>Delete book</Button>
          ) : null}
        </div>
        {confirming ? (
          <DeleteBookConfirm title={bookTitle} pending={actions.remove.isPending} onConfirm={() => actions.remove.mutate()} onCancel={() => setConfirming(false)} />
        ) : null}
      </div>
    </details>
  );
}
