/**
 * «Пользователи» — the studio's accounts, as a screen instead of a scratch script.
 *
 * Forty-nine dictors were let in by hand: logins transliterated from their names,
 * passwords generated once, roles inserted straight into the table. Everything the
 * owner had to ask for — rename someone whose surname was typed wrong, turn an
 * account off, hand out a fresh password — lives here now.
 *
 * Two things the screen is careful about. A generated password is shown once and is
 * not readable again, so it is displayed as its own banner rather than a table cell
 * that scrolls away. And a `tg_*` row is not a duplicate: it is the same person's
 * second way in, which the row says out loud, because deleting it would not merge
 * anything — Telegram login recreates it on the next visit.
 *
 * Объединение выбирается вручную. Сверка по именам находит двойников, только пока имена
 * похожи, а они расходятся сильнее всего там, где это важнее: «Blue Falcon» и «Николай
 * Бондаренко» — один человек, и никакая сверка их не свяжет. Поэтому «Объединить…»
 * предлагает выбрать любую учётку, а найденного двойника ставит первым.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPatchJson, apiPostJson, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { useToast } from "../components/ToastProvider";
import { Button, DataTable, PageHeader, Sheet, type Column } from "../ui";
import { plural } from "../v2/useIsMobile";
import type { BotReachResponse, MeResponse, SaveTelegramWhitelistPayload, TelegramWhitelistItem, UserListItem, UsersResponse } from "../types";
import "./UsersPage.css";

const ROLES = ["admin", "author", "dictor", "agent"] as const;
const ROLE_LABELS: Record<string, string> = { admin: "Administrator", author: "Author", dictor: "Voice actor", agent: "Agent" };

/** The refusals the service can answer with, in the owner's words. */
const REFUSALS: Record<string, string> = {
  last_admin: "This is the last administrator. Keep one administrator to retain access.",
  same_account: "These are the same account.",
  self_delete: "You cannot delete your own account.",
  display_name_required: "A name is required.",
  login_taken: "This username is already taken.",
  user_not_found: "Account not found. It may have been deleted.",
  telegram_taken: "This Telegram ID is linked to another account. Unlink it there first.",
  bad_telegram_id: "Telegram ID must be a number.",
};

function refusal(error: unknown): string {
  const text = describeApiError(error, "");
  for (const [code, message] of Object.entries(REFUSALS)) {
    if (text.includes(code)) return message;
  }
  return describeApiError(error, "The server returned no details.");
}

function fold(value: string): string {
  return (value || "").toLowerCase().replace(/ё/g, "е");
}

