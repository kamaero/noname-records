import "./OnboardingDialog.css";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiGet } from "../api/client";
import type { MeBotReachResponse, MeResponse, PublicAuthConfigResponse } from "../types";
import { Button, Dialog, StepDots } from "../ui";
import { Icon } from "./Icon";

type Step = {
  key: string;
  title: string;
  /** раздел справки для «Подробнее» */
  help: string;
  body: ReactNode;
};

type OnboardingDialogProps = {
  me: MeResponse;
  onClose: () => void;
  /** см. `Dialog.fallbackFocus` */
  fallbackFocus?: () => HTMLElement | null;
};

/** Состояние связи с ботом: `undefined` — ещё не спрашивали, `null` — не удалось проверить. */
function BotStatus({ reachable, checking }: { reachable: boolean | null | undefined; checking: boolean }) {
  if (checking && reachable == null) {
    return (
      <p className="onb-status onb-status--checking" role="status">
        <span className="onb-status-mark" aria-hidden="true">…</span>
        Проверяю, может ли бот вам написать…
      </p>
    );
  }
  if (reachable === true) {
    return (
      <p className="onb-status onb-status--ok" role="status">
        <span className="onb-status-mark" aria-hidden="true">✓</span>
        Бот на связи — уведомления будут приходить.
      </p>
    );
  }
  if (reachable === false) {
    return (
      <p className="onb-status onb-status--bad" role="status">
        <span className="onb-status-mark" aria-hidden="true">✗</span>
        Бот пока не может вам написать — откройте бота и нажмите Start.
      </p>
    );
  }
  return (
    <p className="onb-status onb-status--unknown" role="status">
      <span className="onb-status-mark" aria-hidden="true">?</span>
      Не удалось проверить связь с ботом. Попробуйте ещё раз чуть позже.
    </p>
  );
}

