import "./appShell.css";

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { NavLink, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import type { MeResponse } from "../types";
import { Icon, type IconName } from "../components/Icon";
import { ThemeToggle } from "../components/ThemeToggle";
import { OnboardingDialog } from "../components/OnboardingDialog";
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiPostJson } from "../api/client";
import { AUDITIONS_SEEN_KEY } from "../pages/AuditionsPage";
import { Sheet } from "../ui";
import { SHELL_MOBILE_QUERY, useMediaQuery } from "../hooks/useMediaQuery";

type AppShellProps = {
  me: MeResponse;
  children: ReactNode;
};

type NavItem = {
  to: string;
  label: string;
  icon: IconName;
  show: boolean;
  muted?: boolean;
  /** Which paths light this tab up (NavLink's own matching is too narrow for the book routes). */
  match: (pathname: string) => boolean;
};

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Автопоказ приветствия — раз за сессию вкладки: перезагрузка страницы не открывает
 *  окно снова, даже если сервер ещё не успел записать показ. */
function onboardingSessionKey(uid: string): string {
  // по учётке: сменил пользователя в той же вкладке — его приветствие своё
  return `noname.onboarding.autoShown.${uid}`;
}

function autoShownThisTab(uid: string): boolean {
  try {
    return window.sessionStorage.getItem(onboardingSessionKey(uid)) === "1";
  } catch {
    return false;
  }
}

function markAutoShownThisTab(uid: string) {
  try {
    window.sessionStorage.setItem(onboardingSessionKey(uid), "1");
  } catch {
    /* хранилище недоступно — остаётся флажок в памяти */
  }
}

const startsWithAny = (p: string, prefixes: string[]) => prefixes.some((prefix) => p === prefix || p.startsWith(`${prefix}/`));

/** Агент — и никто больше: носитель роли, у которого нет прав редактора.
 *
 *  Отдельной функцией, а не строкой внутри `roleAccess`, потому что страницы читают
 *  `me` через `useQuery` и до первого ответа держат `undefined` — им нужен признак от
 *  голого списка ролей, а не от целого `MeResponse`. Точное зеркало `app.auth.is_agent`
 *  на сервере: исключение из роли даёт только `admin`/`author`, telegram-identity сюда
 *  не входит. */
export function isAgentRoles(roles: string[]): boolean {
  return roles.includes("agent") && !roles.some((role) => role === "admin" || role === "author");
}

export function roleAccess(me: MeResponse) {
  const editor = me.roles.includes("admin") || me.roles.includes("author") || me.is_owner_telegram;
  const pureDictor = me.roles.includes("dictor") && !editor;
  /* Агент — кастинг-директор: каст, сценарий, запись и ASR его, Подготовка — нет.
     Роль отнимает раздел у того, у кого она единственная, а не у всякого, кто её
     коснулся, — та же форма, что у чистого диктора строкой выше. */
  const isAgent = isAgentRoles(me.roles);
  const isAdmin = me.roles.includes("admin") || me.is_owner_telegram;
  // Одна роль диктора, одна проверка: четыре роли студии — admin, author, dictor, agent.
  const canRecord = me.full_access || me.roles.includes("dictor") || me.roles.includes("admin");
  const canPrepare = me.full_access && !pureDictor && !isAgent;

  return { editor, pureDictor, isAgent, isAdmin, canPrepare, canRecord };
}

export function buildNav(me: MeResponse): { primary: NavItem[]; secondary: NavItem[] } {
  const { isAdmin, editor, isAgent, canPrepare, canRecord } = roleAccess(me);

  const primary: NavItem[] = [
    {
      to: "/",
      label: "Библиотека",
      icon: "books",
      show: me.full_access,
      match: (p) =>
        p === "/" ||
        startsWithAny(p, ["/command", "/books", "/upload"]),
    },
    { to: "/reader", label: "Подготовка", icon: "doc", show: canPrepare, match: (p) => startsWithAny(p, ["/reader", "/script"]) },
    { to: "/recording", label: "Запись", icon: "mic", show: canRecord, match: (p) => startsWithAny(p, ["/recording", "/dictor-pro", "/dictor-neo"]) },
    { to: "/asr", label: "ASR", icon: "wave", show: canRecord, match: (p) => startsWithAny(p, ["/asr"]) },
  ];
  const secondary: NavItem[] = [
    { to: "/auditions", label: "Пробы", icon: "play", show: editor || isAgent, muted: true, match: (p) => startsWithAny(p, ["/auditions"]) },
    { to: "/dictors", label: "Дикторы", icon: "mic", show: editor, muted: true, match: (p) => startsWithAny(p, ["/dictors"]) },
    { to: "/users", label: "Пользователи", icon: "users", show: isAdmin, muted: true, match: (p) => startsWithAny(p, ["/users"]) },
    { to: "/settings", label: "Настройки", icon: "settings", show: isAdmin, muted: true, match: (p) => startsWithAny(p, ["/settings"]) },
    { to: "/log", label: "Лог", icon: "log", show: isAdmin, muted: true, match: (p) => startsWithAny(p, ["/log"]) },
    { to: "/help", label: "Помощь", icon: "help", show: true, muted: true, match: (p) => startsWithAny(p, ["/help"]) },
  ];
  return { primary: primary.filter((n) => n.show), secondary: secondary.filter((n) => n.show) };
}

