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
        Checking whether the bot can message you…
      </p>
    );
  }
  if (reachable === true) {
    return (
      <p className="onb-status onb-status--ok" role="status">
        <span className="onb-status-mark" aria-hidden="true">✓</span>
        The bot is connected. You will receive notifications.
      </p>
    );
  }
  if (reachable === false) {
    return (
      <p className="onb-status onb-status--bad" role="status">
        <span className="onb-status-mark" aria-hidden="true">✗</span>
        The bot cannot message you yet. Open the bot and select Start.
      </p>
    );
  }
  return (
    <p className="onb-status onb-status--unknown" role="status">
      <span className="onb-status-mark" aria-hidden="true">?</span>
      Could not check the bot connection. Please try again later.
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
        The studio sends Telegram notifications when you are cast in a role, when an uploaded recording is missing lines, and when attribution changes assign your role lines that are not in the recording.
      </p>
      <p>The bot cannot message you first. Open it and select Start once to allow notifications.</p>
      <BotStatus reachable={reachable} checking={checking} />
      <div className="onb-actions">
        {username ? (
          <a className="ui-btn ui-btn--primary" href={`https://t.me/${username}`} target="_blank" rel="noopener noreferrer">
            Open bot
          </a>
        ) : null}
        <Button variant="secondary" loading={checking} onClick={recheck}>
          Check again
        </Button>
      </div>
    </>
  );
}

function buildSteps(me: MeResponse): Step[] {
  const steps: Step[] = [];
  if (me.auth_source === "telegram") {
    steps.push({ key: "bot", title: "Notification bot", help: "bot", body: <BotStep me={me} /> });
  }
  steps.push(
    {
      key: "role",
      title: "Your role",
      help: "role-lines",
      body: (
        <>
          <p>
            Find your role’s text in Script: choose a role above the text and a chapter on the left. Your roles appear first in the Role list.
          </p>
          <p>
            Select My lines above the text to see your role across all chapters in order, with the surrounding paragraphs for context.
          </p>
        </>
      ),
    },
    {
      key: "stress",
      title: "Stress marks and word traps",
      help: "stress",
      body: (
        <>
          <p>
            Turn on Stress marks above the text to mark stressed vowels. If a mark is wrong, select the word, choose the correct vowel, and select For this book. Everyone will see the correction.
          </p>
          <p>
            The My lines page includes Word traps: unusual words in your role, grouped by chapter, that are easy to mispronounce while reading.
          </p>
        </>
      ),
    },
    {
      key: "upload",
      title: "Uploading takes",
      help: "upload",
      body: (
        <>
          <p>
            Upload files in Uploads, also available through Upload take above the script. Check the book, role, and chapter. The upload type should say Approved role take. Copy the suggested filename and use it exactly.
          </p>
          <p>
            Drag your file into the upload area and wait for Done. The accepted file will appear under Recent chapter files.
          </p>
          {me.auth_source === "telegram" ? (
            <p>
              For a book currently in production, you can also send takes to the Telegram bot: a chapter file or the whole role in one file, with any filename. The bot will split it by chapter and send a report.
            </p>
          ) : null}
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
      title="How it works"
      subtitle={`Step ${index + 1} of ${steps.length}`}
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
                Back
              </Button>
            ) : null}
            {last ? (
              <Button variant="primary" data-autofocus onClick={onClose}>
                Got it
              </Button>
            ) : (
              <Button variant="primary" data-autofocus onClick={() => setIndex((i) => i + 1)}>
                Next
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
          Learn more in Help
          <Icon name="arrow" />
        </Link>
      </section>
    </Dialog>
  );
}
