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
      pushToast({ tone: "error", title: `${label}: failed`, detail: result.error || "The server returned no details." });
      return;
    }
    pushToast({ tone: "success", title: `${label}: done` });
    await invalidateReader(queryClient, bookId, ["profile", "chapters", "cast", "queue"]);
  };
  const sync = useMutation({
    mutationFn: () => apiPostJson<ProfileActionResponse>(`${base}/sync`, {}),
    onSuccess: (r) => after("Author profile updated", r),
    onError: (e) => pushToast({ tone: "error", title: "Could not update author profile", detail: describeApiError(e, "Synchronization failed.") }),
  });
  // Binding is the step that makes «Применить профиль» mean anything: an unbound
  // book has no profile to apply, which is exactly how a cast ends up empty.
  const bind = useMutation({
    mutationFn: (authorId: string) =>
      apiPostJson<BindAuthorResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/author`, { author_id: authorId }),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Author not linked", detail: result.error || "The server returned no details." });
        return;
      }
      const applied = result.applied;
      pushToast({
        tone: "success",
        title: result.author_id ? `Author: ${result.author_name}` : "Author unlinked",
        detail: applied ? `Voice actors assigned: ${applied.actors_applied}, colors: ${applied.colours_applied}.` : undefined,
      });
      await invalidateReader(queryClient, bookId, ["profile", "chapters", "cast", "queue"]);
      await queryClient.invalidateQueries({ queryKey: ["book-budget", bookId] });
    },
    onError: (e) => pushToast({ tone: "error", title: "Could not link author", detail: describeApiError(e, "Linking failed.") }),
  });

  const apply = useMutation({
    mutationFn: () => apiPostJson<ProfileActionResponse>(`${base}/apply`, { overwrite }),
    onSuccess: (r) => after("Profile applied to book", r),
    onError: (e) => pushToast({ tone: "error", title: "Could not apply profile", detail: describeApiError(e, "Profile application failed.") }),
  });

  const data = profile.data;
  const busy = sync.isPending || apply.isPending || bind.isPending;
  const counts = data?.counts;

  return (
    <section className="panel hub-card" aria-labelledby="hub-profile-title">
      <header className="hub-card-head">
        <h2 id="hub-profile-title" className="hub-card-title">Book profile</h2>
      </header>
      {profile.isLoading ? (
        <div className="hub-skel" aria-busy="true" aria-label="Loading profile">
          <span /><span /><span />
        </div>
      ) : profile.isError ? (
        <p className="ui-note ui-note--error">Could not load profile: {describeApiError(profile.error, "the server did not respond")}.</p>
      ) : data ? (
        <dl className="hub-kv">
          <div>
            <dt>Author</dt>
            <dd>
              <select
                className="ui-select hub-author"
                value={data.book.author_id}
                disabled={busy}
                aria-label="Book author"
                onChange={(event) => bind.mutate(event.target.value)}
              >
                <option value="">unlinked</option>
                {data.authors.map((author) => (
                  <option key={author.id} value={author.id}>{author.name}</option>
                ))}
              </select>
            </dd>
          </div>
          <div><dt>Book dictionary</dt><dd className="num">{counts?.book_pronunciation_terms ?? 0} {plural(counts?.book_pronunciation_terms ?? 0, "stress entry", "stress entries", "stress entries")}</dd></div>
          <div><dt>Author dictionary</dt><dd className="num">{counts?.author_pronunciations ?? 0} {plural(counts?.author_pronunciations ?? 0, "stress entry", "stress entries", "stress entries")}</dd></div>
          <div>
            <dt>Linked to author</dt>
            <dd className="num">{counts?.linked_characters ?? 0} of {counts?.author_characters ?? 0} characters</dd>
          </div>
          <div><dt>Synchronization</dt><dd>{data.last_sync ? formatWhen(data.last_sync.at) : <span className="hub-dim">not yet</span>}</dd></div>
        </dl>
      ) : null}
      {data?.suggested ? (
        <p className="ui-note hub-suggest">
          Possible match: {data.suggested.name}: matched {data.suggested.matched} {plural(data.suggested.matched, "role", "roles", "roles")} in cast.{" "}
          <Button size="sm" variant="secondary" loading={bind.isPending} onClick={() => bind.mutate(data.suggested!.author_id)}>
            Link and fill cast
          </Button>
        </p>
      ) : null}
      <div className="hub-card-actions">
        <Button size="sm" variant="secondary" disabled={busy || !data} loading={sync.isPending} onClick={() => sync.mutate()}>
          Update author profile
        </Button>
        <Button size="sm" variant="secondary" disabled={busy || !data} loading={apply.isPending} onClick={() => apply.mutate()}>
          Apply profile to book
        </Button>
        <label className="ui-check hub-check">
          <input type="checkbox" checked={overwrite} disabled={busy} onChange={(e) => setOverwrite(e.target.checked)} />
          <span>overwrite manually selected colors and voice actors</span>
        </label>
      </div>
    </section>
  );
}
