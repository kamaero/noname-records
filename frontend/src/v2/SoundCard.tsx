/**
 * Карточка маркера звукового слоя — лист справа, как прочие листы Читалки.
 * Правка полей по виду маркера, «удалить», перенос на другой абзац (выбор абзаца
 * нажатием в тексте), «добавить звук к этому абзацу», «переразметить главу» и список
 * потерявшихся маркеров главы — их владелец ставит на место руками.
 *
 * Выбор абзаца живёт в Читалке (`pickSegment`): лист на это время закрывается, чтобы
 * текст был доступен, а после выбора открывается снова. Поэтому то, что делается с
 * выбранным абзацем, передаётся замыканием — оно не зависит от жизни карточки.
 */
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/ToastProvider";
import { Sheet } from "./Sheet";
import {
  addSoundMarker,
  ambientAudioUrl,
  describeAmbientError,
  describeSoundError,
  dismissSoundMarker,
  editSoundMarker,
  regenerateSceneAmbient,
  rerunSoundChapter,
  soundKeys,
  splitQueries,
} from "./soundApi";
import type { SoundAmbient, SoundMarker, SoundPlace } from "./types";

/** Запрос на выбор абзаца: подсказка на плашке и что сделать с выбранным абзацем. */
export type SoundPick = {
  hint: string;
  /** вернуть id маркера, чью карточку открыть после выбора; ошибка — тост с её словами */
  onPick: (segmentId: string) => Promise<string | void>;
};

type SoundCardProps = {
  chapterId: string;
  bookId: string;
  marker: SoundMarker;
  place: SoundPlace | undefined;
  /** все места книги — для выбора места сцены */
  places: readonly SoundPlace[];
  /** номер сцены в главе (для заголовка), 0 — не сцена */
  sceneNumber?: number;
  /** потерявшиеся маркеры этой главы */
  lost: readonly SoundMarker[];
  pickSegment: (pick: SoundPick) => void;
  onOpenMarker: (marker: SoundMarker) => void;
  onClose: () => void;
};

const KIND_TITLE: Record<SoundMarker["kind"], string> = { scene: "Scene", transition: "Transition", sound: "Sound" };

/** `c1:00012` → 13: люди считают абзацы с единицы. */
export function paragraphNumber(segmentId: string): number {
  const ordinal = Number(segmentId.slice(segmentId.lastIndexOf(":") + 1));
  return Number.isFinite(ordinal) ? ordinal + 1 : 0;
}

/** Короткая подпись маркера для списков. */
export function markerCaption(marker: SoundMarker, place?: SoundPlace): string {
  if (marker.kind === "scene") return place?.name || marker.payload.ambience || "scene";
  if (marker.kind === "transition") return marker.payload.what || "transition";
  return marker.payload.description || marker.quote || "sound";
}

type Draft = {
  place_id: string;
  time_of_day: string;
  weather: string;
  ambience: string;
  mood: string;
  music: string;
  what: string;
  description: string;
  queries: string;
  quote: string;
};

function draftOf(marker: SoundMarker): Draft {
  const p = marker.payload;
  return {
    place_id: marker.place_id,
    time_of_day: p.time_of_day ?? "",
    weather: p.weather ?? "",
    ambience: p.ambience ?? "",
    mood: p.mood ?? "",
    music: (p.music_queries ?? []).join(", "),
    what: p.what ?? "",
    description: p.description ?? "",
    queries: (p.queries ?? []).join(", "),
    quote: marker.quote,
  };
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="snd-card-field">
      <span className="snd-card-label">{label}</span>
      {children}
      {hint ? <span className="snd-card-hint">{hint}</span> : null}
    </label>
  );
}

/** Сколько карточка ждёт новый трек сцены, прежде чем перестать переспрашивать: трек
 * сочиняется минутами, а прогон, который так и не ответил, не должен держать «сочиняется» вечно. */
const AMBIENT_WAIT_MS = 10 * 60_000;

