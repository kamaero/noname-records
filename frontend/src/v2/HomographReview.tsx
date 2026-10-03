/**
 * «Спорные ударения» — where the reading depends on the sentence.
 *
 * The dictionary calls 522 words of «Крыльев» ambiguous and the context layer chose a
 * reading for each of their 5 342 occurrences. Confirming 5 342 decisions by hand is
 * not review, it is retyping, so the list is ordered by how much the model wavered:
 * «сердца» 4 against 4 first, «время» 268 out of 268 last. An even split means the
 * model had no idea; a 727-against-5 split is a model telling you which five to check.
 *
 * A homograph cannot be settled by a rule — «уже» is «уже́» here and «у́же» three
 * chapters on — so a choice here reaches exactly one place. The rule stays available
 * for the words read one way everywhere, where it is the right tool.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPostJson, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { editorKeys } from "./editorApi";
import { plural } from "./useIsMobile";

type Reading = { vowel_offset: number; count: number; form: string };
type Option = { vowel_offset: number; form: string };

type HomographWord = {
  word: string;
  places: number;
  mixed: boolean;
  rare_places: number;
  doubt: number;
  readings: Reading[];
  options: Option[];
};

type WordsResponse = {
  ok: boolean;
  words: HomographWord[];
  counts: { words: number; places: number; mixed_words: number; rare_places: number };
};

type Place = {
  segment_id: string;
  chapter_id: string;
  chapter_index: number;
  text: string;
  word_start: number;
  word_end: number;
  chosen: number;
  rare: boolean;
  options: Option[];
};

type HomographReviewProps = {
  bookId: string;
  /** open the place in the chapter, with the stress constructor on the word */
  onOpenPlace: (place: { segment_id: string; chapter_index: number; word: string }) => void;
};

const CONTEXT = 90;

/** The sentence around the word, so the choice can be made without opening the chapter. */
function around(text: string, start: number, end: number) {
  const from = Math.max(0, start - CONTEXT);
  const to = Math.min(text.length, end + CONTEXT);
  return {
    before: (from > 0 ? "…" : "") + text.slice(from, start),
    word: text.slice(start, end),
    after: text.slice(end, to) + (to < text.length ? "…" : ""),
  };
}

export function HomographReview({ bookId, onOpenPlace }: HomographReviewProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [openWord, setOpenWord] = useState<string | null>(null);

  const words = useQuery({
    queryKey: ["v2", "homographs", bookId],
    queryFn: () => apiGet<WordsResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/homographs`),
    enabled: Boolean(bookId),
    staleTime: 30_000,
  });

  const places = useQuery({
    queryKey: ["v2", "homographs", bookId, openWord],
    queryFn: () =>
      apiGet<{ ok: boolean; word: string; places: Place[] }>(
        `/api/v2/books/${encodeURIComponent(bookId)}/homographs?word=${encodeURIComponent(openWord || "")}`,
      ),
    enabled: Boolean(bookId && openWord),
  });

  const decide = useMutation({
    mutationFn: (payload: { place: Place; vowel: number }) =>
      apiPostJson<{ ok: boolean }>(`/api/v2/segments/${encodeURIComponent(payload.place.segment_id)}/stress`, {
        word_start: payload.place.word_start,
        word_end: payload.place.word_end,
        vowel_offset: payload.vowel,
      }),
    onSuccess: (_result, { place, vowel }) => {
      const form = place.options.find((option) => option.vowel_offset === vowel)?.form || "";
      pushToast({ tone: "success", title: `Здесь «${form}»`, detail: `Глава ${place.chapter_index}. Решение только для этого места.` });
      void queryClient.invalidateQueries({ queryKey: ["v2", "homographs", bookId] });
      void queryClient.invalidateQueries({ queryKey: editorKeys.queue(bookId) });
    },
    onError: (error) => pushToast({ tone: "error", title: "Не сохранено", detail: describeApiError(error, "") }),
  });

  if (words.isLoading) return <p className="v2r-sheet-note">Собираю спорные места…</p>;
  if (words.isError || !words.data) {
    return <p className="v2r-sheet-error">{describeApiError(words.error, "Не удалось собрать спорные ударения.")}</p>;
  }

  const { counts } = words.data;
  if (counts.words === 0) {
    return <p className="v2r-sheet-empty">Спорных ударений нет: слов с двумя чтениями в книге не нашлось.</p>;
  }

  return (
    <>
      <p className="v2r-sheet-note">
        Модель выбрала чтение в {counts.places.toLocaleString("ru-RU")}{" "}
        {plural(counts.places, "месте", "местах", "местах")}. Сверху — где она колебалась сильнее всего;{" "}
        <strong>{counts.rare_places}</strong> {plural(counts.rare_places, "место", "места", "мест")} прочитано не так,
        как обычно у этого слова, — с них и стоит начать.
      </p>

      {words.data.words.map((row) => {
        const open = openWord === row.word;
        return (
          <section key={row.word} className={open ? "v2r-homo is-open" : "v2r-homo"}>
            <button
              type="button"
              className="v2r-homo-head"
              onClick={() => setOpenWord(open ? null : row.word)}
              aria-expanded={open}
            >
              <span className="v2r-homo-word">{row.word}</span>
              <span className="v2r-homo-readings">
                {row.readings.map((reading) => (
                  <span key={reading.vowel_offset} className="v2r-homo-reading">
                    {reading.form} <span className="num">×{reading.count}</span>
                  </span>
                ))}
              </span>
              {row.mixed ? <span className="v2r-homo-flag">по-разному</span> : null}
            </button>

            {open ? (
              places.isLoading ? (
                <p className="v2r-sheet-note">Ищу места…</p>
              ) : (
                <ol className="v2r-homo-places">
                  {(places.data?.places ?? []).map((place) => {
                    const cut = around(place.text, place.word_start, place.word_end);
                    return (
                      <li key={`${place.segment_id}:${place.word_start}`} className={place.rare ? "v2r-homo-place is-rare" : "v2r-homo-place"}>
                        <p className="v2r-homo-text">
                          {cut.before}
                          <mark>{cut.word}</mark>
                          {cut.after}
                        </p>
                        <div className="v2r-homo-actions">
                          <span className="v2r-homo-where num">гл. {place.chapter_index}</span>
                          {place.options.map((option) => (
                            <button
                              key={option.vowel_offset}
                              type="button"
                              className={
                                option.vowel_offset === place.chosen
                                  ? "btn btn-sm v2r-btn is-on"
                                  : "btn btn-sm v2r-btn"
                              }
                              disabled={decide.isPending}
                              title={
                                option.vowel_offset === place.chosen
                                  ? "Так прочла модель — нажмите, чтобы закрепить"
                                  : "Поставить это чтение здесь"
                              }
                              onClick={() => decide.mutate({ place, vowel: option.vowel_offset })}
                            >
                              {option.form}
                            </button>
                          ))}
                          <button
                            type="button"
                            className="btn btn-sm btn-ghost v2r-btn"
                            onClick={() => onOpenPlace({ segment_id: place.segment_id, chapter_index: place.chapter_index, word: row.word })}
                            title="Открыть главу и поставить ударение вручную"
                          >
                            В главе
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              )
            ) : null}
          </section>
        );
      })}
    </>
  );
}
