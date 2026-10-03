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
const rub = (value: number): string => value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });

function priceLine(choice: HubModelChoice): string {
  if (choice.rub_in === null || choice.rub_out === null) return "курс доллара не задан — цена в ₽ неизвестна";
  const base = `${rub(choice.rub_in)} ₽ за 1M входящих · ${rub(choice.rub_out)} ₽ за 1M исходящих`;
  const dollars = choice.currency === "USD" ? ` (${choice.price_in} / ${choice.price_out} $)` : "";
  // A base price that has a peak above it is the cheap half of the day, not the price.
  const when = choice.peak_rub_in !== null ? " — вне пиковых часов" : "";
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
        pushToast({ tone: "error", title: "Модель не сохранена", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      const chosen = model.choices.find((item) => item.key === key);
      pushToast({ tone: "success", title: `Разметка на «${chosen?.label || key}»`, detail: "Применится со следующего прогона." });
      await queryClient.invalidateQueries({ queryKey: hubKeys.progress(bookId) });
    },
    onError: (error) => {
      setPending(null);
      pushToast({ tone: "error", title: "Модель не сохранена", detail: describeApiError(error, "Не удалось сохранить выбор.") });
    },
  });

  return (
    <section className="panel hub-card" aria-labelledby="hub-model-title">
      <header className="hub-card-head">
        <h2 id="hub-model-title" className="hub-card-title">Модель разметки</h2>
        {model.last_run_cost_rub !== null ? (
          <span className="hub-dim" title={`Курс студии: ${rub(model.usd_rub_rate)} ₽/$`}>
            последний прогон ≈ {rub(model.last_run_cost_rub)} ₽{model.last_run_peak ? " (по дневному тарифу)" : ""}
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
                {current ? <span className="hub-model-badge">выбрана</span> : null}
                {choice.trains_on_text ? (
                  <span className="hub-model-badge hub-model-badge--warn" title="Провайдер вправе использовать присланный текст для обучения">
                    учится на тексте
                  </span>
                ) : null}
              </div>
              <div className="hub-model-price num">{priceLine(choice)}</div>
              {choice.peak_rub_in !== null && choice.peak_rub_out !== null ? (
                <div className="hub-model-price num">
                  {choice.peak_hours} — вдвое дороже: {rub(choice.peak_rub_in)} / {rub(choice.peak_rub_out)} ₽
                </div>
              ) : null}
              {choice.accuracy ? <div className="hub-model-accuracy">{choice.accuracy}</div> : null}
              <p className="hub-model-note">{choice.note}</p>
              {current ? null : confirming ? (
                <div className="hub-model-confirm">
                  <span>
                    {choice.trains_on_text
                      ? "Текст книги уйдёт провайдеру, который вправе на нём учиться. Разрешение автора получено?"
                      : "Переключить разметку на эту модель?"}
                  </span>
                  <Button size="sm" variant="primary" loading={choose.isPending} onClick={() => choose.mutate(choice.key)}>
                    Да, переключить
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setPending(null)}>Отмена</Button>
                </div>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={locked}
                  title={locked ? "Дождитесь конца текущего прогона" : undefined}
                  onClick={() => setPending(choice.key)}
                >
                  Выбрать
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
