import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPostJson, describeApiError } from "../../../api/client";
import { Button } from "../../../ui";
import { useToast } from "../../ToastProvider";
import { formatWhen, invalidateReader, useBookProfile } from "../../../v2/editorApi";
import type { BindAuthorResponse, ProfileActionResponse } from "../../../v2/types";
import { plural } from "../../../viewModels/bookHub";

/** Author, the two dictionaries, the link to the author profile, and the same sync/apply as the reader's panel. */
export function ProfileCard({ bookId }: { bookId: string }) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const profile = useBookProfile(bookId, true);
  const [overwrite, setOverwrite] = useState(false);
  const base = `/api/v2/books/${encodeURIComponent(bookId)}/profile`;

  const after = async (label: string, result: ProfileActionResponse) => {
    if (!result.ok) {
      pushToast({ tone: "error", title: `${label}: не выполнено`, detail: result.error || "Сервер ответил без подробностей." });
      return;
    }
    pushToast({ tone: "success", title: `${label}: готово` });
    await invalidateReader(queryClient, bookId, ["profile", "chapters", "cast", "queue"]);
  };
  const sync = useMutation({
    mutationFn: () => apiPostJson<ProfileActionResponse>(`${base}/sync`, {}),
    onSuccess: (r) => after("Профиль автора обновлён", r),
    onError: (e) => pushToast({ tone: "error", title: "Не удалось обновить профиль автора", detail: describeApiError(e, "Ошибка синхронизации.") }),
  });
  // Binding is the step that makes «Применить профиль» mean anything: an unbound
  // book has no profile to apply, which is exactly how a cast ends up empty.
  const bind = useMutation({
    mutationFn: (authorId: string) =>
      apiPostJson<BindAuthorResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/author`, { author_id: authorId }),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Автор не привязан", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      const applied = result.applied;
      pushToast({
        tone: "success",
        title: result.author_id ? `Автор: ${result.author_name}` : "Автор отвязан",
        detail: applied ? `Актёров проставлено: ${applied.actors_applied}, цветов: ${applied.colours_applied}.` : undefined,
      });
      await invalidateReader(queryClient, bookId, ["profile", "chapters", "cast", "queue"]);
      await queryClient.invalidateQueries({ queryKey: ["book-budget", bookId] });
    },
    onError: (e) => pushToast({ tone: "error", title: "Не удалось привязать автора", detail: describeApiError(e, "Ошибка привязки.") }),
  });

  const apply = useMutation({
    mutationFn: () => apiPostJson<ProfileActionResponse>(`${base}/apply`, { overwrite }),
    onSuccess: (r) => after("Профиль применён к книге", r),
    onError: (e) => pushToast({ tone: "error", title: "Не удалось применить профиль", detail: describeApiError(e, "Ошибка применения профиля.") }),
  });

  const data = profile.data;
  const busy = sync.isPending || apply.isPending || bind.isPending;
  const counts = data?.counts;

  return (
    <section className="panel hub-card" aria-labelledby="hub-profile-title">
      <header className="hub-card-head">
        <h2 id="hub-profile-title" className="hub-card-title">Профиль книги</h2>
      </header>
      {profile.isLoading ? (
        <div className="hub-skel" aria-busy="true" aria-label="Загрузка профиля">
          <span /><span /><span />
        </div>
      ) : profile.isError ? (
        <p className="ui-note ui-note--error">Не удалось загрузить профиль: {describeApiError(profile.error, "сервер не ответил")}.</p>
      ) : data ? (
        <dl className="hub-kv">
          <div>
            <dt>Автор</dt>
            <dd>
              <select
                className="ui-select hub-author"
                value={data.book.author_id}
                disabled={busy}
                aria-label="Автор книги"
                onChange={(event) => bind.mutate(event.target.value)}
              >
                <option value="">не привязан</option>
                {data.authors.map((author) => (
                  <option key={author.id} value={author.id}>{author.name}</option>
                ))}
              </select>
            </dd>
          </div>
          <div><dt>Словарь книги</dt><dd className="num">{counts?.book_pronunciation_terms ?? 0} {plural(counts?.book_pronunciation_terms ?? 0, "ударение", "ударения", "ударений")}</dd></div>
          <div><dt>Словарь автора</dt><dd className="num">{counts?.author_pronunciations ?? 0} {plural(counts?.author_pronunciations ?? 0, "ударение", "ударения", "ударений")}</dd></div>
          <div>
            <dt>Связано с автором</dt>
            <dd className="num">{counts?.linked_characters ?? 0} из {counts?.author_characters ?? 0} персонажей</dd>
          </div>
          <div><dt>Синхронизация</dt><dd>{data.last_sync ? formatWhen(data.last_sync.at) : <span className="hub-dim">ещё не было</span>}</dd></div>
        </dl>
      ) : null}
      {data?.suggested ? (
        <p className="ui-note hub-suggest">
          Похоже, это {data.suggested.name}: совпало {data.suggested.matched} {plural(data.suggested.matched, "роль", "роли", "ролей")} из каста.{" "}
          <Button size="sm" variant="secondary" loading={bind.isPending} onClick={() => bind.mutate(data.suggested!.author_id)}>
            Привязать и заполнить каст
          </Button>
        </p>
      ) : null}
      <div className="hub-card-actions">
        <Button size="sm" variant="secondary" disabled={busy || !data} loading={sync.isPending} onClick={() => sync.mutate()}>
          Обновить профиль автора
        </Button>
        <Button size="sm" variant="secondary" disabled={busy || !data} loading={apply.isPending} onClick={() => apply.mutate()}>
          Применить профиль к книге
        </Button>
        <label className="ui-check hub-check">
          <input type="checkbox" checked={overwrite} disabled={busy} onChange={(e) => setOverwrite(e.target.checked)} />
          <span>перезаписать ручные цвета и актёров</span>
        </label>
      </div>
    </section>
  );
}
