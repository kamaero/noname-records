/**
 * «Первые шаги»: от установки до первой размеченной книги.
 *
 * Отметки ставит сервер по настоящим данным — карточка только показывает и ведёт к нужному
 * экрану. Не-администратору сервер отвечает 403, и карточки просто нет: ошибку тут
 * показывать некому и незачем. Обновляется при возврате на вкладку — добавили ключ в
 * «Настройках», вернулись, шаг уже отмечен.
 */
import "./FirstStepsCard.css";

import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPostJson, apiPutJson, describeApiError } from "../api/client";
import { useToast } from "./ToastProvider";
import { Button } from "../ui";
import type { FirstSteps, FirstStep } from "../types";

const MARKS: Record<FirstStep["state"], string> = { done: "✓", todo: "", running: "…", failed: "!", locked: "" };

function rub(value: number): string {
  return `${Math.max(1, Math.round(value)).toLocaleString("ru-RU")} ₽`;
}

export function FirstStepsCard() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const query = useQuery({
    queryKey: ["first-steps"],
    queryFn: () => apiGet<FirstSteps>("/api/first-steps"),
    refetchOnWindowFocus: true,
    retry: false,
  });
  const refresh = (data: FirstSteps) => client.setQueryData(["first-steps"], data);
  const flags = useMutation({
    mutationFn: (body: { hidden?: boolean; no_limit?: boolean }) => apiPutJson<FirstSteps>("/api/first-steps", body),
    onSuccess: refresh,
    onError: (error) => pushToast({ tone: "error", title: "Не сохранилось", detail: describeApiError(error, "Попробуйте ещё раз.") }),
  });
  const sample = useMutation({
    mutationFn: () => apiPostJson<{ book_id: string }>("/api/first-steps/sample", {}),
    onSuccess: ({ book_id }) => {
      client.invalidateQueries({ queryKey: ["first-steps"] });
      client.invalidateQueries({ queryKey: ["books"] });
      navigate(`/books/${encodeURIComponent(book_id)}`);
    },
    onError: (error) => pushToast({ tone: "error", title: "Пример не добавлен", detail: describeApiError(error, "Попробуйте ещё раз.") }),
  });

  const data = query.data;
  if (!data || !data.visible) return null;
  const book = data.sample_book_id ? `/books/${encodeURIComponent(data.sample_book_id)}` : "";
  const reader = `/reader?book_id=${encodeURIComponent(data.sample_book_id)}`;

  function action(step: FirstStep) {
    if (step.done && step.key !== "result") return null;
    switch (step.key) {
      case "key":
      case "models":
        return <Link className="fs-link" to="/settings">Настройки → Нейросети</Link>;
      case "limit":
        return (
          <span className="fs-actions">
            <Link className="fs-link" to="/settings?tab=spend">Задать лимит</Link>
            <Button size="sm" variant="ghost" loading={flags.isPending} onClick={() => flags.mutate({ no_limit: true })}>Работаю без лимита</Button>
          </span>
        );
      case "sample":
        return <Button size="sm" variant="primary" loading={sample.isPending} onClick={() => sample.mutate()}>Добавить пример</Button>;
      case "run":
        return book && step.state !== "locked" ? <Link className="fs-link" to={book}>{step.state === "failed" ? "Открыть журнал" : "Открыть книгу"}</Link> : null;
      case "result":
        return book && step.state !== "locked" ? (
          <span className="fs-actions">
            <Link className="fs-link" to={`${book}/cast`}>Каст</Link>
            {/* сразу в первую размеченную главу примера: из общего списка книг его ещё надо найти */}
            <Link className="fs-link" to={reader}>Читалка</Link>
          </span>
        ) : null;
    }
  }

  return (
    <section className="panel fs-card" aria-label="Первые шаги">
      <header className="fs-head">
        <h2>Первые шаги</h2>
        <span className="fs-count">{data.done_count} из {data.total}</span>
      </header>
      <ol className="fs-steps">
        {data.steps.map((step) => (
          <li key={step.key} className={`fs-step is-${step.state}`}>
            <span className="fs-mark" aria-hidden="true">{MARKS[step.state]}</span>
            <div className="fs-body">
              <strong>{step.title}</strong>
              <span className="fs-detail">
                {step.detail}
                {step.key === "run" && step.state === "todo" && data.sample_estimate_rub !== null
                  ? (data.sample_estimate_unknown ? " Цена модели неизвестна — впишите её в «Настройках»." : ` По смете — около ${rub(data.sample_estimate_rub)}.`)
                  : ""}
              </span>
            </div>
            <div className="fs-action">{action(step)}</div>
          </li>
        ))}
      </ol>
      <footer className="fs-foot">
        <span className="fs-detail">Дальше: <Link to="/dictors">пригласите дикторов</Link> · подключите Telegram-бота (на сервере, см. README)</span>
        <Button size="sm" variant="ghost" onClick={() => flags.mutate({ hidden: true })}>Скрыть</Button>
      </footer>
    </section>
  );
}