/** Подпись состояния трека сцены. */
function ambientStatus(ambient: SoundAmbient | null | undefined, waiting: boolean, timedOut: boolean): { text: string; tone: "" | "is-bad" } {
  if (timedOut) return { text: "No response; refresh this card", tone: "is-bad" };
  if (waiting) return { text: "A new track is being generated. It will appear here in a few minutes", tone: "" };
  if (!ambient) return { text: "No track yet", tone: "" };
  if (ambient.status === "failed") {
    const kept = ambient.audio_file_id ? " The project still uses the previous track." : "";
    return { text: `Could not complete the action${ambient.error ? `: ${ambient.error}` : ""}.${kept}`, tone: "is-bad" };
  }
  if (ambient.status === "done") return { text: ambient.file_name ? `Ready · ${ambient.file_name}` : "Ready", tone: "" };
  return { text: "Generating…", tone: "" };
}

/** Блок «Эмбиент» сцены: состояние, плеер, промпт и перегенерация одного трека.
 *
 * Промпт уходит на сервер, только если его правили: нетронутый — сервер попросит новый
 * у Opus, а не повторит прежний слово в слово. Прежний трек остаётся в папке главы. */
function AmbientBlock({ chapterId, bookId, marker }: { chapterId: string; bookId: string; marker: SoundMarker }) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const ambient = marker.ambient ?? null;
  const original = ambient?.prompt ?? "";
  const [prompt, setPrompt] = useState(original);
  // снимок трека на момент запуска: пока сцена не получила другой, карточка ждёт и переспрашивает
  const [waitingFrom, setWaitingFrom] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  // когда начали ждать: повторный запуск с тем же снимком заводит таймаут заново
  const [waitingSince, setWaitingSince] = useState(0);
  const snapshot = JSON.stringify(ambient);
  const pending = waitingFrom !== null && waitingFrom === snapshot;
  const waiting = pending && !timedOut;

  useEffect(() => {
    setPrompt(original);
  }, [original]);

  useEffect(() => {
    if (!waiting) return undefined;
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: soundKeys.chapter(chapterId) });
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [waiting, queryClient, chapterId]);

  // Снимок сменился — ответ пришёл, таймаут снимается; не сменился за 10 минут — перестаём ждать.
  useEffect(() => {
    if (!pending) {
      setTimedOut(false);
      return undefined;
    }
    const timer = window.setTimeout(() => setTimedOut(true), AMBIENT_WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [pending, waitingSince]);

  const edited = prompt.trim() !== original.trim() && Boolean(prompt.trim());
  const regenerate = useMutation({
    mutationFn: () => regenerateSceneAmbient(marker.id, edited ? prompt.trim() : undefined),
    onSuccess: async () => {
      setWaitingFrom(snapshot);
      setTimedOut(false);
      setWaitingSince(Date.now());
      pushToast({ tone: "success", title: "Ambience queued", detail: "The track takes a few minutes to generate and will appear in this card and the chapter project automatically." });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: soundKeys.chapter(chapterId) }),
        queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] }),
      ]);
    },
    onError: (error) => pushToast({ tone: "error", title: "Ambience not started", detail: describeAmbientError(error) }),
  });

  const status = ambientStatus(ambient, waiting, pending && timedOut);
  const label = ambient ? "Regenerate ambience" : "Generate ambience";
  return (
    <section className="v2r-sheet-section snd-ambient">
      <h3 className="v2r-sheet-h">Ambience</h3>
      <p className={`snd-ambient-status ${status.tone}`}>{status.text}</p>
      {ambient?.audio_file_id ? (
        // ключ — трек: новый файл сцены — новый плеер, а не старый звук под новой подписью
        <audio key={ambient.audio_file_id} className="snd-ambient-player" controls preload="none" src={ambientAudioUrl(ambient.audio_file_id)} />
      ) : null}
      <Row label="Prompt" hint="Edited prompts are sent to ElevenLabs as entered; otherwise Opus writes a fresh prompt.">
        <textarea
          className="ui-input snd-card-text"
          rows={4}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="No prompt yet; Opus will write it"
          maxLength={2000}
          disabled={regenerate.isPending}
        />
      </Row>
      <div className="snd-card-actions">
        <button
          type="button"
          className="btn btn-sm v2r-btn"
          disabled={regenerate.isPending || waiting}
          onClick={() => {
            if (window.confirm("Generate a new track? This uses one track from your ElevenLabs quota. The previous track stays in the chapter folder.")) {
              regenerate.mutate();
            }
          }}
        >
          {regenerate.isPending ? "Queuing…" : `${label}…`}
        </button>
        {edited ? <span className="snd-card-hint">with edited prompt</span> : null}
      </div>
    </section>
  );
}

