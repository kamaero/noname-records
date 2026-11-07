import { useEffect, useState } from "react";
import { Button, Field } from "../../ui";
import type { BookBudgetResponse } from "../../types";
import { formatRub } from "../../viewModels/booksCast";

type CastBudgetLineProps = {
  budget: BookBudgetResponse | undefined;
  /** already calculated and formatted by the cast page; do not recount roles here */
  summaryText: string;
  saving: boolean;
  onSave: (next: { sound_engineer_cost_rub: number; extra_cost_rub: number }) => Promise<unknown> | void;
  /** the studio's default rate and how to change it; every book reads the same number */
  rate: number;
  /** rubles per dollar — model prices on the book hub are converted with it */
  usdRate: number;
  savingRate: boolean;
  onSaveRate: (next: { rate: number; usdRate: number }) => Promise<unknown> | void;
};

function toNumber(value: string): number {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.round(n)) : 0;
}

/** Состав и главные деньги одной строкой; подробные статьи остаются в «Изменить». */
export function CastBudgetLine({ budget, summaryText, saving, onSave, rate, usdRate, savingRate, onSaveRate }: CastBudgetLineProps) {
  const [editing, setEditing] = useState(false);
  const [rateDraft, setRateDraft] = useState(String(rate));
  const [usdDraft, setUsdDraft] = useState(String(usdRate));
  const [engineer, setEngineer] = useState("0");
  const [extra, setExtra] = useState("0");

  const totals = budget?.totals;
  const engineerNow = budget?.budget.sound_engineer_cost_rub ?? 0;
  const extraNow = budget?.budget.extra_cost_rub ?? 0;

  useEffect(() => {
    if (!editing) {
      setEngineer(String(engineerNow));
      setExtra(String(extraNow));
      setRateDraft(String(rate));
      setUsdDraft(String(usdRate));
    }
  }, [editing, engineerNow, extraNow, rate, usdRate]);

  if (!budget) return <div className="cast-budget cast-budget--skeleton" aria-hidden="true" />;

  const submit = async () => {
    await onSave({ sound_engineer_cost_rub: toNumber(engineer), extra_cost_rub: toNumber(extra) });
    const nextRate = Math.max(1, toNumber(rateDraft));
    const nextUsd = Math.max(0, Number(usdDraft) || 0);
    if (nextRate !== rate || nextUsd !== usdRate) await onSaveRate({ rate: nextRate, usdRate: nextUsd });
    setEditing(false);
  };

  return (
    <div className="cast-budget">
      <p className="cast-budget-line num" aria-label="Cast and budget totals">
        <span className="cast-budget-item cast-budget-summary">{summaryText}</span>
        <span className="cast-budget-item">
          <span className="cast-budget-key">total</span> <strong>{formatRub(totals?.grand_total_rub || 0)} ₽</strong>
        </span>
        <span className="cast-budget-item">
          <span className="cast-budget-key">cast</span> {formatRub(totals?.character_total_rub || 0)}
        </span>
        <span className="cast-budget-item" title="Default studio rate, used for roles without a custom rate">
          <span className="cast-budget-key">studio rate</span> {formatRub(rate)} RUB/min
        </span>
        {!editing ? (
          <Button size="sm" variant="ghost" className="cast-budget-edit-btn" onClick={() => setEditing(true)}>
            Edit
          </Button>
        ) : null}
      </p>
      {editing ? (
        <form
          className="cast-budget-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Field label="Sound engineer, RUB">
            {(props) => (
              <input
                {...props}
                className="ui-input num"
                type="number"
                min={0}
                inputMode="numeric"
                value={engineer}
                autoFocus
                onChange={(event) => setEngineer(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setEditing(false);
                }}
              />
            )}
          </Field>
          <Field label="Studio rate, RUB/min" hint="Default for all books; used for roles without a custom rate">
            {(props) => (
              <input
                {...props}
                className="ui-input num"
                type="number"
                min={1}
                inputMode="numeric"
                value={rateDraft}
                onChange={(event) => setRateDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setEditing(false);
                }}
              />
            )}
          </Field>
          <Field label="USD exchange rate, RUB" hint="Used to calculate costs for models billed in USD">
            {(props) => (
              <input
                {...props}
                className="ui-input num"
                type="number"
                min={0}
                step="0.01"
                inputMode="decimal"
                value={usdDraft}
                onChange={(event) => setUsdDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setEditing(false);
                }}
              />
            )}
          </Field>
          <Field label="Extra expenses, RUB">
            {(props) => (
              <input
                {...props}
                className="ui-input num"
                type="number"
                min={0}
                inputMode="numeric"
                value={extra}
                onChange={(event) => setExtra(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setEditing(false);
                }}
              />
            )}
          </Field>
          <div className="cast-budget-form-actions">
            <Button type="submit" variant="primary" loading={saving || savingRate}>
              Save budget items
            </Button>
            <Button type="button" variant="ghost" disabled={saving} onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
