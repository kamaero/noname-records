import { roleLabel as displayRoleName } from "../v2/types";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiGet, apiPostJson, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { useToast } from "../components/ToastProvider";
import { Button, Dialog, EmptyState, Field, PageHeader } from "../ui";
import type { PayoutPayment, PayoutPerson, PayoutRole, PayoutsResponse } from "../types";
import "./PayoutsPage.css";

/* ============================================================
   Смета — кому сколько заплатить за записанные роли (спека 2026-10-06-payouts).
   Только владельцу: сервер отдаёт остальным 403, пункта в меню у них нет.
   К оплате — полная сумма сданной роли минус выплаченное; аванс без роли зачитывается
   при выплате по роли (сервер считает зачёт сам, экран лишь называет цену).
   ============================================================ */

const REFUSALS: Record<string, string> = {
  nothing_to_pay: "Nothing to pay for this role.",
  bad_amount: "The amount is invalid or exceeds the remaining role balance.",
  no_fee: "The role amount has not been calculated. Open the book budget.",
  not_in_progress: "Advances are available only for roles in progress.",
  role_not_found: "Role not found. Refresh the page.",
  person_not_found: "Voice actor not found. Refresh the page.",
  not_found: "This entry has already been cancelled.",
  advance_offset: "This advance has already been applied to a role. Undo that application first.",
};

const KIND_WORDS: Record<string, string> = {
  advance: "advance",
  final: "payment",
  offset: "advance applied",
  person_advance: "unassigned advance",
};

function rub(value: number): string {
  return `${Math.round(value || 0).toLocaleString("en-US")} ₽`;
}

function day(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("en-US", { day: "numeric", month: "long" });
}

function refusal(error: unknown): string {
  if (error instanceof ApiError && REFUSALS[error.errorCode]) return REFUSALS[error.errorCode];
  return describeApiError(error, "Could not save.");
}

function roleLabel(person: PayoutPerson, role: PayoutRole): string {
  return `${person.name}, «${displayRoleName(role.role_name)}» («${role.book_title}»)`;
}

/** Остаток сданной роли и сколько из него закроет незачтённый аванс — только для текста вопроса. */
function finalParts(person: PayoutPerson, role: PayoutRole): { offset: number; money: number } {
  const remainder = Math.max(0, (role.fee || 0) - role.paid);
  const offset = Math.min(person.credit, remainder);
  return { offset, money: remainder - offset };
}

function RoleState({ role }: { role: PayoutRole }) {
  if (role.state === "past") return <span className="pay-state is-muted">past role (unassigned or book deleted) · paid {rub(role.paid)}</span>;
  if (role.state === "no_chapters") return <span className="pay-state is-muted">no chapters</span>;
  if (role.state === "no_fee") return <span className="pay-state is-muted">amount not calculated; open the book budget</span>;
  if (role.state === "in_progress") {
    return (
      <span className="pay-state is-muted">
        in progress
        {role.paid > 0 ? ` · advance ${rub(role.paid)} of ${rub(role.fee || 0)}` : ""}
      </span>
    );
  }
  if (role.owed > 0) return <span className="pay-state is-due">due {rub(role.owed)}</span>;
  if (role.overpaid > 0) return <span className="pay-state is-warn">overpaid {rub(role.overpaid)}</span>;
  return <span className="pay-state is-ok">paid in full</span>;
}

/** Оплаченная роль — денежных дел по ней не осталось: сдана и закрыта ровно, либо это прошлая
    роль, которая живёт в смете только ради истории выплат. Переплату не прячем — её надо разобрать. */
function isSettled(role: PayoutRole): boolean {
  if (role.state === "past") return true;
  return role.state === "done" && role.owed <= 0 && role.overpaid <= 0;
}

function fold(text: string): string {
  return (text || "").toLowerCase().replace(/ё/g, "е");
}

type AdvanceTarget = { person: PayoutPerson; role: PayoutRole | null };

