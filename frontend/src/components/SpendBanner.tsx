/** Полоса о лимите трат — администратору, когда месяц перешёл 80 % лимита. */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiGet } from "../api/client";
import type { SpendMonth } from "../types";

export function SpendBanner({ enabled }: { enabled: boolean }) {
  const query = useQuery({
    queryKey: ["spend", "current"],
    queryFn: () => apiGet<SpendMonth>("/api/settings/spend"),
    enabled,
    retry: false,
    staleTime: 60_000,
  });
  const data = query.data;
  if (!enabled || !data || !data.limit_rub) return null;
  const share = data.total_rub / data.limit_rub;
  if (share < 0.8) return null;
  const over = share >= 1;
  return (
    <div className={`spend-banner${over ? " is-over" : ""}`} role="status">
      {over
        ? "Месячный лимит трат на нейросети исчерпан: новые платные прогоны не запустятся без «сверх лимита». "
        : `Потрачено ${Math.round(share * 100)} % месячного лимита на нейросети. `}
      <Link to="/settings?tab=spend">Траты</Link>
    </div>
  );
}
