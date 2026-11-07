import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPostJson, describeApiError } from "../../../api/client";
import { Button, Field } from "../../../ui";
import { useToast } from "../../ToastProvider";
import { hubKeys } from "./hubApi";

type Language = "en" | "ru";
type LanguageResponse = { ok: boolean; language: Language; changed?: boolean };

export function BookLanguageCard({ bookId, locked }: { bookId: string; locked: boolean }) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [selected, setSelected] = useState<Language | null>(null);
  const [changed, setChanged] = useState(false);
  const queryKey = ["book-language", bookId];
  const path = `/api/v2/books/${encodeURIComponent(bookId)}/language`;
  const language = useQuery({ queryKey, queryFn: () => apiGet<LanguageResponse>(path) });
  const value = selected ?? language.data?.language ?? "en";
  const save = useMutation({
    mutationFn: () => apiPostJson<LanguageResponse>(path, { language: value }),
    onSuccess: async (result) => {
      queryClient.setQueryData(queryKey, result);
      setSelected(null);
      setChanged(Boolean(result.changed));
      pushToast({ tone: "success", title: "Book language saved", detail: "Existing data was kept. No processing started." });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: hubKeys.recording(bookId) }),
        queryClient.invalidateQueries({ queryKey: hubKeys.progress(bookId) }),
        queryClient.invalidateQueries({ queryKey: ["books"] }),
      ]);
    },
    onError: (error) => pushToast({ tone: "error", title: "Language not saved", detail: describeApiError(error, "Try again.") }),
  });
  return (
    <section className="panel hub-card" aria-labelledby="hub-language-title">
      <header className="hub-card-head"><h2 id="hub-language-title" className="hub-card-title">Book language</h2></header>
      <Field label="Language of the text" hint="Applies to future analysis and transcription. Existing text, edits and recordings are preserved.">
        {(props) => <select {...props} className="ui-input" value={language.data ? value : ""}
          disabled={locked || save.isPending || !language.data}
          onChange={(event) => setSelected(event.target.value as Language)}>
          {!language.data ? <option value="">{language.isError ? "Unavailable" : "Loading…"}</option> : null}
          <option value="en">English</option><option value="ru">Russian</option>
        </select>}
      </Field>
      <p className="ui-note" role={changed ? "status" : undefined}>
        {changed ? "Language changed. " : ""}Previous analysis and ASR results may need review after a language change.
        Nothing is reprocessed automatically.
      </p>
      {language.isError ? <p className="ui-note ui-note--error" role="alert">Could not load the book language. <button type="button" className="ui-link" onClick={() => language.refetch()}>Retry</button></p> : null}
      <div className="hub-title-actions"><Button size="sm" variant="secondary" loading={save.isPending}
        disabled={locked || !language.data || value === language.data.language} onClick={() => save.mutate()}>Save language</Button></div>
    </section>
  );
}
