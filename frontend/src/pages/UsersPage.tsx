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

/** The refusals the service can answer with, in the owner's words. */
const REFUSALS: Record<string, string> = {
  last_admin: "Это последний администратор — иначе в студию будет не войти.",
  same_account: "Это одна и та же учётка.",
  self_delete: "Свою собственную учётку удалить нельзя.",
  display_name_required: "Нужно имя.",
  login_taken: "Такой логин уже занят.",
  user_not_found: "Учётка не найдена — возможно, её уже удалили.",
  telegram_taken: "Этот Telegram ID уже привязан к другой учётке — сначала отвяжите его там.",
  bad_telegram_id: "Telegram ID — это число.",
};

function refusal(error: unknown): string {
  const text = describeApiError(error, "");
  for (const [code, message] of Object.entries(REFUSALS)) {
    if (text.includes(code)) return message;
  }
  return describeApiError(error, "Сервер ответил без подробностей.");
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
  const download = (url: string, blob?: Blob, name = "доступы.txt") => {
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
        pushToast({ tone: "error", title: "Бот не настроен", detail: "В .env нет токена бота." });
        return;
      }
      pushToast({
        tone: result.unreachable ? "info" : "success",
        title: result.unreachable ? `Бот не достучится до ${result.unreachable}` : "Бот достучится до всех",
        detail: `Проверено ${result.checked}.`,
      });
    },
    onError: (error) => pushToast({ tone: "error", title: "Проверка не прошла", detail: refusal(error) }),
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
        title: result.telegram_user_id ? "Telegram привязан" : "Telegram отвязан",
        detail: result.telegram_user_id || undefined,
      });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не привязалось", detail: refusal(error) }),
  });

  const issuePasswords = useMutation({
    mutationFn: () => apiPostJson<{ ok: boolean; issued: number; roster: string }>("/api/users/issue-passwords", {}),
    onSuccess: async (result) => {
      download("", new Blob([result.roster], { type: "text/plain;charset=utf-8" }), "доступы-с-паролями.txt");
      pushToast({
        tone: "success",
        title: `Паролей выдано: ${result.issued}`,
        detail: "Файл скачан. Пароли в нём больше нигде не хранятся.",
      });
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (error) => pushToast({ tone: "error", title: "Не вышло", detail: refusal(error) }),
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
    onError: onRefusal("Учётка не создана"),
  });

  const updateUser = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) =>
      apiPatchJson<{ ok: boolean; user: UserListItem }>(`/api/users/${encodeURIComponent(id)}`, patch),
    onSuccess: () => void refresh(),
    onError: onRefusal("Не сохранено"),
  });

  const resetPassword = useMutation({
    mutationFn: (user: UserListItem) =>
      apiPostJson<{ ok: boolean; password: string }>(`/api/users/${encodeURIComponent(user.id)}/password`, {}).then(
        (result) => ({ ...result, login: user.login }),
      ),
    onSuccess: (result) => setSecret({ login: result.login, password: result.password }),
    onError: onRefusal("Пароль не выдан"),
  });

  const removeUser = useMutation({
    mutationFn: (id: string) => apiDelete<{ ok: boolean }>(`/api/users/${encodeURIComponent(id)}`),
    onSuccess: () => {
      pushToast({ tone: "success", title: "Учётка удалена" });
      void refresh();
    },
    onError: onRefusal("Не удалено"),
  });

  const mergeUsers = useMutation({
    mutationFn: (payload: { keep_id: string; drop_id: string }) =>
      apiPostJson<{ ok: boolean; user: UserListItem; dropped_login: string }>("/api/users/merge", payload),
    onSuccess: (result) => {
      pushToast({
        tone: "success",
        title: "Учётки объединены",
        detail: `Осталась ${result.user.login}; ${result.dropped_login} удалена, история перенесена.`,
      });
      void refresh();
    },
    onError: onRefusal("Не объединено"),
  });

  const bulkRoles = useMutation({
    mutationFn: (payload: { ids: string[]; roles: string[] }) =>
      apiPostJson<{ ok: boolean; changed: number }>("/api/users/bulk-roles", payload),
    onSuccess: (result) => {
      pushToast({ tone: "success", title: `Роль изменена у ${result.changed}` });
      void refresh();
    },
    onError: onRefusal("Роли не изменены"),
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
    const next = window.prompt("Имя и фамилия", user.display_name);
    if (next === null || next.trim() === user.display_name) return;
    updateUser.mutate({ id: user.id, patch: { display_name: next.trim() } });
  };

  const columns: Column<UserListItem>[] = [
    {
      key: "name",
      header: "Имя",
      render: (user) => (
        // the name is the affordance: renaming is the commonest fix and does not
        // deserve a button competing for the row's right edge
        <button
          type="button"
          className={user.is_active ? "usr-name" : "usr-name usr-off"}
          onClick={() => rename(user)}
          title="Переименовать"
        >
          <strong>{user.display_name}</strong>
          <span className="usr-login">{user.login}</span>
        </button>
      ),
    },
    {
      key: "auth",
      header: "Вход",
      width: 190,
      render: (user) => (
        <span className="usr-auth">
          {/* Оба пути сразу — это и есть ответ на «узнает ли система меня»: и пароль,
              и телеграм ведут в одну строку. */}
          <span className="usr-ways">
            {user.ways_in.includes("password") ? <span className="usr-way">логин и пароль</span> : null}
            {/* Телеграм привязывается здесь же: владелец смотрит на строку человека и
                уже знает, чей это айди — спрашивать его у сверки по именам нелепо. */}
            <button
              type="button"
              className={user.telegram_user_id ? "usr-way usr-way--tg" : "usr-way usr-way--add"}
              disabled={attachTelegram.isPending}
              title={user.telegram_user_id ? `Telegram ID ${user.telegram_user_id} — нажмите, чтобы изменить` : "Привязать Telegram"}
              onClick={() => {
                const entered = window.prompt(
                  `Telegram ID для «${user.display_name}»` +
                    (user.telegram_user_id ? "\n\nПустое поле — отвязать." : ""),
                  user.telegram_user_id || "",
                );
                if (entered === null) return;
                attachTelegram.mutate({ id: user.id, telegram_user_id: entered.trim() });
              }}
            >
              {user.telegram_user_id ? "Telegram" : "+ Telegram"}
            </button>
          </span>
          {user.twin_login ? <span className="usr-twin">похож на — {user.twin_login}</span> : null}
        </span>
      ),
    },
    {
      key: "roles",
      header: "Роли",
      width: 210,
      render: (user) => (
        <span className="usr-roles">
          {user.roles.length === 0 ? <span className="usr-role">без ролей</span> : null}
          {user.roles.map((role) => (
            <span key={role} className={role === "admin" ? "usr-role usr-role--admin" : "usr-role"}>
              {role}
            </span>
          ))}
        </span>
      ),
    },
    {
      key: "status",
      header: "Статус",
      width: 96,
      nowrap: true,
      render: (user) => (user.is_active ? "активна" : "выключена"),
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
            aria-label={`Роль учётки ${user.display_name}`}
          >
            <option value="" disabled>
              роль…
            </option>
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => updateUser.mutate({ id: user.id, patch: { is_active: !user.is_active } })}
          >
            {user.is_active ? "Выкл." : "Вкл."}
          </Button>
          {user.auth === "password" ? (
            <Button size="sm" variant="ghost" onClick={() => resetPassword.mutate(user)}>
              Пароль
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => setMergeFrom(user)} disabled={mergeUsers.isPending}>
            Объединить…
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              if (window.confirm(`Удалить учётку «${user.display_name}» (${user.login})?`)) removeUser.mutate(user.id);
            }}
          >
            Удалить
          </Button>
        </span>
      ),
    },
  ];

  if (!allowed) return <div className="panel panel-pad">Страница доступна только владельцу и администраторам.</div>;
  if (usersQuery.isError) {
    return <div className="panel panel-pad">{describeApiError(usersQuery.error, "Не удалось загрузить пользователей.")}</div>;
  }

  return (
    <section className="usr">
      <PageHeader
        title="Пользователи"
        subtitle={`${users.length} учёток · логины и пароли выдаются здесь`}
        actions={
          <>
            <Button variant="secondary" onClick={() => download("/api/users/export.txt")}>
              Выгрузить доступы
            </Button>
            <Button
              variant="secondary"
              loading={checkBot.isPending}
              title="Кому бот сможет прислать «ты утверждён на роль» и «не нашлось реплик»"
              onClick={() => checkBot.mutate()}
            >
              Связь с ботом
            </Button>
            {withoutPassword.length ? (
              <Button
                variant="secondary"
                disabled={issuePasswords.isPending}
                title={`Без пароля: ${withoutPassword.map((u) => u.display_name).join(", ")}`}
                onClick={() => {
                  if (
                    window.confirm(
                      `Выдать пароли тем, у кого их нет (${withoutPassword.length})? ` +
                        `Скачается файл с новыми паролями — другого случая их увидеть не будет.\n\n` +
                        `Тем, у кого пароль есть, новый не выдаётся.`,
                    )
                  )
                    issuePasswords.mutate();
                }}
              >
                Выдать пароли · {withoutPassword.length}
              </Button>
            ) : null}
            <Button
              onClick={() => {
                const name = window.prompt("Имя и фамилия нового участника");
                if (!name || !name.trim()) return;
                createUser.mutate({ display_name: name.trim(), roles: ["dictor"] });
              }}
            >
              <Icon name="users" /> Завести учётку
            </Button>
          </>
        }
      />

      {secret ? (
        <div className="usr-secret" role="status">
          <span>
            <strong>{secret.login}</strong> — пароль <code>{secret.password}</code>
          </span>
          <span className="usr-secret-note">Показывается один раз. Скопируйте и передайте лично.</span>
          <Button size="sm" variant="ghost" onClick={() => setSecret(null)}>
            Скрыть
          </Button>
        </div>
      ) : null}

      {authorsOnly.length > 0 ? (
        <div className="panel panel-pad usr-bulk">
          <span>
            У <strong>{authorsOnly.length}</strong> {plural(authorsOnly.length, "учётки", "учёток", "учёток")} роль{" "}
            <strong>author</strong> — это право править разметку книги.
          </span>
          <Button
            size="sm"
            onClick={() => {
              if (window.confirm(`Перевести ${authorsOnly.length} учёток в dictor?`))
                bulkRoles.mutate({ ids: authorsOnly.map((user) => user.id), roles: ["dictor"] });
            }}
            disabled={bulkRoles.isPending}
          >
            Сделать их дикторами
          </Button>
        </div>
      ) : null}

      <div className="usr-head">
        <h2>Учётки</h2>
        <span className="usr-count">{found.length === users.length ? users.length : `${found.length} из ${users.length}`}</span>
        <span className="usr-spacer" />
        <label className="usr-search">
          <Icon name="search" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Имя или логин…"
            aria-label="Поиск по учёткам"
          />
        </label>
      </div>

      {botReach ? (
        <Sheet
          title="Связь с ботом"
          subtitle={`Проверено ${botReach.checked} · не достучится до ${botReach.unreachable}`}
          onClose={() => setBotReach(null)}
        >
          <p className="usr-reach-note">
            Вход через «Log in with Telegram» работает без бота — Telegram подтверждает личность сам. А писать
            человеку бот не может, пока тот сам не нажал Start у{" "}
            <strong>бота студии</strong>: так устроен Telegram. До тех, кто ниже, уведомления
            «ты утверждён на роль» и «не нашлось реплик» не дойдут.
          </p>
          <ul className="usr-reach-list">
            {botReach.items.map((item) => (
              <li key={item.telegram_user_id} className={item.reachable ? "usr-reach usr-reach--ok" : "usr-reach"}>
                <span className="usr-reach-who">
                  <strong>{item.display_name}</strong>
                  {item.telegram_name && item.telegram_name !== item.display_name ? (
                    <span className="usr-login"> в телеграме — {item.telegram_name}
                      {item.username ? ` @${item.username}` : ""}
                    </span>
                  ) : null}
                </span>
                <span className={item.reachable ? "usr-reach-state" : "usr-reach-state usr-reach-state--bad"}>
                  {item.reachable === null ? "не спросили" : item.reachable ? "пишет" : "не пишет"}
                </span>
              </li>
            ))}
          </ul>
        </Sheet>
      ) : null}

      {mergeFrom ? (
        <Sheet
          title={`Объединить «${mergeFrom.display_name}»`}
          subtitle="Эта учётка исчезнет, а все её входы, роли и история перейдут выбранной"
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
              placeholder="Имя или логин"
              aria-label="Найти учётку"
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
                        `Оставить «${candidate.display_name}» (${candidate.login}) и убрать ` +
                          `«${mergeFrom.display_name}» (${mergeFrom.login})?\n\n` +
                          `Входы, роли и история перейдут оставшейся учётке.`,
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
                  {candidate.id === mergeFrom.twin_id ? <span className="usr-merge-hint">похож по имени</span> : null}
                </button>
              </li>
            ))}
            {mergeCandidates.length === 0 ? <li className="usr-merge-empty">Никого не нашлось.</li> : null}
          </ul>
        </Sheet>
      ) : null}

      <DataTable
        columns={columns}
        rows={found}
        rowKey={(user) => user.id}
        loading={usersQuery.isLoading}
        empty="Ничего не найдено."
        aria-label="Учётки"
      />

      <div className="panel panel-pad">
        <h2>Telegram whitelist</h2>
        <p className="usr-count">
          Кого пускать по кнопке «Log in with Telegram» — {whitelist.length}{" "}
          {plural(whitelist.length, "запись", "записи", "записей")}, из них{" "}
          {whitelist.filter((item) => item.linked_login).length} привязано к учёткам. Учётка с логином и паролем
          заводится сразу здесь же; если такой человек в студии уже есть, запись привяжется к нему. Привязать
          телеграм к конкретной учётке можно и в её строке выше.
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
        title: result.created_account ? "Внесён и заведена учётка" : "Whitelist обновлён",
        detail: result.created_account ? `Логин ${result.created_account.login}` : undefined,
      });
      onForm({ telegram_user_id: "", display_name: "", role: "dictor", access_scope: "full", is_active: true });
      onSaved();
    },
    onError: (error) => pushToast({ tone: "error", title: "Whitelist не сохранён", detail: refusal(error) }),
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
          Имя и фамилия
          <input value={form.display_name} onChange={(event) => onForm({ ...form, display_name: event.target.value })} required />
        </label>
        <label>
          Роль
          <select value={form.role} onChange={(event) => onForm({ ...form, role: event.target.value })}>
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
        </label>
        <label>
          Статус
          <select value={form.is_active ? "true" : "false"} onChange={(event) => onForm({ ...form, is_active: event.target.value === "true" })}>
            <option value="true">активен</option>
            <option value="false">выключен</option>
          </select>
        </label>
        <div className="form-actions">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Сохраняю…" : "Добавить / обновить"}
          </Button>
        </div>
      </form>
      <ul className="list compact">
        {items.map((item) => (
          <li className="whitelist-row" key={item.id}>
            <strong>{item.display_name || item.telegram_user_id}</strong>
            <span>
              {item.telegram_user_id} · {item.role} · {item.is_active ? "активен" : "выключен"}
              {item.linked_login ? (
                <span className="whitelist-linked"> · учётка {item.linked_login}</span>
              ) : (
                <span className="whitelist-loose"> · ничья — вход заведёт новую учётку</span>
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
              Изменить
            </Button>
          </li>
        ))}
      </ul>
    </>
  );
}
