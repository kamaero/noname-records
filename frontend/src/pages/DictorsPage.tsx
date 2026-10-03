import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { ApiError, apiDelete, apiGet, apiPatchJson, apiPostForm, apiPostJson, describeApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { useToast } from "../components/ToastProvider";
import { Button, DataTable, Dialog, Field, PageHeader, Sheet, type Column } from "../ui";
import { plural } from "../v2/useIsMobile";
import type { DictorCard, DictorListItem, DictorsListResponse, MeResponse, PublicAuthConfigResponse } from "../types";
import { DeadlineChip } from "../components/DeadlineChip";
import { DictorAssignDialog } from "./DictorAssignDialog";
import { inviteText } from "../utils/invite";
import "./DictorsPage.css";

/* ============================================================
   Дикторы — творческий пульт: кто есть, как звучит, где занят.
   «Пользователи» — технический доступ; здесь — голоса и роли.
   Видят админ и автор (сервер отдаёт остальным 403).
   ============================================================ */

type Filter = "all" | "unreachable" | "nodemo" | "noroles";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "Все" },
  { key: "unreachable", label: "Не на связи" },
  { key: "nodemo", label: "Без демо" },
  { key: "noroles", label: "Без ролей" },
];

const REFUSALS: Record<string, string> = {
  name_taken: "Такое имя уже носит другая учётка.",
  bad_name: "Имя пустое.",
  bad_telegram_id: "Telegram id — только цифры.",
  telegram_taken: "Этот Telegram уже привязан к другой учётке.",
  has_roles: "За диктором есть роли — сначала переназначьте их.",
  duplicate: "Такое демо у диктора уже есть.",
  not_media: "Не получилось разобрать файл — нужен аудио или видео.",
  last_admin: "Это последний администратор.",
  self_delete: "Себя удалить нельзя.",
  login_taken: "Учётка с этим Telegram id уже есть.",
  env_whitelist: "Этот Telegram прописан в белом списке в .env — сначала уберите его оттуда, иначе после перезапуска человек вернётся.",
};

function refusal(error: unknown, fallback: string): string {
  if (error instanceof ApiError && REFUSALS[error.errorCode]) return REFUSALS[error.errorCode];
  return describeApiError(error, fallback);
}

function fold(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е");
}

function seconds(value: number): string {
  const total = Math.round(value || 0);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function telegramLink(item: { telegram_user_id: string; username: string }): string {
  if (item.username) return `https://t.me/${item.username}`;
  return item.telegram_user_id ? `tg://user?id=${item.telegram_user_id}` : "";
}


/** Один плеер на страницу: новое демо останавливает прежнее. */
function useDemoPlayer() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string>("");
  useEffect(() => () => audio.current?.pause(), []);
  const toggle = (demoId: string) => {
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.onended = () => setPlaying("");
      audio.current.onerror = () => setPlaying("");
    }
    if (playing === demoId) {
      audio.current.pause();
      setPlaying("");
      return;
    }
    audio.current.src = `/api/dictors/demos/${encodeURIComponent(demoId)}/audio`;
    void audio.current.play().catch(() => setPlaying(""));
    setPlaying(demoId);
  };
  return { playing, toggle };
}

function PlayButton({ demoId, playing, onToggle, label }: { demoId: string; playing: string; onToggle: (id: string) => void; label: string }) {
  const on = playing === demoId;
  return (
    <button
      type="button"
      className={on ? "dic-play is-on" : "dic-play"}
      aria-label={on ? `Остановить: ${label}` : `Слушать: ${label}`}
      aria-pressed={on}
      onClick={(event) => {
        event.stopPropagation();
        onToggle(demoId);
      }}
    >
      {on ? <span className="dic-stop" aria-hidden="true" /> : <Icon name="play" />}
    </button>
  );
}

function Reach({ value }: { value: boolean | null }) {
  if (value === true) return <span className="dic-reach dic-reach--ok" title="Бот может писать">на связи</span>;
  if (value === false) return <span className="dic-reach dic-reach--bad" title="Не нажал Start у бота">не на связи</span>;
  return <span className="dic-reach" title="Ещё не спрашивали Telegram">—</span>;
}

