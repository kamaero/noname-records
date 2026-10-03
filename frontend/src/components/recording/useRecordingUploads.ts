/**
 * Upload state for the recording panel: a batch (validate → preview → upload).
 * Same endpoints and bodies as before:
 *   POST /dictor-pro/batch-validate   (json)  — what the server will store, before it stores it
 *   POST /api/recording/batch         (form)  — files[] + meta_json
 *
 * The old per-line take (`/api/recording/replica-patch`, `line_index` = fountain
 * source line) is not offered here: v2 segments carry no fountain line index, and
 * `daw_exports.py` reads that field, so a made-up index would misfile the audio.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiPostJson, apiUploadForm, describeApiError } from "../../api/client";
import { useToast } from "../ToastProvider";
import type { BatchUploadResponse, BatchValidateResponse, BatchValidatedItem } from "../../types";

export type UploadTone = "info" | "success" | "error";
export type UploadStatus = { text: string; tone: UploadTone };

/** дубль утверждённой роли — или проба на роль, у которой нет главы.
    Выбирает не диктор, а сервер: роль за тобой — дубль, чужая роль — проба. */
export type UploadKind = "take" | "audition";

export type UploadTarget = {
  /** derived from the book title on the server; never typed */
  bookCode: string;
  /** the chapter label the audio is filed under (title or «Глава N») */
  chapter: string;
  role: string;
  actorName: string;
  kind: UploadKind;
};

const IDLE: UploadStatus = { text: "Выберите файлы или перетащите их сюда.", tone: "info" };

/** Отказал ли сервер именно по несовпадению главы: `{ok:false, items:[{error:…}]}` в теле 400. */
function hasChapterMismatchItem(error: ApiError): boolean {
  if (error.status !== 400) return false;
  const items = error.payload.items;
  if (!Array.isArray(items)) return false;
  return items.some((item) => String((item as { error?: unknown })?.error ?? "") === "chapter_mismatch");
}

