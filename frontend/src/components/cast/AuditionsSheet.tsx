/**
 * Пробы одной роли — послушать и решить.
 *
 * Лист, а не раскрытая строка: таблица каста виртуализирована, и её окно считается
 * арифметикой по высоте строки. Строка, которая раскрывается на три плеера, ломает
 * эту арифметику ровно так, как её ломала строка «Персонаж» — таблица начинала
 * дрожать. Лист ничего не знает о высоте строк.
 *
 * Плеер здесь первый в системе. Отдача — `/api/v2/auditions/{id}/audio`, с `Range`,
 * иначе у `<audio>` не двигается ползунок.
 */
import { useState } from "react";

import { Button, ScoreScale, Sheet } from "../../ui";
import type { AuditionItem, AuditionLane, AuditionPair, AuditionRejection } from "../../v2/types";
import { asksReason, busyConfirmText, isBusy, isRecommended, roleOpen, sortForRole, type ScoreAnswer } from "../../viewModels/auditionPairs";
import { ReasonNote, useReasonPrompt } from "../auditions/ReasonPrompt";

type AuditionsSheetProps = {
  role: string;
  pairs: AuditionPair[];
  /** назначить актёра на роль прямо отсюда — это и есть решение, ради которого слушают.
      Пусто, если слушающий не вправе утверждать: слушают все, решают двое. */
  onAssign?: (actorName: string) => void;
  onClose: () => void;

  /** владелец и автор удаляют любую пробу — тот же признак, что решает `can_approve` */
  canDeleteAny?: boolean;
  /** Может ли он удалять хотя бы свои пробы. У агента — нет. Дыры первого капкана
      здесь нет — сравнение идёт с `myDisplayName`, собственным именем, а не с именем
      из формы, — но признак нужен, чтобы правило было записано явно, а не выводилось
      из разницы между этим листом и панелью записи. */
  canDeleteOwn?: boolean;
  /** имя того, кто сейчас слушает — чтобы диктор мог убрать только свою пробу */
  myDisplayName?: string;
  /** id пробы, которую сейчас удаляют */
  deletingId?: string;
  /** вызывается уже ПОСЛЕ подтверждения — сам вопрос про цену задаёт этот лист */
  onDelete?: (item: AuditionItem) => void;

  /** дорожки, которые слушающий вправе нажимать: автор — верхнюю, студия — нижнюю */
  canScore?: AuditionLane[];
  /** владелец и автор видят, что будет с отказом; диктору — только итог на своей */
  showRejection?: boolean;
  /** ключ пары, оценку которой сейчас сохраняют */
  scoringKey?: string;
  /** `answer` — причина автора при оценке ниже 7 и слова для студии; спрашивает сам лист */
  onScore?: (pair: AuditionPair, lane: AuditionLane, value: number | null, answer?: ScoreAnswer | null) => void;
};

