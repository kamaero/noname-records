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

const KIND_TITLE: Record<SoundMarker["kind"], string> = { scene: "Сцена", transition: "Переход", sound: "Звук" };

/** `c1:00012` → 13: люди считают абзацы с единицы. */
export function paragraphNumber(segmentId: string): number {
  const ordinal = Number(segmentId.slice(segmentId.lastIndexOf(":") + 1));
  return Number.isFinite(ordinal) ? ordinal + 1 : 0;
}

/** Короткая подпись маркера для списков. */
export function markerCaption(marker: SoundMarker, place?: SoundPlace): string {
  if (marker.kind === "scene") return place?.name || marker.payload.ambience || "сцена";
  if (marker.kind === "transition") return marker.payload.what || "переход";
  return marker.payload.description || marker.quote || "звук";
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
  if (timedOut) return { text: "Нет ответа — обновите карточку", tone: "is-bad" };
  if (waiting) return { text: "Новый трек сочиняется — минуты; появится здесь сам", tone: "" };
  if (!ambient) return { text: "Трека ещё нет", tone: "" };
  if (ambient.status === "failed") {
    const kept = ambient.audio_file_id ? " В проекте остался прежний трек." : "";
    return { text: `Не вышло${ambient.error ? `: ${ambient.error}` : ""}.${kept}`, tone: "is-bad" };
  }
  if (ambient.status === "done") return { text: ambient.file_name ? `Готов · ${ambient.file_name}` : "Готов", tone: "" };
  return { text: "Сочиняется…", tone: "" };
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
      pushToast({ tone: "success", title: "Эмбиент в очереди", detail: "Трек сочиняется минутами — появится в карточке и в проекте главы сам." });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: soundKeys.chapter(chapterId) }),
        queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] }),
      ]);
    },
    onError: (error) => pushToast({ tone: "error", title: "Эмбиент не запущен", detail: describeAmbientError(error) }),
  });

  const status = ambientStatus(ambient, waiting, pending && timedOut);
  const label = ambient ? "Перегенерировать эмбиент" : "Сгенерировать эмбиент";
  return (
    <section className="v2r-sheet-section snd-ambient">
      <h3 className="v2r-sheet-h">Эмбиент</h3>
      <p className={`snd-ambient-status ${status.tone}`}>{status.text}</p>
      {ambient?.audio_file_id ? (
        // ключ — трек: новый файл сцены — новый плеер, а не старый звук под новой подписью
        <audio key={ambient.audio_file_id} className="snd-ambient-player" controls preload="none" src={ambientAudioUrl(ambient.audio_file_id)} />
      ) : null}
      <Row label="Промпт" hint="Идёт в ElevenLabs как есть, если правили; нетронутый — новый промпт напишет Opus.">
        <textarea
          className="ui-input snd-card-text"
          rows={4}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="Промпта ещё нет — его напишет Opus"
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
            if (window.confirm("Сгенерировать новый трек?\n\nСпишется 1 трек из квоты ElevenLabs; прежний останется в папке главы.")) {
              regenerate.mutate();
            }
          }}
        >
          {regenerate.isPending ? "Ставлю в очередь…" : `${label}…`}
        </button>
        {edited ? <span className="snd-card-hint">с правленым промптом</span> : null}
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
      pushToast({ tone: "success", title: "Сохранено", detail: "Повторный прогон эту правку не тронет." });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не сохранилось", detail: describeSoundError(error) }),
  });

  const remove = useMutation({
    mutationFn: () => dismissSoundMarker(marker.id),
    onSuccess: async () => {
      pushToast({ tone: "success", title: `${KIND_TITLE[marker.kind]}: удалено`, detail: "Повторный прогон его не вернёт." });
      onClose();
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не удалилось", detail: describeSoundError(error) }),
  });

  const rerun = useMutation({
    mutationFn: () => rerunSoundChapter(chapterId),
    onSuccess: async () => {
      pushToast({ tone: "success", title: "В очереди; результат появится сам" });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Переразметка не запущена", detail: describeSoundError(error) }),
  });

  const addHere = useMutation({
    mutationFn: (segmentId: string) => addSoundMarker(chapterId, "sound", segmentId, addFields()),
    onSuccess: async (result) => {
      pushToast({ tone: "success", title: "Звук добавлен", detail: `абз. ${paragraphNumber(result.marker.segment_id)}` });
      setAdding(false);
      setAdded({ quote: "", description: "", queries: "" });
      await refresh();
      onOpenMarker(result.marker);
    },
    onError: (error) => pushToast({ tone: "error", title: "Звук не добавлен", detail: describeSoundError(error) }),
  });

  function addFields() {
    return { quote: added.quote.trim(), description: added.description.trim(), queries: splitQueries(added.queries) };
  }

  /** Перенос этого (или потерянного) маркера: у звука с собой уходит цитата из поля. */
  const moveTo = (target: SoundMarker, quote: string) => async (segmentId: string) => {
    const fields = target.kind === "sound" ? { segment_id: segmentId, quote } : { segment_id: segmentId };
    await editSoundMarker(target.id, fields);
    pushToast({ tone: "success", title: `${KIND_TITLE[target.kind]}: теперь с абз. ${paragraphNumber(segmentId)}` });
    await refresh();
    return target.id;
  };

  const startMove = (target: SoundMarker, quote: string) => {
    const hint =
      target.kind === "scene"
        ? "Нажмите на абзац, с которого начинается сцена"
        : target.kind === "transition"
          ? "Нажмите на абзац, перед которым стоит переход"
          : `Нажмите на абзац, где есть цитата «${quote.length > 40 ? `${quote.slice(0, 39)}…` : quote}»`;
    pickSegment({ hint, onPick: moveTo(target, quote) });
  };

  const busy = save.isPending || remove.isPending || rerun.isPending || addHere.isPending;
  const where = `абз. ${paragraphNumber(marker.segment_id)}`;
  const title = marker.kind === "scene" && sceneNumber ? `Сцена ${sceneNumber}` : KIND_TITLE[marker.kind];
  const placeKnown = places.some((item) => item.id === draft.place_id);
  const otherLost = lost.filter((item) => item.id !== marker.id);

  return (
    <Sheet
      title={title}
      subtitle={`${lostHere ? "потерялся" : where} · ${marker.source === "human" ? "правлено вручную" : "разметка ИИ"}`}
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
                  "Переразметить главу заново?\n\nЭто платный запрос к ИИ — деньги спишутся с баланса RouterAI. " +
                    "Заменятся только маркеры ИИ этой главы: ваши правки и удалённое останутся как есть.",
                )
              ) {
                rerun.mutate();
              }
            }}
          >
            {rerun.isPending ? "Ставлю в очередь…" : "Переразметить главу…"}
          </button>
        </div>
      }
    >
      {lostHere ? (
        <section className="snd-card-lost" role="note">
          <p>
            Текст главы поменялся, и маркер не нашёл свой абзац
            {marker.kind === "sound" && marker.quote ? <> по цитате «{marker.quote}»</> : null}. Поставьте его руками.
          </p>
          <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => startMove(marker, draft.quote.trim())}>
            Поставить на абзац
          </button>
        </section>
      ) : null}

      <section className="v2r-sheet-section snd-card-form">
        {marker.kind === "scene" ? (
          <>
            <Row label="Место">
              <select className="ui-input" value={draft.place_id} onChange={set("place_id")} disabled={busy}>
                {!placeKnown ? <option value={draft.place_id}>{place?.name || "— без места —"}</option> : null}
                {places.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Row>
            <div className="snd-card-pair">
              <Row label="Время суток">
                <input className="ui-input" value={draft.time_of_day} onChange={set("time_of_day")} placeholder="ночь" disabled={busy} />
              </Row>
              <Row label="Погода">
                <input className="ui-input" value={draft.weather} onChange={set("weather")} placeholder="дождь" disabled={busy} />
              </Row>
            </div>
            <Row label="Фон">
              <textarea className="ui-input snd-card-text" rows={2} value={draft.ambience} onChange={set("ambience")} disabled={busy} />
            </Row>
            <Row label="Настроение">
              <input className="ui-input" value={draft.mood} onChange={set("mood")} disabled={busy} />
            </Row>
            <Row label="Музыка — запросы" hint="До трёх, через запятую, по-английски. Запросы фона — у места, в «Местах книги».">
              <input className="ui-input" value={draft.music} onChange={set("music")} placeholder="dark tavern ambient" disabled={busy} />
            </Row>
          </>
        ) : null}
        {marker.kind === "transition" ? (
          <Row label="Что происходит">
            <input className="ui-input" value={draft.what} onChange={set("what")} placeholder="флешбэк, сон, фон уходит в тишину…" disabled={busy} />
          </Row>
        ) : null}
        {marker.kind === "sound" ? (
          <>
            <Row label="Описание">
              <input className="ui-input" value={draft.description} onChange={set("description")} disabled={busy} />
            </Row>
            <Row label="Запросы" hint="До трёх, через запятую, по-английски.">
              <input className="ui-input" value={draft.queries} onChange={set("queries")} placeholder="door slam wooden" disabled={busy} />
            </Row>
            <Row label="Цитата" hint="Дословно из абзаца. При переносе она должна быть в новом абзаце.">
              <textarea className="ui-input snd-card-text" rows={2} value={draft.quote} onChange={set("quote")} disabled={busy} />
            </Row>
          </>
        ) : null}
        <div className="snd-card-actions">
          <button type="button" className="btn btn-sm btn-primary" disabled={busy || !dirty} onClick={() => save.mutate()}>
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </button>
          {!lostHere ? (
            <button type="button" className="btn btn-sm v2r-btn" disabled={busy} onClick={() => startMove(marker, draft.quote.trim())}>
              {marker.kind === "scene" ? "Начало сцены — другой абзац" : "Перенести на другой абзац"}
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-sm btn-ghost snd-card-danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Удалить ${KIND_TITLE[marker.kind].toLowerCase()} «${markerCaption(marker, place)}»? Повторный прогон его не вернёт.`)) {
                remove.mutate();
              }
            }}
          >
            Удалить
          </button>
        </div>
      </section>

      {marker.kind === "scene" && !lostHere ? <AmbientBlock chapterId={chapterId} bookId={bookId} marker={marker} /> : null}

      {!lostHere ? (
        <section className="v2r-sheet-section">
          {!adding ? (
            <button type="button" className="btn btn-sm v2r-btn snd-card-add" disabled={busy} onClick={() => setAdding(true)}>
              + Добавить звук к этому абзацу
            </button>
          ) : (
            <div className="snd-card-subform">
              <h3 className="v2r-sheet-h">Новый звук · {where}</h3>
              <Row label="Цитата" hint="Дословно из абзаца — по ней звук найдёт себя, если текст поменяется. Можно оставить пустой.">
                <textarea
                  className="ui-input snd-card-text"
                  rows={2}
                  value={added.quote}
                  onChange={(event) => setAdded((a) => ({ ...a, quote: event.target.value }))}
                  disabled={busy}
                  autoFocus
                />
              </Row>
              <Row label="Описание">
                <input className="ui-input" value={added.description} onChange={(event) => setAdded((a) => ({ ...a, description: event.target.value }))} disabled={busy} />
              </Row>
              <Row label="Запросы" hint="До трёх, через запятую, по-английски.">
                <input className="ui-input" value={added.queries} onChange={(event) => setAdded((a) => ({ ...a, queries: event.target.value }))} disabled={busy} />
              </Row>
              <div className="snd-card-actions">
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  disabled={busy || !added.description.trim()}
                  onClick={() => addHere.mutate(marker.segment_id)}
                >
                  {addHere.isPending ? "Добавляю…" : `Добавить к абз. ${paragraphNumber(marker.segment_id)}`}
                </button>
                <button
                  type="button"
                  className="btn btn-sm v2r-btn"
                  disabled={busy || !added.description.trim()}
                  onClick={() => {
                    const fields = addFields();
                    pickSegment({
                      hint: "Нажмите на абзац, к которому добавить звук",
                      onPick: async (segmentId) => {
                        const result = await addSoundMarker(chapterId, "sound", segmentId, fields);
                        pushToast({ tone: "success", title: "Звук добавлен", detail: `абз. ${paragraphNumber(segmentId)}` });
                        await refresh();
                        return result.marker.id;
                      },
                    });
                  }}
                >
                  к другому абзацу…
                </button>
                <button type="button" className="btn btn-sm btn-ghost" disabled={busy} onClick={() => setAdding(false)}>
                  Отмена
                </button>
              </div>
            </div>
          )}
        </section>
      ) : null}

      {otherLost.length > 0 ? (
        <section className="v2r-sheet-section">
          <h3 className="v2r-sheet-h">
            Потерялись в главе <span className="v2r-legend-count num">{otherLost.length}</span>
          </h3>
          <p className="v2r-sheet-note">Текст главы поменялся, и эти маркеры не нашли свой абзац.</p>
          <ul className="snd-lost-list">
            {otherLost.map((item) => (
              <li key={item.id} className="snd-lost">
                <button type="button" className="snd-lost-open" onClick={() => onOpenMarker(item)} title="Открыть карточку">
                  <span className="snd-lost-kind">{KIND_TITLE[item.kind]}</span> {markerCaption(item, places.find((p) => p.id === item.place_id))}
                </button>
                <button type="button" className="btn btn-sm v2r-btn" disabled={busy} onClick={() => startMove(item, item.quote)}>
                  поставить на абзац
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Sheet>
  );
}
