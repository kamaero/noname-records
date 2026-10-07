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
  attribution: "Разметка реплик", characters: "Персонажи книги", consilium_reader_1: "Консилиум: первый чтец",
  consilium_reader_2: "Консилиум: второй чтец", consilium_arbiter: "Консилиум: арбитр", sound: "Звуковая разметка",
  ambient_text: "Эмбиент: описание сцены", ambient_audio: "Эмбиент: звук", asr: "Распознавание речи", other: "Прочее",
};
const PROVIDERS: Record<string, string> = {
  deepseek: "DeepSeek", claude: "Claude", openai: "OpenAI", routerai: "RouterAI", openrouter: "OpenRouter",
  elevenlabs: "ElevenLabs", azure: "Azure", zai: "Z.ai", other: "Другой",
};

function rub(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value).toLocaleString("ru-RU")} ₽`;
}

function monthTitle(key: string): string {
  const [year, month] = key.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleString("ru-RU", { month: "long", year: "numeric" });
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
            <span className="sp-muted">{item.calls} {item.calls === 1 ? "вызов" : "вызовов"}{item.unknown_calls ? `, без цены — ${item.unknown_calls}` : ""}</span>
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
      pushToast({ tone: "success", title: "Лимит сохранён" });
    },
    onError: (error) => pushToast({ tone: "error", title: "Лимит не сохранён", detail: describeApiError(error, "Попробуйте ещё раз.") }),
  });

  if (query.isError) return <div className="panel panel-pad">{describeApiError(query.error, "Не удалось загрузить траты.")}</div>;
  const data = query.data;
  if (!data) return <div className="panel panel-pad">Загружаю…</div>;
  const share = data.limit_rub ? Math.min(1, data.total_rub / data.limit_rub) : 0;
  const limitValue = limit ?? String(data.limit_rub || "");
  const parsed = Number(limitValue.replace(/\s/g, "") || "0");
  const limitValid = Number.isInteger(parsed) && parsed >= 0;

  return (
    <>
      <section className="panel st-section">
        <div className="st-section-head">
          <h2>Траты за {monthTitle(data.month)}</h2>
          {data.months.length > 1 ? (
            <select className="ui-input sp-month" value={data.month} onChange={(event) => setMonth(event.target.value)} aria-label="Месяц">
              {data.months.map((key) => <option key={key} value={key}>{monthTitle(key)}</option>)}
            </select>
          ) : null}
        </div>
        <p className="sp-total"><strong>{rub(data.total_rub)}</strong>{data.limit_rub ? ` из ${rub(data.limit_rub)} · осталось ${rub(data.left_rub)}` : " · лимит не задан"}</p>
        {data.limit_rub ? (
          <div className={`sp-bar${share >= 1 ? " is-over" : share >= 0.8 ? " is-near" : ""}`} aria-hidden="true">
            <span style={{ width: `${Math.round(share * 100)}%` }} />
          </div>
        ) : null}
        {data.unknown_calls ? (
          <p className="st-warning">
            Вызовов без известной цены: {data.unknown_calls}. В рубли и в лимит они не входят — впишите цену модели на вкладке «Нейросети».
          </p>
        ) : null}
        <form className="sp-limit" onSubmit={(event) => { event.preventDefault(); if (limitValid) saveLimit.mutate(parsed); }}>
          <Field label="Лимит на месяц, ₽" hint="0 — без лимита. Начатый прогон доделывается, новые сверх лимита не запустятся без подтверждения администратора.">
            {(props) => <input {...props} className="ui-input" inputMode="numeric" value={limitValue} onChange={(event) => setLimit(event.target.value)} />}
          </Field>
          <Button type="submit" variant="primary" size="sm" loading={saveLimit.isPending} disabled={!limitValid || limit === null}>Сохранить</Button>
        </form>
      </section>

      <section className="panel st-section">
        <Buckets title="По шагам" items={data.by_step} names={STEP_TITLES} />
        <Buckets title="По провайдерам" items={data.by_provider} names={PROVIDERS} />
        {!Object.keys(data.by_step).length ? <p className="sp-muted">В этом месяце платных вызовов не было.</p> : null}
      </section>

      {data.recent.length ? (
        <section className="panel st-section">
          <h2>Последние вызовы</h2>
          <ul className="sp-recent">
            {data.recent.map((row, index) => (
              <li key={`${row.created_at}-${index}`}>
                <span className="sp-muted">{new Date(row.created_at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
                <span>{STEP_TITLES[row.step] || row.step}</span>
                <span className="sp-muted sp-model">{PROVIDERS[row.provider] || row.provider} · {row.model}</span>
                <span className="sp-muted">{row.unit === "seconds" ? `${Math.round(row.input_units / 60 * 10) / 10} мин` : `${row.input_units.toLocaleString("ru-RU")} / ${row.output_units.toLocaleString("ru-RU")} ток.`}</span>
                <strong>{row.rub === null ? "цена неизвестна" : rub(row.rub)}</strong>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
