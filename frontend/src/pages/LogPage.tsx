import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../api/client";
import { formatServerDateTime } from "../utils/serverTime";
import type { LogResponse, MeResponse } from "../types";

export function LogPage({ me }: { me: MeResponse }) {
  const [actionFilter, setActionFilter] = useState("");
  const [actorFilter, setActorFilter] = useState("");
  const canOpenLog = me.roles.includes("admin") || me.is_owner_telegram;
  const query = useQuery({
    queryKey: ["log"],
    queryFn: () => apiGet<LogResponse>("/api/log"),
    enabled: canOpenLog,
  });
  const filteredActivity = useMemo(() => {
    const items = query.data?.recent_activity || [];
    return items.filter((item) => {
      const actionOk = !actionFilter.trim() || item.action_label.toLowerCase().includes(actionFilter.trim().toLowerCase());
      const actorOk = !actorFilter.trim() || item.actor_name.toLowerCase().includes(actorFilter.trim().toLowerCase());
      return actionOk && actorOk;
    });
  }, [actionFilter, actorFilter, query.data?.recent_activity]);

  if (!canOpenLog) {
    return <div className="panel">This page is available to the owner and administrators only.</div>;
  }
  if (query.isLoading) {
    return <div className="panel">Loading log…</div>;
  }
  if (query.isError) {
    return <div className="panel error">Could not load log.</div>;
  }
  const data = query.data;
  if (!data) {
    return <div className="panel error">Empty API response.</div>;
  }

  return (
    <section className="grid">
      <div className="panel">
        <h2>LLM usage</h2>
        <ul className="list">
          {data.usage_by_actor.map((item) => (
            <li key={item.created_by_name}>
              <strong>{item.created_by_name}</strong>
              <span>{item.total_tokens.toLocaleString("en-US")} tokens · ${item.cost_usd.toFixed(4)}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="panel">
        <h2>Final stage usage</h2>
        <ul className="list">
          {data.usage_by_stage.map((item) => (
            <li key={item.stage}>
              <strong>{item.stage}</strong>
              <span>
                {item.total_tokens.toLocaleString("en-US")} tokens · ${item.cost_usd.toFixed(4)} ·{" "}
                {Math.round(item.runtime_ms / 1000).toLocaleString("en-US")} s
              </span>
              <small>
                requests: {item.request_count.toLocaleString("en-US")} · books: {item.book_count.toLocaleString("en-US")}
              </small>
            </li>
          ))}
        </ul>
      </div>
      <div className="panel">
        <h2>Recent activity</h2>
        <div className="form-grid">
          <label>
            Filter by action
            <input value={actionFilter} onChange={(event) => setActionFilter(event.target.value)} placeholder="annotation, whitelist, sign-in" />
          </label>
          <label>
            Filter by user
            <input value={actorFilter} onChange={(event) => setActorFilter(event.target.value)} placeholder="name or username" />
          </label>
        </div>
        <ul className="list">
          {filteredActivity.map((item, index) => (
            <li key={`${item.entity_type}-${item.entity_id}-${index}`}>
              <strong>{item.action_label}</strong>
              <span>{item.actor_name} · {formatServerDateTime(item.created_at)}</span>
              <small>{item.entity_type} · {item.entity_id}</small>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