export function useRecordingUploads(target: UploadTarget) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [preview, setPreview] = useState<BatchValidatedItem[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  /** 0…1 — сколько байт ушло. Дубль весит восемьдесят мегабайт, и без этого числа
      «Загружаю…» стоит несколько минут и читается как «зависло». */
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<UploadStatus>(IDLE);
  /** «Глава выбрана верно, в имени файла номер другой» — диктор подтвердил расхождение.
      Сбрасывается новым выбором файлов: подтверждение относится к конкретной пачке. */
  const [confirmChapter, setConfirmChapter] = useState(false);
  /** Сервер отказал по `chapter_mismatch` — а предпросмотр этого не показал.
      Предпросмотр не гарантирован: упавший `/dictor-pro/batch-validate` просто
      очищает список, и тогда галочки «Глава выбрана верно» на экране нет — снять отказ
      диктору нечем, и пачка встаёт намертво. Отказ загрузки об этом знает достоверно,
      поэтому галочку показываем и по нему. Живёт по тем же правилам, что и само
      подтверждение: новая пачка или новая цель начинают всё заново. */
  const [chapterMismatchRefused, setChapterMismatchRefused] = useState(false);

  // The latest target wins: a preview started for the old role must not land on the new one.
  const targetRef = useRef(target);
  targetRef.current = target;
  const previewSeq = useRef(0);

  const refreshPreview = useCallback(async (next: File[], confirm: boolean) => {
    const seq = ++previewSeq.current;
    if (!next.length) {
      setPreview([]);
      return;
    }
    const t = targetRef.current;
    const override = {
      book_code: t.bookCode,
      chapter: t.chapter,
      role: t.role.trim(),
      actor_name: t.actorName.trim(),
      kind: t.kind,
      confirm_chapter: confirm,
    };
    try {
      const result = await apiPostJson<BatchValidateResponse>("/dictor-pro/batch-validate", {
        book_code: t.bookCode,
        actor_name: t.actorName.trim(),
        files: next.map((file) => ({ name: file.name, size: file.size })),
        overrides: next.map(() => override),
      });
      if (seq === previewSeq.current) setPreview(result.items ?? []);
    } catch {
      // The preview is an aid, not a gate the upload depends on.
      if (seq === previewSeq.current) setPreview([]);
    }
  }, []);

  const select = useCallback(
    (picked: File[]) => {
      const valid = picked.filter(Boolean);
      setFiles(valid);
      setConfirmChapter(false);
      setChapterMismatchRefused(false);
      setStatus(valid.length ? { text: `Файлов к загрузке: ${valid.length}.`, tone: "info" } : IDLE);
      void refreshPreview(valid, false);
    },
    [refreshPreview],
  );

  const clear = useCallback(() => {
    setFiles([]);
    setPreview([]);
    setConfirmChapter(false);
    setChapterMismatchRefused(false);
    setStatus(IDLE);
  }, []);

  /** Галочка «Глава выбрана верно» — снятие отказа перевалидирует пачку тем же вызовом,
      что и смена файлов, иначе кнопка осталась бы заблокированной до следующего события. */
  const onConfirmChapter = useCallback(
    (value: boolean) => {
      setConfirmChapter(value);
      if (files.length) void refreshPreview(files, value);
    },
    [files, refreshPreview],
  );

  // Role or chapter changed under a pending batch: the canonical names change with them,
  // and a confirmation given for the old chapter must not silently wave through a new
  // mismatch — a target change starts the batch over, unconfirmed, same as `select`.
  const targetKey = `${target.bookCode}|${target.chapter}|${target.role}|${target.actorName}|${target.kind}`;
  useEffect(() => {
    setConfirmChapter(false);
    setChapterMismatchRefused(false);
    if (files.length) void refreshPreview(files, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey]);

  const uploadBatch = useCallback(async () => {
    const t = targetRef.current;
    if (!files.length) return;
    if (!t.role.trim()) {
      setStatus({ text: "Выберите роль — без неё файлы некуда сохранить.", tone: "error" });
      return;
    }
    // у пробы главы нет: её и спрашивать не станут
    if (t.kind === "take" && !t.chapter.trim()) {
      setStatus({ text: "Выберите главу — без неё файлы некуда сохранить.", tone: "error" });
      return;
    }
    setIsUploading(true);
    setProgress(0);
    const megabytes = files.reduce((sum, file) => sum + file.size, 0) / (1024 * 1024);
    setStatus({ text: `Отправляю ${megabytes.toFixed(0)} МБ… 0%`, tone: "info" });
    try {
      const fd = new FormData();
      files.forEach((file) => fd.append("files", file));
      const meta = files.map(() => ({
        book_code: t.bookCode,
        chapter: t.chapter,
        role: t.role.trim(),
        actor_name: t.actorName.trim(),
        confirm_chapter: confirmChapter,
      }));
      fd.append("meta_json", JSON.stringify(meta));
      const result = await apiUploadForm<BatchUploadResponse>("/api/recording/batch", fd, (fraction) => {
        setProgress(fraction);
        // Сто процентов отправленных байт — ещё не сохранение: сервер их только принял.
        const text = fraction >= 1
          ? "Файлы у сервера, сохраняю…"
          : `Отправляю ${megabytes.toFixed(0)} МБ… ${Math.round(fraction * 100)}%`;
        setStatus({ text, tone: "info" });
      });
      if (!result.ok) throw new Error(result.message || result.error || "upload_failed");
      const saved = result.saved_count ?? 0;
      setFiles([]);
      setPreview([]);
      setConfirmChapter(false);
      setChapterMismatchRefused(false);
      setProgress(0);
      setStatus({
        text: `Готово: сохранено ${saved} ${saved === 1 ? "файл" : saved < 5 ? "файла" : "файлов"}.`,
        tone: "success",
      });
      pushToast({
        tone: "success",
        title: t.kind === "audition" ? "Пробы загружены" : "Дубли загружены",
        detail: `Сохранено файлов: ${saved}.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["recording-workspace"] });
      await queryClient.invalidateQueries({ queryKey: ["v2", "auditions"] });
    } catch (error) {
      const detail = describeApiError(error, "Сервер не принял файлы.");
      setProgress(0);
      if (error instanceof ApiError && hasChapterMismatchItem(error)) setChapterMismatchRefused(true);
      // Частичный отказ (507 «диск полон»): часть пачки уже сохранена и закоммичена.
      // Сказать про неё «файлы не загружены» — соврать и заставить прислать гигабайты
      // заново; диктору нужно знать, сколько дошло и что именно не приняли.
      const savedAnyway = error instanceof ApiError ? Number(error.payload.saved_count ?? 0) : 0;
      if (savedAnyway > 0) {
        const sent = files.length;
        setStatus({ text: `Загружено ${savedAnyway} из ${sent}, не принято: ${detail}`, tone: "error" });
        pushToast({ tone: "error", title: `Загружено ${savedAnyway} из ${sent}`, detail });
        await queryClient.invalidateQueries({ queryKey: ["recording-workspace"] });
        await queryClient.invalidateQueries({ queryKey: ["v2", "auditions"] });
      } else {
        setStatus({ text: `Не удалось загрузить: ${detail}`, tone: "error" });
        pushToast({ tone: "error", title: "Файлы не загружены", detail });
      }
    } finally {
      setIsUploading(false);
    }
  }, [files, confirmChapter, pushToast, queryClient]);

  const blocked = preview.some((item) => !item.ok);
  return {
    files, preview, isUploading, progress, status, blocked,
    confirmChapter, chapterMismatchRefused, onConfirmChapter, select, clear, uploadBatch,
  };
}

/**
 * Точное имя, которым диктору назвать файл при нынешнем выборе.
 *
 * Не образец, а именно то имя, которого от файла ждут: «назовите как-нибудь так»
 * оставляет варианты, а вариантов быть не должно. Считает сервер — транслитерация
 * живёт в одном месте, и второй её экземпляр на другом языке разошёлся бы с первым.
 */
export function useExpectedFilename(target: Omit<UploadTarget, "kind">): { filename: string; kind: UploadKind } {
  const role = target.role.trim();
  const query = useQuery({
    queryKey: ["recording-filename", target.bookCode, target.chapter, role, target.actorName],
    queryFn: () => {
      const params = new URLSearchParams({
        book_code: target.bookCode,
        chapter: target.chapter,
        role,
        actor_name: target.actorName.trim(),
      });
      return apiGet<{ ok: boolean; filename: string; kind: UploadKind }>(`/api/recording/filename?${params.toString()}`);
    },
    enabled: Boolean(role && target.bookCode),
    staleTime: 60_000,
  });
  return { filename: query.data?.filename ?? "", kind: query.data?.kind ?? "take" };
}
