import { roleLabel } from "../../v2/types";
/**
 * Опись главы — что лежит на диске под строкой покрытия.
 *
 * Строка главы отвечает «сколько ролей записано». Она не отвечает на соседний вопрос,
 * который задают чаще: а что там, собственно, записано — каким файлом, какой длины,
 * доехала ли копия на NAS и как это послушать, не выкачивая архив на всю главу.
 * Файлового менеджера над хранилищем в системе нет, и эта опись — он и есть.
 *
 * Роли, а не плоский список: незаписанная роль здесь видна пустой строкой, и «кого мы
 * ещё ждём» читается тем же взглядом, что и «что уже есть».
 *
 * `preload="none"` у плеера не украшение: у главы бывает десяток дублей, каждый до
 * сотни мегабайт, и браузер, которому это не запретить, полез бы за всеми сразу при
 * раскрытии строки.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPostJson, describeApiError } from "../../api/client";
import { Button } from "../../ui";
import { useToast } from "../ToastProvider";
import type { ChapterRecordingResponse, ChapterRecordingRole, ChapterTake, NasDelivery } from "../../types";

type ChapterFilesRowProps = {
  chapterId: string;
  /** только владелец и автор; у остальных кнопки нет вовсе, а не серой */
  canDelete: boolean;
  /** на какой запрос покрытия навесить пересчёт после удаления */
  bookId: string;
  nasDelivery?: NasDelivery;
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

