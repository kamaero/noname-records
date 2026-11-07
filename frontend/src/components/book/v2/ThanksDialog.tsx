import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiPostJson, describeApiError } from "../../../api/client";
import { Button, Dialog } from "../../../ui";

type ThanksPerson = { actor: string; chat: string; roles: string[]; name: string; sent_at: string; delivered: boolean };
type ThanksResponse = {
  ok: boolean;
  text: string;
  preview: string;
  people: ThanksPerson[];
  no_chat: { actor: string; roles: string[] }[];
};
type ThanksResult = { sent: string[]; failed: string[]; already: string[] };

/** «А, Б и В» — как в письме, которое соберёт сервер. */
function joinRoles(roles: string[]): string {
  if (roles.length <= 1) return roles.join("");
  return `${roles.slice(0, -1).join(", ")} and ${roles[roles.length - 1]}`;
}

/** Актёры записаны «Фамилия Имя»: в письме обращаются по имени. */
function firstName(actor: string): string {
  const parts = actor.trim().split(/\s+/);
  return parts.length > 1 ? parts[1] : parts[0] || "";
}

/** Предпросмотр считается здесь, а не берётся с сервера: текст правят на лету,
 *  а запрос на каждую букву не нужен. */
function fill(text: string, person: ThanksPerson | undefined, book: string): string {
  return text
    .split("{name}").join(person ? person.name || firstName(person.actor) : "")
    .split("{roles}").join(person ? joinRoles(person.roles) : "")
    .split("{book}").join(book);
}

type Props = { bookId: string; bookTitle: string; onClose: () => void };

export function ThanksDialog({ bookId, bookTitle, onClose }: Props) {
  const path = `/api/v2/books/${encodeURIComponent(bookId)}/thanks`;
  const query = useQuery({ queryKey: ["v2", "book-thanks", bookId], queryFn: () => apiGet<ThanksResponse>(path), retry: false, gcTime: 0 });
  // null — человек ещё не правил текст, показываем серверный
  const [edited, setEdited] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ThanksResult | null>(null);

  const data = query.data;
  const text = edited ?? data?.text ?? "";
  const willGet = (data?.people ?? []).filter((p) => !p.sent_at);
  const already = (data?.people ?? []).filter((p) => p.sent_at && p.delivered);
  // Отметка есть, а доставки нет: отправка упала. Повторно не шлём (письмо могло дойти) — пишут сами.
  const undelivered = (data?.people ?? []).filter((p) => p.sent_at && !p.delivered);

  const send = async () => {
    if (!willGet.length || !text.trim()) return;
    if (!window.confirm(`Send message to ${willGet.length} recipients? This cannot be undone.`)) return;
    setSending(true);
    setError("");
    try {
      setResult(await apiPostJson<ThanksResult>(path, { text }));
    } catch (err) {
      setError(describeApiError(err, "could not send"));
    } finally {
      setSending(false);
    }
  };

  const footer = result ? (
    <Button variant="primary" onClick={onClose} data-autofocus>Close</Button>
  ) : (
    <>
      <Button variant="ghost" onClick={onClose} disabled={sending}>Cancel</Button>
      <Button variant="primary" loading={sending} disabled={!willGet.length || !text.trim()} onClick={send}>
        {`Send ${willGet.length}`}
      </Button>
    </>
  );

  return (
    <Dialog title="Thank-you message" subtitle={bookTitle} onClose={onClose} footer={footer} className="thanks-dialog">
      {query.isLoading ? <p className="ui-note ui-note--info">Loading…</p> : null}
      {query.isError ? (
        <p className="ui-note ui-note--error" role="alert">
          {describeApiError(query.error, "could not load message")}
        </p>
      ) : null}
      {result ? (
        <div className="thanks-summary" role="status">
          <p>Sent: {result.sent.length}{result.sent.length ? ` — ${result.sent.join(", ")}` : ""}</p>
          <p>Undelivered: {result.failed.length}{result.failed.length ? ` — ${result.failed.join(", ")}` : ""}</p>
          <p>Previously sent: {result.already.length}{result.already.length ? ` — ${result.already.join(", ")}` : ""}</p>
        </div>
      ) : data ? (
        <div className="thanks-body">
          <label className="thanks-label" htmlFor="thanks-text">Message text</label>
          <textarea id="thanks-text" className="thanks-text" rows={8} value={text} onChange={(e) => setEdited(e.target.value)} />
          <p className="thanks-hint">Supported formatting: {"{name}"}, {"{roles}"} and {"{book}"}.</p>
          {willGet[0] ? (
            <>
              <div className="thanks-label">Message preview for {willGet[0].actor}</div>
              <pre className="thanks-preview">{fill(text, willGet[0], bookTitle)}</pre>
            </>
          ) : null}
          <div className="thanks-label">Recipients ({willGet.length})</div>
          {willGet.length ? (
            <ul className="thanks-list">
              {willGet.map((p) => <li key={p.actor}>{p.actor} → {p.name} <span>{joinRoles(p.roles)}</span></li>)}
            </ul>
          ) : <p className="thanks-hint">No recipients.</p>}
          {already.length ? (
            <>
              <div className="thanks-label">Previously sent ({already.length})</div>
              <ul className="thanks-list">
                {already.map((p) => <li key={p.actor}>{p.actor} → {p.name} <span>{joinRoles(p.roles)}</span></li>)}
              </ul>
            </>
          ) : null}
          {undelivered.length ? (
            <>
              <div className="thanks-label">Undelivered; contact them directly ({undelivered.length})</div>
              <ul className="thanks-list">
                {undelivered.map((p) => <li key={p.actor}>{p.actor} → {p.name} <span>{joinRoles(p.roles)}</span></li>)}
              </ul>
            </>
          ) : null}
          {data.no_chat.length ? (
            <>
              <div className="thanks-label">Bot unreachable; contact them directly</div>
              <ul className="thanks-list">
                {data.no_chat.map((p) => <li key={p.actor}>{p.actor} <span>{joinRoles(p.roles)}</span></li>)}
              </ul>
            </>
          ) : null}
          {error ? <p className="ui-note ui-note--error" role="alert">{error}</p> : null}
        </div>
      ) : null}
    </Dialog>
  );
}
