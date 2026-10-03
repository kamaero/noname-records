import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Icon } from "../Icon";
import type { CastRow } from "../../viewModels/booksCast";
import "./ActorPicker.css";

/* Выбор актёра роли (план 2б): список дикторов с поиском и ▶ главного демо вместо поля
   с подсказками. Сохраняет ту же строку, что поле раньше («Имя» или «Имя?»), — поэтому
   голоса, перенос по циклу, письма и рассказчик работают как прежде. Список рисуется
   поверх страницы: таблица каста прокручивается внутри себя и обрезала бы его. */

export type PickerDictor = {
  user_id: string;
  name: string;
  reachable: boolean | null;
  has_telegram: boolean;
  roles: number;
  main_demo: { id: string; title: string; duration_seconds: number } | null;
  demos: number;
  note: string;
};

function fold(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е");
}

function norm(name: string): string {
  return fold(name.replace(/\?+\s*$/, "")).split(/\s+/).filter(Boolean).sort().join(" ");
}

type Props = {
  row: CastRow;
  rows: CastRow[];
  dictors: PickerDictor[];
  canListen: boolean;
  /** агент только предлагает: сервер сам дописывает «?» */
  proposeOnly: boolean;
  canCreate: boolean;
  canRecast: boolean;
  saving: boolean;
  onCommit: (value: string, chosen: PickerDictor | null) => void;
  onCreate: (name: string) => void;
  onRecast: () => void;
};