/** «2:35», «0:07», «1:02:03» — тот же счёт, каким `AuditionsSheet` меряет пробу. */
function formatDuration(totalSeconds: number): string {
  const total = Math.max(0, Math.round(totalSeconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const ss = String(seconds).padStart(2, "0");
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${ss}`;
  return `${minutes}:${ss}`;
}

/** Опись и сводка читают одно подтверждение: старая отметка времени не
    должна превращать ошибку нового журнала в зелёную галочку. */
function describeMirror(file?: NasDelivery["files"][number]): { text: string; broken: boolean } {
  if (!file) return { text: "copy unconfirmed", broken: false };
  if (file.state === "error") return { text: file.error || "copy failed", broken: true };
  if (file.state === "delivered") return { text: "copy verified ✓", broken: false };
  if (file.state === "pending") return { text: "copy pending", broken: false };
  return { text: "copy status unknown", broken: false };
}

/** Вопрос называет цену — чей дубль, какая роль, когда прислан, сколько длится, — а не
    голое «удалить файл?». У роли в главе бывает несколько дублей близкой длины, и без
    времени два подтверждения неотличимы: можно стереть не тот безвозвратно. Тот же
    вопрос задают панель записи и лист проб. */
function confirmDelete(take: ChapterTake): boolean {
  return window.confirm(
    `Delete take for role «${take.role || "no role"}»`
      + (take.actor_name ? ` recorded by «${take.actor_name}»` : "")
      + `, uploaded ${formatWhen(take.uploaded_at) || "at an unknown time"}`
      + `, duration ${formatDuration(take.duration_seconds)}?\n\n`
      + `File «${take.canonical_filename}» will be deleted from the server and NAS mirror. This cannot be undone.`,
  );
}

function TakeLine({ take, canDelete, deleting, onDelete, onCopyPath, nasDelivery }: {
  take: ChapterTake;
  nasDelivery?: NasDelivery;
  canDelete: boolean;
  deleting: boolean;
  onDelete: (take: ChapterTake) => void;
  onCopyPath: (take: ChapterTake) => void;
}) {
  const mirror = describeMirror(nasDelivery?.files.find((file) => file.audio_id === take.id));
  const audio = `/api/v2/takes/${encodeURIComponent(take.id)}/audio`;
  return (
    <li className="cf-take">
      <div className="cf-take-head">
        <span className="cf-name mono" title={take.canonical_filename}>{take.canonical_filename}</span>
        <span className="cf-meta">
          {[formatSize(take.size_bytes), formatDuration(take.duration_seconds), formatWhen(take.uploaded_at)]
            .filter(Boolean)
            .join(" · ")}
          {" · "}
          <span className={mirror.broken ? "cf-broken" : "cf-dim"}>{mirror.text}</span>
        </span>
      </div>
      <audio className="cf-player" controls preload="none" src={audio}>
        Your browser does not support audio playback.
      </audio>
      <div className="cf-take-foot">
        {/* Путь — для человека за терминалом и для Audition: до файла иначе не дотянуться.
            Кнопкой, а не выделением мышью: путь не переносится и в узкой колонке обрезан. */}
        <button type="button" className="cf-path mono" title={`${take.stored_key} — click to copy`} onClick={() => onCopyPath(take)}>
          {take.stored_key}
        </button>
        <div className="cf-actions">
          <a className="cf-link" href={`${audio}?download=1`}>download</a>
          {canDelete ? (
            <Button size="sm" variant="danger" loading={deleting} onClick={() => {
              if (confirmDelete(take)) onDelete(take);
            }}>
              Delete
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function RoleBlock({ role, canDelete, deletingId, onDelete, onCopyPath, nasDelivery }: {
  role: ChapterRecordingRole;
  nasDelivery?: NasDelivery;
  canDelete: boolean;
  deletingId: string | undefined;
  onDelete: (take: ChapterTake) => void;
  onCopyPath: (take: ChapterTake) => void;
}) {
  const lines = `${role.lines} ${role.lines === 1 ? "line" : role.lines < 5 ? "lines" : "lines"}`;
  return (
    <div className="cf-role">
      <div className="cf-role-head">
        <strong>{roleLabel(role.role)}</strong>
        {role.actor_name ? <span className="cf-dim"> · {role.actor_name}</span> : null}
        <span className="cf-dim"> · {lines}</span>
      </div>
      {role.takes.length === 0 ? (
        <p className="cf-empty">not recorded</p>
      ) : (
        <ul className="cf-takes">
          {role.takes.map((take) => (
            <TakeLine
              key={take.id}
              take={take}
              canDelete={canDelete}
              deleting={take.id === deletingId}
              onDelete={onDelete}
              onCopyPath={onCopyPath}
              nasDelivery={nasDelivery}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

export function ChapterFilesRow({ chapterId, canDelete, bookId, nasDelivery }: ChapterFilesRowProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();

  // Компонент существует только у раскрытой главы, поэтому запрос не нужно гасить
  // флагом: свёрнутая глава его и не монтирует.
  const opis = useQuery({
    queryKey: ["v2", "chapter-recording", chapterId],
    queryFn: () => apiGet<ChapterRecordingResponse>(`/api/v2/chapters/${encodeURIComponent(chapterId)}/recording`),
    staleTime: 15_000,
  });

  const remove = useMutation({
    mutationFn: (take: ChapterTake) =>
      apiPostJson<{ ok: boolean }>(`/api/recording/files/${encodeURIComponent(take.id)}/delete`, {}),
    onSuccess: async (_result, take) => {
      pushToast({ tone: "success", title: "Take deleted", detail: `«${take.canonical_filename}» from the server and mirror.` });
      // И опись, и строка покрытия над ней: у роли стало меньше дублей, а глава могла
      // перестать быть готовой.
      await queryClient.invalidateQueries({ queryKey: ["v2", "chapter-recording", chapterId] });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Could not complete the action", detail: describeApiError(error, "the server did not respond") }),
  });

  /* Отчёт обязателен: скопировалось молча — неотличимо от не скопировалось, а путь
     потом вставляют в терминал и удивляются пустоте. Буфер закрыт в небезопасном
     контексте и под запретом разрешений, поэтому отказ тоже называется вслух. */
  const copyPath = (take: ChapterTake) => {
    navigator.clipboard?.writeText(take.stored_key).then(
      () => pushToast({ tone: "success", title: "Path copied", detail: take.stored_key }),
      () => pushToast({ tone: "error", title: "Clipboard unavailable", detail: take.stored_key }),
    );
  };

  if (opis.isLoading) return <p className="cf-note">Loading chapter inventory…</p>;
  if (opis.isError || !opis.data) {
    return (
      <p className="cf-note cf-broken">
        Could not load inventory: {describeApiError(opis.error, "the server did not respond")}.
      </p>
    );
  }

  const { roles, orphan_takes: orphans } = opis.data;
  if (roles.length === 0 && orphans.length === 0) {
    return <p className="cf-note">This chapter has no speaking roles.</p>;
  }

  return (
    <div className="cf">
      {roles.map((role) => (
        <RoleBlock
          key={role.role}
          role={role}
          canDelete={canDelete}
          deletingId={remove.isPending ? remove.variables?.id : undefined}
          onDelete={(take) => remove.mutate(take)}
          onCopyPath={copyPath}
          nasDelivery={nasDelivery}
        />
      ))}
      {orphans.length > 0 ? (
        <div className="cf-role cf-orphans">
          <div className="cf-role-head">
            <strong className="cf-broken">Unassigned role</strong>
            <span className="cf-dim"> · these roles were renamed or merged and are no longer in the script</span>
          </div>
          <ul className="cf-takes">
            {orphans.map((take) => (
              <TakeLine
                key={take.id}
                take={take}
                canDelete={canDelete}
                deleting={remove.isPending && remove.variables?.id === take.id}
                onDelete={(item) => remove.mutate(item)}
                onCopyPath={copyPath}
                nasDelivery={nasDelivery}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
