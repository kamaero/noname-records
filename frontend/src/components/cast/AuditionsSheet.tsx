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
import { Button, Sheet } from "../../ui";
import type { AuditionItem, AuditionRejection } from "../../v2/types";

type AuditionsSheetProps = {
  role: string;
  items: AuditionItem[];
  /** назначить актёра на роль прямо отсюда — это и есть решение, ради которого слушают.
      Пусто, если слушающий не вправе утверждать: слушают все, решают двое. */
  onAssign?: (actorName: string) => void;
  assignedTo?: string;
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

  /** 👍/👎 ставит только автор; у остальных — метка без кнопок */
  canReact?: boolean;
  /** владелец и автор видят, что будет с отказом; диктору — только итог на своей */
  showRejection?: boolean;
  /** id пробы, реакцию на которую сейчас сохраняют */
  reactingId?: string;
  /** 0 — снять реакцию */
  onReact?: (item: AuditionItem, value: 1 | -1 | 0) => void;
};

function formatSize(bytes: number): string {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}

function formatWhen(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
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
    `Удалить пробу «${item.actor_name || "без имени"}» на роль «${role}»`
      + (item.chapter ? `, глава «${item.chapter}»` : "")
      + `, загружена ${formatWhen(item.uploaded_at) || "неизвестно когда"}`
      + `, длительность ${formatDuration(item.duration_seconds)}?\n\n`
      + "Файл будет стёрт с сервера и с зеркала на NAS. Отката не будет.",
  );
}

function formatClock(iso: string): string {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

/** Что будет с отказом — словами, а не статусом. */
function rejectionLine(rejection: AuditionRejection, canReact: boolean): string {
  switch (rejection.status) {
    case "pending":
      return `Отказ уйдёт диктору в ${formatClock(rejection.due_at)}` + (canReact ? " — снимите 👎, чтобы отменить" : "");
    case "sent":
      return `Отказ отправлен ${formatWhen(rejection.sent_at)}`;
    case "no_account":
    case "ambiguous":
      return "Отказ не доставлен: у диктора нет учётки — сообщите сами";
    case "failed":
      return "Отказ не ушёл — Telegram не ответил";
  }
}

export function AuditionsSheet({ role, items, onAssign, assignedTo, onClose, canDeleteAny, canDeleteOwn = true, myDisplayName, deletingId, onDelete, canReact, showRejection, reactingId, onReact }: AuditionsSheetProps) {
  return (
    <Sheet
      title={`Пробы · ${role}`}
      subtitle={
        (items.length === 1 ? "Одна проба" : `Проб: ${items.length}`) +
        (onAssign ? "" : " · утверждают владелец студии и автор")
      }
      onClose={onClose}
    >
      <ul className="aud-list">
        {items.map((item) => {
          const mine = Boolean(assignedTo && assignedTo.trim() === item.actor_name.trim());
          return (
            <li key={item.id} className="aud-item">
              <div className="aud-head">
                <strong className="aud-actor">{item.actor_name || "без имени"}</strong>
                <span className="aud-when muted">{formatWhen(item.uploaded_at)}</span>
              </div>
              <audio className="aud-player" controls preload="none" src={`/api/v2/auditions/${encodeURIComponent(item.id)}/audio`}>
                Ваш браузер не умеет проигрывать аудио.
              </audio>
              <div className="aud-foot">
                <span className="aud-file mono" title={item.original_filename}>
                  {item.original_filename}
                  {formatSize(item.size_bytes) ? ` · ${formatSize(item.size_bytes)}` : ""}
                </span>
                <div className="aud-actions">
                  {canReact && onReact ? (
                    <span className="aud-react-group" role="group" aria-label="Реакция автора">
                      {([1, -1] as const).map((value) => {
                        const pressed = item.author_reaction === value;
                        return (
                          <button
                            key={value}
                            type="button"
                            className="aud-react"
                            aria-pressed={pressed}
                            disabled={item.id === reactingId}
                            title={value === 1 ? "Подходит" : "Не подходит — через 15 минут диктору уйдёт вежливый отказ"}
                            onClick={() => onReact(item, pressed ? 0 : value)}
                          >
                            {value === 1 ? "👍" : "👎"}
                          </button>
                        );
                      })}
                    </span>
                  ) : item.author_reaction ? (
                    <span className="aud-reaction">Автор: {item.author_reaction === 1 ? "👍" : "👎"}</span>
                  ) : null}
                  {/* «назначен» видят все — это факт; кнопку видит тот, кто вправе решать */}
                  {mine ? (
                    <span className="aud-assigned">утверждён на роль</span>
                  ) : onAssign && item.actor_name ? (
                    <Button size="sm" variant="secondary" onClick={() => onAssign(item.actor_name)}>
                      Утвердить на роль
                    </Button>
                  ) : null}
                  {onDelete && canDeleteAudition(item, myDisplayName || "", Boolean(canDeleteAny), canDeleteOwn) ? (
                    <Button
                      size="sm"
                      variant="danger"
                      loading={Boolean(item.id) && item.id === deletingId}
                      onClick={() => {
                        if (confirmDeleteAudition(item, role)) onDelete(item);
                      }}
                    >
                      Удалить
                    </Button>
                  ) : null}
                </div>
              </div>
              {item.rejection && (showRejection || item.rejection.status === "sent") ? (
                <p className={`aud-rejection aud-rejection-${item.rejection.status}`}>
                  {rejectionLine(item.rejection, Boolean(canReact))}
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Sheet>
  );
}