export function AppShell({ me, children }: AppShellProps) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const isMobile = useMediaQuery(SHELL_MOBILE_QUERY);
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState(() => params.get("q") || "");
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const autoShownRef = useRef(false);
  // куда вернуть фокус после окна, если открывшего его элемента уже нет (строка меню,
  // автопоказ): «?» в шапке на компьютере, гамбургер на телефоне
  const helpButtonRef = useRef<HTMLButtonElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const onboardingFocusFallback = useCallback(() => helpButtonRef.current || menuButtonRef.current, []);

  const { primary, secondary: secondaryBase } = buildNav(me);
  // «Пробы · 3» — новых с прошлого визита раздела (метка визита — в браузере смотрящего)
  const auditionsSeen = (() => {
    try {
      return window.localStorage.getItem(AUDITIONS_SEEN_KEY) || "";
    } catch {
      return "";
    }
  })();
  const auditionsNew = useQuery({
    queryKey: ["auditions-new", auditionsSeen],
    queryFn: () => apiGet<{ new_count: number }>(`/api/auditions/feed?since=${encodeURIComponent(auditionsSeen)}`),
    enabled: Boolean(auditionsSeen) && secondaryBase.some((n) => n.to === "/auditions"),
    staleTime: 60_000,
  });
  const newAuditions = auditionsNew.data?.new_count || 0;
  const secondary = secondaryBase.map((n) => (n.to === "/auditions" && newAuditions ? { ...n, label: `Пробы · ${newAuditions}` } : n));
  const canSearch = me.full_access;

  // ?onboarding=1 — ссылка «Первые шаги» из письма бота: окно открывается всегда, даже
  // если его уже показывали; параметр снимается, чтобы перезагрузка не открыла снова
  useEffect(() => {
    if (params.get("onboarding") !== "1") return;
    setOnboardingOpen(true);
    const next = new URLSearchParams(params);
    next.delete("onboarding");
    setParams(next, { replace: true });
  }, [params, setParams]);

  // route change closes the menu and the mobile search
  useEffect(() => {
    setMenuOpen(false);
    setSearchOpen(false);
  }, [pathname]);
  // leaving mobile closes the drawer
  useEffect(() => {
    if (!isMobile) {
      setMenuOpen(false);
      setSearchOpen(false);
    }
  }, [isMobile]);
  // the library's ?q= drives the field; clearing the URL clears the field
  useEffect(() => {
    setQuery(params.get("q") || "");
  }, [params]);

  const closeMenu = useCallback(() => setMenuOpen(false), []);
  const closeOnboarding = useCallback(() => setOnboardingOpen(false), []);

  // Когда показывать само — решает сервер (`show_onboarding`); показ фиксируется сразу,
  // в момент автопоказа. Ручное открытие из шапки ничего не фиксирует.
  useEffect(() => {
    if (!me.show_onboarding || autoShownRef.current || autoShownThisTab(me.uid)) return;
    autoShownRef.current = true;
    markAutoShownThisTab(me.uid);
    setOnboardingOpen(true);
    apiPostJson("/api/me/onboarding-shown", {}).catch(() => {
      /* не записалось — сервер покажет окно в следующий раз, это не беда */
    });
  }, [me.show_onboarding, me.uid]);

  const openOnboardingFromMenu = () => {
    setMenuOpen(false);
    setOnboardingOpen(true);
  };

  const onSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const q = query.trim();
    navigate(q ? `/?q=${encodeURIComponent(q)}` : "/");
  };

  const renderTab = (n: NavItem) => (
    <NavLink
      key={n.to}
      to={n.to}
      className={["tab", n.match(pathname) ? "is-active" : "", n.muted ? "tab-muted" : ""].filter(Boolean).join(" ")}
      aria-current={n.match(pathname) ? "page" : undefined}
    >
      <Icon name={n.icon} />
      {n.label}
    </NavLink>
  );

  const renderMenuRow = (n: NavItem) => (
    <NavLink
      key={n.to}
      to={n.to}
      className={["shell-menu-row", n.match(pathname) ? "is-active" : ""].filter(Boolean).join(" ")}
      aria-current={n.match(pathname) ? "page" : undefined}
      onClick={closeMenu}
    >
      <Icon name={n.icon} />
      <span>{n.label}</span>
    </NavLink>
  );

  const searchForm = (
    <form className={`topbar-search shell-search${isMobile ? " shell-search--mobile" : ""}`} role="search" onSubmit={onSearch}>
      <Icon name="search" />
      <input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Найти книгу…"
        aria-label="Поиск книги"
        autoFocus={isMobile}
      />
      {isMobile ? (
        <button type="button" className="shell-iconbtn shell-iconbtn--inline" aria-label="Закрыть поиск" onClick={() => setSearchOpen(false)}>
          <Icon name="close" />
        </button>
      ) : (
        <kbd>↵</kbd>
      )}
    </form>
  );

  return (
    <div className="app">
      <header className={`topbar shell-topbar${isMobile && searchOpen ? " is-searching" : ""}`}>
        <NavLink to={primary[0]?.to || "/"} className="brand shell-brand" aria-label="На главную">
          <div className="brand-mark">
            <Icon name="wave" />
          </div>
          <div className="brand-text">
            <span className="brand-name">NONAME RECORDS</span>
            <span className="brand-sub">студия озвучки книг</span>
          </div>
        </NavLink>

        {canSearch && (!isMobile || searchOpen) ? searchForm : null}

        <div className="topbar-right shell-right">
          {isMobile ? (
            <>
              {canSearch && !searchOpen ? (
                <button type="button" className="shell-iconbtn" aria-label="Поиск" onClick={() => setSearchOpen(true)}>
                  <Icon name="search" />
                </button>
              ) : null}
              <button
                ref={menuButtonRef}
                type="button"
                className="shell-iconbtn"
                aria-label="Меню"
                aria-expanded={menuOpen}
                aria-haspopup="dialog"
                onClick={() => setMenuOpen(true)}
              >
                <Icon name="menu" />
              </button>
            </>
          ) : (
            <>
              <ThemeToggle />
              <button
                ref={helpButtonRef}
                type="button"
                className="shell-iconbtn shell-helpbtn"
                aria-label="Как тут всё устроено"
                title="Как тут всё устроено"
                aria-haspopup="dialog"
                onClick={() => setOnboardingOpen(true)}
              >
                <span aria-hidden="true">?</span>
              </button>
              <div className="userbox">
                <div className="userbox-info">
                  <strong>{me.display_name || me.uid}</strong>
                  <span>{me.roles.join(", ") || "без ролей"}</span>
                </div>
                <div className="avatar">{initialsOf(me.display_name || me.uid)}</div>
              </div>
            </>
          )}
        </div>
      </header>

      {!isMobile ? (
        <nav className="tabnav shell-tabs" aria-label="Основные разделы">
          {primary.map(renderTab)}
          <span className="tab-spacer" />
          {secondary.map(renderTab)}
        </nav>
      ) : null}

      <main className="content shell-content">{children}</main>

      {isMobile && menuOpen ? (
        <Sheet
          title="Разделы"
          side="left"
          onClose={closeMenu}
          footer={
            <div className="shell-menu-footwrap">
              <button type="button" className="shell-menu-row shell-menu-helprow" aria-haspopup="dialog" onClick={openOnboardingFromMenu}>
                <span className="shell-helpmark" aria-hidden="true">?</span>
                <span>Как тут всё устроено</span>
              </button>
              <div className="shell-menu-foot">
                <div className="shell-menu-user">
                  <div className="avatar">{initialsOf(me.display_name || me.uid)}</div>
                  <div className="userbox-info shell-menu-userinfo">
                    <strong>{me.display_name || me.uid}</strong>
                    <span>{me.roles.join(", ") || "без ролей"}</span>
                  </div>
                </div>
                <ThemeToggle />
              </div>
            </div>
          }
        >
          <nav className="shell-menu" aria-label="Основные разделы">
            {primary.map(renderMenuRow)}
            {secondary.length ? <hr className="divider shell-menu-divider" /> : null}
            {secondary.map(renderMenuRow)}
          </nav>
        </Sheet>
      ) : null}

      {onboardingOpen ? <OnboardingDialog me={me} onClose={closeOnboarding} fallbackFocus={onboardingFocusFallback} /> : null}
    </div>
  );
}

export function RequireAppShell({ children, me }: { children: ReactNode; me: MeResponse | null }) {
  if (!me?.authenticated) {
    return <Navigate to="/login" replace />;
  }
  return <AppShell me={me}>{children}</AppShell>;
}
