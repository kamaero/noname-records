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
  nothing_to_pay: "По роли нечего выплачивать.",
  bad_amount: "Сумма больше остатка по роли или не число.",
  no_fee: "Сумма роли не посчитана — откройте смету книги.",
  not_in_progress: "Аванс — только по роли в работе.",
  role_not_found: "Роль не найдена — обновите страницу.",
  person_not_found: "Диктор не найден — обновите страницу.",
  not_found: "Запись уже отменена.",
  advance_offset: "Этот аванс уже зачтён в роли — сначала отмените зачёт.",
};

const KIND_WORDS: Record<string, string> = {
  advance: "аванс",
  final: "выплата",
  offset: "зачёт аванса",
  person_advance: "аванс без роли",
};

function rub(value: number): string {
  return `${Math.round(value || 0).toLocaleString("ru-RU")} ₽`;
}

function day(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

function refusal(error: unknown): string {
  if (error instanceof ApiError && REFUSALS[error.errorCode]) return REFUSALS[error.errorCode];
  return describeApiError(error, "Не получилось сохранить.");
}

function roleLabel(person: PayoutPerson, role: PayoutRole): string {
  return `${person.name}, «${role.role_name}» («${role.book_title}»)`;
}

/** Остаток сданной роли и сколько из него закроет незачтённый аванс — только для текста вопроса. */
function finalParts(person: PayoutPerson, role: PayoutRole): { offset: number; money: number } {
  const remainder = Math.max(0, (role.fee || 0) - role.paid);
  const offset = Math.min(person.credit, remainder);
  return { offset, money: remainder - offset };
}

function RoleState({ role }: { role: PayoutRole }) {
  if (role.state === "past") return <span className="pay-state is-muted">прошлая роль (снята или книга удалена) · выплачено {rub(role.paid)}</span>;
  if (role.state === "no_chapters") return <span className="pay-state is-muted">нет глав</span>;
  if (role.state === "no_fee") return <span className="pay-state is-muted">сумма не посчитана — откройте смету книги</span>;
  if (role.state === "in_progress") {
    return (
      <span className="pay-state is-muted">
        в работе
        {role.paid > 0 ? ` · аванс ${rub(role.paid)} из ${rub(role.fee || 0)}` : ""}
      </span>
    );
  }
  if (role.owed > 0) return <span className="pay-state is-due">к оплате {rub(role.owed)}</span>;
  if (role.overpaid > 0) return <span className="pay-state is-warn">переплата {rub(role.overpaid)}</span>;
  return <span className="pay-state is-ok">оплачено полностью</span>;
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
    return parts.length ? `Записано: ${parts.join(", ")}.` : "Записано.";
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
    onSuccess: async () => { pushToast({ tone: "success", title: "Запись отменена." }); await done(); },
    onError,
  });

  if (query.error) {
    const forbidden = query.error instanceof ApiError && query.error.status === 403;
    return (
      <div className="pay">
        <PageHeader title="Смета" />
        <EmptyState text={forbidden ? "Раздел доступен только владельцу студии." : describeApiError(query.error, "Не получилось загрузить смету.")} />
      </div>
    );
  }
  const data = query.data;

  const payFinal = (person: PayoutPerson, role: PayoutRole) => {
    const { offset, money } = finalParts(person, role);
    const what = offset > 0
      ? `Зачесть аванс ${rub(offset)}${money > 0 ? ` и выплатить ${rub(money)}` : ""}`
      : `Отметить выплату ${rub(money)}`;
    if (!window.confirm(`${what} — ${roleLabel(person, role)}?`)) return;
    pay.mutate({ book_id: role.book_id, role_key: role.role_key, actor_name: role.actor_name, kind: "final" });
  };

  const askCancel = (person: PayoutPerson, payment: PayoutPayment, role: PayoutRole | null) => {
    const where = role ? roleLabel(person, role) : person.name;
    if (!window.confirm(`Отменить «${KIND_WORDS[payment.kind] || payment.kind}» ${rub(payment.amount_rub)} от ${day(payment.paid_at)} — ${where}?`)) return;
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
      pushToast({ tone: "error", title: "Сумма — целое число больше нуля." });
      return;
    }
    const where = advance.role ? roleLabel(advance.person, advance.role) : `${advance.person.name}, без роли`;
    if (!window.confirm(`Записать аванс ${rub(value)} — ${where}?`)) return;
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
        title="Смета"
        subtitle={data ? `К оплате ${rub(data.totals.owed)} · выплачено ${rub(data.totals.paid)} · авансы ${rub(data.totals.advances)} · в работе ${rub(data.totals.in_work)}` : "Загрузка…"}
        actions={
          <>
            {settledCount > 0 && !needle ? (
              <Button onClick={() => setShowPaid((value) => !value)} aria-pressed={showPaid}>
                {showPaid ? "Скрыть оплаченные" : `Показать оплаченные (${settledCount})`}
              </Button>
            ) : null}
            <a className="ui-btn ui-btn--secondary" href="/api/payouts/export.xlsx" download>
              Выгрузить в Excel
            </a>
          </>
        }
      />
      {data && data.people.length === 0 ? <EmptyState text="Пока никого: нет утверждённых ролей." /> : null}
      {data && data.people.length > 0 ? (
        <label className="pay-search">
          <Icon name="search" />
          <input type="search" value={search} placeholder="Диктор, роль или книга" aria-label="Найти в смете"
            onChange={(e) => setSearch(e.target.value)} />
        </label>
      ) : null}
      {data && data.people.length > 0 && people.length === 0
        ? <EmptyState text={needle ? "Ничего не найдено." : "Всё оплачено."} /> : null}
      {people.map(({ person, roles }) => (
        <section key={person.key} className="pay-card">
          <header className="pay-card-head">
            <div className="pay-who">
              <h2>{person.name}</h2>
              {person.telegram_url ? (
                <a href={person.telegram_url} target="_blank" rel="noreferrer">Написать в Telegram</a>
              ) : (
                <span className="pay-muted">Telegram не привязан</span>
              )}
            </div>
            <dl className="pay-sums">
              <div><dt>К оплате</dt><dd className={person.owed > 0 ? "is-due" : ""}>{rub(person.owed)}</dd></div>
              <div><dt>Выплачено</dt><dd>{rub(person.paid)}</dd></div>
              <div><dt>Авансы</dt><dd>{rub(person.advances)}</dd></div>
              <div><dt>В работе</dt><dd>{rub(person.in_work)}</dd></div>
            </dl>
          </header>

          <div className="pay-advance">
            {/* строка — только у тех, кому аванс выдавали: иначе девяносто одинаковых нулей */}
            {person.person_advances.length ? (
              <span>
                Аванс без роли: выдано {rub(person.person_advances.reduce((sum, a) => sum + a.amount_rub, 0))}, не зачтено {rub(person.credit)}
                {person.unearned > 0 ? <strong className="pay-warn"> · аванс не отработан: {rub(person.unearned)}</strong> : null}
              </span>
            ) : <span />}
            <Button size="sm" onClick={() => openAdvance(person, null)} disabled={busy}>Аванс без роли</Button>
          </div>
          {person.person_advances.length ? (
            <ul className="pay-payments">
              {person.person_advances.map((payment) => (
                <li key={payment.id}>
                  {KIND_WORDS[payment.kind]} {rub(payment.amount_rub)}, {day(payment.paid_at)}{payment.note ? ` — ${payment.note}` : ""}
                  <button type="button" className="pay-link" onClick={() => askCancel(person, payment, null)} disabled={busy}>Отменить</button>
                </li>
              ))}
            </ul>
          ) : null}

          <ul className="pay-roles">
            {roles.map((role) => (
              <li key={`${role.book_id}:${role.role_key}`} className="pay-role">
                <div className="pay-role-main">
                  <div className="pay-role-name">
                    <strong>«{role.role_name}»</strong> <span className="pay-muted">{role.book_title}</span>
                  </div>
                  <div className="pay-role-meta">
                    {role.total > 0 ? <span>сдано {role.done} из {role.total}</span> : null}
                    {role.fee ? <span>{rub(role.fee)}</span> : null}
                    <RoleState role={role} />
                  </div>
                </div>
                <div className="pay-role-actions">
                  {role.state === "done" && role.owed > 0 ? (
                    <Button size="sm" variant="primary" onClick={() => payFinal(person, role)} disabled={busy}>Выплачено</Button>
                  ) : null}
                  {role.state === "in_progress" && role.fee && role.fee - role.paid > 0 ? (
                    <Button size="sm" onClick={() => openAdvance(person, role)} disabled={busy}>Аванс</Button>
                  ) : null}
                </div>
                {role.payments.length ? (
                  <ul className="pay-payments">
                    {role.payments.map((payment) => (
                      <li key={payment.id}>
                        {KIND_WORDS[payment.kind]} {rub(payment.amount_rub)}, {day(payment.paid_at)}
                        <button type="button" className="pay-link" onClick={() => askCancel(person, payment, role)} disabled={busy}>Отменить</button>
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
          title={advance.role ? `Аванс — «${advance.role.role_name}»` : "Аванс без роли"}
          subtitle={advance.role ? `${advance.person.name}, остаток по роли ${rub(Math.max(0, (advance.role.fee || 0) - advance.role.paid))}` : advance.person.name}
          onClose={() => setAdvance(null)}
          footer={
            <>
              <Button onClick={() => setAdvance(null)}>Отмена</Button>
              <Button variant="primary" onClick={submitAdvance}>Записать</Button>
            </>
          }
        >
          <Field label="Сумма, ₽">
            {(props) => <input {...props} className="ui-input" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value)} />}
          </Field>
          {!advance.role ? (
            <Field label="За что (необязательно)">
              {(props) => <input {...props} className="ui-input" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />}
            </Field>
          ) : null}
        </Dialog>
      ) : null}
    </div>
  );
}
