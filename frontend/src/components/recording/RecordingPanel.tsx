import { roleLabel } from "../../v2/types";
/**
 * The «Загрузка» form: which book, which role, which chapter, what the file becomes —
 * then the batch drop zone and the chapter's last files. Until 29.10 it was a narrow
 * sticky panel beside the script; on its own page it gets two columns: the choice on
 * the left, the files on the right.
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
export type PanelChapter = { id: string; index: number; title: string; released?: boolean };

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
  return date.toLocaleString("en-US", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
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
    `Delete take for role «${row.role || "no role"}», chapter «${row.chapter || "untitled"}», `
      + `uploaded ${formatTime(row.uploaded_at)}, duration ${formatDuration(row.duration_seconds)}?\n\n`
      + `File «${row.canonical_filename}» will be deleted from the server and NAS mirror. This cannot be undone.`,
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
    { key: "at", header: "When", render: (row) => <span className="num">{formatTime(row.uploaded_at)}</span>, nowrap: true, width: "5.5em" },
    { key: "role", header: "Role", render: (row) => row.role || "—" },
    { key: "file", header: "File", render: (row) => <span className="rec-file-cell mono" title={row.canonical_filename}>{row.canonical_filename}</span> },
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
            Delete
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
    ? `Your assigned roles: ${myRoles.map((item) => item.name).join(", ")}.`
    : cast.length
      ? "No role assigned: your name is not in this book's cast. Choose a role or enter one."
      : chapters.length
        ? "No role assigned: this chapter has no cast yet. Enter a role manually."
        : undefined;

  return (
    <div className="rec-panel panel">
      <div className="rec-col rec-col--choose">
        {!canUploadForOthers ? (
          <p className="rec-book-line">
            <span className="rec-label">Recorded by</span>
            <strong title="Voice actor name for files">{actorName || "—"}</strong>
          </p>
        ) : null}

        {/* Имя актёра решает, чья это запись: по нему считается покрытие, уходят
            уведомления и складывается смета. Менять его вправе только те, к кому
            попадают записи, присланные мимо системы. */}
        {canUploadForOthers && onActorName ? (
          <Field label="Recorded by" hint="Name shown on the files. Keep your own name for your recordings.">
            {(field) => (
              <>
                <input
                  {...field}
                  className="ui-input"
                  type="text"
                  list="rec-actor-names"
                  value={actorName}
                  placeholder="Voice actor name as shown in cast"
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
          <Field label="Book">
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
            <span className="rec-label">Book</span>
            <strong>{books[0].title}</strong>
          </p>
        ) : null}

        <Field label="Role" hint={roleHint}>
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
                <option value="">— choose a role —</option>
                {offered.map((item) => {
                  // Роль утверждена за другим (имя без «?») — пробу на неё не примут (решение
                  // владельца 05.10); показываем, но выбрать нельзя, чтобы не писать впустую.
                  const holder = (item.actor || "").trim();
                  // Свои роли сервер узнаёт нестрогим сравнением имён (`myRoles`) — «Мелвис
                  // Моркен» в касте и «Моркен Мелвис» в учётке; строгое сравнение здесь
                  // заперло бы человеку его же роль.
                  const mine = sameActor(holder, actorName) || myRoles.some((own) => own.name === item.name);
                  const taken = Boolean(holder) && !holder.endsWith("?") && !mine;
                  return (
                    <option key={item.name} value={item.name} disabled={taken}>
                      {roleLabel(item.name)} · {item.lines}{taken ? " · approved" : ""}
                    </option>
                  );
                })}
                <option value={CUSTOM}>Another role…</option>
              </select>
              {custom ? (
                <input
                  className="ui-input"
                  type="text"
                  value={role}
                  placeholder="Role name as shown in cast"
                  aria-label="Role name"
                  onChange={(event) => onRole(event.target.value)}
                />
              ) : null}
              {role && bookId ? (
                <Link
                  className="rec-role-all"
                  to={roleScriptHref(bookId, role, "/recording/role", roleScriptSearch)}
                  title="All lines for this role throughout the book, with context"
                >
                  All lines for this role →
                </Link>
              ) : null}
            </div>
          )}
        </Field>

        {/* У пробы главу не требуют — назвать можно, но роль ещё не назначена. Именно
            требование главы и загоняло слово «пробы» внутрь имени файла. */}
        <Field label={chapterOptional ? "Chapter read from" : "Chapter"}>
          {(field) => (
            <select
              {...field}
              className="ui-input rec-select"
              value={chapterId}
              disabled={loading || !chapters.length}
              onChange={(event) => onChapter(event.target.value)}
            >
              {chapterOptional ? <option value="">— not specified —</option> : null}
              {!chapters.length && !chapterOptional ? <option value="">— no published chapters —</option> : null}
              {chapters.map((chapter) => (
                <option key={chapter.id} value={chapter.id}>
                  {chapter.index}. {chapter.title || "Untitled"}{chapter.released ? " · delivered" : ""}
                </option>
              ))}
            </select>
          )}
        </Field>

        <section className="rec-section" aria-label={audition ? "Audition" : "Take"}>
          {/* Диктора не спрашивают, проба это или дубль: он не обязан помнить, на что
              утверждён, — а система обязана. Ему показывают, что из этого вышло. */}
          <div className={audition ? "rec-kind-badge is-audition" : "rec-kind-badge"}>
            <strong>
              {audition ? (takenByOther ? `Audition; role assigned to «${roleOwner}»` : "Role audition") : "Take for an assigned role"}
            </strong>
            <span>
              {!audition
                ? `Role assigned to «${actorName}»; this file will become a chapter recording.`
                : takenByOther
                  ? `This file will be saved as an audition for «${actorName}». If it was recorded by «${roleOwner}», enter their name in Recorded by above.`
                  : "This role is unassigned. The file will be saved as an audition. Chapter is optional; you may submit multiple attempts."}
            </span>
          </div>

          {/* Не образец, а именно то имя, которого ждут от этого файла. */}
          {expectedFilename ? (
            <div className="rec-naming">
              <span className="rec-naming-label">Name your file:</span>
              <code className="rec-naming-name mono">{expectedFilename}</code>
              <button
                type="button"
                className="rec-naming-copy"
                title="Copy filename"
                onClick={() => void navigator.clipboard?.writeText(expectedFilename)}
              >
                Copy
              </button>
            </div>
          ) : (
            <p className="rec-naming-empty muted">
              Choose a role to see the exact filename to use.
            </p>
          )}
          <p className="rec-naming-rule muted">
            Assigned role — <code className="mono">KP_Ch01_Dgarnin_Zotov.wav</code>, audition —{" "}
            <code className="mono">KP_Ch01_Osniva_NataliGolubeva_proba.wav</code>. Book, chapter, role, voice actor, in that order.{" "}
            <code className="mono">Ch01</code> — chapter number: <code className="mono">Ch01</code> — first, <code className="mono">Ch34</code> — thirty-fourth. The chapter is taken from your selection above; mismatching filenames are rejected.
          </p>
          {/* Правило хранения исходников: запись на сервере лежит в одном экземпляре,
              и без копии у диктора её неоткуда восстановить, если она пропадёт. */}
          <p className="rec-naming-rule muted">
            Keep your original recordings until the final chapter is released, and store a safe backup. The server holds one copy; lost recordings cannot be recovered without your originals.
          </p>
        </section>
      </div>

      <div className="rec-col rec-col--files">
        <section className="rec-section" aria-label="Files">
          <h3 className="rec-label">Files</h3>
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

        <section className="rec-section" aria-label="Recent files">
          <h3 className="rec-label">Recent chapter files</h3>
          <DataTable
            className="rec-table"
            columns={fileColumns}
            rows={recentFiles}
            rowKey={(row) => row.id || row.canonical_filename}
            loading={loading}
            skeletonRows={2}
            empty={<p className="rec-table-empty">No files uploaded for this chapter yet.</p>}
            aria-label="Recently uploaded files"
          />
        </section>
      </div>
    </div>
  );
}
