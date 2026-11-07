/**
 * Вкладка «Траты»: сколько ушло на нейросети за месяц, по шагам и провайдерам, и потолок.
 *
 * Вызовы без известной цены считаются отдельно и не входят в рубли — иначе ноль выглядел бы
 * как бесплатный вызов, а лимит молча его пропускал. Страница это говорит прямо.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPutJson, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { Button, Field } from "../ui";
import type { SpendBucket, SpendMonth } from "../types";

const STEP_TITLES: Record<string, string> = {
  attribution: "Role attribution", characters: "Book characters", consilium_reader_1: "AI review: first reviewer",
  consilium_reader_2: "AI review: second reviewer", consilium_arbiter: "AI review: arbiter", sound: "Sound annotation",
  ambient_text: "Ambience: scene description", ambient_audio: "Ambience: audio", asr: "Speech recognition", other: "Other",
};
const PROVIDERS: Record<string, string> = {
  deepseek: "DeepSeek", claude: "Claude", openai: "OpenAI", routerai: "RouterAI", openrouter: "OpenRouter",
  elevenlabs: "ElevenLabs", azure: "Azure", zai: "Z.ai", other: "Other",
};

function rub(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value).toLocaleString("en-US")} ₽`;
}

function monthTitle(key: string): string {
  const [year, month] = key.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleString("en-US", { month: "long", year: "numeric" });
}

function Buckets({ title, items, names }: { title: string; items: Record<string, SpendBucket>; names: Record<string, string> }) {
  const rows = Object.entries(items).sort((a, b) => b[1].rub - a[1].rub);
  if (!rows.length) return null;
  return (
    <div className="sp-buckets">
      <h3>{title}</h3>
      <ul>
        {rows.map(([key, item]) => (
          <li key={key}>
            <span>{names[key] || key}</span>
            <span className="sp-muted">{item.calls} {item.calls === 1 ? "call" : "calls"}{item.unknown_calls ? `, unknown price — ${item.unknown_calls}` : ""}</span>
            {/* все вызовы без цены — прочерк, а не «0 ₽»: ноль читается как «бесплатно» */}
            <strong>{item.unknown_calls === item.calls ? "—" : rub(item.rub)}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SpendTab() {
  const client = useQueryClient();
  const { pushToast } = useToast();
  const [month, setMonth] = useState("");
  const query = useQuery({
    queryKey: ["spend", month || "current"],
    queryFn: () => apiGet<SpendMonth>(`/api/settings/spend${month ? `?month=${encodeURIComponent(month)}` : ""}`),
  });
  const [limit, setLimit] = useState<string | null>(null);
  const saveLimit = useMutation({
    mutationFn: (value: number) => apiPutJson<{ ok: boolean }>("/api/settings/spend/limit", { limit_rub: value }),
    onSuccess: () => {
      setLimit(null);
      client.invalidateQueries({ queryKey: ["spend"] });
      pushToast({ tone: "success", title: "Limit saved" });
    },
    onError: (error) => pushToast({ tone: "error", title: "Limit not saved", detail: describeApiError(error, "Please try again.") }),
  });

  if (query.isError) return <div className="panel panel-pad">{describeApiError(query.error, "Could not load spending.")}</div>;
  const data = query.data;
  if (!data) return <div className="panel panel-pad">Loading…</div>;
  const share = data.limit_rub ? Math.min(1, data.total_rub / data.limit_rub) : 0;
  const limitValue = limit ?? String(data.limit_rub || "");
  const parsed = Number(limitValue.replace(/\s/g, "") || "0");
  const limitValid = Number.isInteger(parsed) && parsed >= 0;

  return (
    <>
      <section className="panel st-section">
        <div className="st-section-head">
          <h2>Spending for {monthTitle(data.month)}</h2>
          {data.months.length > 1 ? (
            <select className="ui-input sp-month" value={data.month} onChange={(event) => setMonth(event.target.value)} aria-label="Month">
              {data.months.map((key) => <option key={key} value={key}>{monthTitle(key)}</option>)}
            </select>
          ) : null}
        </div>
        <p className="sp-total"><strong>{rub(data.total_rub)}</strong>{data.limit_rub ? ` of ${rub(data.limit_rub)} · remaining ${rub(data.left_rub)}` : " · no limit set"}</p>
        {data.limit_rub ? (
          <div className={`sp-bar${share >= 1 ? " is-over" : share >= 0.8 ? " is-near" : ""}`} aria-hidden="true">
            <span style={{ width: `${Math.round(share * 100)}%` }} />
          </div>
        ) : null}
        {data.unknown_calls ? (
          <p className="st-warning">
            Calls with unknown prices: {data.unknown_calls}. These are excluded from RUB totals and the monthly limit. Enter the model price in AI services.
          </p>
        ) : null}
        <form className="sp-limit" onSubmit={(event) => { event.preventDefault(); if (limitValid) saveLimit.mutate(parsed); }}>
          <Field label="Monthly limit, RUB" hint="0 means unlimited. Existing runs finish; new runs above the limit require administrator confirmation.">
            {(props) => <input {...props} className="ui-input" inputMode="numeric" value={limitValue} onChange={(event) => setLimit(event.target.value)} />}
          </Field>
          <Button type="submit" variant="primary" size="sm" loading={saveLimit.isPending} disabled={!limitValid || limit === null}>Save</Button>
        </form>
      </section>

      <section className="panel st-section">
        <Buckets title="By task" items={data.by_step} names={STEP_TITLES} />
        <Buckets title="By provider" items={data.by_provider} names={PROVIDERS} />
        {!Object.keys(data.by_step).length ? <p className="sp-muted">No paid calls this month.</p> : null}
      </section>

      {data.recent.length ? (
        <section className="panel st-section">
          <h2>Recent calls</h2>
          <ul className="sp-recent">
            {data.recent.map((row, index) => (
              <li key={`${row.created_at}-${index}`}>
                <span className="sp-muted">{new Date(row.created_at).toLocaleString("en-US", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
                <span>
                  {STEP_TITLES[row.step] || row.step}
                  {row.book_id ? <span className="sp-muted sp-book"> · {row.book_title || "book deleted"}</span> : null}
                </span>
                <span className="sp-muted sp-model">{PROVIDERS[row.provider] || row.provider} · {row.model}</span>
                <span className="sp-muted">{row.unit === "seconds" ? `${Math.round(row.input_units / 60 * 10) / 10} min` : `${row.input_units.toLocaleString("en-US")} / ${row.output_units.toLocaleString("en-US")} tokens`}</span>
                <strong>{row.rub === null ? "price unknown" : rub(row.rub)}</strong>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
