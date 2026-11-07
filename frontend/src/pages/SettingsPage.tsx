/**
 * «Настройки → Нейросети»: ключи провайдеров, проверка, балансы и модель каждого шага.
 *
 * Студия без технического человека не должна править .env, чтобы вставить ключ. Ключ сюда
 * не приходит никогда — только откуда он взят и последние четыре знака: страница может
 * оказаться открытой на чужом экране. Модель шага меняется со следующего прогона — прогон
 * фиксирует её при старте, и страница говорит об этом прямо.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPostJson, apiPutJson, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { roleAccess } from "../layout/AppShell";
import { Button, Field, PageHeader } from "../ui";
import type { AiBalance, AiProvider, AiSettings, AiStep, FirstSteps, MeResponse } from "../types";
import { useSearchParams } from "react-router-dom";
import { SpendBanner } from "../components/SpendBanner";
import { StressModelBlock } from "../components/StressModelBlock";
import { SpendTab } from "./SpendTab";
import "./SettingsPage.css";
import "../components/FirstStepsCard.css";

/** Скрытую карточку «Первые шаги» можно вернуть отсюда — скрыли рано, а шаги не сделаны. */
function ShowFirstSteps() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["first-steps"], queryFn: () => apiGet<FirstSteps>("/api/first-steps"), retry: false });
  const show = useMutation({
    mutationFn: () => apiPutJson<FirstSteps>("/api/first-steps", { hidden: false }),
    onSuccess: (data) => client.setQueryData(["first-steps"], data),
  });
  if (!query.data?.hidden || query.data.done_count >= query.data.total) return null;
  return <p className="st-lead"><button type="button" className="fs-link-button" onClick={() => show.mutate()}>Show first steps</button> on the home page.</p>;
}

const PROVIDER_LABELS: Record<string, string> = {
  deepseek: "DeepSeek", claude: "Claude", openai: "OpenAI", routerai: "RouterAI",
  openrouter: "OpenRouter", elevenlabs: "ElevenLabs", azure: "Azure",
};

const STATUS_WORDS: Record<string, string> = {
  ok: "working", bad_key: "invalid key", no_money: "insufficient balance", unreachable: "service unavailable",
  error: "check failed",
};

function checkedAt(iso: string): string {
  const moment = new Date(iso);
  return Number.isNaN(moment.getTime())
    ? ""
    : moment.toLocaleString("en-US", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

function sourceText(item: AiProvider): string {
  if (item.unreadable) return "Saved key cannot be decrypted. Enter it again.";
  if (item.source === "site") return `on this site · …${item.last4}`;
  if (item.source === "env") return "configured in .env";
  return "no key";
}

function KeyRow({ item, onSaved }: { item: AiProvider; onSaved: () => void }) {
  const { pushToast } = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const path = `/api/settings/ai/keys/${item.provider}`;
  const done = (title: string) => (next: AiProvider) => {
    setEditing(false);
    setValue("");
    onSaved();
    const tone = next.check_status === "ok" ? "success" : next.check_status ? "error" : "info";
    pushToast({ tone, title, detail: next.check_detail || undefined });
  };
  const fail = (error: unknown) =>
    pushToast({ tone: "error", title: "Could not complete the action", detail: describeApiError(error, "Please try again.") });
  const save = useMutation({ mutationFn: () => apiPutJson<AiProvider>(path, { key: value }), onSuccess: done("Key saved"), onError: fail });
  const check = useMutation({ mutationFn: () => apiPostJson<AiProvider>(`${path}/check`, {}), onSuccess: done("Key checked"), onError: fail });
  const remove = useMutation({ mutationFn: () => apiDelete<AiProvider>(path), onSuccess: done("Key removed"), onError: fail });
  const busy = save.isPending || check.isPending || remove.isPending;
  const status = item.check_status ? STATUS_WORDS[item.check_status] || item.check_status : "";

  return (
    <li className={`st-key${item.unreadable ? " is-broken" : ""}`}>
      <div className="st-key-head">
        <strong>{item.label}</strong>
        <span className="st-key-source">{sourceText(item)}</span>
        {status ? <span className={`st-status st-status--${item.check_status}`}>{status}</span> : null}
        {item.checked_at ? <span className="st-key-source">checked {checkedAt(item.checked_at)}</span> : null}
      </div>
      {editing ? (
        <form
          className="st-key-edit"
          onSubmit={(event) => {
            event.preventDefault();
            if (value.trim()) save.mutate();
          }}
        >
          <Field label={`Key ${item.label}`} hint="Paste the complete key. It will be encrypted and checked immediately.">
            {(props) => (
              <input {...props} className="ui-input" type="password" autoComplete="off" value={value}
                     disabled={busy} onChange={(event) => setValue(event.target.value)} />
            )}
          </Field>
          <div className="st-actions">
            <Button type="submit" variant="primary" size="sm" loading={save.isPending} disabled={!value.trim() || busy}>Save</Button>
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setEditing(false); setValue(""); }}>Cancel</Button>
          </div>
        </form>
      ) : (
        <div className="st-actions">
          <Button size="sm" disabled={busy} onClick={() => setEditing(true)}>{item.source === "site" ? "Edit" : "Add key"}</Button>
          {item.source !== "none" ? (
            <Button size="sm" variant="ghost" loading={check.isPending} disabled={busy} onClick={() => check.mutate()}>Check</Button>
          ) : null}
          {item.source === "site" || item.unreadable ? (
            <Button size="sm" variant="ghost" disabled={busy}
                    onClick={() => { if (window.confirm(`Remove key ${item.label} from this site?`)) remove.mutate(); }}>
              Remove
            </Button>
          ) : null}
        </div>
      )}
    </li>
  );
}

