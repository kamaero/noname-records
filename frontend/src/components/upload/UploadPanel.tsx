import "./upload.css";

import { useId, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, apiPostJson, apiUploadForm } from "../../api/client";
import { useToast } from "../ToastProvider";
import { Icon } from "../Icon";
import { Button, Field } from "../../ui";
import type { UploadBookResponse } from "../../types";

const ACCEPT = ".txt,.epub,.fb2,.docx";
const ACCEPT_EXT = ACCEPT.split(",");
const MAX_MB = 200;

type UploadPanelProps = {
  /** called after the book exists (and v2 was started, if asked); default navigates to the book */
  onDone?: (bookId: string) => void;
  /** shown as a ghost «Отмена» when the panel is inline */
  onCancel?: () => void;
  autoFocus?: boolean;
};

type Phase = "idle" | "uploading" | "starting";

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${(bytes / (1024 * 1024)).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
}

function describeUploadError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.errorCode) {
      case "file_required":
        return "Сервер не получил файл. Выберите его заново.";
      case "active_book_exists":
        return "Сервер ещё держит запрет на вторую активную книгу. Нужен перезапуск сервера.";
    }
    if (error.status === 413) return `Файл слишком большой. Предел на сервере меньше ${MAX_MB} МБ.`;
    if (error.status === 403) return "У вашей роли нет права загружать книги.";
    if (error.status === 0) return error.message || "Сеть недоступна. Проверьте соединение и повторите.";
    if (error.message && !error.message.startsWith("API ")) return error.message;
    return `Сервер ответил ошибкой ${error.status}. Повторите через минуту.`;
  }
  if (error instanceof Error && error.message) return error.message;
  return "Не удалось загрузить книгу.";
}

/**
 * Upload form used inline by the Library and by /upload.
 * POST /api/books/upload (multipart; query params as in app/api/book_actions.py:api_upload_book),
 * then optionally POST /api/v2/books/{id}/run.
 */
