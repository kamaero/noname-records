/**
 * «Консилиум ИИ» в шаге «Проверка»: смета перед запуском, ход прогона, итог.
 * Только для того, кто правит разметку — хаб не рисует блок остальным.
 * Прогон стоит денег, поэтому запуск — всегда через окно со сметой обоих режимов.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, LinkButton } from "../../../ui";
import { useToast } from "../../ToastProvider";
import {
  describeConsiliumError,
  editorKeys,
  startConsilium,
  stopConsilium,
  useConsilium,
  useConsiliumEstimate,
} from "../../../v2/editorApi";
import { isConsiliumRunActive, runOutcomeLine, runProgressLine } from "../../../v2/consilium";
import type { ConsiliumBlock, ConsiliumMode } from "../../../v2/types";

const BLOCK_TEXT: Record<Exclude<ConsiliumBlock, "">, string> = {
  no_credits: "insufficient RouterAI balance",
  book_busy: "book annotation is running",
  already_running: "a run is already in progress",
};

const rub = (value: number) => Math.round(value).toLocaleString("en-US");

export function ConsiliumHubBlock({ bookId, consiliumTo }: { bookId: string; consiliumTo: string }) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [confirming, setConfirming] = useState(false);
  const findings = useConsilium(bookId, true);
  const estimate = useConsiliumEstimate(bookId, confirming);
  const run = findings.data?.run ?? null;
  const active = isConsiliumRunActive(run);

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: editorKeys.consilium(bookId) }),
      queryClient.invalidateQueries({ queryKey: editorKeys.consiliumEstimate(bookId) }),
    ]);

  const start = useMutation({
    mutationFn: (mode: ConsiliumMode) => startConsilium(bookId, mode),
    onSuccess: async () => {
      setConfirming(false);
      pushToast({ tone: "success", title: "AI review started", detail: "Track progress here. You will receive a Telegram message when it finishes." });
      await refresh();
    },
    onError: async (error) => {
      pushToast({ tone: "error", title: "AI review not started", detail: describeConsiliumError(error) });
      await refresh();
    },
  });

  const stop = useMutation({
    mutationFn: () => stopConsilium(bookId),
    onSuccess: async () => {
      pushToast({ tone: "success", title: "Stop requested", detail: "The current section will finish and be saved." });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not stop", detail: describeConsiliumError(error) }),
  });

  if (active && run) {
    const fraction = run.phase === "arbiter" || run.phase === "saving"
      ? run.arbiter_total ? run.arbiter_done / run.arbiter_total : 0
      : run.chapters_total ? run.chapters_done / run.chapters_total : 0;
    return (
      <div className="hub-consilium" aria-live="polite">
        <div className="hub-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(fraction * 100)}>
          <span style={{ width: `${Math.round(fraction * 100)}%` }} />
        </div>
        <span className="hub-consilium-line">{runProgressLine(run)}</span>
        <Button size="sm" variant="ghost" loading={stop.isPending} disabled={run.stop_requested} onClick={() => stop.mutate()}>
          {run.stop_requested ? "stopping…" : "stop"}
        </Button>
      </div>
    );
  }

  const outcome = run ? runOutcomeLine(run, findings.data?.counts ?? {}) : "";
  const failed = run && (run.status === "stopped" || run.status === "failed");
  const plan = estimate.data;

  return (
    <div className="hub-consilium">
      {!confirming ? (
        <div className="hub-consilium-row">
          <Button size="sm" variant="secondary" onClick={() => setConfirming(true)}>
            {failed ? "Resume AI review" : "AI review panel"}
          </Button>
          {outcome ? (
            run?.status === "done" ? (
              <LinkButton to={consiliumTo} size="sm" variant="ghost">{outcome} →</LinkButton>
            ) : (
              <span className="hub-consilium-line hub-consilium-line--warn">{outcome}</span>
            )
          ) : null}
        </div>
      ) : (
        <div className="hub-confirm" role="group" aria-label="Start AI review">
          {estimate.isLoading ? <p>Calculating estimate…</p> : null}
          {estimate.isError ? <p>{describeConsiliumError(estimate.error)}</p> : null}
          {plan ? (
            <>
              <p>
                Reviewers read independently. Unchanged chapters keep their existing responses; rechecking only charges for arbitration. Total chapters: {plan.chapters_total}, to reread: {plan.chapters_to_read.recheck}.
                {plan.credits !== null ? ` RouterAI balance: ${rub(plan.credits)} ₽.` : " RouterAI balance unavailable."}
              </p>
              <div className="hub-confirm-actions">
                {(["recheck", "reread"] as ConsiliumMode[]).map((mode) => {
                  const blocked = plan.blocked[mode];
                  return (
                    <Button
                      key={mode}
                      size="sm"
                      variant={mode === "recheck" ? "primary" : "secondary"}
                      disabled={Boolean(blocked) || start.isPending}
                      loading={start.isPending && start.variables === mode}
                      title={blocked ? BLOCK_TEXT[blocked] : undefined}
                      onClick={() => start.mutate(mode)}
                    >
                      {mode === "recheck" ? "Recheck" : "Reread all"} — ≈ {rub(plan.estimate_rub[mode])} ₽
                      {blocked ? ` (${BLOCK_TEXT[blocked]})` : ""}
                    </Button>
                  );
                })}
                <Button size="sm" variant="ghost" disabled={start.isPending} onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