export function UsersPage({ me }: { me: MeResponse }) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const allowed = me.roles.includes("admin") || me.is_owner_telegram;

  const [query, setQuery] = useState("");
  /** a password just generated: shown once, then gone */
  const [secret, setSecret] = useState<{ login: string; password: string } | null>(null);
  /** учётка, которую сливают: выбирается вручную, потому что имена расходятся сильнее
      всего там, где это важнее — ник в Telegram и ФИО в касте у одного человека бывают совсем разными */
  const [mergeFrom, setMergeFrom] = useState<UserListItem | null>(null);
  const [mergeQuery, setMergeQuery] = useState("");

  /* Реестр отдаётся текстом, и сохранить его надо тем же жестом, что и любой файл.
     Ссылка живёт мгновение: она нужна только чтобы браузер начал скачивание. */
  const download = (url: string, blob?: Blob, name = "credentials.txt") => {
    const href = blob ? URL.createObjectURL(blob) : url;
    const link = document.createElement("a");
    link.href = href;
    if (blob) link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    if (blob) URL.revokeObjectURL(href);
  };

  /* Вход через виджет и письма бота — разные каналы: виджет работает без бота, а писать
     человеку бот не может, пока тот сам не нажал Start. Человек прекрасно входит и молча
     не получает ни одного уведомления — проверка отвечает на это заранее. */
  const [botReach, setBotReach] = useState<BotReachResponse | null>(null);
  const checkBot = useMutation({
    mutationFn: () => apiGet<BotReachResponse>("/api/users/bot-reach"),
    onSuccess: (result) => {
      setBotReach(result);
      if (result.error === "no_token") {
        pushToast({ tone: "error", title: "Bot not configured", detail: "No bot token configured in .env." });
        return;
      }
      pushToast({
        tone: result.unreachable ? "info" : "success",
        title: result.unreachable ? `The bot cannot reach ${result.unreachable}` : "The bot can reach everyone",
        detail: `Checked ${result.checked}.`,
      });
    },
    onError: (error) => pushToast({ tone: "error", title: "Check failed", detail: refusal(error) }),
  });

  const attachTelegram = useMutation({
    mutationFn: ({ id, telegram_user_id }: { id: string; telegram_user_id: string }) =>
      apiPostJson<{ ok: boolean; telegram_user_id: string }>(
        `/api/users/${encodeURIComponent(id)}/telegram`,
        { telegram_user_id },
      ),
    onSuccess: async (result) => {
      pushToast({
        tone: "success",
        title: result.telegram_user_id ? "Telegram linked" : "Telegram unlinked",
        detail: result.telegram_user_id || undefined,
      });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not link", detail: refusal(error) }),
  });

  const issuePasswords = useMutation({
    mutationFn: () => apiPostJson<{ ok: boolean; issued: number; roster: string }>("/api/users/issue-passwords", {}),
    onSuccess: async (result) => {
      download("", new Blob([result.roster], { type: "text/plain;charset=utf-8" }), "credentials-with-passwords.txt");
      pushToast({
        tone: "success",
        title: `Passwords issued: ${result.issued}`,
        detail: "File downloaded. These passwords are not stored anywhere else.",
      });
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not complete the action", detail: refusal(error) }),
  });
  const [whitelistForm, setWhitelistForm] = useState<SaveTelegramWhitelistPayload>({
    telegram_user_id: "",
    display_name: "",
    role: "dictor",
    access_scope: "full",
    is_active: true,
  });

  const usersQuery = useQuery({
    queryKey: ["users"],
    queryFn: () => apiGet<UsersResponse>("/api/users"),
    enabled: allowed,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });
  const onRefusal = (title: string) => (error: unknown) =>
    pushToast({ tone: "error", title, detail: refusal(error) });

  const createUser = useMutation({
    mutationFn: (payload: { display_name: string; roles: string[] }) =>
      apiPostJson<{ ok: boolean; user: UserListItem; password: string }>("/api/users", payload),
    onSuccess: (result) => {
      setSecret({ login: result.user.login, password: result.password });
      void refresh();
    },
    onError: onRefusal("Account not created"),
  });

  const updateUser = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) =>
      apiPatchJson<{ ok: boolean; user: UserListItem }>(`/api/users/${encodeURIComponent(id)}`, patch),
    onSuccess: () => void refresh(),
    onError: onRefusal("Not saved"),
  });

  const resetPassword = useMutation({
    mutationFn: (user: UserListItem) =>
      apiPostJson<{ ok: boolean; password: string }>(`/api/users/${encodeURIComponent(user.id)}/password`, {}).then(
        (result) => ({ ...result, login: user.login }),
      ),
    onSuccess: (result) => setSecret({ login: result.login, password: result.password }),
    onError: onRefusal("Password not issued"),
  });

  const removeUser = useMutation({
    mutationFn: (id: string) => apiDelete<{ ok: boolean }>(`/api/users/${encodeURIComponent(id)}`),
    onSuccess: () => {
      pushToast({ tone: "success", title: "Account deleted" });
      void refresh();
    },
    onError: onRefusal("Not deleted"),
  });

  const mergeUsers = useMutation({
    mutationFn: (payload: { keep_id: string; drop_id: string }) =>
      apiPostJson<{ ok: boolean; user: UserListItem; dropped_login: string }>("/api/users/merge", payload),
    onSuccess: (result) => {
      pushToast({
        tone: "success",
        title: "Accounts merged",
        detail: `Kept ${result.user.login}; ${result.dropped_login} deleted; history transferred.`,
      });
      void refresh();
    },
    onError: onRefusal("Not merged"),
  });

  const bulkRoles = useMutation({
    mutationFn: (payload: { ids: string[]; roles: string[] }) =>
      apiPostJson<{ ok: boolean; changed: number }>("/api/users/bulk-roles", payload),
    onSuccess: (result) => {
      pushToast({ tone: "success", title: `Role changed for ${result.changed}` });
      void refresh();
    },
    onError: onRefusal("Roles not changed"),
  });

  const users = usersQuery.data?.users ?? [];
  const found = useMemo(() => {
    const needle = fold(query).trim();
    if (!needle) return users;
    return users.filter((user) => fold(user.display_name).includes(needle) || fold(user.login).includes(needle));
  }, [users, query]);

  /** Кого предлагаем оставить: все, кроме сливаемой. Найденный двойник идёт первым —
      это подсказка, а не решение. */
  const mergeCandidates = useMemo(() => {
    if (!mergeFrom) return [];
    const needle = fold(mergeQuery).trim();
    const rest = users.filter(
      (user) =>
        user.id !== mergeFrom.id &&
        (!needle || fold(user.display_name).includes(needle) || fold(user.login).includes(needle)),
    );
    rest.sort((a, b) => {
      if (a.id === mergeFrom.twin_id) return -1;
      if (b.id === mergeFrom.twin_id) return 1;
      return a.display_name.localeCompare(b.display_name, "ru");
    });
    return rest.slice(0, 40);
  }, [users, mergeFrom, mergeQuery]);

  const whitelist = usersQuery.data?.owner_whitelist_accounts ?? [];

  /** Кому нечем войти по логину: пароля им не выдавали, учётка родилась из телеграма. */
  const withoutPassword = useMemo(
    () => users.filter((user) => user.is_active && !user.ways_in.includes("password")),
    [users],
  );

  // The 49 dictors were all given `author`, which is the right to edit the markup.
  const authorsOnly = useMemo(
    () => users.filter((user) => user.roles.length === 1 && user.roles[0] === "author"),
    [users],
  );

  const rename = (user: UserListItem) => {
    const next = window.prompt("Full name", user.display_name);
    if (next === null || next.trim() === user.display_name) return;
    updateUser.mutate({ id: user.id, patch: { display_name: next.trim() } });
  };

  const columns: Column<UserListItem>[] = [
    {
      key: "name",
      header: "Name",
      render: (user) => (
        // the name is the affordance: renaming is the commonest fix and does not
        // deserve a button competing for the row's right edge
        <button
          type="button"
          className={user.is_active ? "usr-name" : "usr-name usr-off"}
          onClick={() => rename(user)}
          title="Rename"
        >
          <strong>{user.display_name}</strong>
          <span className="usr-login">{user.login}</span>
        </button>
      ),
    },
    {
      key: "auth",
      header: "Sign-in",
      width: 190,
      render: (user) => (
        <span className="usr-auth">
          {/* Оба пути сразу — это и есть ответ на «узнает ли система меня»: и пароль,
              и телеграм ведут в одну строку. */}
          <span className="usr-ways">
            {user.ways_in.includes("password") ? <span className="usr-way">username and password</span> : null}
            {/* Телеграм привязывается здесь же: владелец смотрит на строку человека и
                уже знает, чей это айди — спрашивать его у сверки по именам нелепо. */}
            <button
              type="button"
              className={user.telegram_user_id ? "usr-way usr-way--tg" : "usr-way usr-way--add"}
              disabled={attachTelegram.isPending}
              title={user.telegram_user_id ? `Telegram ID ${user.telegram_user_id} — click to change` : "Link Telegram"}
              onClick={() => {
                const entered = window.prompt(
                  `Telegram ID for «${user.display_name}»` +
                    (user.telegram_user_id ? "\n\nLeave blank to unlink." : ""),
                  user.telegram_user_id || "",
                );
                if (entered === null) return;
                attachTelegram.mutate({ id: user.id, telegram_user_id: entered.trim() });
              }}
            >
              {user.telegram_user_id ? "Telegram" : "+ Telegram"}
            </button>
          </span>
          {user.twin_login ? <span className="usr-twin">possible match — {user.twin_login}</span> : null}
        </span>
      ),
    },
    {
      key: "roles",
      header: "Roles",
      width: 210,
      render: (user) => (
        <span className="usr-roles">
          {user.roles.length === 0 ? <span className="usr-role">no roles</span> : null}
          {user.roles.map((role) => (
            <span key={role} className={role === "admin" ? "usr-role usr-role--admin" : "usr-role"}>
              {ROLE_LABELS[role] || role}
            </span>
          ))}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      width: 96,
      nowrap: true,
      render: (user) => (user.is_active ? "active" : "disabled"),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      width: 420,
      render: (user) => (
        <span className="usr-actions">
          <select
            className="usr-rolepick"
            value={user.roles[0] || ""}
            onChange={(event) => updateUser.mutate({ id: user.id, patch: { roles: [event.target.value] } })}
            aria-label={`Account role for ${user.display_name}`}
          >
            <option value="" disabled>
              role…
            </option>
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABELS[role] || role}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => updateUser.mutate({ id: user.id, patch: { is_active: !user.is_active } })}
          >
            {user.is_active ? "Disable" : "Enable"}
          </Button>
          {user.auth === "password" ? (
            <Button size="sm" variant="ghost" onClick={() => resetPassword.mutate(user)}>
              Password
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => setMergeFrom(user)} disabled={mergeUsers.isPending}>
            Merge…
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              if (window.confirm(`Delete account «${user.display_name}» (${user.login})?`)) removeUser.mutate(user.id);
            }}
          >
            Delete
          </Button>
        </span>
      ),
    },
  ];

  if (!allowed) return <div className="panel panel-pad">This page is available to the owner and administrators only.</div>;
  if (usersQuery.isError) {
    return <div className="panel panel-pad">{describeApiError(usersQuery.error, "Could not load users.")}</div>;
  }

  return (
    <section className="usr">
      <PageHeader
        title="Users"
        subtitle={`${users.length} accounts · manage usernames and passwords here`}
        actions={
          <>
            <Button variant="secondary" onClick={() => download("/api/users/export.txt")}>
              Export credentials
            </Button>
            <Button
              variant="secondary"
              loading={checkBot.isPending}
              title="Check who can receive casting and missing-line notifications"
              onClick={() => checkBot.mutate()}
            >
              Bot reachability
            </Button>
            {withoutPassword.length ? (
              <Button
                variant="secondary"
                disabled={issuePasswords.isPending}
                title={`Without a password: ${withoutPassword.map((u) => u.display_name).join(", ")}`}
                onClick={() => {
                  if (
                    window.confirm(
                      `Issue passwords to accounts without one (${withoutPassword.length})? ` +
                        `A file with new passwords will be downloaded. This is the only time they will be visible.

` +
                        `Existing passwords will remain unchanged.`,
                    )
                  )
                    issuePasswords.mutate();
                }}
              >
                Issue passwords · {withoutPassword.length}
              </Button>
            ) : null}
            <Button
              onClick={() => {
                const name = window.prompt("New member's full name");
                if (!name || !name.trim()) return;
                createUser.mutate({ display_name: name.trim(), roles: ["dictor"] });
              }}
            >
              <Icon name="users" /> Create account
            </Button>
          </>
        }
      />

      {secret ? (
        <div className="usr-secret" role="status">
          <span>
            <strong>{secret.login}</strong> — password <code>{secret.password}</code>
          </span>
          <span className="usr-secret-note">Shown once. Copy it and share privately.</span>
          <Button size="sm" variant="ghost" onClick={() => setSecret(null)}>
            Hide
          </Button>
        </div>
      ) : null}

      {authorsOnly.length > 0 ? (
        <div className="panel panel-pad usr-bulk">
          <span>
            <strong>{authorsOnly.length}</strong> {plural(authorsOnly.length, "account has", "accounts have", "accounts have")} the{" "}
            <strong>Author</strong> role, which grants permission to edit book annotations.
          </span>
          <Button
            size="sm"
            onClick={() => {
              if (window.confirm(`Change ${authorsOnly.length} accounts to voice actors?`))
                bulkRoles.mutate({ ids: authorsOnly.map((user) => user.id), roles: ["dictor"] });
            }}
            disabled={bulkRoles.isPending}
          >
            Make them voice actors
          </Button>
        </div>
      ) : null}

      <div className="usr-head">
        <h2>Accounts</h2>
        <span className="usr-count">{found.length === users.length ? users.length : `${found.length} of ${users.length}`}</span>
        <span className="usr-spacer" />
        <label className="usr-search">
          <Icon name="search" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Name or username…"
            aria-label="Search accounts"
          />
        </label>
      </div>

      {botReach ? (
        <Sheet
          title="Bot reachability"
          subtitle={`Checked ${botReach.checked} · unreachable ${botReach.unreachable}`}
          onClose={() => setBotReach(null)}
        >
          <p className="usr-reach-note">
            Log in with Telegram works without the bot: Telegram verifies the identity. The bot can only send messages after the person presses Start in the{" "}
            <strong>studio bot</strong>. The people below cannot receive casting and missing-line notifications until then.
          </p>
          <ul className="usr-reach-list">
            {botReach.items.map((item) => (
              <li key={item.telegram_user_id} className={item.reachable ? "usr-reach usr-reach--ok" : "usr-reach"}>
                <span className="usr-reach-who">
                  <strong>{item.display_name}</strong>
                  {item.telegram_name && item.telegram_name !== item.display_name ? (
                    <span className="usr-login"> in Telegram — {item.telegram_name}
                      {item.username ? ` @${item.username}` : ""}
                    </span>
                  ) : null}
                </span>
                <span className={item.reachable ? "usr-reach-state" : "usr-reach-state usr-reach-state--bad"}>
                  {item.reachable === null ? "not checked" : item.reachable ? "reachable" : "unreachable"}
                </span>
              </li>
            ))}
          </ul>
        </Sheet>
      ) : null}

      {mergeFrom ? (
        <Sheet
          title={`Merge «${mergeFrom.display_name}»`}
          subtitle="This account will be removed; sign-in methods, roles and history will move to the selected account"
          onClose={() => {
            setMergeFrom(null);
            setMergeQuery("");
          }}
        >
          <label className="usr-merge-search">
            <Icon name="search" />
            <input
              type="search"
              autoFocus
              value={mergeQuery}
              placeholder="Name or username"
              aria-label="Find account"
              onChange={(event) => setMergeQuery(event.target.value)}
            />
          </label>
          <ul className="usr-merge-list">
            {mergeCandidates.map((candidate) => (
              <li key={candidate.id}>
                <button
                  type="button"
                  className={candidate.id === mergeFrom.twin_id ? "usr-merge-pick is-suggested" : "usr-merge-pick"}
                  disabled={mergeUsers.isPending}
                  onClick={() => {
                    if (
                      window.confirm(
                        `Keep «${candidate.display_name}» (${candidate.login}) and remove ` +
                          `«${mergeFrom.display_name}» (${mergeFrom.login})?\n\n` +
                          `Sign-in methods, roles and history will move to the remaining account.`,
                      )
                    ) {
                      mergeUsers.mutate({ keep_id: candidate.id, drop_id: mergeFrom.id });
                      setMergeFrom(null);
                      setMergeQuery("");
                    }
                  }}
                >
                  <strong>{candidate.display_name}</strong>
                  <span className="usr-login">{candidate.login}</span>
                  {candidate.id === mergeFrom.twin_id ? <span className="usr-merge-hint">similar name</span> : null}
                </button>
              </li>
            ))}
            {mergeCandidates.length === 0 ? <li className="usr-merge-empty">No people found.</li> : null}
          </ul>
        </Sheet>
      ) : null}

      <DataTable
        columns={columns}
        rows={found}
        rowKey={(user) => user.id}
        loading={usersQuery.isLoading}
        empty="Nothing found."
        aria-label="Accounts"
      />

      <div className="panel panel-pad">
        <h2>Telegram whitelist</h2>
        <p className="usr-count">
          Accounts allowed to use Log in with Telegram — {whitelist.length}{" "}
          {plural(whitelist.length, "entry", "entries", "entries")}, including{" "}
          {whitelist.filter((item) => item.linked_login).length} linked to accounts. A username and password are created here; existing members are linked to their accounts. You can also link Telegram from the account row above.
        </p>
        <WhitelistForm
          form={whitelistForm}
          onForm={setWhitelistForm}
          items={whitelist}
          onSaved={() => void refresh()}
          onCreated={setSecret}
        />
      </div>
    </section>
  );
}

