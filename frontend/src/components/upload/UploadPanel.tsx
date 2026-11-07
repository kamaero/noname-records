import "./upload.css";
import { postWithLimit } from "../../utils/limitConfirm";

import { useId, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, apiUploadForm } from "../../api/client";
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
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toLocaleString("en-US", { maximumFractionDigits: 1 })} MB`;
}

function describeUploadError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.errorCode) {
      case "file_required":
        return "The server did not receive the file. Please select it again.";
      case "active_book_exists":
        return "Another book is still active. The server needs to be restarted.";
    }
    if (error.status === 413) return `The file is too large. The server limit is below ${MAX_MB} MB.`;
    if (error.status === 403) return "You do not have permission to upload books.";
    if (error.status === 0) return error.message || "Network unavailable. Check your connection and try again.";
    if (error.message && !error.message.startsWith("API ")) return error.message;
    return `The server returned error ${error.status}. Please try again shortly.`;
  }
  if (error instanceof Error && error.message) return error.message;
  return "Could not upload the book.";
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
  const [language, setLanguage] = useState<"en" | "ru">("en");
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
      setFileError(`Unsupported format. Choose ${ACCEPT_EXT.join(", ")}.`);
      return;
    }
    if (next.size > MAX_MB * 1024 * 1024) {
      setFile(null);
      setFileError(`The file exceeds ${MAX_MB} MB. Split or compress it.`);
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
      setFileError("Choose a book file.");
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
    params.set("language", language);
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
        setFormError(describeUploadError(new ApiError(result.error || "Could not upload the book.", { status: 200, errorCode: result.error })));
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
        await postWithLimit(`/api/v2/books/${encodeURIComponent(bookId)}/run`, {});
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
      title: "Book uploaded",
      detail: runStarted ? `“${bookTitle}” — analysis started.` : runNote ? `“${bookTitle}”. Could not start analysis: ${runNote}` : `“${bookTitle}” was added to the library.`,
    });
    setPhase("idle");
    if (onDone) onDone(bookId);
    else navigate(`/books/${encodeURIComponent(bookId)}`);
  };

  const submitLabel = phase === "uploading" ? `Uploading… ${Math.round(progress * 100)}%` : phase === "starting" ? "Starting analysis…" : runV2 ? "Upload and analyze" : "Upload book";

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
                <strong>Drop a file here</strong>
                <span className="upl-file-meta">txt, epub, fb2 or docx</span>
              </div>
            )}
            <label htmlFor={dropId} className="ui-btn ui-btn--secondary ui-btn--sm upl-pick">
              {file ? "Choose another file" : "Choose file"}
            </label>
          </div>
          {fileError ? (
            <p className="ui-field-error" id={`${dropId}-hint`}>
              {fileError}
            </p>
          ) : (
            <p className="ui-field-hint" id={`${dropId}-hint`}>
              One file per book. Chapters are detected from headings.
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
          <Field label="Title" hint="Leave blank to use the filename. Enter the title without the author.">
            {(props) => (
              <input
                {...props}
                className="ui-input"
                value={title}
                disabled={busy}
                placeholder="From filename"
                onChange={(event) => setTitle(event.target.value)}
              />
            )}
          </Field>
          <Field label="Author" hint="The author will be shown alongside the book title.">
            {(props) => <input {...props} className="ui-input" value={author} disabled={busy} placeholder="From filename, when available" onChange={(event) => setAuthor(event.target.value)} />}
          </Field>
          <Field label="Book language" hint="Choose the language of the book, not the interface.">
            {(props) => (
              <select {...props} className="ui-input" value={language} disabled={busy}
                onChange={(event) => setLanguage(event.target.value as "en" | "ru")}>
                <option value="en">English</option>
                <option value="ru">Russian</option>
              </select>
            )}
          </Field>
          <label className="ui-check">
            <input type="checkbox" checked={runV2} disabled={busy} onChange={(event) => setRunV2(event.target.checked)} />
            Start analysis immediately
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
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}