const PER_MINUTE_STEPS = new Set(["asr", "ambient_audio"]);

function priceText(step: AiStep): string {
  const p = step.price;
  if (p.source === "none") return "price unknown";
  const cur = p.currency === "USD" ? "$" : "₽";
  const fmt = (v: number | null) => (v ?? 0).toLocaleString("en-US");
  const value = p.unit === "seconds" ? `${fmt(p.price_unit)} ${cur} per minute` : `${fmt(p.price_in)} / ${fmt(p.price_out)} ${cur} per 1M tokens`;
  return p.source === "studio" ? `${value} · custom studio price` : value;
}

function PriceEditor({ step, onSaved }: { step: AiStep; onSaved: () => void }) {
  const { pushToast } = useToast();
  const perMinute = PER_MINUTE_STEPS.has(step.step);
  const [open, setOpen] = useState(false);
  const [priceIn, setPriceIn] = useState(String(step.price.price_in ?? ""));
  const [priceOut, setPriceOut] = useState(String(step.price.price_out ?? ""));
  const [priceUnit, setPriceUnit] = useState(String(step.price.price_unit ?? ""));
  const [currency, setCurrency] = useState<"RUB" | "USD">(step.price.currency === "USD" ? "USD" : "RUB");
  const num = (v: string) => Number(v.replace(",", "."));
  const valid = perMinute ? num(priceUnit) >= 0 && priceUnit !== "" : priceIn !== "" && priceOut !== "" && num(priceIn) >= 0 && num(priceOut) >= 0;
  const after = (title: string) => () => { setOpen(false); onSaved(); pushToast({ tone: "success", title }); };
  const fail = (error: unknown) => pushToast({ tone: "error", title: "Price not saved", detail: describeApiError(error, "Please try again.") });
  const save = useMutation({
    mutationFn: () => apiPutJson("/api/settings/ai/prices", {
      provider: step.provider, model: step.model, currency, unit: perMinute ? "seconds" : "tokens",
      ...(perMinute ? { price_unit: num(priceUnit) } : { price_in: num(priceIn), price_out: num(priceOut) }),
    }),
    onSuccess: after("Price saved"), onError: fail,
  });
  const reset = useMutation({
    mutationFn: () => apiDelete(`/api/settings/ai/prices?provider=${encodeURIComponent(step.provider)}&model=${encodeURIComponent(step.model)}`),
    onSuccess: after("Price reset"), onError: fail,
  });
  return (
    <div className="st-price">
      <span className={step.price.source === "none" ? "st-warning" : "st-step-source"}>
        {step.price.source === "none" ? "Price unknown. Enter it so this step's spending counts toward the monthly limit." : `Price: ${priceText(step)}`}
      </span>
      {open ? (
        <form className="st-price-edit" onSubmit={(event) => { event.preventDefault(); if (valid) save.mutate(); }}>
          {perMinute ? (
            <Field label="Per minute">{(props) => <input {...props} className="ui-input" inputMode="decimal" value={priceUnit} onChange={(e) => setPriceUnit(e.target.value)} />}</Field>
          ) : (
            <>
              <Field label="Input, per 1M tokens">{(props) => <input {...props} className="ui-input" inputMode="decimal" value={priceIn} onChange={(e) => setPriceIn(e.target.value)} />}</Field>
              <Field label="Output, per 1M tokens">{(props) => <input {...props} className="ui-input" inputMode="decimal" value={priceOut} onChange={(e) => setPriceOut(e.target.value)} />}</Field>
            </>
          )}
          <Field label="Currency">
            {(props) => (
              <select {...props} className="ui-input" value={currency} onChange={(e) => setCurrency(e.target.value as "RUB" | "USD")}>
                <option value="RUB">₽</option>
                <option value="USD">$</option>
              </select>
            )}
          </Field>
          <div className="st-actions">
            <Button type="submit" variant="primary" size="sm" loading={save.isPending} disabled={!valid}>Save price</Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </form>
      ) : (
        <div className="st-actions">
          <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>{step.price.source === "none" ? "Enter price" : "Edit price"}</Button>
          {step.price.source === "studio" ? (
            <Button size="sm" variant="ghost" loading={reset.isPending} onClick={() => reset.mutate()}>Use built-in price</Button>
          ) : null}
        </div>
      )}
    </div>
  );
}