function formatSize(bytes: number): string {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatWhen(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("en-US", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** «2:35», «0:07», «1:02:03» — то, что слышно на плеере, не байты. Тот же счёт,
    каким `RecordingPanel` меряет длительность дубля. */
function formatDuration(totalSeconds: number): string {
  const total = Math.max(0, Math.round(totalSeconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const ss = String(seconds).padStart(2, "0");
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${ss}`;
  return `${minutes}:${ss}`;
}

/* То же сравнение имён, каким `RecordingPage` сверяет актёра каста с собой:
   без диакритики, регистра и пробелов по краям — точное совпадение тут не годится. */
function sameActor(a: string, b: string): boolean {
  const left = a.trim().toLocaleLowerCase("ru-RU");
  return left !== "" && left === b.trim().toLocaleLowerCase("ru-RU");
}

const DICTOR_DELETE_WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * У чужой или старше суток пробы кнопки нет вовсе, а не серая — серая сообщала бы,
 * что действие существует, и провоцировала бы просьбы «нажмите за меня». Настоящее
 * право проверяет сервер (`delete_audio_file`) теми же двумя условиями.
 */
function canDeleteAudition(
  item: AuditionItem,
  myDisplayName: string,
  canDeleteAny: boolean,
  canDeleteOwn: boolean,
): boolean {
  if (canDeleteAny) return true;
  if (!canDeleteOwn) return false;
  if (!sameActor(item.actor_name, myDisplayName)) return false;
  const uploaded = new Date(item.uploaded_at).getTime();
  if (Number.isNaN(uploaded)) return false;
  return Date.now() - uploaded < DICTOR_DELETE_WINDOW_MS;
}

/** Вопрос называет цену — чья проба, на какую роль, когда прислана, сколько
    длится — а не голое «удалить файл?»: слушают редко, курсор к этому моменту
    давно потерян. Время загрузки — не для красоты: на боевой базе у одного
    актёра случается четыре пробы на одну роль, и без времени два подтверждения
    неотличимы друг от друга — можно удалить не ту, безвозвратно. */
function confirmDeleteAudition(item: AuditionItem, role: string): boolean {
  return window.confirm(
    `Delete audition «${item.actor_name || "unnamed"}» for role «${role}»`
      + (item.chapter ? `, chapter «${item.chapter}»` : "")
      + `, uploaded ${formatWhen(item.uploaded_at) || "at an unknown time"}`
      + `, duration ${formatDuration(item.duration_seconds)}?\n\n`
      + "The file will be deleted from the server and NAS mirror. This cannot be undone.",
  );
}

function formatClock(iso: string): string {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

/** Что будет с отказом — словами, а не статусом. */
function rejectionLine(rejection: AuditionRejection): string {
  switch (rejection.status) {
    case "pending":
      return `The rejection will be sent via ${formatClock(rejection.due_at)}`;
    case "sent":
      return `Rejection sent ${formatWhen(rejection.sent_at)}`;
    case "no_account":
    case "ambiguous":
      return "Rejection not delivered: no voice actor account. Contact them directly";
    case "failed":
      return "Rejection not delivered: Telegram did not respond";
  }
}

const LANES: AuditionLane[] = ["author", "studio"];
const LANE_TITLE: Record<AuditionLane, string> = { author: "Author rating", studio: "Studio rating" };

export function AuditionsSheet({ role, pairs, onAssign, onClose, canDeleteAny, canDeleteOwn = true, myDisplayName, deletingId, onDelete, canScore = [], showRejection, scoringKey, onScore }: AuditionsSheetProps) {
  const [showBusy, setShowBusy] = useState(false);
  const reasonPrompt = useReasonPrompt();
  const press = async (pair: AuditionPair, lane: AuditionLane, value: number | null) => {
    const answer = asksReason(lane, value) ? await reasonPrompt.ask(pair, lane, true) : null;
    onScore?.(pair, lane, value, answer);
  };
  const sorted = sortForRole(pairs);
  const hiddenBusy = sorted.filter(isBusy).length;
  const shown = showBusy ? sorted : sorted.filter((pair) => !isBusy(pair));
  return (
    <Sheet
      title={`Auditions · ${role}`}
      subtitle={
        (pairs.length === 1 ? "One candidate" : `Candidates: ${pairs.length}`) +
        (onAssign ? "" : " · approved by the studio owner and author")
      }
      onClose={onClose}
    >
      <ul className="aud-list">
        {shown.map((pair) => {
          // Признак с сервера: строгое сравнение строк не узнавало «Мелвис Моркен»
          // в утверждённом «Моркен Мелвис» и предлагало утвердить его повторно.
          const mine = pair.approved;
          return (
            <li key={pair.key} className="aud-item">
              <div className="aud-head">
                <strong className="aud-actor">{pair.actor_name || "unnamed"}</strong>
                {!pair.approved && roleOpen(pair) && isRecommended(pair) ? <span className="aud-recommended">pending approval</span> : null}
                {pair.busy_on.length ? <span className="aud-busy">approved: {pair.busy_on.map((r) => `«${r}»`).join(", ")}</span> : null}
                {mine ? (
                  <span className="aud-assigned">approved for the role</span>
                ) : onAssign && pair.actor_name ? (
                  <Button size="sm" variant={roleOpen(pair) && isRecommended(pair) ? "primary" : "secondary"} onClick={() => {
                    if (isBusy(pair) && !window.confirm(busyConfirmText(pair))) return;
                    onAssign(pair.actor_name);
                  }}>
                    Approve for role
                  </Button>
                ) : null}
              </div>
              {pair.scores && pair.lane_labels ? (
                <div className="aud-scores">
                  {LANES.map((lane) => {
                    const can = canScore.includes(lane) && Boolean(onScore);
                    const value = pair.scores?.[lane]?.score ?? null;
                    return (
                      <div key={lane} className="aud-lane">
                        <span className="aud-lane-name" title={pair.scores?.[lane]?.name || undefined}>{pair.lane_labels?.[lane]}</span>
                        <ScoreScale value={value} label={LANE_TITLE[lane]} size={can ? "md" : "sm"} disabled={pair.key === scoringKey}
                          onChange={can ? (next) => void press(pair, lane, next) : undefined} />
                        <span className="aud-lane-value num">{value ?? ""}</span>
                        <ReasonNote pair={pair} lane={lane} canAsk={can}
                          onAsk={async (reasons) => {
                            const answer = await reasonPrompt.ask(pair, lane, reasons);
                            if (answer) onScore?.(pair, lane, value, answer);
                          }} />
                      </div>
                    );
                  })}
                </div>
              ) : null}
              {pair.auditions.map((item) => (
                <div key={item.id} className="aud-file-row">
                  <audio className="aud-player" controls preload="none" src={`/api/v2/auditions/${encodeURIComponent(item.id)}/audio`}>
                    Your browser does not support audio playback.
                  </audio>
                  <div className="aud-foot">
                    <span className="aud-file mono" title={item.original_filename}>
                      {formatWhen(item.uploaded_at)} · {item.original_filename}
                      {formatSize(item.size_bytes) ? ` · ${formatSize(item.size_bytes)}` : ""}
                    </span>
                    {onDelete && canDeleteAudition(item, myDisplayName || "", Boolean(canDeleteAny), canDeleteOwn) ? (
                      <Button size="sm" variant="danger" loading={Boolean(item.id) && item.id === deletingId}
                        onClick={() => {
                          if (confirmDeleteAudition(item, role)) onDelete(item);
                        }}>
                        Delete
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
              {pair.rejection && (showRejection || pair.rejection.status === "sent") ? (
                <p className={`aud-rejection aud-rejection-${pair.rejection.status}`}>{rejectionLine(pair.rejection)}</p>
              ) : null}
            </li>
          );
        })}
      </ul>
      {hiddenBusy ? (
        <Button size="sm" variant="ghost" onClick={() => setShowBusy(!showBusy)}>
          {showBusy ? "Hide assigned" : `Show assigned (${hiddenBusy})`}
        </Button>
      ) : null}
      {reasonPrompt.dialog}
    </Sheet>
  );
}
