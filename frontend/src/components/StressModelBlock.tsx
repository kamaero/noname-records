/**
 * Модель ударений в настольной версии: ~700 МБ, качается только по кнопке. Пока её нет,
 * ударения ставятся по словарям — разметка не ждёт. На VPS (state "auto") блока нет:
 * там модель скачивается сама.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPostJson, describeApiError } from "../api/client";
import { useToast } from "./ToastProvider";
import { Button } from "../ui";

type StressModelState = {
  state: "absent" | "downloading" | "ready" | "failed" | "auto";
  downloaded_mb: number;
  error: string;
};

export function StressModelBlock() {
  const client = useQueryClient();
  const { pushToast } = useToast();
  const query = useQuery({
    queryKey: ["stress-model"],
    queryFn: () => apiGet<StressModelState>("/api/settings/stress-model"),
    refetchInterval: (q) => (q.state.data?.state === "downloading" ? 3000 : false),
  });
  const start = useMutation({
    mutationFn: () => apiPostJson<StressModelState>("/api/settings/stress-model", {}),
    onSuccess: (data) => client.setQueryData(["stress-model"], data),
    onError: (error) => pushToast({ tone: "error", title: "Не началось", detail: describeApiError(error, "Попробуйте ещё раз.") }),
  });
  const data = query.data;
  if (!data || data.state === "auto") return null;
  return (
    <section className="panel st-section">
      <h2>Модель ударений</h2>
      <p className="st-lead">
        Расставляет ударения по смыслу фразы (замо́к — за́мок). Около 700 МБ, скачивается один раз.
        Без неё ударения берутся из словарей.
      </p>
      {data.state === "ready" ? <p>Скачана, работает.</p> : null}
      {data.state === "downloading" ? <p>Скачиваю… {data.downloaded_mb} из ~700 МБ.</p> : null}
      {data.state === "failed" ? <p className="st-warning">Не скачалась: {data.error}</p> : null}
      {data.state === "absent" || data.state === "failed" ? (
        <div>
          <Button variant="primary" size="sm" loading={start.isPending} onClick={() => start.mutate()}>
            Скачать модель
          </Button>
        </div>
      ) : null}
    </section>
  );
}
