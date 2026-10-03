/**
 * The right-hand «Запись» panel: which book, which role, which chapter — then the
 * batch drop zone and the chapter's last files. The reader on the left is the
 * main object; this panel only names what the uploads get filed under.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Button, DataTable, Field, type Column } from "../../ui";
import type { DictorRecentFile } from "../../types";
import { BatchUpload } from "./BatchUpload";
import type { UploadKind, UploadStatus } from "./useRecordingUploads";
import { roleScriptHref } from "../../v2/roleRoutes";
import "./recording.css";

export type PanelBook = { id: string; title: string };
export type PanelRole = { name: string; lines: number; actor?: string };
export type PanelChapter = { id: string; index: number; title: string };

const CUSTOM = "\u0000custom";

type RecordingPanelProps = {
  books: PanelBook[];
  bookId: string;
  onBook: (id: string) => void;

  actorName: string;
  /** владелец и автор грузят за других: к ним попадают записи, присланные мимо системы */
  canUploadForOthers?: boolean;
  onActorName?: (name: string) => void;
  /** имена актёров из каста — подсказка, чтобы имя совпало с тем, что знает система */
  actorNames?: string[];
  /** за кем закреплена выбранная роль — чтобы сказать, почему выходит проба */
  roleOwner?: string;
  /** the actor's own roles (by cast actor name); empty → whole cast is offered */
  myRoles: PanelRole[];
  cast: PanelRole[];
  role: string;
  onRole: (role: string) => void;
  roleScriptSearch?: string;

  chapters: PanelChapter[];
  chapterId: string;
  onChapter: (id: string) => void;

  /** что выйдет из этой загрузки — решает сервер, а не диктор */
  kind: UploadKind;
  /** у пробы глава необязательна: её читают из какого-то куска, но роль ещё не назначена */
  chapterOptional?: boolean;
  /** точное имя, которым назвать файл при нынешнем выборе */
  expectedFilename?: string;

  /** one quiet line above the fields (e.g. the preview flag) */
  notice?: string;

  upload: {
    files: File[];
    preview: Parameters<typeof BatchUpload>[0]["preview"];
    status: UploadStatus;
    isUploading: boolean;
    progress: number;
    blocked: boolean;
    disabledReason?: string;
    /** диктор подтвердил: глава выбрана верно, номер в имени файла — просто другой */
    confirmChapter: boolean;
    /** сервер отказал по несовпадению главы — галочка нужна даже без предпросмотра */
    chapterMismatchRefused: boolean;
    onSelect: (files: File[]) => void;
    onClear: () => void;
    onUpload: () => void;
    onConfirmChapter: (value: boolean) => void;
  };

  recentFiles: DictorRecentFile[];
  loading?: boolean;

  /** владелец и автор удаляют любой файл — тот же признак, что за «Записал» выше */
  canDeleteAny?: boolean;
  /** Может ли он удалять хотя бы свои. У агента — нет: он грузит, но не стирает.
      Без этого признака ему достались бы кнопки на файлах актёра, за которого он
      сейчас грузит: `actorName` у грузящего за других — имя из формы, а не своё. */
  canDeleteOwn?: boolean;
  /** id записи, которую сейчас удаляют — блокирует и подсвечивает именно её кнопку */
  deletingFileId?: string;
  /** вызывается уже ПОСЛЕ подтверждения — сам вопрос про цену задаёт эта таблица */
  onDeleteFile?: (row: DictorRecentFile) => void;
};

