/**
 * Как книга подписана: «Автор - "Название"».
 *
 * Автор и название правятся порознь не для удобства формы: из названия
 * складывается код книги, которым актёры подписывают файлы (на сервере —
 * `derive_book_code`), и автор, затёкший в название, даёт чужой код. Поэтому
 * сервер возвращает получившийся код, а карточка показывает его сразу после
 * сохранения — чтобы смена названия не осталась незамеченной для записи.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiPostJson, describeApiError } from "../../../api/client";
import { Button, Field } from "../../../ui";
import { useToast } from "../../ToastProvider";
import { hubKeys } from "./hubApi";

type TitleCardProps = {
  bookId: string;
  /** чистое название без автора — то, что лежит в `title` */
  title: string;
  /** автор так, как его показывают; пусто — книга подписана одним названием */
  author: string;
};

type SaveResponse = { ok: boolean; error?: string; display_title?: string; book_code?: string };

/** отказ «название меняет код книги»: сколько записей подписано прежним кодом */
type CodeClash = { wasCode: string; code: string; files: number };

function preview(author: string, title: string): string {
  const a = author.trim();
  const t = title.trim();
  if (a && t) return `${a} - "${t}"`;
  return t || a || "—";
}

export function TitleCard({ bookId, title, author }: TitleCardProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [nextTitle, setNextTitle] = useState(title);
  const [nextAuthor, setNextAuthor] = useState(author);
  const [clash, setClash] = useState<CodeClash | null>(null);

  const dirty = nextTitle.trim() !== title.trim() || nextAuthor.trim() !== author.trim();

  const save = useMutation({
    mutationFn: (confirm: boolean) =>
      apiPostJson<SaveResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/title`, {
        title: nextTitle.trim(),
        author: nextAuthor.trim(),
        confirm,
      }),
    onSuccess: async (result) => {
      setClash(null);
      if (!result.ok) {
        pushToast({ tone: "error", title: "Title not saved", detail: result.error || "The server returned no details." });
        return;
      }
      pushToast({
        tone: "success",
        title: "Book renamed",
        detail: `«${result.display_title || preview(nextAuthor, nextTitle)}». Filename code: ${result.book_code || "—"}.`,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["books"] }),
        queryClient.invalidateQueries({ queryKey: hubKeys.progress(bookId) }),
      ]);
    },
    onError: (error) => {
      // 409 «code_changes» — не сбой, а вопрос: записи актёров держатся за код книги
      if (error instanceof ApiError && error.errorCode === "code_changes") {
        setClash({
          wasCode: String(error.payload.was_code || ""),
          code: String(error.payload.code || ""),
          files: Number(error.payload.files || 0),
        });
        return;
      }
      pushToast({ tone: "error", title: "Title not saved", detail: describeApiError(error, "Could not save the title.") });
    },
  });

  return (
    <section className="panel hub-card" aria-labelledby="hub-title-title">
      <header className="hub-card-head">
        <h2 id="hub-title-title" className="hub-card-title">Book title</h2>
        <span className="hub-dim">{preview(nextAuthor, nextTitle)}</span>
      </header>
      <div className="hub-title-fields">
        <Field label="Author" hint="Author name shown in the book list">
          {(props) => (
            <input
              {...props}
              className="ui-input"
              value={nextAuthor}
              disabled={save.isPending}
              placeholder="No author"
              onChange={(event) => { setClash(null); setNextAuthor(event.target.value); }}
            />
          )}
        </Field>
        <Field label="Title" hint="Title only: used to generate the book code in recording filenames">
          {(props) => (
            <input
              {...props}
              className="ui-input"
              value={nextTitle}
              disabled={save.isPending}
              onChange={(event) => { setClash(null); setNextTitle(event.target.value); }}
            />
          )}
        </Field>
      </div>
      {clash ? (
        <p className="ui-note ui-note--error" role="alert">
          The new title changes the book code: <strong>{clash.wasCode}</strong> → <strong>{clash.code}</strong>. Files submitted under the old code: {clash.files} — these will no longer be found under the new code. Rename anyway?
        </p>
      ) : null}
      <div className="hub-title-actions">
        <Button
          size="sm"
          variant={clash ? "primary" : "secondary"}
          disabled={!dirty || !nextTitle.trim()}
          loading={save.isPending}
          onClick={() => save.mutate(Boolean(clash))}
        >
          {clash ? "Yes, rename" : "Save"}
        </Button>
        {dirty ? (
          <Button size="sm" variant="ghost" disabled={save.isPending} onClick={() => { setNextTitle(title); setNextAuthor(author); setClash(null); }}>
            Cancel
          </Button>
        ) : null}
      </div>
    </section>
  );
}