export function DictorsPage({ me }: { me: MeResponse }) {
  const allowed = me.roles.includes("admin") || me.roles.includes("author") || me.is_owner_telegram;
  const isAdmin = me.roles.includes("admin") || me.is_owner_telegram;
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const player = useDemoPlayer();
  // ?q= — ссылка из ленты «Пробы»: сразу найти диктора
  const [params] = useSearchParams();
  const [query, setQuery] = useState(() => params.get("q") || "");
  const [filter, setFilter] = useState<Filter>("all");
  const [openId, setOpenId] = useState<string>("");
  const [creating, setCreating] = useState(false);

  const listQuery = useQuery({
    queryKey: ["dictors"],
    queryFn: () => apiGet<DictorsListResponse>("/api/dictors"),
    enabled: allowed,
  });
  const configQuery = useQuery({
    queryKey: ["public-auth-config"],
    queryFn: () => apiGet<PublicAuthConfigResponse>("/api/public/auth-config"),
    retry: false,
  });
  const bot = configQuery.data?.telegram_bot_username || "";

  const items = listQuery.data?.items || [];
  const found = useMemo(() => {
    const needle = fold(query.trim());
    return items.filter((item) => {
      if (needle && !fold(`${item.name} ${item.username}`).includes(needle)) return false;
      if (filter === "unreachable") return item.reachable !== true;
      if (filter === "nodemo") return item.demos === 0;
      if (filter === "noroles") return item.roles === 0;
      return true;
    });
  }, [items, query, filter]);

  const copyInvite = async () => {
    try {
      await navigator.clipboard.writeText(inviteText(bot, configQuery.data?.studio_name || ""));
      pushToast({ tone: "success", title: "Приглашение скопировано", detail: "Вставьте его в личный чат с диктором." });
    } catch {
      pushToast({ tone: "error", title: "Не удалось скопировать", detail: "Браузер не дал доступ к буферу обмена." });
    }
  };

  if (!allowed) return <div className="panel panel-pad">Раздел доступен администратору и автору.</div>;
  if (listQuery.isError) {
    return <div className="panel panel-pad">{describeApiError(listQuery.error, "Не удалось загрузить дикторов.")}</div>;
  }

  const columns: Column<DictorListItem>[] = [
    {
      key: "play",
      header: <span className="dic-sr">Демо</span>,
      width: 44,
      render: (item) =>
        item.main_demo ? (
          <PlayButton demoId={item.main_demo.id} playing={player.playing} onToggle={player.toggle} label={item.name} />
        ) : null,
    },
    {
      key: "name",
      header: "Диктор",
      render: (item) => (
        <span className="dic-name">
          <strong>{item.name}</strong>
          {item.main_demo ? (
            <span className="dic-sub">
              {item.main_demo.title} · {seconds(item.main_demo.duration_seconds)}
              {item.demos > 1 ? ` · ещё ${item.demos - 1}` : ""}
            </span>
          ) : (
            <span className="dic-sub">без демо</span>
          )}
        </span>
      ),
    },
    {
      key: "tg",
      header: "Telegram",
      nowrap: true,
      className: "dic-wide",
      render: (item) => {
        const link = telegramLink(item);
        return link ? (
          <a className="dic-tg" href={link} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
            {item.username ? `@${item.username}` : "чат"}
          </a>
        ) : (
          <span className="dic-sub">—</span>
        );
      },
    },
    { key: "reach", header: "Бот", nowrap: true, className: "dic-wide", render: (item) => (item.telegram_user_id ? <Reach value={item.reachable} /> : null) },
    {
      key: "roles",
      header: "Роли",
      nowrap: true,
      render: (item) =>
        item.roles ? (
          <span>
            {item.roles} {plural(item.roles, "роль", "роли", "ролей")} в {item.books}{" "}
            {plural(item.books, "книге", "книгах", "книгах")}
          </span>
        ) : (
          <span className="dic-sub">—</span>
        ),
    },
    { key: "note", header: "Заметка", className: "dic-wide dic-note-cell", render: (item) => <span className="dic-note">{item.note}</span> },
  ];

  return (
    <section className="dic">
      <PageHeader
        title="Дикторы"
        subtitle={`${items.length} ${plural(items.length, "диктор", "диктора", "дикторов")} · голоса, роли и связь с ботом`}
        actions={
          <>
            {bot ? (
              <Button variant="secondary" onClick={copyInvite} title="Текст приглашения в бота — вставить в личный чат">
                Скопировать приглашение
              </Button>
            ) : null}
            {isAdmin ? (
              <Button onClick={() => setCreating(true)}>
                <Icon name="plus" /> Новый диктор
              </Button>
            ) : null}
          </>
        }
      />

      <div className="dic-head">
        <label className="dic-search">
          <Icon name="search" />
          <input
            type="search"
            value={query}
            placeholder="Имя или @username"
            aria-label="Найти диктора"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="dic-filters" role="group" aria-label="Фильтр">
          {FILTERS.map((option) => (
            <button
              key={option.key}
              type="button"
              className={filter === option.key ? "dic-chip is-on" : "dic-chip"}
              aria-pressed={filter === option.key}
              onClick={() => setFilter(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className="dic-count">
          {found.length === items.length ? "" : `найдено ${found.length}`}
        </span>
      </div>

      <DataTable
        columns={columns}
        rows={found}
        rowKey={(item) => item.user_id}
        onRowClick={(item) => setOpenId(item.user_id)}
        rowLabel={(item) => `Открыть карточку: ${item.name}`}
        selectedKey={openId || null}
        loading={listQuery.isLoading}
        empty="Никого не нашлось."
        aria-label="Дикторы"
      />

      {openId ? (
        <DictorSheet
          userId={openId}
          isAdmin={isAdmin}
          player={player}
          onClose={() => setOpenId("")}
          onChanged={() => queryClient.invalidateQueries({ queryKey: ["dictors"] })}
        />
      ) : null}

      {creating ? (
        <CreateDialog
          onClose={() => setCreating(false)}
          onCreated={(userId) => {
            setCreating(false);
            void queryClient.invalidateQueries({ queryKey: ["dictors"] });
            setOpenId(userId);
          }}
        />
      ) : null}
    </section>
  );
}

function DictorSheet({
  userId,
  isAdmin,
  player,
  onClose,
  onChanged,
}: {
  userId: string;
  isAdmin: boolean;
  player: ReturnType<typeof useDemoPlayer>;
  onClose: () => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const cardQuery = useQuery({
    queryKey: ["dictor", userId],
    queryFn: () => apiGet<DictorCard>(`/api/dictors/${encodeURIComponent(userId)}`),
  });
  const card = cardQuery.data;
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["dictor", userId] });
    onChanged();
  };
  const failed = (title: string) => (error: unknown) => pushToast({ tone: "error", title, detail: refusal(error, title) });

  const [note, setNote] = useState<string | null>(null);
  const [noteState, setNoteState] = useState<"" | "saving" | "saved">("");
  const [name, setName] = useState("");
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [assigning, setAssigning] = useState(false);

  useEffect(() => {
    if (card) {
      setNote((current) => (current === null ? card.note : current));
      setName(card.name);
    }
  }, [card]);

  const saveNote = useMutation({
    mutationFn: (value: string) => apiPatchJson(`/api/dictors/${encodeURIComponent(userId)}/note`, { note: value }),
    onMutate: () => setNoteState("saving"),
    onSuccess: () => {
      setNoteState("saved");
      onChanged();
    },
    onError: (error) => {
      setNoteState("");
      failed("Заметка не сохранилась")(error);
    },
  });

  // Несохранённый текст: закрыли карточку раньше, чем сработал таймер, — досохраняем.
  const pending = useRef<string | null>(null);
  const saveRef = useRef(saveNote.mutate);
  saveRef.current = saveNote.mutate;
  useEffect(() => {
    if (note === null || !card || note === card.note) return;
    pending.current = note;
    const timer = window.setTimeout(() => {
      pending.current = null;
      saveRef.current(note);
    }, 800);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note]);
  useEffect(
    () => () => {
      if (pending.current !== null) {
        void apiPatchJson(`/api/dictors/${encodeURIComponent(userId)}/note`, { note: pending.current }).then(onChanged);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const rename = useMutation({
    mutationFn: (value: string) => apiPatchJson<{ characters: number; profiles: number; narrators: number }>(
      `/api/dictors/${encodeURIComponent(userId)}`, { name: value }),
    onSuccess: (report) => {
      pushToast({
        tone: "success",
        title: "Имя сменилось",
        detail: `в касте: ${report.characters}, в профилях автора: ${report.profiles}, рассказчик: ${report.narrators}`,
      });
      refresh();
    },
    onError: failed("Не удалось переименовать"),
  });
  const star = useMutation({
    mutationFn: (demoId: string) => apiPostJson(`/api/dictors/${encodeURIComponent(userId)}/main-demo`, { demo_id: demoId }),
    onSuccess: refresh,
    onError: failed("Не удалось сделать главным"),
  });
  const removeDemo = useMutation({
    mutationFn: (demoId: string) => apiDelete(`/api/dictors/demos/${encodeURIComponent(demoId)}`),
    onSuccess: refresh,
    onError: failed("Не удалось удалить демо"),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiPostForm(`/api/dictors/${encodeURIComponent(userId)}/demos`, form);
    },
    onSuccess: () => {
      pushToast({ tone: "success", title: "Демо добавлено" });
      refresh();
    },
    onError: failed("Демо не загрузилось"),
  });
  const remove = useMutation({
    mutationFn: () => apiDelete(`/api/dictors/${encodeURIComponent(userId)}`),
    onSuccess: () => {
      pushToast({ tone: "success", title: "Диктор удалён" });
      onChanged();
      onClose();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.errorCode === "has_roles") {
        const roles = (error.payload.detail as { book: string; role: string }[] | undefined) || [];
        pushToast({
          tone: "error",
          title: "Сначала переназначьте роли",
          detail: roles.map((r) => `${r.role} («${r.book}»)`).join(", "),
          durationMs: 9000,
        });
        return;
      }
      failed("Не удалось удалить")(error);
    },
  });

  if (cardQuery.isError) {
    return (
      <Sheet title="Диктор" onClose={onClose}>
        <p>{describeApiError(cardQuery.error, "Не удалось открыть карточку.")}</p>
      </Sheet>
    );
  }
  if (!card) {
    return (
      <Sheet title="Диктор" onClose={onClose}>
        <p className="dic-sub">Загружаю…</p>
      </Sheet>
    );
  }

  const link = telegramLink(card);
  return (
    <Sheet
      title={card.name}
      subtitle={
        <span className="dic-card-sub">
          {link ? (
            <a href={link} target="_blank" rel="noreferrer">{card.username ? `@${card.username}` : "чат в Telegram"}</a>
          ) : (
            "без Telegram"
          )}
          {card.telegram_user_id ? <Reach value={card.reachable} /> : null}
        </span>
      }
      onClose={onClose}
    >
      <div className="dic-card">
        <div className="dic-card-cta">
          <Button variant="primary" onClick={() => setAssigning(true)}>Назначить на роль</Button>
        </div>
        {assigning ? (
          <DictorAssignDialog name={card.name} onClose={() => setAssigning(false)} onAssigned={refresh} />
        ) : null}
        {isAdmin ? (
          <section className="dic-block">
            <h3>Имя</h3>
            <form
              className="dic-rename"
              onSubmit={(event) => {
                event.preventDefault();
                const value = name.trim();
                if (!value || value === card.name) return;
                if (window.confirm(`Переименовать «${card.name}» в «${value}»?\n\nИмя сменится в касте всех книг, в профиле автора, у рассказчика и в записях.`)) {
                  rename.mutate(value);
                }
              }}
            >
              <input value={name} aria-label="Фамилия Имя" onChange={(event) => setName(event.target.value)} />
              <Button type="submit" variant="secondary" size="sm" loading={rename.isPending} disabled={name.trim() === card.name}>
                Переименовать
              </Button>
            </form>
          </section>
        ) : null}

        <section className="dic-block">
          <h3>
            Демо <span className="dic-sub">{card.demos.length || ""}</span>
          </h3>
          {card.demos.length ? (
            <ul className="dic-demos">
              {card.demos.map((demo) => (
                <li key={demo.id} className={demo.id === card.main_demo_id ? "dic-demo is-main" : "dic-demo"}>
                  <PlayButton demoId={demo.id} playing={player.playing} onToggle={player.toggle} label={demo.title} />
                  <span className="dic-demo-title">
                    {demo.title}
                    <span className="dic-sub">
                      {seconds(demo.duration_seconds)}
                      {demo.trimmed ? " · обрезано до 2 мин" : ""}
                    </span>
                  </span>
                  {isAdmin ? (
                    <span className="dic-demo-actions">
                      <button
                        type="button"
                        className={demo.id === card.main_demo_id ? "dic-star is-on" : "dic-star"}
                        aria-pressed={demo.id === card.main_demo_id}
                        title="Главное демо — его играет ▶ в списке"
                        onClick={() => star.mutate(demo.id)}
                      >
                        ★
                      </button>
                      <button
                        type="button"
                        className="dic-x"
                        title="Удалить демо"
                        aria-label={`Удалить демо «${demo.title}»`}
                        onClick={() => {
                          if (window.confirm(`Удалить демо «${demo.title}»?`)) removeDemo.mutate(demo.id);
                        }}
                      >
                        <Icon name="close" />
                      </button>
                    </span>
                  ) : demo.id === card.main_demo_id ? (
                    <span className="dic-star is-on" aria-label="главное">★</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="dic-sub">Демо пока нет.</p>
          )}
          {isAdmin ? (
            <>
              <input
                ref={fileInput}
                type="file"
                accept="audio/*,video/*"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) upload.mutate(file);
                  event.target.value = "";
                }}
              />
              <Button variant="secondary" size="sm" loading={upload.isPending} onClick={() => fileInput.current?.click()}>
                <Icon name="upload" /> Загрузить демо
              </Button>
              <p className="dic-hint">Аудио или видео: в демо уходят первые две минуты, MP3.</p>
            </>
          ) : null}
        </section>

        <section className="dic-block">
          <h3>Роли</h3>
          {card.roles.length ? (
            <ul className="dic-roles">
              {card.roles.map((role) => (
                <li key={`${role.book_id}:${role.role}`}>
                  <span className="dic-book">{role.book}</span>
                  <strong>{role.role}</strong>
                  <span className={role.state === "approved" ? "dic-state" : "dic-state dic-state--proposed"}>
                    {role.state === "approved" ? "утверждён" : "предложен"}
                  </span>
                  {role.recorded ? <span className="dic-state dic-state--rec">записано</span> : null}
                  <DeadlineChip deadline={role.deadline} />
                  {isAdmin && role.deadline ? <DeadlineActions deadlineId={role.deadline.id} onChanged={refresh} /> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="dic-sub">Ролей пока нет.</p>
          )}
        </section>

        {card.links.length ? (
          <section className="dic-block">
            <h3>Портфолио</h3>
            <ul className="dic-links">
              {card.links.map((item) => (
                <li key={item.id}>
                  <a href={item.url} target="_blank" rel="noreferrer">{item.title || item.url}</a>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {card.recasts.length ? (
          <section className="dic-block">
            <h3>Рекасты</h3>
            <ul className="dic-recasts">
              {card.recasts.map((r, index) => (
                <li key={index}>
                  <strong>{r.role_name}</strong>: {r.from_actor} → {r.to_actor}
                  {r.comment ? <span className="dic-sub"> · {r.comment}</span> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <section className="dic-block">
          <h3>
            Заметка{" "}
            <span className="dic-sub" aria-live="polite">
              {noteState === "saving" ? "сохраняю…" : noteState === "saved" ? "сохранено" : ""}
            </span>
          </h3>
          <textarea
            className="dic-note-edit"
            rows={4}
            value={note ?? ""}
            placeholder="Тембр, типаж, с кем удобно работать…"
            aria-label="Заметка о дикторе"
            onChange={(event) => {
              setNoteState("");
              setNote(event.target.value);
            }}
          />
        </section>

        {isAdmin ? (
          <section className="dic-block dic-danger">
            <Button
              variant="danger"
              size="sm"
              loading={remove.isPending}
              onClick={() => {
                if (window.confirm(`Удалить диктора «${card.name}»?\n\nУйдут учётка, вход через Telegram, карточка и демо. Отменить нельзя.`)) {
                  remove.mutate();
                }
              }}
            >
              Удалить диктора
            </Button>
          </section>
        ) : null}
      </div>
    </Sheet>
  );
}

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (userId: string) => void }) {
  const { pushToast } = useToast();
  const [name, setName] = useState("");
  const [telegramId, setTelegramId] = useState("");
  const [username, setUsername] = useState("");
  const [error, setError] = useState("");
  const create = useMutation({
    mutationFn: () =>
      apiPostJson<{ user_id: string; password: string }>("/api/dictors", {
        name,
        telegram_user_id: telegramId.trim(),
        username: username.trim(),
      }),
    onSuccess: (made) => {
      pushToast({
        tone: "success",
        title: "Диктор заведён",
        detail: made.password ? `Логин и пароль — на экране «Пользователи». Пароль: ${made.password}` : "Вход — через Telegram.",
        durationMs: made.password ? 20000 : 4200,
      });
      onCreated(made.user_id);
    },
    onError: (err) => setError(refusal(err, "Не удалось завести диктора.")),
  });
  return (
    <Dialog
      title="Новый диктор"
      subtitle="Учётка с ролью диктора. С Telegram id вход — через Telegram, без пароля."
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
          <Button loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>Завести</Button>
        </>
      }
    >
      <div className="form-grid">
        <Field label="Фамилия Имя" required error={error}>
          {(props) => <input {...props} data-autofocus value={name} onChange={(e) => { setError(""); setName(e.target.value); }} />}
        </Field>
        <Field label="Telegram id" hint="Цифры; можно оставить пустым">
          {(props) => <input {...props} inputMode="numeric" value={telegramId} onChange={(e) => setTelegramId(e.target.value)} />}
        </Field>
        <Field label="Username в Telegram" hint="Без @; необязательно">
          {(props) => <input {...props} value={username} onChange={(e) => setUsername(e.target.value)} />}
        </Field>
      </div>
    </Dialog>
  );
}

/** Продлить или закрыть срок — только админ (решение владельца 01.10). */
function DeadlineActions({ deadlineId, onChanged }: { deadlineId: string; onChanged: () => void }) {
  const { pushToast } = useToast();
  const [date, setDate] = useState("");
  const extend = useMutation({
    mutationFn: (body: { days?: number; date?: string }) =>
      apiPostJson<{ notified: boolean }>(`/api/deadlines/${encodeURIComponent(deadlineId)}/extend`, body),
    onSuccess: (result) => {
      pushToast({ tone: "success", title: "Срок продлён", detail: result.notified ? "Диктору ушло письмо с новой датой." : "Бот не достучался — сообщите диктору сами." });
      setDate("");
      onChanged();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не удалось продлить", detail: describeApiError(error, "Сервер не принял срок.") }),
  });
  const close = useMutation({
    mutationFn: () => apiPostJson(`/api/deadlines/${encodeURIComponent(deadlineId)}/close`, {}),
    onSuccess: () => {
      pushToast({ tone: "success", title: "Срок закрыт" });
      onChanged();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не удалось закрыть", detail: describeApiError(error, "") }),
  });
  const busy = extend.isPending || close.isPending;
  return (
    <span className="dic-deadline-actions">
      {[1, 3, 7].map((days) => (
        <button key={days} type="button" className="dic-mini" disabled={busy} onClick={() => extend.mutate({ days })}>
          +{days} дн.
        </button>
      ))}
      <input
        type="date"
        className="dic-mini-date"
        aria-label="Продлить до даты"
        value={date}
        onChange={(event) => setDate(event.target.value)}
      />
      {date ? (
        <button type="button" className="dic-mini" disabled={busy} onClick={() => extend.mutate({ date })}>
          до даты
        </button>
      ) : null}
      <button
        type="button"
        className="dic-mini"
        disabled={busy}
        onClick={() => {
          if (window.confirm("Закрыть срок? Напоминаний по нему больше не будет.")) close.mutate();
        }}
      >
        закрыть
      </button>
    </span>
  );
}
