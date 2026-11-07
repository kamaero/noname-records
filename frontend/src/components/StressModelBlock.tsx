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
    onError: (error) => pushToast({ tone: "error", title: "Could not start", detail: describeApiError(error, "Please try again.") }),
  });
  const data = query.data;
  if (!data || data.state === "auto") return null;
  return (
    <section className="panel st-section">
      <h2>Word stress model</h2>
      <p className="st-lead">
        Resolves Russian word stress from context. English books do not require this model. About 700 MB, downloaded once. Without it, stress marks come from dictionaries.
      </p>
      {data.state === "ready" ? <p>Downloaded and ready.</p> : null}
      {data.state === "downloading" ? <p>Downloading… {data.downloaded_mb} of about 700 MB.</p> : null}
      {data.state === "failed" ? <p className="st-warning">Download failed: {data.error}</p> : null}
      {data.state === "absent" || data.state === "failed" ? (
        <div>
          <Button variant="primary" size="sm" loading={start.isPending} onClick={() => start.mutate()}>
            Download model
          </Button>
        </div>
      ) : null}
    </section>
  );
}