export function SoundCard({ chapterId, bookId, marker, place, places, sceneNumber = 0, lost, pickSegment, onOpenMarker, onClose }: SoundCardProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [draft, setDraft] = useState<Draft>(() => draftOf(marker));
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState({ quote: "", description: "", queries: "" });
  const set = (key: keyof Draft) => (event: { target: { value: string } }) => setDraft((d) => ({ ...d, [key]: event.target.value }));

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: soundKeys.chapter(chapterId) }),
      queryClient.invalidateQueries({ queryKey: soundKeys.book(bookId) }),
    ]);

  const initial = draftOf(marker);
  const dirty = (Object.keys(draft) as (keyof Draft)[]).some((key) => draft[key] !== initial[key]);
  const lostHere = marker.status === "lost";

  const save = useMutation({
    mutationFn: () => {
      const fields: Parameters<typeof editSoundMarker>[1] = {};
      if (marker.kind === "scene") {
        Object.assign(fields, {
          time_of_day: draft.time_of_day.trim(),
          weather: draft.weather.trim(),
          ambience: draft.ambience.trim(),
          mood: draft.mood.trim(),
          music_queries: splitQueries(draft.music),
        });
        if (draft.place_id && draft.place_id !== marker.place_id) fields.place_id = draft.place_id;
      } else if (marker.kind === "transition") {
        fields.what = draft.what.trim();
      } else {
        Object.assign(fields, { description: draft.description.trim(), queries: splitQueries(draft.queries) });
        // новая цитата в том же абзаце — тот же «перенос» на свой абзац: сервер её проверит
        if (!lostHere && draft.quote.trim() !== marker.quote) Object.assign(fields, { segment_id: marker.segment_id, quote: draft.quote.trim() });
      }
      return editSoundMarker(marker.id, fields);
    },
    onSuccess: async (result) => {
      // черновик — из сохранённого маркера: «Сохранить» снова неактивна до новой правки
      setDraft(draftOf(result.marker));
      pushToast({ tone: "success", title: "Saved", detail: "Future runs will preserve this edit." });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Not saved", detail: describeSoundError(error) }),
  });

  const remove = useMutation({
    mutationFn: () => dismissSoundMarker(marker.id),
    onSuccess: async () => {
      pushToast({ tone: "success", title: `${KIND_TITLE[marker.kind]}: deleted`, detail: "Future runs will not restore it." });
      onClose();
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Not deleted", detail: describeSoundError(error) }),
  });

  const rerun = useMutation({
    mutationFn: () => rerunSoundChapter(chapterId),
    onSuccess: async () => {
      pushToast({ tone: "success", title: "Queued; results will appear automatically" });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Reannotation not started", detail: describeSoundError(error) }),
  });

  const addHere = useMutation({
    mutationFn: (segmentId: string) => addSoundMarker(chapterId, "sound", segmentId, addFields()),
    onSuccess: async (result) => {
      pushToast({ tone: "success", title: "Sound added", detail: `paragraph ${paragraphNumber(result.marker.segment_id)}` });
      setAdding(false);
      setAdded({ quote: "", description: "", queries: "" });
      await refresh();
      onOpenMarker(result.marker);
    },
    onError: (error) => pushToast({ tone: "error", title: "Sound not added", detail: describeSoundError(error) }),
  });

  function addFields() {
    return { quote: added.quote.trim(), description: added.description.trim(), queries: splitQueries(added.queries) };
  }

  /** Перенос этого (или потерянного) маркера: у звука с собой уходит цитата из поля. */
  const moveTo = (target: SoundMarker, quote: string) => async (segmentId: string) => {
    const fields = target.kind === "sound" ? { segment_id: segmentId, quote } : { segment_id: segmentId };
    await editSoundMarker(target.id, fields);
    pushToast({ tone: "success", title: `${KIND_TITLE[target.kind]}: now starts at paragraph ${paragraphNumber(segmentId)}` });
    await refresh();
    return target.id;
  };

  const startMove = (target: SoundMarker, quote: string) => {
    const hint =
      target.kind === "scene"
        ? "Click the paragraph where this scene begins"
        : target.kind === "transition"
          ? "Click the paragraph following the transition"
          : `Click the paragraph containing «${quote.length > 40 ? `${quote.slice(0, 39)}…` : quote}»`;
    pickSegment({ hint, onPick: moveTo(target, quote) });
  };

  const busy = save.isPending || remove.isPending || rerun.isPending || addHere.isPending;
  const where = `paragraph ${paragraphNumber(marker.segment_id)}`;
  const title = marker.kind === "scene" && sceneNumber ? `Scene ${sceneNumber}` : KIND_TITLE[marker.kind];
  const placeKnown = places.some((item) => item.id === draft.place_id);
  const otherLost = lost.filter((item) => item.id !== marker.id);

  return (
    <Sheet
      title={title}
      subtitle={`${lostHere ? "unplaced" : where} · ${marker.source === "human" ? "manually edited" : "AI annotation"}`}
      onClose={onClose}
      footer={
        <div className="snd-card-foot">
          <button
            type="button"
            className="btn btn-sm v2r-btn"
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  "Annotate this chapter again? This is a paid AI request charged to your RouterAI balance. " +
                    "Only this chapter's AI markers will be replaced. Your edits and deletions will be preserved.",
                )
              ) {
                rerun.mutate();
              }
            }}
          >
            {rerun.isPending ? "Queuing…" : "Annotate chapter again…"}
          </button>
        </div>
      }
    >
      {lostHere ? (
        <section className="snd-card-lost" role="note">
          <p>
            The chapter text changed and this marker could not find its paragraph
            {marker.kind === "sound" && marker.quote ? <> using the quote «{marker.quote}»</> : null}. Place it manually.
          </p>
          <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => startMove(marker, draft.quote.trim())}>
            Place on paragraph
          </button>
        </section>
      ) : null}

      <section className="v2r-sheet-section snd-card-form">
        {marker.kind === "scene" ? (
          <>
            <Row label="Location">
              <select className="ui-input" value={draft.place_id} onChange={set("place_id")} disabled={busy}>
                {!placeKnown ? <option value={draft.place_id}>{place?.name || "— no location —"}</option> : null}
                {places.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Row>
            <div className="snd-card-pair">
              <Row label="Time of day">
                <input className="ui-input" value={draft.time_of_day} onChange={set("time_of_day")} placeholder="night" disabled={busy} />
              </Row>
              <Row label="Weather">
                <input className="ui-input" value={draft.weather} onChange={set("weather")} placeholder="rain" disabled={busy} />
              </Row>
            </div>
            <Row label="Ambience">
              <textarea className="ui-input snd-card-text" rows={2} value={draft.ambience} onChange={set("ambience")} disabled={busy} />
            </Row>
            <Row label="Mood">
              <input className="ui-input" value={draft.mood} onChange={set("mood")} disabled={busy} />
            </Row>
            <Row label="Music queries" hint="Up to three comma-separated queries in English. Edit environmental ambience queries in Book locations.">
              <input className="ui-input" value={draft.music} onChange={set("music")} placeholder="dark tavern ambient" disabled={busy} />
            </Row>
          </>
        ) : null}
        {marker.kind === "transition" ? (
          <Row label="What happens">
            <input className="ui-input" value={draft.what} onChange={set("what")} placeholder="flashback, dream, ambience fades to silence…" disabled={busy} />
          </Row>
        ) : null}
        {marker.kind === "sound" ? (
          <>
            <Row label="Description">
              <input className="ui-input" value={draft.description} onChange={set("description")} disabled={busy} />
            </Row>
            <Row label="Queries" hint="Up to three comma-separated queries in English.">
              <input className="ui-input" value={draft.queries} onChange={set("queries")} placeholder="door slam wooden" disabled={busy} />
            </Row>
            <Row label="Quote" hint="Copy exactly from the paragraph. To move this marker, the quote must exist in the new paragraph.">
              <textarea className="ui-input snd-card-text" rows={2} value={draft.quote} onChange={set("quote")} disabled={busy} />
            </Row>
          </>
        ) : null}
        <div className="snd-card-actions">
          <button type="button" className="btn btn-sm btn-primary" disabled={busy || !dirty} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save"}
          </button>
          {!lostHere ? (
            <button type="button" className="btn btn-sm v2r-btn" disabled={busy} onClick={() => startMove(marker, draft.quote.trim())}>
              {marker.kind === "scene" ? "Move scene start to another paragraph" : "Move to another paragraph"}
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-sm btn-ghost snd-card-danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Delete ${KIND_TITLE[marker.kind].toLowerCase()} «${markerCaption(marker, place)}»? Future runs will not restore it.`)) {
                remove.mutate();
              }
            }}
          >
            Delete
          </button>
        </div>
      </section>

      {marker.kind === "scene" && !lostHere ? <AmbientBlock chapterId={chapterId} bookId={bookId} marker={marker} /> : null}

      {!lostHere ? (
        <section className="v2r-sheet-section">
          {!adding ? (
            <button type="button" className="btn btn-sm v2r-btn snd-card-add" disabled={busy} onClick={() => setAdding(true)}>
              + Add sound to this paragraph
            </button>
          ) : (
            <div className="snd-card-subform">
              <h3 className="v2r-sheet-h">New sound · {where}</h3>
              <Row label="Quote" hint="An exact quote helps locate the sound after text edits. Optional.">
                <textarea
                  className="ui-input snd-card-text"
                  rows={2}
                  value={added.quote}
                  onChange={(event) => setAdded((a) => ({ ...a, quote: event.target.value }))}
                  disabled={busy}
                  autoFocus
                />
              </Row>
              <Row label="Description">
                <input className="ui-input" value={added.description} onChange={(event) => setAdded((a) => ({ ...a, description: event.target.value }))} disabled={busy} />
              </Row>
              <Row label="Queries" hint="Up to three comma-separated queries in English.">
                <input className="ui-input" value={added.queries} onChange={(event) => setAdded((a) => ({ ...a, queries: event.target.value }))} disabled={busy} />
              </Row>
              <div className="snd-card-actions">
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  disabled={busy || !added.description.trim()}
                  onClick={() => addHere.mutate(marker.segment_id)}
                >
                  {addHere.isPending ? "Adding…" : `Add to paragraph ${paragraphNumber(marker.segment_id)}`}
                </button>
                <button
                  type="button"
                  className="btn btn-sm v2r-btn"
                  disabled={busy || !added.description.trim()}
                  onClick={() => {
                    const fields = addFields();
                    pickSegment({
                      hint: "Click a paragraph to add sound",
                      onPick: async (segmentId) => {
                        const result = await addSoundMarker(chapterId, "sound", segmentId, fields);
                        pushToast({ tone: "success", title: "Sound added", detail: `paragraph ${paragraphNumber(segmentId)}` });
                        await refresh();
                        return result.marker.id;
                      },
                    });
                  }}
                >
                  to another paragraph…
                </button>
                <button type="button" className="btn btn-sm btn-ghost" disabled={busy} onClick={() => setAdding(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </section>
      ) : null}

      {otherLost.length > 0 ? (
        <section className="v2r-sheet-section">
          <h3 className="v2r-sheet-h">
            Unplaced in chapter <span className="v2r-legend-count num">{otherLost.length}</span>
          </h3>
          <p className="v2r-sheet-note">The chapter text changed and these markers could not find their paragraphs.</p>
          <ul className="snd-lost-list">
            {otherLost.map((item) => (
              <li key={item.id} className="snd-lost">
                <button type="button" className="snd-lost-open" onClick={() => onOpenMarker(item)} title="Open card">
                  <span className="snd-lost-kind">{KIND_TITLE[item.kind]}</span> {markerCaption(item, places.find((p) => p.id === item.place_id))}
                </button>
                <button type="button" className="btn btn-sm v2r-btn" disabled={busy} onClick={() => startMove(item, item.quote)}>
                  place on paragraph
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Sheet>
  );
}
