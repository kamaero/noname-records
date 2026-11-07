/**
 * Which model marks this book up — and what that choice costs and consents to.
 *
 * The two are not interchangeable in the way a settings dropdown usually implies:
 * the cheap tier is cheap because the provider may train on the text it is sent,
 * which is the author's call, not the studio's. So the row that carries that
 * consequence says so on the card, and the change is confirmed rather than applied
 * on selection.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPostJson, describeApiError } from "../../../api/client";
import { Button } from "../../../ui";
import { useToast } from "../../ToastProvider";
import type { HubModel, HubModelChoice } from "../../../viewModels/bookHub";
import { hubKeys } from "./hubApi";

type ModelCardProps = {
  bookId: string;
  model: HubModel;
  /** a run is going: the choice applies to the next one, so it waits */
  locked: boolean;
};

/** Prices are single-digit rubles with kopecks; the cast table's integer format loses them. */
const rub = (value: number): string => value.toLocaleString("en-US", { maximumFractionDigits: 2 });

function priceLine(choice: HubModelChoice): string {
  if (choice.rub_in === null || choice.rub_out === null) return "USD exchange rate is unset; RUB cost is unknown";
  const base = `${rub(choice.rub_in)} RUB per 1M input · ${rub(choice.rub_out)} RUB per 1M output`;
  const dollars = choice.currency === "USD" ? ` (${choice.price_in} / ${choice.price_out} $)` : "";
  // A base price that has a peak above it is the cheap half of the day, not the price.
  const when = choice.peak_rub_in !== null ? " — off-peak" : "";
  return base + dollars + when;
}

export function ModelCard({ bookId, model, locked }: ModelCardProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [pending, setPending] = useState<string | null>(null);

  const choose = useMutation({
    mutationFn: (key: string) =>
      apiPostJson<{ ok: boolean; error?: string }>(`/api/v2/books/${encodeURIComponent(bookId)}/model`, { model_key: key }),
    onSuccess: async (result, key) => {
      setPending(null);
      if (!result.ok) {
        pushToast({ tone: "error", title: "Model not saved", detail: result.error || "The server returned no details." });
        return;
      }
      const chosen = model.choices.find((item) => item.key === key);
      pushToast({ tone: "success", title: `Annotation using «${chosen?.label || key}»`, detail: "Applies to the next run." });
      await queryClient.invalidateQueries({ queryKey: hubKeys.progress(bookId) });
    },
    onError: (error) => {
      setPending(null);
      pushToast({ tone: "error", title: "Model not saved", detail: describeApiError(error, "Could not save selection.") });
    },
  });

  return (
    <section className="panel hub-card" aria-labelledby="hub-model-title">
      <header className="hub-card-head">
        <h2 id="hub-model-title" className="hub-card-title">Annotation model</h2>
        {model.last_run_cost_rub !== null ? (
          <span className="hub-dim" title={`Studio exchange rate: ${rub(model.usd_rub_rate)} ₽/$`}>
            last run ≈ {rub(model.last_run_cost_rub)} ₽{model.last_run_peak ? " (at daytime rates)" : ""}
          </span>
        ) : null}
      </header>

      <ul className="hub-models">
        {model.choices.map((choice) => {
          const current = choice.key === model.key;
          const confirming = pending === choice.key;
          return (
            <li key={choice.key} className={current ? "hub-model is-on" : "hub-model"}>
              <div className="hub-model-head">
                <strong>{choice.label}</strong>
                {current ? <span className="hub-model-badge">selected</span> : null}
                {choice.trains_on_text ? (
                  <span className="hub-model-badge hub-model-badge--warn" title="The provider may use submitted text for training">
                    may train on text
                  </span>
                ) : null}
              </div>
              <div className="hub-model-price num">{priceLine(choice)}</div>
              {choice.peak_rub_in !== null && choice.peak_rub_out !== null ? (
                <div className="hub-model-price num">
                  {choice.peak_hours} — twice the price: {rub(choice.peak_rub_in)} / {rub(choice.peak_rub_out)} ₽
                </div>
              ) : null}
              {choice.accuracy ? <div className="hub-model-accuracy">{choice.accuracy}</div> : null}
              <p className="hub-model-note">{choice.note}</p>
              {current ? null : confirming ? (
                <div className="hub-model-confirm">
                  <span>
                    {choice.trains_on_text
                      ? "The provider may use the submitted book text for training. Do you have the author's permission?"
                      : "Switch annotation to this model?"}
                  </span>
                  <Button size="sm" variant="primary" loading={choose.isPending} onClick={() => choose.mutate(choice.key)}>
                    Yes, switch
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setPending(null)}>Cancel</Button>
                </div>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={locked}
                  title={locked ? "Wait for the current run to finish" : undefined}
                  onClick={() => setPending(choice.key)}
                >
                  Select
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