export function ActorPicker({ row, rows, dictors, canListen, proposeOnly, canCreate, canRecast, saving, onCommit, onCreate, onRecast }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [place, setPlace] = useState<{ top: number; left: number; width: number; up: boolean }>({ top: 0, left: 0, width: 360, up: false });
  const [playing, setPlaying] = useState("");
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const value = row.actor_name || "";

  useLayoutEffect(() => {
    if (!open || !button.current) return;
    const rect = button.current.getBoundingClientRect();
    const width = Math.min(380, window.innerWidth - 16);
    const up = rect.bottom + 420 > window.innerHeight && rect.top > 420;
    setPlace({ top: up ? rect.top - 8 : rect.bottom + 6, left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)), width, up });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!panel.current?.contains(event.target as Node) && !button.current?.contains(event.target as Node)) setOpen(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
        button.current?.focus();
      }
    };
    // Позиция считается один раз: прокрутили таблицу или окно — строка уехала, и кнопки
    // списка сохранили бы актёра не в ту роль, на которую смотрят (ревью 02.10). Закрываем.
    const scrolled = (event: Event) => {
      if (!panel.current?.contains(event.target as Node)) setOpen(false);
    };
    const resized = () => setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", key, true);
    window.addEventListener("scroll", scrolled, true);
    window.addEventListener("resize", resized);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("scroll", scrolled, true);
      window.removeEventListener("resize", resized);
    };
  }, [open]);

  // Строка таблицы размонтируется при прокрутке (каст «окнами») и при уходе со страницы —
  // демо не должно играть дальше без кнопки, которой его остановить (ревью 02.10).
  useEffect(() => () => audio.current?.pause(), []);

  useEffect(() => {
    if (open) return;
    audio.current?.pause();
    setPlaying("");
    setQuery("");
  }, [open]);

  // Кто уже занят в этой книге — «одна роль на актёра» (договорённость с автором 26.09).
  const busyIn = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const other of rows) {
      if (other.character_id === row.character_id || !other.actor_name) continue;
      const key = norm(other.actor_name);
      map.set(key, [...(map.get(key) || []), other.name]);
    }
    return map;
  }, [rows, row.character_id]);

  const found = useMemo(() => {
    const needle = fold(query.trim());
    return dictors.filter((d) => !needle || fold(d.name).includes(needle));
  }, [dictors, query]);
  const LIMIT = 60;
  const exact = dictors.some((d) => norm(d.name) === norm(query));

  const toggle = (demoId: string) => {
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.onended = () => setPlaying("");
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

  const commit = (text: string, chosen: PickerDictor | null) => {
    setOpen(false);
    button.current?.focus();
    // Тот же актёр — не повторять голос, тост и приглашение (прежнее поле молчало так же).
    if (text.trim() === value.trim()) return;
    onCommit(text, chosen);
  };

  return (
    <>
      <button
        ref={button}
        type="button"
        className={value ? "actor-pick" : "actor-pick is-empty"}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Актёр роли ${row.name}: ${value || "не назначен"}`}
        disabled={saving}
        onClick={() => setOpen(!open)}
      >
        <span className="actor-pick-name">{value || "назначить"}</span>
        <Icon name="chevron" />
      </button>
      {open
        ? createPortal(
            <div
              ref={panel}
              className="actor-pop"
              role="dialog"
              aria-label={`Актёр роли ${row.name}`}
              style={{ top: place.top, left: place.left, width: place.width, transform: place.up ? "translateY(-100%)" : undefined }}
            >
              <label className="actor-pop-search">
                <Icon name="search" />
                <input autoFocus type="search" value={query} placeholder="Найти диктора" aria-label="Найти диктора"
                  onChange={(e) => setQuery(e.target.value)} />
              </label>
              <ul className="actor-pop-list">
                {found.slice(0, LIMIT).map((d) => {
                  const taken = busyIn.get(norm(d.name));
                  const current = norm(value) === norm(d.name);
                  return (
                    <li key={d.user_id} className={current ? "is-current" : ""} title={d.note || undefined}>
                      {canListen && d.main_demo ? (
                        <button type="button" className={playing === d.main_demo.id ? "actor-pop-play is-on" : "actor-pop-play"}
                          aria-label={`Слушать демо: ${d.name}`} onClick={() => toggle(d.main_demo!.id)}>
                          {playing === d.main_demo.id ? <span className="actor-pop-stop" /> : <Icon name="play" />}
                        </button>
                      ) : (
                        <span className="actor-pop-play is-none" aria-hidden="true" />
                      )}
                      <span className="actor-pop-who">
                        <strong>{d.name}</strong>
                        <span className="actor-pop-sub">
                          {d.reachable === true ? "на связи" : d.has_telegram ? "не на связи" : "без Telegram"}
                          {canListen && d.demos > 1 ? ` · демо: ${d.demos}` : ""}
                          {taken ? <span className="actor-pop-taken"> · уже {taken.join(", ")} в этой книге</span> : null}
                        </span>
                      </span>
                      <span className="actor-pop-actions">
                        <button type="button" onClick={() => commit(`${d.name}?`, d)}>{proposeOnly ? "Предложить" : "На пробу"}</button>
                        {proposeOnly ? null : (
                          <button type="button" className="is-main" onClick={() => commit(d.name, d)}>Утвердить</button>
                        )}
                      </span>
                    </li>
                  );
                })}
                {found.length === 0 ? <li className="actor-pop-empty">Дикторов не нашлось.</li> : null}
                {found.length > LIMIT ? (
                  <li className="actor-pop-empty">Показаны {LIMIT} из {found.length} — уточните поиск.</li>
                ) : null}
              </ul>
              <div className="actor-pop-foot">
                {query.trim() && !exact ? (
                  <>
                    <button type="button" onClick={() => commit(query.trim(), null)}>
                      Записать «{query.trim()}» строкой
                    </button>
                    {canCreate ? (
                      <button type="button" onClick={() => { setOpen(false); onCreate(query.trim()); }}>
                        Создать диктора «{query.trim()}»
                      </button>
                    ) : (
                      <span className="actor-pop-hint">нет учётки — письмо не дойдёт</span>
                    )}
                  </>
                ) : null}
                {canRecast ? (
                  <button type="button" onClick={() => { setOpen(false); onRecast(); }}>Переназначить по циклу…</button>
                ) : null}
                {value ? (
                  <button type="button" className="is-danger" onClick={() => commit("", null)}>Снять актёра</button>
                ) : null}
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