function WhitelistForm({
  form,
  onForm,
  items,
  onSaved,
  onCreated,
}: {
  form: SaveTelegramWhitelistPayload;
  onForm: (value: SaveTelegramWhitelistPayload) => void;
  items: TelegramWhitelistItem[];
  onSaved: () => void;
  /** пароль заведённой заодно учётки — показать один раз */
  onCreated: (secret: { login: string; password: string }) => void;
}) {
  const { pushToast } = useToast();
  const save = useMutation({
    mutationFn: (payload: SaveTelegramWhitelistPayload) =>
      apiPostJson<{ ok: boolean; entry: TelegramWhitelistItem; created_account?: { login: string; display_name: string; password: string } }>(
        "/api/users/telegram-whitelist",
        payload,
      ),
    onSuccess: (result) => {
      // Учётка заводится вместе с записью, и пароль показывается тут же: другого раза
      // его увидеть не будет.
      if (result.created_account) onCreated(result.created_account);
      pushToast({
        tone: "success",
        title: result.created_account ? "Entry and account created" : "Whitelist updated",
        detail: result.created_account ? `Username ${result.created_account.login}` : undefined,
      });
      onForm({ telegram_user_id: "", display_name: "", role: "dictor", access_scope: "full", is_active: true });
      onSaved();
    },
    onError: (error) => pushToast({ tone: "error", title: "Whitelist not saved", detail: refusal(error) }),
  });

  return (
    <>
      <form
        className="form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate(form);
        }}
      >
        <label>
          Telegram ID
          <input value={form.telegram_user_id} onChange={(event) => onForm({ ...form, telegram_user_id: event.target.value })} required />
        </label>
        <label>
          Full name
          <input value={form.display_name} onChange={(event) => onForm({ ...form, display_name: event.target.value })} required />
        </label>
        <label>
          Role
          <select value={form.role} onChange={(event) => onForm({ ...form, role: event.target.value })}>
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABELS[role] || role}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select value={form.is_active ? "true" : "false"} onChange={(event) => onForm({ ...form, is_active: event.target.value === "true" })}>
            <option value="true">active</option>
            <option value="false">disabled</option>
          </select>
        </label>
        <div className="form-actions">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Add / update"}
          </Button>
        </div>
      </form>
      <ul className="list compact">
        {items.map((item) => (
          <li className="whitelist-row" key={item.id}>
            <strong>{item.display_name || item.telegram_user_id}</strong>
            <span>
              {item.telegram_user_id} · {ROLE_LABELS[item.role] || item.role} · {item.is_active ? "active" : "disabled"}
              {item.linked_login ? (
                <span className="whitelist-linked"> · account {item.linked_login}</span>
              ) : (
                <span className="whitelist-loose"> · unlinked — sign-in will create a new account</span>
              )}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                onForm({
                  entry_id: item.id,
                  telegram_user_id: item.telegram_user_id,
                  display_name: item.display_name,
                  role: item.role,
                  access_scope: item.access_scope,
                  is_active: item.is_active,
                })
              }
            >
              Edit
            </Button>
          </li>
        ))}
      </ul>
    </>
  );
}
