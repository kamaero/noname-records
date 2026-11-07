import { roleLabel } from "../v2/types";
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
  { key: "all", label: "All" },
  { key: "unreachable", label: "Unreachable" },
  { key: "nodemo", label: "No demo" },
  { key: "noroles", label: "No roles" },
];

const REFUSALS: Record<string, string> = {
  name_taken: "Another account already uses this name.",
  bad_name: "Name is empty.",
  bad_telegram_id: "Telegram ID must contain digits only.",
  telegram_taken: "This Telegram account is already linked to another account.",
  has_roles: "This voice actor has assigned roles. Reassign them first.",
  duplicate: "This voice actor already has this demo.",
  not_media: "Could not read the file. Choose an audio or video file.",
  last_admin: "This is the last administrator.",
  self_delete: "You cannot delete yourself.",
  login_taken: "An account with this Telegram ID already exists.",
  env_whitelist: "This Telegram ID is allowlisted in .env. Remove it there first, or the account will return after a restart.",
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

function telegramLink(item: { username: string }): string {
  const reserved = new Set(["joinchat", "share", "proxy", "socks", "iv", "addstickers", "addemoji", "setlanguage",
    "confirmphone", "login", "boost", "contact", "invoice", "addlist", "addtheme"]);
  return /^[A-Za-z][A-Za-z0-9_]{3,31}$/.test(item.username) && !reserved.has(item.username.toLowerCase())
    ? `https://t.me/${item.username}` : "";
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
      aria-label={on ? `Stop: ${label}` : `Listen: ${label}`}
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
  if (value === true) return <span className="dic-reach dic-reach--ok" title="The bot can send messages">reachable</span>;
  if (value === false) return <span className="dic-reach dic-reach--bad" title="Has not pressed Start in the bot">unreachable</span>;
  return <span className="dic-reach" title="Telegram has not been checked yet">—</span>;
}

export function DictorsPage({ me }: { me: MeResponse }) {
  const allowed = me.roles.includes("admin") || me.roles.includes("author") || me.is_owner_telegram;
  const isAdmin = me.roles.includes("admin") || me.is_owner_telegram;
  const canManageUsers = Boolean(me.can_manage_users);
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
      pushToast({ tone: "success", title: "Invitation copied", detail: "Paste it into a private chat with the voice actor." });
    } catch {
      pushToast({ tone: "error", title: "Could not copy", detail: "The browser denied clipboard access." });
    }
  };

  if (!allowed) return <div className="panel panel-pad">This section is available to administrators and authors.</div>;
  if (listQuery.isError) {
    return <div className="panel panel-pad">{describeApiError(listQuery.error, "Could not load voice actors.")}</div>;
  }

  const columns: Column<DictorListItem>[] = [
    {
      key: "play",
      header: <span className="dic-sr">Demo</span>,
      width: 44,
      render: (item) =>
        item.main_demo ? (
          <PlayButton demoId={item.main_demo.id} playing={player.playing} onToggle={player.toggle} label={item.name} />
        ) : null,
    },
    {
      key: "name",
      header: "Voice actor",
      render: (item) => (
        <span className="dic-name">
          <strong>{item.name}</strong>
          {item.main_demo ? (
            <span className="dic-sub">
              {item.main_demo.title} · {seconds(item.main_demo.duration_seconds)}
              {item.demos > 1 ? ` · more ${item.demos - 1}` : ""}
            </span>
          ) : (
            <span className="dic-sub">no demo</span>
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
          <a className="dic-tg" href={link} title="Last username received by the bot; updated when the actor messages the bot" target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
            @{item.username}
          </a>
        ) : (
          <span className="dic-sub" title="Ask the actor to message the bot to update their Telegram username">No chat link</span>
        );
      },
    },
    { key: "reach", header: "Bot", nowrap: true, className: "dic-wide", render: (item) => (item.telegram_user_id ? <Reach value={item.reachable} /> : null) },
    {
      key: "roles",
      header: "Roles",
      nowrap: true,
      render: (item) =>
        item.roles ? (
          <span>
            {item.roles} {plural(item.roles, "role", "roles", "roles")} in {item.books}{" "}
            {plural(item.books, "book", "books", "books")}
          </span>
        ) : (
          <span className="dic-sub">—</span>
        ),
    },
    { key: "note", header: "Note", className: "dic-wide dic-note-cell", render: (item) => <span className="dic-note">{item.note}</span> },
  ];

  return (
    <section className="dic">
      <PageHeader
        title="Voice actors"
        subtitle={`${items.length} ${plural(items.length, "voice actor", "voice actors", "voice actors")} · voices, roles and bot reachability`}
        actions={
          <>
            {bot ? (
              <Button variant="secondary" onClick={copyInvite} title="Bot invitation text to paste into a private chat">
                Copy invitation
              </Button>
            ) : null}
            {canManageUsers ? (
              <Button onClick={() => setCreating(true)}>
                <Icon name="plus" /> New voice actor
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
            placeholder="Name or @username"
            aria-label="Find voice actor"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="dic-filters" role="group" aria-label="Filter">
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
          {found.length === items.length ? "" : `found ${found.length}`}
        </span>
      </div>

      <DataTable
        columns={columns}
        rows={found}
        rowKey={(item) => item.user_id}
        onRowClick={(item) => setOpenId(item.user_id)}
        rowLabel={(item) => `Open profile: ${item.name}`}
        selectedKey={openId || null}
        loading={listQuery.isLoading}
        empty="No people found."
        aria-label="Voice actors"
      />

      {openId ? (
        <DictorSheet
          userId={openId}
          isAdmin={isAdmin}
          canManageUsers={canManageUsers}
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
  canManageUsers,
  player,
  onClose,
  onChanged,
}: {
  userId: string;
  isAdmin: boolean;
  canManageUsers: boolean;
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
      failed("Note not saved")(error);
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
        title: "Name changed",
        detail: `in cast: ${report.characters}, in author profiles: ${report.profiles}, narrator: ${report.narrators}`,
      });
      refresh();
    },
    onError: failed("Could not rename"),
  });
  const star = useMutation({
    mutationFn: (demoId: string) => apiPostJson(`/api/dictors/${encodeURIComponent(userId)}/main-demo`, { demo_id: demoId }),
    onSuccess: refresh,
    onError: failed("Could not set primary demo"),
  });
  const removeDemo = useMutation({
    mutationFn: (demoId: string) => apiDelete(`/api/dictors/demos/${encodeURIComponent(demoId)}`),
    onSuccess: refresh,
    onError: failed("Could not delete demo"),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiPostForm(`/api/dictors/${encodeURIComponent(userId)}/demos`, form);
    },
    onSuccess: () => {
      pushToast({ tone: "success", title: "Demo added" });
      refresh();
    },
    onError: failed("Demo upload failed"),
  });
  const remove = useMutation({
    mutationFn: () => apiDelete(`/api/dictors/${encodeURIComponent(userId)}`),
    onSuccess: () => {
      pushToast({ tone: "success", title: "Voice actor deleted" });
      onChanged();
      onClose();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.errorCode === "has_roles") {
        const roles = (error.payload.detail as { book: string; role: string }[] | undefined) || [];
        pushToast({
          tone: "error",
          title: "Reassign roles first",
          detail: roles.map((r) => `${roleLabel(r.role)} («${r.book}»)`).join(", "),
          durationMs: 9000,
        });
        return;
      }
      failed("Could not delete")(error);
    },
  });

  if (cardQuery.isError) {
    return (
      <Sheet title="Voice actor" onClose={onClose}>
        <p>{describeApiError(cardQuery.error, "Could not open profile.")}</p>
      </Sheet>
    );
  }
  if (!card) {
    return (
      <Sheet title="Voice actor" onClose={onClose}>
        <p className="dic-sub">Loading…</p>
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
            <a href={link} title="Last username received by the bot; updated when the actor messages the bot" target="_blank" rel="noreferrer">@{card.username}</a>
          ) : (
            <span title="Ask the actor to set a Telegram username and message the bot to update it">No username known to the bot — no chat link</span>
          )}
          {card.telegram_user_id ? <Reach value={card.reachable} /> : null}
        </span>
      }
      onClose={onClose}
    >
      <div className="dic-card">
        <div className="dic-card-cta">
          <Button variant="primary" onClick={() => setAssigning(true)}>Assign to role</Button>
        </div>
        {assigning ? (
          <DictorAssignDialog name={card.name} onClose={() => setAssigning(false)} onAssigned={refresh} />
        ) : null}
        {canManageUsers ? (
          <section className="dic-block">
            <h3>Name</h3>
            <form
              className="dic-rename"
              onSubmit={(event) => {
                event.preventDefault();
                const value = name.trim();
                if (!value || value === card.name) return;
                if (window.confirm(`Rename «${card.name}» to «${value}»? This updates the name in all book casts, author profiles, narrator assignments and recordings.`)) {
                  rename.mutate(value);
                }
              }}
            >
              <input value={name} aria-label="Full name" onChange={(event) => setName(event.target.value)} />
              <Button type="submit" variant="secondary" size="sm" loading={rename.isPending} disabled={name.trim() === card.name}>
                Rename
              </Button>
            </form>
          </section>
        ) : null}

        <section className="dic-block">
          <h3>
            Demo <span className="dic-sub">{card.demos.length || ""}</span>
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
                      {demo.trimmed ? " · trimmed to 2 min" : ""}
                    </span>
                  </span>
                  {isAdmin ? (
                    <span className="dic-demo-actions">
                      <button
                        type="button"
                        className={demo.id === card.main_demo_id ? "dic-star is-on" : "dic-star"}
                        aria-pressed={demo.id === card.main_demo_id}
                        title="Primary demo; played by ▶ in the list"
                        onClick={() => star.mutate(demo.id)}
                      >
                        ★
                      </button>
                      <button
                        type="button"
                        className="dic-x"
                        title="Delete demo"
                        aria-label={`Delete demo «${demo.title}»`}
                        onClick={() => {
                          if (window.confirm(`Delete demo «${demo.title}»?`)) removeDemo.mutate(demo.id);
                        }}
                      >
                        <Icon name="close" />
                      </button>
                    </span>
                  ) : demo.id === card.main_demo_id ? (
                    <span className="dic-star is-on" aria-label="primary">★</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="dic-sub">No demo yet.</p>
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
                <Icon name="upload" /> Upload demo
              </Button>
              <p className="dic-hint">Audio or video: the first two minutes are saved as an MP3 demo.</p>
            </>
          ) : null}
        </section>

        <section className="dic-block">
          <h3>Roles</h3>
          {card.roles.length ? (
            <ul className="dic-roles">
              {card.roles.map((role) => (
                <li key={`${role.book_id}:${role.role}`}>
                  <span className="dic-book">{role.book}</span>
                  <strong>{roleLabel(role.role)}</strong>
                  <span className={role.state === "approved" ? "dic-state" : "dic-state dic-state--proposed"}>
                    {role.state === "approved" ? "approved" : "suggested"}
                  </span>
                  {role.recorded ? <span className="dic-state dic-state--rec">recorded</span> : null}
                  <DeadlineChip deadline={role.deadline} />
                  {isAdmin && role.deadline ? <DeadlineActions deadlineId={role.deadline.id} onChanged={refresh} /> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="dic-sub">No roles yet.</p>
          )}
        </section>

        {card.links.length ? (
          <section className="dic-block">
            <h3>Portfolio</h3>
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
            <h3>Reassignments</h3>
            <ul className="dic-recasts">
              {card.recasts.map((r, index) => (
                <li key={index}>
                  <strong>{roleLabel(r.role_name)}</strong>: {r.from_actor} → {r.to_actor}
                  {r.comment ? <span className="dic-sub"> · {r.comment}</span> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <section className="dic-block">
          <h3>
            Note{" "}
            <span className="dic-sub" aria-live="polite">
              {noteState === "saving" ? "saving…" : noteState === "saved" ? "saved" : ""}
            </span>
          </h3>
          <textarea
            className="dic-note-edit"
            rows={4}
            value={note ?? ""}
            placeholder="Voice quality, casting type, collaborators…"
            aria-label="Voice actor note"
            onChange={(event) => {
              setNoteState("");
              setNote(event.target.value);
            }}
          />
        </section>

        {canManageUsers ? (
          <section className="dic-block dic-danger">
            <Button
              variant="danger"
              size="sm"
              loading={remove.isPending}
              onClick={() => {
                if (window.confirm(`Delete voice actor «${card.name}»? This removes their account, Telegram sign-in, profile and demos. This cannot be undone.`)) {
                  remove.mutate();
                }
              }}
            >
              Delete voice actor
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
        title: "Voice actor created",
        detail: made.password ? `Username and password are on the Users page. Password: ${made.password}` : "Sign in with Telegram.",
        durationMs: made.password ? 20000 : 4200,
      });
      onCreated(made.user_id);
    },
    onError: (err) => setError(refusal(err, "Could not create voice actor.")),
  });
  return (
    <Dialog
      title="New voice actor"
      subtitle="Creates a voice actor account. With a Telegram ID, they can sign in through Telegram without a password."
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>Create</Button>
        </>
      }
    >
      <div className="form-grid">
        <Field label="Full name" required error={error}>
          {(props) => <input {...props} data-autofocus value={name} onChange={(e) => { setError(""); setName(e.target.value); }} />}
        </Field>
        <Field label="Telegram id" hint="Digits only; optional">
          {(props) => <input {...props} inputMode="numeric" value={telegramId} onChange={(e) => setTelegramId(e.target.value)} />}
        </Field>
        <Field label="Telegram username" hint="Without @; optional">
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
      pushToast({ tone: "success", title: "Deadline extended", detail: result.notified ? "The voice actor was notified of the new date." : "The bot could not reach them; contact the voice actor directly." });
      setDate("");
      onChanged();
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not extend deadline", detail: describeApiError(error, "The server rejected the deadline.") }),
  });
  const close = useMutation({
    mutationFn: () => apiPostJson(`/api/deadlines/${encodeURIComponent(deadlineId)}/close`, {}),
    onSuccess: () => {
      pushToast({ tone: "success", title: "Deadline closed" });
      onChanged();
    },
    onError: (error) => pushToast({ tone: "error", title: "Could not close", detail: describeApiError(error, "") }),
  });
  const busy = extend.isPending || close.isPending;
  return (
    <span className="dic-deadline-actions">
      {[1, 3, 7].map((days) => (
        <button key={days} type="button" className="dic-mini" disabled={busy} onClick={() => extend.mutate({ days })}>
          +{days} days
        </button>
      ))}
      <input
        type="date"
        className="dic-mini-date"
        aria-label="Extend to date"
        value={date}
        onChange={(event) => setDate(event.target.value)}
      />
      {date ? (
        <button type="button" className="dic-mini" disabled={busy} onClick={() => extend.mutate({ date })}>
          to date
        </button>
      ) : null}
      <button
        type="button"
        className="dic-mini"
        disabled={busy}
        onClick={() => {
          if (window.confirm("Close this deadline? Reminders will stop.")) close.mutate();
        }}
      >
        close
      </button>
    </span>
  );
}
