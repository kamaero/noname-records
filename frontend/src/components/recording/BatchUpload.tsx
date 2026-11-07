/**
 * The batch drop zone: pick or drop several takes, see what the server will store
 * them as, then upload. Errors are words under the file, never a blocked button alone.
 */
import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { Icon } from "../Icon";
import { Button } from "../../ui";
import type { BatchValidatedItem } from "../../types";
import type { UploadStatus } from "./useRecordingUploads";

const ACCEPT = ".wav,.mp3,.m4a,.flac,.ogg,.aac,.opus,.webm,audio/*";

const ERROR_TEXT: Record<string, string> = {
  chapter_required: "chapter not specified",
  role_required: "role not specified",
  duplicate_in_batch: "duplicate files in this batch",
  duplicate_in_storage: "this file was already uploaded",
  chapter_mismatch: "filename chapter does not match your selection",
};

/* Предупреждение — не отказ. Роман выбрал в форме «Сатухух», а прислал `03-Za'Lur.wav`,
   и до сих пор форма и файл могли расходиться молча. Решает всё равно диктор. */
const WARNING_TEXT: Record<string, string> = {
  role_name_mismatch: "filename role differs; check your selection",
};

function describeErrors(errors: string[] | undefined): string {
  return (errors ?? []).map((code) => ERROR_TEXT[code] || code).join(", ");
}

function describeWarnings(warnings: string[] | undefined): string {
  return (warnings ?? []).map((code) => WARNING_TEXT[code]).filter(Boolean).join(", ");
}

type BatchUploadProps = {
  files: File[];
  preview: BatchValidatedItem[];
  status: UploadStatus;
  isUploading: boolean;
  /** 0…1 — сколько байт ушло; без этого «Загружаю…» читается как «зависло» */
  progress?: number;
  blocked: boolean;
  disabled?: boolean;
  /** why the zone is disabled (shown instead of the drop hint) */
  disabledReason?: string;
  /** диктор подтвердил: глава выбрана верно, номер в имени файла — просто другой */
  confirmChapter: boolean;
  /** сервер отказал по несовпадению главы: галочка нужна и тогда, когда предпросмотр молчит */
  chapterMismatchRefused: boolean;
  onSelect: (files: File[]) => void;
  onClear: () => void;
  onUpload: () => void;
  onConfirmChapter: (value: boolean) => void;
};

export function BatchUpload({
  files,
  preview,
  status,
  isUploading,
  progress = 0,
  blocked,
  disabled = false,
  disabledReason,
  confirmChapter,
  chapterMismatchRefused,
  onSelect,
  onClear,
  onUpload,
  onConfirmChapter,
}: BatchUploadProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);
  const dragDepth = useRef(0);
  const locked = disabled || isUploading;
  const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer.types).includes("Files");

  useEffect(() => {
    // Сброс мимо области не должен открывать исходник вместо приложения. Текстовые
    // переносы оставляем браузеру; сторож живёт только вместе с формой загрузки.
    const preventFileNavigation = (event: globalThis.DragEvent) => {
      if (event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files")) event.preventDefault();
    };
    window.addEventListener("dragover", preventFileNavigation);
    window.addEventListener("drop", preventFileNavigation);
    return () => {
      window.removeEventListener("dragover", preventFileNavigation);
      window.removeEventListener("drop", preventFileNavigation);
    };
  }, []);

  const open = () => {
    if (!locked) inputRef.current?.click();
  };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  };

  const previewByName = new Map(preview.map((item) => [item.original_filename, item]));
  /* Тикнутая галочка гасит саму ошибку в предпросмотре — но не прячет строку: без неё
     не видно ни что подтверждение в силе, ни как его снять. Отказ сервера считается
     наравне с предпросмотром: предпросмотр мог не ответить вовсе, и тогда он ни о чём
     не предупредит, а отказ уже пришёл — без галочки пачку было бы не отправить. */
  const hasChapterMismatch =
    confirmChapter
    || chapterMismatchRefused
    || preview.some((item) => (item.errors ?? []).includes("chapter_mismatch"));

  return (
    <div
      className={["rec-upload", over ? "is-over" : ""].filter(Boolean).join(" ")}
      onDragEnter={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        dragDepth.current += 1;
        if (!locked) setOver(true);
      }}
      onDragOver={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = locked ? "none" : "copy";
      }}
      onDragLeave={(event) => {
        if (!hasFiles(event)) return;
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (!dragDepth.current) setOver(false);
      }}
      onDrop={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        dragDepth.current = 0;
        setOver(false);
        if (!locked) onSelect(Array.from(event.dataTransfer.files));
      }}
    >
      <div
        role="button"
        tabIndex={locked ? -1 : 0}
        aria-disabled={locked || undefined}
        className={["rec-drop", over ? "is-over" : "", locked ? "is-disabled" : ""].filter(Boolean).join(" ")}
        onClick={open}
        onKeyDown={onKey}
      >
        <Icon name="upload" />
        <span className="rec-drop-text">
          {disabled && disabledReason ? disabledReason : isUploading ? "Uploading recordings…" : "Drop audio files anywhere in this Files area, or click to choose"}
        </span>
        <input
          ref={inputRef}
          type="file"
          multiple
          disabled={locked}
          accept={ACCEPT}
          className="rec-drop-input"
          tabIndex={-1}
          onChange={(event) => {
            if (!locked) onSelect(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
      </div>

      {files.length > 0 ? (
        <ul className="rec-files" aria-label="Files to upload">
          {files.map((file) => {
            const item = previewByName.get(file.name);
            const bad = item ? !item.ok : false;
            const warning = bad ? "" : describeWarnings(item?.warnings);
            return (
              <li key={`${file.name}-${file.size}`} className={bad ? "rec-file is-bad" : "rec-file"}>
                <span className="rec-file-name">{file.name}</span>
                {item ? (
                  <span className={bad ? "rec-file-as rec-file-as--bad" : "rec-file-as mono"}>
                    {bad ? (item.error_text || describeErrors(item.errors)) : `Name preview: ${item.canonical_filename}`}
                  </span>
                ) : null}
                {warning ? <span className="rec-file-warn">{warning}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      {disabled ? null : (
        <>
          {hasChapterMismatch ? (
            <label className="batch-confirm">
              <input
                type="checkbox"
                checked={confirmChapter}
                onChange={(event) => onConfirmChapter(event.target.checked)}
              />
              <span>The selected chapter is correct; the filename number is different</span>
            </label>
          ) : null}
          <div className="rec-upload-actions">
            <Button variant="primary" onClick={onUpload} loading={isUploading} disabled={!files.length || blocked}>
              {files.length ? `Upload files (${files.length})` : "Upload files"}
            </Button>
            {files.length ? (
              <Button variant="ghost" onClick={onClear} disabled={isUploading}>
                Clear list
              </Button>
            ) : null}
          </div>
          {isUploading ? (
            <div className="rec-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
              <span style={{ width: `${Math.max(2, Math.round(progress * 100))}%` }} />
            </div>
          ) : null}
          <p className={`rec-status rec-status--${status.tone}`} role="status" aria-live="polite">
            {status.text}
          </p>
        </>
      )}
    </div>
  );
}