function StepRow({ step, keyed, onSaved }: { step: AiStep; keyed: Set<string>; onSaved: () => void }) {
  const { pushToast } = useToast();
  const [editing, setEditing] = useState(false);
  const [provider, setProvider] = useState(step.provider);
  const [model, setModel] = useState(step.model);
  const path = `/api/settings/ai/steps/${step.step}`;
  const after = (title: string) => () => { setEditing(false); onSaved(); pushToast({ tone: "success", title }); };
  const fail = (error: unknown) =>
    pushToast({ tone: "error", title: "Could not complete the action", detail: describeApiError(error, "Please try again.") });
  const save = useMutation({ mutationFn: () => apiPutJson<AiStep>(path, { provider, model: model.trim() }), onSuccess: after("Model selected"), onError: fail });
  const reset = useMutation({ mutationFn: () => apiPutJson<AiStep>(path, { reset: true }), onSuccess: after("Default model restored"), onError: fail });
  const missing = !keyed.has(step.provider);

  return (
    <li className="st-step">
      <div className="st-step-head">
        <strong>{step.title}</strong>
        <span className="st-step-model">{PROVIDER_LABELS[step.provider] || step.provider} · <code>{step.model}</code></span>
        <span className="st-step-source">{step.source === "site" ? "selected on this site" : "default"}</span>
      </div>
      {/* ключ по модели и цене: черновик полей не переносит цену прежней модели на новую */}
      <PriceEditor key={`${step.provider}:${step.model}:${JSON.stringify(step.price)}`} step={step} onSaved={onSaved} />
      {missing ? <p className="st-warning">Missing key: {PROVIDER_LABELS[step.provider] || step.provider} — this step cannot run.</p> : null}
      {editing ? (
        <form className="st-step-edit" onSubmit={(event) => { event.preventDefault(); if (model.trim()) save.mutate(); }}>
          {step.options.length > 1 || step.options[0]?.price ? (
            <ul className="st-options" aria-label="Tested models">
              {step.options.map((option) => {
                const chosen = option.provider === provider && option.model === model;
                return (
                  <li key={`${option.provider}/${option.model}`}>
                    <button type="button" className={`st-option${chosen ? " is-chosen" : ""}`} aria-pressed={chosen}
                            onClick={() => { setProvider(option.provider); setModel(option.model); }}>
                      <strong>{option.label}</strong>
                      <span>{PROVIDER_LABELS[option.provider] || option.provider}{option.price ? ` · ${option.price}` : ""}</span>
                      {option.trains_on_text ? <span className="st-warning">The provider may train on your text.</span> : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          <Field label="Provider">
            {(props) => (
              <select {...props} className="ui-input" value={provider} onChange={(event) => setProvider(event.target.value)}>
                {step.providers.map((name) => <option key={name} value={name}>{PROVIDER_LABELS[name] || name}</option>)}
              </select>
            )}
          </Field>
          <Field label="Model" hint="Use the provider's model name, for example anthropic/claude-opus-5.">
            {(props) => <input {...props} className="ui-input" value={model} onChange={(event) => setModel(event.target.value)} />}
          </Field>
          <div className="st-actions">
            <Button type="submit" variant="primary" size="sm" loading={save.isPending} disabled={!model.trim()}>Save</Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </form>
      ) : (
        <div className="st-actions">
          <Button size="sm" onClick={() => { setProvider(step.provider); setModel(step.model); setEditing(true); }}>Edit</Button>
          {step.source === "site" ? (
            <Button size="sm" variant="ghost" loading={reset.isPending} onClick={() => reset.mutate()}>
              Restore default ({step.default.model})
            </Button>
          ) : null}
        </div>
      )}
    </li>
  );
}

export function SettingsPage({ me }: { me: MeResponse }) {
  const { isAdmin } = roleAccess(me);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "spend" ? "spend" : "ai";
  const client = useQueryClient();
  const { pushToast } = useToast();
  const query = useQuery({
    queryKey: ["settings-ai"],
    queryFn: () => apiGet<AiSettings>("/api/settings/ai"),
    enabled: isAdmin,
  });
  const [balances, setBalances] = useState<AiBalance[] | null>(null);
  const refreshBalances = useMutation({
    mutationFn: () => apiPostJson<{ balances: AiBalance[] }>("/api/settings/ai/balances", {}),
    onSuccess: (data) => setBalances(data.balances),
    onError: (error) =>
      pushToast({ tone: "error", title: "Balances not updated", detail: describeApiError(error, "Please try again.") }),
  });
  const reload = () => client.invalidateQueries({ queryKey: ["settings-ai"] });

  if (!isAdmin) return <div className="panel panel-pad">Settings are available to administrators.</div>;
  const tabs = (
    <div className="st-tabs" role="tablist">
      {([["ai", "AI services"], ["spend", "Spending"]] as const).map(([key, label]) => (
        <button key={key} type="button" role="tab" aria-selected={tab === key} className={`st-tab${tab === key ? " is-active" : ""}`}
                onClick={() => setParams(key === "ai" ? {} : { tab: key })}>{label}</button>
      ))}
    </div>
  );
  if (tab === "spend") {
    return (
      <div className="st-page">
        <PageHeader title="Settings" subtitle="AI spending and monthly limit" />
        <SpendBanner enabled />
        {tabs}
        <SpendTab />
      </div>
    );
  }
  if (query.isError) return <div className="panel panel-pad">{describeApiError(query.error, "Could not load settings.")}</div>;
  const data = query.data;
  const keyed = new Set(data?.keyed || []);

  return (
    <div className="st-page">
      <PageHeader title="Settings" subtitle="AI services: keys, balances and task models" />
      <SpendBanner enabled />
      {tabs}
      <ShowFirstSteps />
      {!data ? <div className="panel panel-pad">Loading…</div> : (
        <>
          <section className="panel st-section">
            <h2>Keys</h2>
            <p className="st-lead">
              Keys entered here override .env keys. No restart is needed: the site uses them immediately and background runs pick them up within 30 seconds.
            </p>
            <ul className="st-list">
              {data.providers.map((item) => <KeyRow key={item.provider} item={item} onSaved={reload} />)}
            </ul>
          </section>

          <section className="panel st-section">
            <div className="st-section-head">
              <h2>Balances</h2>
              <Button size="sm" loading={refreshBalances.isPending} onClick={() => refreshBalances.mutate()}>Refresh</Button>
            </div>
            {balances ? (
              <ul className="st-balances">
                {balances.map((item) => (
                  <li key={item.provider}>
                    <span>{PROVIDER_LABELS[item.provider] || item.provider}</span>
                    {item.available && item.amount !== null ? (
                      <strong>{item.amount.toLocaleString("en-US")} {item.unit}</strong>
                    ) : (
                      <span className="st-muted">{item.detail || "no data"}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : <p className="st-muted">Click Refresh to query provider balances.</p>}
          </section>

          <section className="panel st-section">
            <h2>Models</h2>
            <p className="st-lead">
              New model selections apply to runs started after 30 seconds. Changing an AI reviewer does not reread previously reviewed chapters.
            </p>
            <ul className="st-list">
              {data.steps.map((step) => <StepRow key={step.step} step={step} keyed={keyed} onSaved={reload} />)}
            </ul>
          </section>

          <StressModelBlock />
        </>
      )}
    </div>
  );
}
