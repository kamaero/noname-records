import "./LoginPage.css";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Navigate, useSearchParams } from "react-router-dom";
import { apiGet } from "../api/client";
import type { BooksResponse, MeResponse, PublicAuthConfigResponse } from "../types";
import { Icon } from "../components/Icon";
import { ThemeToggle } from "../components/ThemeToggle";
import { Button, Field } from "../ui";

/** Причины отказа во входе — коды шлёт сервер (`app/auth_routes.py`, LOGIN_ERROR_CODES). */
const LOGIN_ERRORS: Record<string, string> = {
  setup: "Password sign-in has not been configured on the server.",
  credentials: "Incorrect username or password.",
  disabled: "This account is disabled.",
  no_roles: "This account does not have access yet. Contact your studio administrator.",
  telegram: "Telegram sign-in failed. Please try again.",
  not_whitelisted: "This Telegram account does not have studio access. Contact your administrator.",
};
const LOGIN_ERROR_FALLBACK = "Sign-in failed. Please try again.";

export function LoginPage() {
  const [params] = useSearchParams();

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => apiGet<MeResponse>("/api/me"),
    retry: false,
  });
  const configQuery = useQuery({
    queryKey: ["public-auth-config"],
    queryFn: () => apiGet<PublicAuthConfigResponse>("/api/public/auth-config"),
    retry: false,
  });
  const telegramBotUsername = configQuery.data?.telegram_bot_username || "";
  // Кнопку рисует скрипт с telegram.org (окошко oauth.telegram.org). У кого этот адрес не
  // открывается — провайдер, VPN, блокировщик, встроенный браузер, — кнопки просто нет, и
  // раньше на её месте оставалась пустота. Не дождались — говорим прямо.
  const [widgetMissing, setWidgetMissing] = useState(false);

  useEffect(() => {
    if (!telegramBotUsername) {
      return;
    }
    const wrap = document.getElementById("telegramWidgetWrap");
    if (!wrap || wrap.childNodes.length > 0) {
      return;
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).onTelegramAuth = (user: Record<string, string>) => {
      const form = document.getElementById("telegramHiddenForm") as HTMLFormElement | null;
      if (!form || !user) {
        return;
      }
      ["id", "auth_date", "hash", "first_name", "last_name", "username", "photo_url"].forEach((key) => {
        const input = form.querySelector(`input[name="${key}"]`) as HTMLInputElement | null;
        if (input) {
          input.value = user[key] || "";
        }
      });
      form.submit();
    };
    const script = document.createElement("script");
    script.async = true;
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute("data-telegram-login", telegramBotUsername);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-lang", "en");
    script.setAttribute("data-userpic", "false");
    script.setAttribute("data-request-access", "write");
    script.setAttribute("data-onauth", "onTelegramAuth(user)");
    script.onerror = () => setWidgetMissing(true);
    wrap.appendChild(script);
    const timer = window.setTimeout(() => {
      if (!wrap.querySelector("iframe")) setWidgetMissing(true);
    }, 6000);
    return () => window.clearTimeout(timer);
  }, [telegramBotUsername]);

  // В адресе только код причины: текст из адреса вывел бы на настоящей странице входа
  // любую фразу, которую кто-то дописал в ссылку. Неизвестный код — общее сообщение.
  const errorCode = params.get("error") || "";
  const error = errorCode ? LOGIN_ERRORS[errorCode] ?? LOGIN_ERROR_FALLBACK : "";
  const setupMissing = params.get("setup_missing") === "1" || Boolean(configQuery.data?.setup_missing);

  // Диктор после входа попадает сразу в хаб книги: там его работа, а список книг для него
  // лишний клик — книга в студии обычно одна. Запрос идёт только за дикторов и только
  // после того, как вход подтверждён: остальным он не нужен вовсе.
  const isDictor = Boolean(
    meQuery.data?.authenticated && !meQuery.data.full_access && meQuery.data.roles.includes("dictor"),
  );
  const booksQuery = useQuery({
    queryKey: ["books"],
    queryFn: () => apiGet<BooksResponse>("/api/books"),
    enabled: isDictor,
    retry: false,
  });

  const redirectTo = useMemo(() => {
    if (!meQuery.data?.authenticated) {
      return "";
    }
    if (!isDictor) {
      return "/";
    }
    // Пока список книг не пришёл, не уводим никуда: мелькнуть общим экраном и тут же
    // прыгнуть в хаб — хуже, чем подождать долю секунды на странице входа.
    if (booksQuery.isLoading) {
      return "";
    }
    const books = booksQuery.data?.items ?? [];
    return books.length === 1 ? `/books/${encodeURIComponent(books[0].id)}` : "/books";
  }, [meQuery.data, isDictor, booksQuery.isLoading, booksQuery.data]);

  if (redirectTo) {
    return <Navigate to={redirectTo} replace />;
  }

  // Настольная версия: формы входа нет — сессию открывает только запуск программы
  // (ключ запуска), а сюда попадают, когда cookie прошлого запуска перестала действовать.
  if (configQuery.data?.seat_mode === "one") {
    return (
      <div className="lg">
        <main className="lg-col">
          <div className="panel panel-pad">Your session has expired. Close Noname Records and open it again.</div>
        </main>
      </div>
    );
  }

  const telegramNote = configQuery.isLoading
    ? "Loading Telegram sign-in…"
    : configQuery.isError
      ? "Could not load Telegram sign-in settings."
      : !telegramBotUsername
        ? "Telegram sign-in is not configured on the server."
        : "";

  return (
    <div className="lg">
      <div className="lg-theme">
        <ThemeToggle />
      </div>
      <main className="lg-col">
        <div className="lg-brand">
          <div className="brand-mark">
            <Icon name="wave" />
          </div>
          <div className="lg-brand-text">
            <span className="brand-name">NONAME RECORDS</span>
            <span className="brand-sub">audiobook production studio</span>
          </div>
        </div>

        <h1 className="lg-title">Sign in</h1>

        {setupMissing ? (
          <p className="ui-note ui-note--error" role="alert">
            ADMIN_PASSWORD_HASH is not set in the server’s .env file.
          </p>
        ) : null}
        {error ? (
          <p className="ui-note ui-note--error" role="alert">
            {error}
          </p>
        ) : null}

        <form method="post" action="/login" className="lg-form">
          <Field label="Username">
            {(props) => <input {...props} className="ui-input" type="text" name="login" autoComplete="username" required autoFocus />}
          </Field>
          <Field label="Password">
            {(props) => <input {...props} className="ui-input" type="password" name="password" autoComplete="current-password" required />}
          </Field>
          <Button type="submit" variant="primary" block>
            Sign in
          </Button>
        </form>

        <div className="lg-or" aria-hidden="true">
          <span>or</span>
        </div>

        <section className="lg-tg" aria-label="Sign in with Telegram">
          <div id="telegramWidgetWrap" className="lg-tg-widget" />
          {widgetMissing && telegramBotUsername ? (
            <p className="ui-note" role="status">
              The Telegram button did not load. This can happen if telegram.org is unavailable. Sign in
              with a username and password instead. To get them from the bot,{" "}
              <a href={`https://t.me/${telegramBotUsername}?start=dictor`} target="_blank" rel="noreferrer">
                open @{telegramBotUsername}
              </a>{" "}
              and select “🔑 Login and password”.
            </p>
          ) : null}
          <p className="lg-tg-note">{telegramNote || "Telegram sign-in is available to invited users only."}</p>
          {telegramBotUsername && !widgetMissing ? (
            <p className="lg-tg-note">
              Telegram button missing or sign-in not working? Get your username and password from the bot:{" "}
              <a href={`https://t.me/${telegramBotUsername}?start=dictor`} target="_blank" rel="noreferrer">@{telegramBotUsername}</a>{" "}
              → “🔑 Login and password”.
            </p>
          ) : null}
          <form method="post" action="/auth/telegram" id="telegramHiddenForm" hidden>
            <input type="hidden" name="id" />
            <input type="hidden" name="auth_date" />
            <input type="hidden" name="hash" />
            <input type="hidden" name="first_name" />
            <input type="hidden" name="last_name" />
            <input type="hidden" name="username" />
            <input type="hidden" name="photo_url" />
          </form>
        </section>
      </main>
    </div>
  );
}