export function PayoutsPage() {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [advance, setAdvance] = useState<AdvanceTarget | null>(null);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  /* оплаченные роли копятся с каждой книгой и прячут то, что ещё надо заплатить (просьба владельца 13.10);
     переключатель оставлен, чтобы до них можно было дойти и отменить ошибочную выплату */
  const [showPaid, setShowPaid] = useState(false);
  const [search, setSearch] = useState("");

  const query = useQuery({ queryKey: ["payouts"], queryFn: () => apiGet<PayoutsResponse>("/api/payouts") });
  /* кнопки остаются занятыми до свежих данных: второй «Аванс» по устаревшей карточке
     записал бы дубль (ревью) */
  const done = () => queryClient.invalidateQueries({ queryKey: ["payouts"] });
  const onError = (error: unknown) => pushToast({ tone: "error", title: refusal(error) });
  /* тост называет то, что записал сервер: подтверждение считалось по данным на экране */
  const written = (response: unknown) => {
    const parts = ((response as { written?: { kind: string; amount_rub: number }[] }).written || [])
      .map((item) => `${KIND_WORDS[item.kind] || item.kind} ${rub(item.amount_rub)}`);
    return parts.length ? `Recorded: ${parts.join(", ")}.` : "Recorded.";
  };

  const pay = useMutation({
    mutationFn: (body: Record<string, unknown>) => apiPostJson("/api/payouts/pay", body),
    onSuccess: async (response) => { pushToast({ tone: "success", title: written(response) }); await done(); },
    onError,
  });
  const personAdvance = useMutation({
    mutationFn: (body: Record<string, unknown>) => apiPostJson("/api/payouts/person-advance", body),
    onSuccess: async (response) => { pushToast({ tone: "success", title: written(response) }); await done(); },
    onError,
  });
  const cancel = useMutation({
    mutationFn: (paymentId: string) => apiPostJson("/api/payouts/cancel", { payment_id: paymentId }),
    onSuccess: async () => { pushToast({ tone: "success", title: "Entry cancelled." }); await done(); },
    onError,
  });

  if (query.error) {
    const forbidden = query.error instanceof ApiError && query.error.status === 403;
    return (
      <div className="pay">
        <PageHeader title="Budget" />
        <EmptyState text={forbidden ? "This section is available to the studio owner only." : describeApiError(query.error, "Could not load the budget.")} />
      </div>
    );
  }
  const data = query.data;

  const payFinal = (person: PayoutPerson, role: PayoutRole) => {
    const { offset, money } = finalParts(person, role);
    const what = offset > 0
      ? `Apply advance ${rub(offset)}${money > 0 ? ` and pay ${rub(money)}` : ""}`
      : `Record payment ${rub(money)}`;
    if (!window.confirm(`${what} — ${roleLabel(person, role)}?`)) return;
    pay.mutate({ book_id: role.book_id, role_key: role.role_key, actor_name: role.actor_name, kind: "final" });
  };

  const askCancel = (person: PayoutPerson, payment: PayoutPayment, role: PayoutRole | null) => {
    const where = role ? roleLabel(person, role) : person.name;
    if (!window.confirm(`Cancel «${KIND_WORDS[payment.kind] || payment.kind}» ${rub(payment.amount_rub)} dated ${day(payment.paid_at)} — ${where}?`)) return;
    cancel.mutate(payment.id);
  };

  const openAdvance = (person: PayoutPerson, role: PayoutRole | null) => {
    setAdvance({ person, role });
    setAmount(role ? String(Math.max(0, (role.fee || 0) - role.paid)) : "");
    setNote("");
  };

  const submitAdvance = () => {
    if (!advance) return;
    const value = Number(amount.replace(/\s/g, ""));
    if (!Number.isInteger(value) || value <= 0) {
      pushToast({ tone: "error", title: "Enter a whole number greater than zero." });
      return;
    }
    const where = advance.role ? roleLabel(advance.person, advance.role) : `${advance.person.name}, without a role`;
    if (!window.confirm(`Record advance ${rub(value)} — ${where}?`)) return;
    if (advance.role) {
      pay.mutate({ book_id: advance.role.book_id, role_key: advance.role.role_key, actor_name: advance.role.actor_name,
        kind: "advance", amount_rub: value });
    } else {
      personAdvance.mutate({ person_key: advance.person.key, amount_rub: value, note });
    }
    setAdvance(null);
  };

  const settledCount = data ? data.people.reduce((sum, person) => sum + person.roles.filter(isSettled).length, 0) : 0;
  const needle = fold(search.trim());
  /* поиск идёт и по оплаченным: ищут обычно что-то конкретное — например, выплату, которую
     надо отменить, — и прятать находку за переключателем было бы ловушкой */
  const people = needle
    ? (data?.people || [])
      .map((person) => ({
        person,
        // нашёлся диктор — все его роли; иначе только роли, совпавшие названием или книгой
        roles: fold(person.name).includes(needle)
          ? person.roles
          : person.roles.filter((role) => fold(`${role.role_name} ${role.book_title}`).includes(needle)),
      }))
      .filter(({ person, roles }) => roles.length > 0 || fold(person.name).includes(needle))
    : (data?.people || [])
      .map((person) => ({ person, roles: showPaid ? person.roles : person.roles.filter((role) => !isSettled(role)) }))
      /* карточку без открытых ролей и без авансов без роли убираем целиком: иначе остаётся пустая шапка */
      .filter(({ person, roles }) => showPaid || roles.length > 0 || person.person_advances.length > 0);

  const busy = pay.isPending || personAdvance.isPending || cancel.isPending || query.isFetching;

  return (
    <div className="pay">
      <PageHeader
        title="Budget"
        subtitle={data ? `Due ${rub(data.totals.owed)} · paid ${rub(data.totals.paid)} · advances ${rub(data.totals.advances)} · in progress ${rub(data.totals.in_work)}` : "Loading…"}
        actions={
          <>
            {settledCount > 0 && !needle ? (
              <Button onClick={() => setShowPaid((value) => !value)} aria-pressed={showPaid}>
                {showPaid ? "Hide paid" : `Show paid (${settledCount})`}
              </Button>
            ) : null}
            <a className="ui-btn ui-btn--secondary" href="/api/payouts/export.xlsx" download>
              Export to Excel
            </a>
          </>
        }
      />
      {data && data.people.length === 0 ? <EmptyState text="No approved roles yet." /> : null}
      {data && data.people.length > 0 ? (
        <label className="pay-search">
          <Icon name="search" />
          <input type="search" value={search} placeholder="Voice actor, role or book" aria-label="Search budget"
            onChange={(e) => setSearch(e.target.value)} />
        </label>
      ) : null}
      {data && data.people.length > 0 && people.length === 0
        ? <EmptyState text={needle ? "Nothing found." : "Everything is paid."} /> : null}
      {people.map(({ person, roles }) => (
        <section key={person.key} className="pay-card">
          <header className="pay-card-head">
            <div className="pay-who">
              <h2>{person.name}</h2>
              {person.telegram_url ? (
                <a href={person.telegram_url} target="_blank" rel="noreferrer">Message in Telegram</a>
              ) : (
                <span className="pay-muted">Telegram not linked</span>
              )}
            </div>
            <dl className="pay-sums">
              <div><dt>Due</dt><dd className={person.owed > 0 ? "is-due" : ""}>{rub(person.owed)}</dd></div>
              <div><dt>Paid</dt><dd>{rub(person.paid)}</dd></div>
              <div><dt>Advances</dt><dd>{rub(person.advances)}</dd></div>
              <div><dt>In progress</dt><dd>{rub(person.in_work)}</dd></div>
            </dl>
          </header>

          <div className="pay-advance">
            {/* строка — только у тех, кому аванс выдавали: иначе девяносто одинаковых нулей */}
            {person.person_advances.length ? (
              <span>
                Unassigned advances: issued {rub(person.person_advances.reduce((sum, a) => sum + a.amount_rub, 0))}, unapplied {rub(person.credit)}
                {person.unearned > 0 ? <strong className="pay-warn"> · unearned advance: {rub(person.unearned)}</strong> : null}
              </span>
            ) : <span />}
            <Button size="sm" onClick={() => openAdvance(person, null)} disabled={busy}>Unassigned advance</Button>
          </div>
          {person.person_advances.length ? (
            <ul className="pay-payments">
              {person.person_advances.map((payment) => (
                <li key={payment.id}>
                  {KIND_WORDS[payment.kind]} {rub(payment.amount_rub)}, {day(payment.paid_at)}{payment.note ? ` — ${payment.note}` : ""}
                  <button type="button" className="pay-link" onClick={() => askCancel(person, payment, null)} disabled={busy}>Cancel</button>
                </li>
              ))}
            </ul>
          ) : null}

          <ul className="pay-roles">
            {roles.map((role) => (
              <li key={`${role.book_id}:${role.role_key}`} className="pay-role">
                <div className="pay-role-main">
                  <div className="pay-role-name">
                    <strong>«{displayRoleName(role.role_name)}»</strong> <span className="pay-muted">{role.book_title}</span>
                  </div>
                  <div className="pay-role-meta">
                    {role.total > 0 ? <span>delivered {role.done} of {role.total}</span> : null}
                    {role.fee ? <span>{rub(role.fee)}</span> : null}
                    <RoleState role={role} />
                  </div>
                </div>
                <div className="pay-role-actions">
                  {role.state === "done" && role.owed > 0 ? (
                    <Button size="sm" variant="primary" onClick={() => payFinal(person, role)} disabled={busy}>Paid</Button>
                  ) : null}
                  {role.state === "in_progress" && role.fee && role.fee - role.paid > 0 ? (
                    <Button size="sm" onClick={() => openAdvance(person, role)} disabled={busy}>Advance</Button>
                  ) : null}
                </div>
                {role.payments.length ? (
                  <ul className="pay-payments">
                    {role.payments.map((payment) => (
                      <li key={payment.id}>
                        {KIND_WORDS[payment.kind]} {rub(payment.amount_rub)}, {day(payment.paid_at)}
                        <button type="button" className="pay-link" onClick={() => askCancel(person, payment, role)} disabled={busy}>Cancel</button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}

      {advance ? (
        <Dialog
          title={advance.role ? `Advance — «${displayRoleName(advance.role.role_name)}»` : "Unassigned advance"}
          subtitle={advance.role ? `${advance.person.name}, remaining role balance ${rub(Math.max(0, (advance.role.fee || 0) - advance.role.paid))}` : advance.person.name}
          onClose={() => setAdvance(null)}
          footer={
            <>
              <Button onClick={() => setAdvance(null)}>Cancel</Button>
              <Button variant="primary" onClick={submitAdvance}>Record</Button>
            </>
          }
        >
          <Field label="Amount, RUB">
            {(props) => <input {...props} className="ui-input" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value)} />}
          </Field>
          {!advance.role ? (
            <Field label="Description (optional)">
              {(props) => <input {...props} className="ui-input" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />}
            </Field>
          ) : null}
        </Dialog>
      ) : null}
    </div>
  );
}