function BotStep({ me }: { me: MeResponse }) {
  const queryClient = useQueryClient();
  const [reachable, setReachable] = useState<boolean | null | undefined>(me.bot_reachable);
  const [checking, setChecking] = useState(false);
  // та же публичная конфигурация, что на странице входа (тот же ключ — ответ из кэша)
  const configQuery = useQuery({
    queryKey: ["public-auth-config"],
    queryFn: () => apiGet<PublicAuthConfigResponse>("/api/public/auth-config"),
    retry: false,
    staleTime: 5 * 60_000,
  });
  const username = configQuery.data?.telegram_bot_username || "";

  // `/api/me` не ждёт Telegram: при первом заходе ответа ещё нет (null) — спросим сами,
  // один раз, чтобы новичок не увидел «не удалось проверить» вместо настоящего ответа
  const askedOnOpen = useRef(false);
  useEffect(() => {
    if (askedOnOpen.current || me.bot_reachable != null) return;
    askedOnOpen.current = true;
    void recheck();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function recheck() {
    setChecking(true);
    try {
      const result = await apiGet<MeBotReachResponse>("/api/me/bot-reach");
      setReachable(result.bot_reachable);
    } catch {
      setReachable(null);
    } finally {
      setChecking(false);
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    }
  }

  return (
    <>
      <p>
        Студия пишет вам в Telegram: когда вас утвердили на роль, когда в загруженной записи не нашлось каких-то реплик и когда правка разметки отдала вашей роли реплики, которых в записи нет.
      </p>
      <p>Но бот не может написать первым. Откройте его и нажмите Start — один раз, этого достаточно.</p>
      <BotStatus reachable={reachable} checking={checking} />
      <div className="onb-actions">
        {username ? (
          <a className="ui-btn ui-btn--primary" href={`https://t.me/${username}`} target="_blank" rel="noopener noreferrer">
            Открыть бота
          </a>
        ) : null}
        <Button variant="secondary" loading={checking} onClick={recheck}>
          Проверить ещё раз
        </Button>
      </div>
    </>
  );
}

function buildSteps(me: MeResponse): Step[] {
  const steps: Step[] = [];
  if (me.auth_source === "telegram") {
    steps.push({ key: "bot", title: "Бот для уведомлений", help: "bot", body: <BotStep me={me} /> });
  }
  steps.push(
    {
      key: "role",
      title: "Ваша роль",
      help: "role-lines",
      body: (
        <>
          <p>
            Всё для работы — в разделе «Запись»: выберите книгу, роль и главу, и откроется текст главы. Ваши роли по касту перечислены под полем «Роль».
          </p>
          <p>
            Кнопка «Мои реплики» над текстом соберёт вашу роль по всем главам по порядку, с соседними абзацами — чтобы было видно, кому и на что вы отвечаете.
          </p>
        </>
      ),
    },
    {
      key: "stress",
      title: "Ударения и слова-ловушки",
      help: "stress",
      body: (
        <>
          <p>
            Когда над текстом включены «Ударения», над ударной гласной стоит знак. Если он стоит неверно, нажмите слово, выберите нужную гласную и нажмите «Для этой книги» — исправление увидят все.
          </p>
          <p>
            На странице, которую открывает «Мои реплики», есть кнопка «Слова-ловушки»: редкие слова роли по главам, в которых легко ошибиться, читая в потоке.
          </p>
        </>
      ),
    },
    {
      key: "upload",
      title: "Запись и загрузка дублей",
      help: "upload",
      body: (
        <>
          <p>
            Перед загрузкой сверьте книгу, роль и главу в карточке загрузки — там должно быть написано «Дубль утверждённой роли». Скопируйте предложенное имя и назовите файл точно так же.
          </p>
          <p>
            Перетащите файл в область загрузки и дождитесь сообщения «Готово». Принятый файл появится в списке «Последние файлы главы».
          </p>
        </>
      ),
    },
  );
  return steps;
}

/** Приветственное окно диктора: что где в студии, шагами. Шаг про бота — только тем,
 *  кто вошёл через Telegram: при входе по паролю боту некому писать. */
export function OnboardingDialog({ me, onClose, fallbackFocus }: OnboardingDialogProps) {
  const steps = buildSteps(me);
  const [index, setIndex] = useState(0);
  const step = steps[Math.min(index, steps.length - 1)];
  const last = index >= steps.length - 1;

  // «Подробнее»: окно закрывается, ссылка ведёт в справку, а до раздела докручиваем
  // сами — переход внутри приложения к якорю не прокручивает
  const openHelp = (id: string) => {
    onClose();
    let tries = 0;
    const scroll = () => {
      const target = document.getElementById(id);
      if (target) target.scrollIntoView({ block: "start" });
      else if (tries++ < 20) window.requestAnimationFrame(scroll);
    };
    window.requestAnimationFrame(scroll);
  };

  return (
    <Dialog
      title="Как тут всё устроено"
      subtitle={`Шаг ${index + 1} из ${steps.length}`}
      onClose={onClose}
      fallbackFocus={fallbackFocus}
      className="onb-dialog"
      footer={
        <div className="onb-foot">
          <StepDots
            steps={steps.map((s, i) => ({ key: s.key, label: s.title, state: i <= index ? "done" : "pending" }))}
            count={steps.length}
            size="lg"
          />
          <div className="onb-nav">
            {index > 0 ? (
              <Button variant="ghost" onClick={() => setIndex((i) => i - 1)}>
                Назад
              </Button>
            ) : null}
            {last ? (
              <Button variant="primary" data-autofocus onClick={onClose}>
                Понятно
              </Button>
            ) : (
              <Button variant="primary" data-autofocus onClick={() => setIndex((i) => i + 1)}>
                Далее
              </Button>
            )}
          </div>
        </div>
      }
    >
      <section key={step.key} className="onb-step" aria-live="polite">
        <h3 className="onb-step-title">{step.title}</h3>
        {step.body}
        <Link to={`/help#${step.help}`} className="onb-more" onClick={() => openHelp(step.help)}>
          Подробнее в справке
          <Icon name="arrow" />
        </Link>
      </section>
    </Dialog>
  );
}