export function UploadPanel({ onDone, onCancel, autoFocus }: UploadPanelProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const dropId = useId();

  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [runV2, setRunV2] = useState(true);
  const [dragOver, setDragOver] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [fileError, setFileError] = useState("");
  const [formError, setFormError] = useState("");

  const busy = phase !== "idle";

  const pickFile = (next: File | null) => {
    setFileError("");
    setFormError("");
    if (!next) {
      setFile(null);
      return;
    }
    const lower = next.name.toLowerCase();
    if (!ACCEPT_EXT.some((ext) => lower.endsWith(ext))) {
      setFile(null);
      setFileError(`Формат не поддерживается. Подойдут ${ACCEPT_EXT.join(", ")}.`);
      return;
    }
    if (next.size > MAX_MB * 1024 * 1024) {
      setFile(null);
      setFileError(`Файл больше ${MAX_MB} МБ. Разбейте книгу или сожмите файл.`);
      return;
    }
    setFile(next);
  };

  const onInput = (event: ChangeEvent<HTMLInputElement>) => pickFile(event.target.files?.[0] ?? null);

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragOver(false);
    if (busy) return;
    pickFile(event.dataTransfer.files?.[0] ?? null);
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    if (!file) {
      setFileError("Выберите файл книги.");
      inputRef.current?.focus();
      return;
    }
    setFormError("");
    setPhase("uploading");
    setProgress(0);

    const params = new URLSearchParams();
    // `v2` keeps the old orchestrator off the book (app/v2/pipeline.py:is_v2_book);
    // the server normalises unknown modes to `standard`.
    params.set("pipeline_mode", runV2 ? "v2" : "standard");
    const cleanTitle = title.trim();
    const cleanAuthor = author.trim();
    if (cleanTitle) params.set("title", cleanTitle);
    if (cleanAuthor) params.set("author", cleanAuthor);

    const formData = new FormData();
    formData.append("file", file, file.name);

    let bookId = "";
    let bookTitle = cleanTitle || file.name;
    try {
      const result = await apiUploadForm<UploadBookResponse>(`/api/books/upload?${params.toString()}`, formData, setProgress);
      if (!result.ok || !result.book_id) {
        setPhase("idle");
        setFormError(describeUploadError(new ApiError(result.error || "Не удалось загрузить книгу.", { status: 200, errorCode: result.error })));
        return;
      }
      bookId = result.book_id;
      bookTitle = result.book_title || bookTitle;
    } catch (error) {
      setPhase("idle");
      setFormError(describeUploadError(error));
      return;
    }

    let runStarted = false;
    let runNote = "";
    if (runV2) {
      setPhase("starting");
      try {
        await apiPostJson(`/api/v2/books/${encodeURIComponent(bookId)}/run`, {});
        runStarted = true;
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          runStarted = true; // already running — that is what we wanted
        } else {
          runNote = describeUploadError(error);
        }
      }
    }

    void queryClient.invalidateQueries({ queryKey: ["books"] });
    pushToast({
      tone: runNote ? "info" : "success",
      title: "Книга загружена",
      detail: runStarted ? `«${bookTitle}» — разметка v2 запущена.` : runNote ? `«${bookTitle}». Разметку не удалось запустить: ${runNote}` : `«${bookTitle}» добавлена в библиотеку.`,
    });
    setPhase("idle");
    if (onDone) onDone(bookId);
    else navigate(`/books/${encodeURIComponent(bookId)}`);
  };

  const submitLabel = phase === "uploading" ? `Загружаю… ${Math.round(progress * 100)}%` : phase === "starting" ? "Запускаю разметку…" : runV2 ? "Загрузить и разметить" : "Загрузить книгу";

  return (
    <form className="upl" onSubmit={onSubmit} aria-busy={busy || undefined} noValidate>
      <div className="upl-grid">
        <div className={["upl-drop", dragOver ? "is-over" : "", file ? "has-file" : "", fileError ? "has-error" : ""].filter(Boolean).join(" ")}>
          <div
            className="upl-dropzone"
            onDragOver={(event) => {
              event.preventDefault();
              if (!busy) setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
          >
            <input
              ref={inputRef}
              id={dropId}
              className="upl-input"
              type="file"
              accept={ACCEPT}
              disabled={busy}
              onChange={onInput}
              aria-describedby={`${dropId}-hint`}
              aria-invalid={fileError ? true : undefined}
              autoFocus={autoFocus}
            />
            <Icon name={file ? "doc" : "upload"} className="upl-drop-icon" />
            {file ? (
              <div className="upl-file">
                <strong className="upl-file-name">{file.name}</strong>
                <span className="upl-file-meta num">{formatSize(file.size)}</span>
              </div>
            ) : (
              <div className="upl-file">
                <strong>Перетащите файл сюда</strong>
                <span className="upl-file-meta">txt, epub, fb2 или docx</span>
              </div>
            )}
            <label htmlFor={dropId} className="ui-btn ui-btn--secondary ui-btn--sm upl-pick">
              {file ? "Выбрать другой файл" : "Выбрать файл"}
            </label>
          </div>
          {fileError ? (
            <p className="ui-field-error" id={`${dropId}-hint`}>
              {fileError}
            </p>
          ) : (
            <p className="ui-field-hint" id={`${dropId}-hint`}>
              Один файл — одна книга. Главы определятся по заголовкам.
            </p>
          )}
          {phase === "uploading" ? (
            <div className="upl-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
              <span className="bar">
                <i style={{ width: `${Math.round(progress * 100)}%` }} />
              </span>
            </div>
          ) : null}
        </div>

        <div className="upl-fields">
          <Field label="Название" hint="Пусто — возьмём из имени файла. Без автора: из названия складывается код для имён файлов актёров">
            {(props) => (
              <input
                {...props}
                className="ui-input"
                value={title}
                disabled={busy}
                placeholder="Из имени файла"
                onChange={(event) => setTitle(event.target.value)}
              />
            )}
          </Field>
          <Field label="Автор" hint={'Книга покажется как «Иванов - "Тихий берег"»'}>
            {(props) => <input {...props} className="ui-input" value={author} disabled={busy} placeholder="Из имени файла, если узнаётся" onChange={(event) => setAuthor(event.target.value)} />}
          </Field>
          <label className="ui-check">
            <input type="checkbox" checked={runV2} disabled={busy} onChange={(event) => setRunV2(event.target.checked)} />
            Запустить разметку v2 сразу
          </label>
        </div>
      </div>

      {formError ? (
        <p className="ui-note ui-note--error" role="alert">
          {formError}
        </p>
      ) : null}

      <div className="upl-actions">
        <Button type="submit" variant="primary" loading={busy} icon={<Icon name="upload" />}>
          {submitLabel}
        </Button>
        {onCancel ? (
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            Отмена
          </Button>
        ) : null}
      </div>
    </form>
  );
}
