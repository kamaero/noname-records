/**
 * Звуковой слой Читалки — шпаргалка звукорежиссёра на полях сценария. Только редактор.
 * Полоса сцены стоит перед первым абзацем сцены (как заголовок места в киносценарии),
 * переход — пунктир между абзацами, значимый звук — метка на правом поле.
 */
import { useEffect } from "react";
import { useToast } from "../components/ToastProvider";
import { copyQuery, sceneTitle } from "./sound";
import type { SoundMarker, SoundPlace } from "./types";
import "./SoundLayer.css";

function Queries({ items }: { items: string[] }) {
  const { pushToast } = useToast();
  if (!items.length) return null;
  return (
    <span className="snd-queries">
      {items.map((query) => (
        <button
          key={query}
          type="button"
          className="snd-query"
          title="Скопировать запрос"
          onClick={async (event) => {
            event.stopPropagation();
            const ok = await copyQuery(query);
            pushToast({ tone: ok ? "success" : "error", title: ok ? "Запрос скопирован" : "Не скопировалось", detail: query });
          }}
        >
          🔎 {query}
        </button>
      ))}
    </span>
  );
}

export function SceneBand({ marker, place, number, active = false, onOpen }: {
  marker: SoundMarker; place?: SoundPlace; number: number; active?: boolean; onOpen: (marker: SoundMarker) => void;
}) {
  const others = (place?.chapters.length ?? 0) - 1;
  const line = [marker.payload.ambience && `фон: ${marker.payload.ambience}`, marker.payload.mood && `настроение: ${marker.payload.mood}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className={active ? "snd-scene is-active" : "snd-scene"} data-sound={marker.id}>
      <div className="snd-scene-head">
        <span className="snd-scene-title">▸ {sceneTitle(marker, place, number)}</span>
        {others > 0 ? (
          <span className="snd-badge" title={`Это место есть и в главах: ${place!.chapters.join(", ")}`}>
            ещё {others} гл.
          </span>
        ) : null}
        <button
          type="button"
          className="snd-edit"
          aria-label="Править сцену"
          title="Править сцену"
          onClick={(event) => {
            event.stopPropagation();
            onOpen(marker);
          }}
        >
          ✎
        </button>
      </div>
      {line ? <div className="snd-scene-line">{line}</div> : null}
      {/* подложка места и музыка сцены могут повторять друг друга — один запрос одной кнопкой */}
      <Queries items={[...new Set([...(place?.ambience_queries ?? []), ...(marker.payload.music_queries ?? [])])]} />
    </div>
  );
}

export function TransitionLine({ marker, active = false, onOpen }: {
  marker: SoundMarker; active?: boolean; onOpen: (marker: SoundMarker) => void;
}) {
  return (
    <button
      type="button"
      className={active ? "snd-transition is-active" : "snd-transition"}
      data-sound={marker.id}
      title="Переход — править"
      onClick={(event) => {
        event.stopPropagation();
        onOpen(marker);
      }}
    >
      <span>◆ {marker.payload.what || "переход"}</span>
    </button>
  );
}

/** То, чего нет в тексте абзаца: чипы ролей, «роль», значки — и сами метки звука. */
const NOT_TEXT = ".v2r-chip, .v2r-role-hint, .v2r-op, .v2r-cons-mark, .snd-marks";
const fold = (ch: string) => (ch === "ё" ? "е" : ch);

/**
 * Подсветка цитаты звука в тексте абзаца — CSS Custom Highlight, без правки разметки абзаца.
 * Цитата может пересекать границы спанов ролей, а в словах стоят знаки ударения (U+0301),
 * которых в цитате нет, — поэтому текст абзаца собирается целиком, с картой «буква → узел».
 */
function highlight(paragraph: HTMLElement | null, quote: string) {
  const registry = (CSS as unknown as { highlights?: Map<string, unknown> }).highlights;
  const HighlightCtor = (window as unknown as { Highlight?: new (...ranges: Range[]) => unknown }).Highlight;
  if (!registry || !HighlightCtor) return;
  registry.delete("snd-quote");
  const needle = Array.from(quote.toLowerCase().replace(/́/g, "")).map(fold).join("");
  if (!paragraph || !needle) return;
  const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT);
  let text = "";
  const map: Array<[Text, number]> = [];
  for (let node = walker.nextNode() as Text | null; node; node = walker.nextNode() as Text | null) {
    if (node.parentElement?.closest(NOT_TEXT)) continue;
    const data = node.data.toLowerCase();
    for (let i = 0; i < data.length; i += 1) {
      if (data[i] === "́") continue;
      text += fold(data[i]);
      map.push([node, i]);
    }
  }
  const at = text.indexOf(needle);
  if (at < 0) return;
  const [startNode, startOffset] = map[at];
  const [endNode, endOffset] = map[at + needle.length - 1];
  const range = document.createRange();
  range.setStart(startNode, startOffset);
  range.setEnd(endNode, endOffset + 1);
  registry.set("snd-quote", new HighlightCtor(range));
}

/**
 * Метки значимых звуков абзаца. Где справа от страницы есть поле (`.snd-roomy` на
 * странице), метка стоит на нём с описанием; иначе — плашка «● описание» в конце
 * абзаца (описание обрезается, целиком — в подсказке). Шпаргалку читают глазами на
 * прокрутке, поэтому описание видно всегда, а не только в подсказке.
 */
export function SoundMarks({ markers, onOpen }: { markers: SoundMarker[]; onOpen: (marker: SoundMarker) => void }) {
  // подсветка цитаты глобальная: метка, исчезнувшая под курсором (слой выключен,
  // глава сменилась, данные обновились), не должна оставить её висеть в тексте
  useEffect(() => () => highlight(null, ""), []);
  if (!markers.length) return null;
  return (
    <span className="snd-marks">
      {markers.map((marker) => (
        <button
          key={marker.id}
          type="button"
          className="snd-mark"
          data-sound={marker.id}
          title={`${marker.payload.description ?? ""} — «${marker.quote}»`}
          aria-label={`Звук: ${marker.payload.description ?? ""}`}
          onMouseEnter={(event) => highlight(event.currentTarget.closest("p, h2"), marker.quote)}
          onMouseLeave={() => highlight(null, "")}
          onFocus={(event) => highlight(event.currentTarget.closest("p, h2"), marker.quote)}
          onBlur={() => highlight(null, "")}
          onClick={(event) => {
            event.stopPropagation();
            onOpen(marker);
          }}
        >
          <span className="snd-mark-dot" aria-hidden="true">●</span>
          <span className="snd-mark-text">{marker.payload.description}</span>
        </button>
      ))}
    </span>
  );
}
