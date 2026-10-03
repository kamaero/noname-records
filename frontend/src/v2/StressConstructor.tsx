/**
 * The in-text stress constructor, docked under the toolbar: the clicked word as
 * letter squares (vowels clickable), live preview, provenance of the current
 * mark, and «Для этой книги» / «Автору» saves via `POST .../stress-term`.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPostJson, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { stripStress } from "./stressText";
import { placeStress, splitLetters } from "../viewModels/stressConstructor";
import { invalidateReader, stressSourceLabel, useBookProfile } from "./editorApi";
import { stressForWord } from "./render";
import type { ReaderSegment, StressScope, StressTermResponse } from "./types";
import type { WordTarget } from "./useEditorState";

type StressConstructorProps = {
  bookId: string;
  target: WordTarget;
  segment: ReaderSegment | undefined;
  onClose: () => void;
  onSaved: () => void;
};

function segmentsWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "сегмент";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "сегмента";
  return "сегментов";
}

export function StressConstructor({ bookId, target, segment, onClose, onSaved }: StressConstructorProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const rootRef = useRef<HTMLElement>(null);

  const clean = useMemo(() => stripStress(target.word), [target.word]);
  const letters = useMemo(() => splitLetters(clean), [clean]);
  const vowelIndexes = useMemo(() => letters.filter((letter) => letter.isVowel).map((letter) => letter.index), [letters]);

  const current = useMemo(() => (segment ? stressForWord(segment.stress, target.start, target.end) : null), [segment, target]);
  const currentIndex = useMemo(() => {
    if (!current) return -1;
    const at = current.start + current.vowel - target.start;
    return vowelIndexes.includes(at) ? at : -1;
  }, [current, target.start, vowelIndexes]);

  const [picked, setPicked] = useState<number>(currentIndex);
  useEffect(() => setPicked(currentIndex), [currentIndex]);
  useEffect(() => {
    rootRef.current?.focus({ preventScroll: true });
  }, [target]);

  const profile = useBookProfile(bookId, true);
  const hasAuthor = profile.data ? profile.data.author !== null : null;

  const preview = picked >= 0 ? placeStress(clean, picked) : clean;
  const canSave = picked >= 0 && vowelIndexes.length > 0;

  const save = useMutation({
    mutationFn: (scope: StressScope) =>
      apiPostJson<StressTermResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/stress-term`, {
        word: clean,
        stressed: placeStress(clean, picked),
        scope,
      }),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Ударение не сохранено", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      const n = Number(result.segments_updated || 0);
      const extra: string[] = [];
      if (result.occurrences) extra.push(`вхождений: ${result.occurrences}`);
      if (result.skipped) extra.push(`пропущено: ${result.skipped}`);
      if (result.scope === "author") extra.push("записано в словарь автора");
      pushToast({
        tone: "success",
        title: `${result.stressed || preview}: обновлено ${n} ${segmentsWord(n)}`,
        detail: extra.join(" · ") || undefined,
      });
      await invalidateReader(queryClient, bookId, ["chapters", "queue", "profile"]);
      onSaved();
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Ударение не сохранено", detail: describeApiError(error, "Не удалось сохранить ударение.") });
    },
  });

  const move = (delta: 1 | -1) => {
    if (vowelIndexes.length === 0) return;
    const at = vowelIndexes.indexOf(picked);
    const next = at < 0 ? (delta > 0 ? 0 : vowelIndexes.length - 1) : (at + delta + vowelIndexes.length) % vowelIndexes.length;
    setPicked(vowelIndexes[next]);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      move(event.key === "ArrowRight" ? 1 : -1);
      return;
    }
    if (event.key === "Enter") {
      const actionButton = (event.target as HTMLElement | null)?.closest?.("[data-action]");
      if (actionButton) return; // let «Автору» / × handle their own Enter
      event.preventDefault();
      if (canSave && !save.isPending) save.mutate("book");
    }
  };

  const authorHint = hasAuthor === null
    ? "Проверяю, определён ли автор книги…"
    : hasAuthor
      ? "Записать в словарь автора — применится ко всем его книгам"
      : "У книги не определён автор — сохранить можно только в книгу";

  return (
    <section
      ref={rootRef}
      className="v2r-dock"
      role="region"
      aria-label={`Ударение в слове ${clean}`}
      tabIndex={-1}
      onKeyDown={onKeyDown}
    >
      <div className="v2r-dock-head">
        <span className="v2r-dock-title">Ударение</span>
        <span className="v2r-dock-preview" aria-live="polite">{preview}</span>
        <span className="v2r-dock-source">
          сейчас: {current ? `${stressSourceLabel(current.source)}${currentIndex >= 0 ? ` · ${placeStress(clean, currentIndex)}` : ""}` : "нет"}
        </span>
        <button type="button" className="btn btn-sm btn-ghost v2r-btn v2r-dock-close" onClick={onClose} aria-label="Закрыть конструктор" data-action="close">
          ✕
        </button>
      </div>

      <div className="v2r-letters" role="radiogroup" aria-label="Ударная гласная">
        {letters.map((letter) => (
          <button
            key={letter.index}
            type="button"
            className={["v2r-letter", letter.isVowel ? "v2r-letter--vowel" : "", picked === letter.index ? "is-on" : ""].filter(Boolean).join(" ")}
            disabled={!letter.isVowel}
            role="radio"
            aria-checked={picked === letter.index}
            tabIndex={-1}
            onClick={() => setPicked(letter.index)}
          >
            {letter.ch}
          </button>
        ))}
        {vowelIndexes.length === 0 ? <span className="v2r-dock-hint">В слове нет гласных — ударение поставить негде.</span> : null}
      </div>

      <div className="v2r-dock-actions">
        <button
          type="button"
          className="btn btn-sm btn-primary v2r-btn"
          disabled={!canSave || save.isPending}
          onClick={() => save.mutate("book")}
          title="Enter — сохранить для этой книги"
        >
          {save.isPending && save.variables === "book" ? "Сохраняю…" : "Для этой книги"}
        </button>
        <button
          type="button"
          className="btn btn-sm v2r-btn"
          disabled={!canSave || save.isPending || hasAuthor !== true}
          onClick={() => save.mutate("author")}
          title={authorHint}
          data-action="author"
        >
          {save.isPending && save.variables === "author" ? "Сохраняю…" : "Автору"}
        </button>
        <span className="v2r-dock-hint">← → гласная · Enter — в книгу · Esc — закрыть</span>
      </div>
    </section>
  );
}
