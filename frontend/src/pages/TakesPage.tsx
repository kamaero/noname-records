/**
 * Загрузка `/app/takes?book_id=&chapter_id=&role=`
 *
 * The upload form on a page of its own: who recorded, role, chapter, what the file
 * becomes (take or audition), the exact filename, the drop zone, the chapter's last
 * files. Until 29.10 this was the sticky panel beside the script; the owner moved it
 * here so the script page carries only the text and the form gets room. The pick
 * travels in the address, so «Сценарий» and «Загрузка» open on the same chapter.
 *
 * Endpoints are the panel's as before:
 *   GET  /api/recording/workspace?book_id=&chapter_id=
 *   POST /dictor-pro/batch-validate, POST /api/recording/batch  (useRecordingUploads)
 *   POST /api/recording/files/:id/delete
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPostJson, describeApiError } from "../api/client";
import { SkeletonPanel } from "../components/Skeleton";
import { RecordingPanel } from "../components/recording/RecordingPanel";
import { chapterLabel, useRecordingSelection } from "../components/recording/useRecordingSelection";
import { useExpectedFilename, useRecordingUploads } from "../components/recording/useRecordingUploads";
import { useToast } from "../components/ToastProvider";
import { EmptyState, LinkButton, PageHeader } from "../ui";
import type { DictorRecentFile } from "../types";
import "../components/recording/recording.css";

export function TakesPage() {
  const sel = useRecordingSelection();
  const queryClient = useQueryClient();
  const { pushToast } = useToast();

  /* Кнопка живёт в самой таблице (см. `RecordingPanel`) — она уже спросила «точно?»
     словами про роль, главу и длительность; сюда долетает только решённое «да».
     Ручка отвечает человеческим текстом на отказ («Отказы приходят с человеческим
     текстом, не кодом»), поэтому `describeApiError` берёт его как есть. */
  const deleteFileMutation = useMutation({
    mutationFn: (fileId: string) =>
      apiPostJson<{ ok: boolean }>(`/api/recording/files/${encodeURIComponent(fileId)}/delete`, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["recording-workspace"] });
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Not deleted", detail: describeApiError(error, "Could not delete the recording.") });
    },
  });

  const bookCode = sel.workspace?.selected_book?.code || "";
  /* «Глава не указана» — отдельный флажок, а не пустой `chapterId`: глава в адресе
     общая со «Сценарием», и проба, которую читали неизвестно откуда, не должна её
     сбрасывать. Флажок независим от вида загрузки, иначе подсказка имени считалась бы
     по главе, которая от неё же и зависит. */
  const [noChapter, setNoChapter] = useState(false);
  const uploadChapter = noChapter ? "" : chapterLabel(sel.chapter);
  // Проба это или дубль, решает сервер по роли и актёру; здесь только показываем.
  const expected = useExpectedFilename({ bookCode, chapter: uploadChapter, role: sel.role, actorName: sel.actorName });
  const audition = expected.kind === "audition";
  const upload = useRecordingUploads({ bookCode, chapter: uploadChapter, role: sel.role, actorName: sel.actorName, kind: expected.kind });

  const scriptHref = `/recording${sel.searchParams.toString() ? `?${sel.searchParams.toString()}` : ""}`;

  if (sel.workspaceQuery.isLoading) {
    return <SkeletonPanel label="Opening uploads…" lines={6} />;
  }
  if (sel.workspaceQuery.isError) {
    return (
      <EmptyState
        icon="upload"
        text={describeApiError(sel.workspaceQuery.error, "Could not open uploads.")}
        action={<LinkButton to="/">Open library</LinkButton>}
      />
    );
  }
  if (!sel.bookId && !sel.workspace?.selected_book) {
    return (
      <EmptyState
        icon="upload"
        text="No published chapters yet. Uploads will be available when the director publishes the script."
        action={<LinkButton to="/">Open library</LinkButton>}
      />
    );
  }

  const uploadDisabledReason = !sel.chapters.length
    ? "Uploads will be available once chapters are published."
    : sel.preview && !bookCode
      ? "Preview: this book is unpublished; uploads are disabled."
      : undefined;

  return (
    <div className="takes-page">
      <PageHeader
        title="Uploads"
        subtitle="Takes and auditions, organized by chapter. You can also send takes for the active book to the studio bot, including an entire role in one file."
        back={{ to: scriptHref, label: "Script" }}
      />
      <RecordingPanel
        books={sel.panelBooks}
        bookId={sel.bookId}
        onBook={sel.goToBook}
        actorName={sel.actorName}
        canUploadForOthers={sel.canUploadForOthers}
        onActorName={sel.setUploadAs}
        actorNames={sel.castActorNames}
        roleOwner={sel.roleOwner}
        myRoles={sel.myRoles}
        cast={sel.cast}
        role={sel.role}
        onRole={sel.chooseRole}
        roleScriptSearch={sel.searchParams.toString()}
        chapters={sel.chapters}
        chapterId={noChapter ? "" : sel.chapterId}
        onChapter={(id) => {
          setNoChapter(!id);
          if (id) sel.goToChapter(id);
        }}
        kind={expected.kind}
        chapterOptional={audition}
        expectedFilename={expected.filename}
        notice={sel.preview ? "Preview: this book is unpublished; annotated chapters are shown." : undefined}
        upload={{
          files: upload.files,
          preview: upload.preview,
          status: upload.status,
          isUploading: upload.isUploading,
          progress: upload.progress,
          blocked: upload.blocked,
          disabledReason: uploadDisabledReason,
          confirmChapter: upload.confirmChapter,
          chapterMismatchRefused: upload.chapterMismatchRefused,
          onSelect: upload.select,
          onClear: upload.clear,
          onUpload: () => void upload.uploadBatch(),
          onConfirmChapter: upload.onConfirmChapter,
        }}
        recentFiles={sel.workspace?.recent_files ?? []}
        loading={sel.workspaceQuery.isFetching && !sel.workspace}
        canDeleteAny={sel.canDeleteAny}
        canDeleteOwn={!sel.isAgent}
        deletingFileId={deleteFileMutation.isPending ? deleteFileMutation.variables : undefined}
        /* `mutate`, а не `void mutateAsync`: отказ уже разобран в `onError`, а
           отброшенный промис `mutateAsync` вдобавок всплывает как
           unhandled rejection — в консоли и в любом её сборщике. */
        onDeleteFile={(row: DictorRecentFile) => deleteFileMutation.mutate(row.id)}
      />
    </div>
  );
}