function formatTime(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 16).replace("T", " ");
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** «2:35», «0:07», «1:02:03» — то, что человек слышит на плеере, не байты. */
function formatDuration(totalSeconds: number): string {
  const total = Math.max(0, Math.round(totalSeconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const ss = String(seconds).padStart(2, "0");
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${ss}`;
  return `${minutes}:${ss}`;
}

/* Диктор помнит имя, а не то, как оно записано в базе — точное совпадение
   здесь не годится. То же сравнение (без диакритики, регистра и пробелов по
   краям), каким `RecordingPage` уже сверяет актёра каста с самим собой. */
function sameActor(a: string, b: string): boolean {
  const left = a.trim().toLocaleLowerCase("ru-RU");
  return left !== "" && left === b.trim().toLocaleLowerCase("ru-RU");
}

const DICTOR_DELETE_WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * Может ли ЭТОТ человек удалить ЭТОТ файл — решает, показывать ли кнопку вовсе.
 * Настоящее право проверяет сервер (`delete_audio_file`, `app/services/audio_deletion.py`)
 * теми же двумя условиями — здесь только предсказание для интерфейса: у чужого или
 * старше суток файла кнопки нет вовсе, а не серая. Серая сообщала бы, что действие
 * существует, и провоцировала бы просьбы «нажмите за меня».
 */
function canDeleteRecentFile(
  row: DictorRecentFile,
  actorName: string,
  canDeleteAny: boolean,
  canDeleteOwn: boolean,
): boolean {
  if (canDeleteAny) return true;
  if (!canDeleteOwn) return false;
  if (!sameActor(row.actor_name, actorName)) return false;
  const uploaded = new Date(row.uploaded_at).getTime();
  if (Number.isNaN(uploaded)) return false;
  return Date.now() - uploaded < DICTOR_DELETE_WINDOW_MS;
}

/** Вопрос называет цену — какая роль, какая глава, когда прислан, сколько длится
    запись — а не голое «удалить файл?»: эту кнопку нажимают раз в месяц и не
    помнят, что открыто. Время загрузки — та же страховка, что и у пробы рядом
    (`AuditionsSheet`): у роли в главе бывает несколько дублей близкой длины, и
    без времени два подтверждения неотличимы — можно стереть не тот безвозвратно. */
function confirmDeleteRecentFile(row: DictorRecentFile): boolean {
  return window.confirm(
    `Удалить дубль роли «${row.role || "без роли"}», глава «${row.chapter || "без названия"}», `
      + `загружен ${formatTime(row.uploaded_at)}, длительность ${formatDuration(row.duration_seconds)}?\n\n`
      + `Файл «${row.canonical_filename}» будет стёрт с сервера и с зеркала на NAS. Отката не будет.`,
  );
}

function buildFileColumns(
  actorName: string,
  canDeleteAny: boolean,
  canDeleteOwn: boolean,
  deletingFileId: string | undefined,
  onDeleteFile: ((row: DictorRecentFile) => void) | undefined,
): Column<DictorRecentFile>[] {
  return [
    { key: "at", header: "Когда", render: (row) => <span className="num">{formatTime(row.uploaded_at)}</span>, nowrap: true, width: "5.5em" },
    { key: "role", header: "Роль", render: (row) => row.role || "—" },
    { key: "file", header: "Файл", render: (row) => <span className="rec-file-cell mono" title={row.canonical_filename}>{row.canonical_filename}</span> },
    {
      key: "actions",
      header: "",
      nowrap: true,
      width: "5em",
      render: (row) => {
        if (!onDeleteFile || !canDeleteRecentFile(row, actorName, canDeleteAny, canDeleteOwn)) return null;
        return (
          <Button
            size="sm"
            variant="danger"
            loading={Boolean(row.id) && row.id === deletingFileId}
            onClick={() => {
              if (confirmDeleteRecentFile(row)) onDeleteFile(row);
            }}
          >
            Удалить
          </Button>
        );
      },
    },
  ];
}

export function RecordingPanel(props: RecordingPanelProps) {
  const { books, bookId, onBook, actorName, canUploadForOthers, onActorName, actorNames, roleOwner, myRoles, cast, role, onRole, roleScriptSearch, chapters, chapterId, onChapter, kind, chapterOptional, expectedFilename, notice, upload, recentFiles, loading, canDeleteAny, canDeleteOwn = true, deletingFileId, onDeleteFile } = props;
  const fileColumns = useMemo(
    () => buildFileColumns(actorName, Boolean(canDeleteAny), canDeleteOwn, deletingFileId, onDeleteFile),
    [actorName, canDeleteAny, canDeleteOwn, deletingFileId, onDeleteFile],
  );
  /* «Проба» бывает двух видов, и путать их дорого: роль ничья — это настоящая проба;
     роль чужая — почти всегда забыли сменить имя в поле «Записал». */
  const takenByOther = Boolean(roleOwner && roleOwner.trim() && roleOwner.trim() !== actorName.trim());

  const audition = kind === "audition";
  // Предлагаются все роли книги: пробуются как раз на ту, которой ещё нет.
  const offered = cast.length ? cast : myRoles;
  const offeredNames = useMemo(() => new Set(offered.map((item) => item.name)), [offered]);
  // A role the cast does not list (typed by hand) keeps the text input open.
  const [custom, setCustom] = useState(() => Boolean(role) && !offeredNames.has(role));
  const selectValue = custom ? CUSTOM : role;

  const roleHint = myRoles.length
    ? `Ваши роли по касту: ${myRoles.map((item) => item.name).join(", ")}.`
    : cast.length
      ? "Роль не назначена: вашего имени нет в касте этой книги. Выберите роль из списка или впишите её."
      : chapters.length
        ? "Роль не назначена: каст главы ещё пуст. Впишите роль вручную."
        : undefined;

  return (
    <aside className="rec-panel panel" aria-label="Запись">
      <div className="rec-panel-head">
        <h2 className="rec-panel-title">Запись</h2>
        {!canUploadForOthers ? (
          <span className="rec-panel-actor muted" title="Имя актёра для файлов">{actorName || "—"}</span>
        ) : null}
      </div>

      {/* Имя актёра решает, чья это запись: по нему считается покрытие, уходят
          уведомления и складывается смета. Менять его вправе только те, к кому
          попадают записи, присланные мимо системы. */}
      {canUploadForOthers && onActorName ? (
        <Field label="Записал" hint="Чьё имя встанет на файлы. Оставьте своё, если запись ваша.">
          {(field) => (
            <>
              <input
                {...field}
                className="ui-input"
                type="text"
                list="rec-actor-names"
                value={actorName}
                placeholder="Имя актёра как в касте"
                onChange={(event) => onActorName(event.target.value)}
              />
              <datalist id="rec-actor-names">
                {(actorNames || []).map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </>
          )}
        </Field>
      ) : null}

      {notice ? <p className="ui-note ui-note--info rec-note">{notice}</p> : null}

      {books.length > 1 ? (
        <Field label="Книга">
          {(field) => (
            <select {...field} className="ui-input rec-select" value={bookId} onChange={(event) => onBook(event.target.value)}>
              {books.map((book) => (
                <option key={book.id} value={book.id}>
                  {book.title}
                </option>
              ))}
            </select>
          )}
        </Field>
      ) : books.length === 1 ? (
        <p className="rec-book-line">
          <span className="rec-label">Книга</span>
          <strong>{books[0].title}</strong>
        </p>
      ) : null}

      <Field label="Роль" hint={roleHint}>
        {(field) => (
          <div className="rec-role">
            <select
              {...field}
              className="ui-input rec-select"
              value={selectValue}
              disabled={loading}
              onChange={(event) => {
                const value = event.target.value;
                if (value === CUSTOM) {
                  setCustom(true);
                  onRole("");
                  return;
                }
                setCustom(false);
                onRole(value);
              }}
            >
              <option value="">— выберите роль —</option>
              {offered.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name} · {item.lines}
                </option>
              ))}
              <option value={CUSTOM}>Другая роль…</option>
            </select>
            {custom ? (
              <input
                className="ui-input"
                type="text"
                value={role}
                placeholder="Имя роли как в касте"
                aria-label="Имя роли"
                onChange={(event) => onRole(event.target.value)}
              />
            ) : null}
            {role && bookId ? (
              <Link
                className="rec-role-all"
                to={roleScriptHref(bookId, role, "/recording/role", roleScriptSearch)}
                title="Все реплики этой роли по всей книге, с контекстом"
              >
                Все реплики роли в книге →
              </Link>
            ) : null}
          </div>
        )}
      </Field>

      {/* У пробы главу не требуют — назвать можно, но роль ещё не назначена. Именно
          требование главы и загоняло слово «пробы» внутрь имени файла. */}
      <Field label={chapterOptional ? "Глава, из которой читали" : "Глава"}>
        {(field) => (
          <select
            {...field}
            className="ui-input rec-select"
            value={chapterId}
            disabled={loading || !chapters.length}
            onChange={(event) => onChapter(event.target.value)}
          >
            {chapterOptional ? <option value="">— не указана —</option> : null}
            {!chapters.length && !chapterOptional ? <option value="">— нет опубликованных глав —</option> : null}
            {chapters.map((chapter) => (
              <option key={chapter.id} value={chapter.id}>
                {chapter.index}. {chapter.title || "Без названия"}
              </option>
            ))}
          </select>
        )}
      </Field>

      <section className="rec-section" aria-label={audition ? "Пробы" : "Дубли"}>
        {/* Диктора не спрашивают, проба это или дубль: он не обязан помнить, на что
            утверждён, — а система обязана. Ему показывают, что из этого вышло. */}
        <div className={audition ? "rec-kind-badge is-audition" : "rec-kind-badge"}>
          <strong>
            {audition ? (takenByOther ? `Проба — а роль за «${roleOwner}»` : "Проба на роль") : "Дубль утверждённой роли"}
          </strong>
          <span>
            {!audition
              ? `Роль за «${actorName}» — файл встанет в запись главы.`
              : takenByOther
                ? `Файл уйдёт в пробы «${actorName}». Если это запись «${roleOwner}» — впишите его в поле «Записал» выше.`
                : "Роль пока ничья — файл уйдёт в пробы. Главу можно не указывать, попыток может быть несколько."}
          </span>
        </div>

        {/* Не образец, а именно то имя, которого ждут от этого файла. */}
        {expectedFilename ? (
          <div className="rec-naming">
            <span className="rec-naming-label">Назовите файл так:</span>
            <code className="rec-naming-name mono">{expectedFilename}</code>
            <button
              type="button"
              className="rec-naming-copy"
              title="Скопировать имя"
              onClick={() => void navigator.clipboard?.writeText(expectedFilename)}
            >
              Скопировать
            </button>
          </div>
        ) : (
          <p className="rec-naming-empty muted">
            Выберите роль — и здесь появится точное имя, которым назвать файл.
          </p>
        )}
        <p className="rec-naming-rule muted">
          Утверждённая роль — <code className="mono">KP_Ch01_Dgarnin_Zotov.wav</code>, проба —{" "}
          <code className="mono">KP_Ch01_Osniva_NataliGolubeva_proba.wav</code>. Книга, глава, роль, актёр — в этом порядке.{" "}
          <code className="mono">Ch01</code> — номер главы: <code className="mono">Ch01</code> — первая, <code className="mono">Ch34</code> — тридцать
          четвёртая. Глава берётся из выбора выше, а не из имени файла; если они разойдутся, система не примет файл.
        </p>
        {/* Правило хранения исходников: запись на сервере лежит в одном экземпляре,
            и без копии у диктора её неоткуда восстановить, если она пропадёт. */}
        <p className="rec-naming-rule muted">
          Не удаляйте исходники у себя до выхода последней главы и сохраните копию в надёжном месте.
          На сервере запись хранится в одном экземпляре — восстановить её, если она пропадёт, будет неоткуда.
        </p>
        <BatchUpload
          files={upload.files}
          preview={upload.preview}
          status={upload.status}
          isUploading={upload.isUploading}
          progress={upload.progress}
          blocked={upload.blocked}
          disabled={Boolean(upload.disabledReason)}
          disabledReason={upload.disabledReason}
          confirmChapter={upload.confirmChapter}
          chapterMismatchRefused={upload.chapterMismatchRefused}
          onSelect={upload.onSelect}
          onClear={upload.onClear}
          onUpload={upload.onUpload}
          onConfirmChapter={upload.onConfirmChapter}
        />
      </section>

      <section className="rec-section" aria-label="Последние файлы">
        <h3 className="rec-label">Последние файлы главы</h3>
        <DataTable
          className="rec-table"
          columns={fileColumns}
          rows={recentFiles}
          rowKey={(row) => row.id || row.canonical_filename}
          loading={loading}
          skeletonRows={2}
          empty={<p className="rec-table-empty">В этой главе пока ничего не загружено.</p>}
          aria-label="Последние загруженные файлы"
        />
      </section>
    </aside>
  );
}
