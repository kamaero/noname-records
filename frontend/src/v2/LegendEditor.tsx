/**
 * Palette editing from the legend: `LegendEditor` opens the v1
 * `CharacterPaletteModal` for one cast entry (with the v1 conflict guard);
 * `LegendConflicts` is the «Конфликты палитры: N» line with the v1 auto-fix.
 * Both post to `POST /api/budget/character/{character_id}` exactly like v1.
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPostJson, describeApiError } from "../api/client";
import { CharacterPaletteModal } from "../components/books/CharacterPaletteModal";
import { useToast } from "../components/ToastProvider";
import type { BudgetCharacterRow, SaveBudgetCharacterPayload, SaveBudgetCharacterResponse } from "../types";
import { PaletteWarning } from "../components/books/PaletteWarning";
import { PaletteFixSheet } from "../components/cast/PaletteFixSheet";
import {
  findMeaningfulPaletteConflicts,
  normalizeHexColor,
  paletteFromRow,
  palettePeers,
  type PaletteDraft,
  type PaletteResolutionEntry,
} from "../viewModels/booksCast";
import { invalidateReader, toBudgetRow, useBookCast } from "./editorApi";
import { NARRATOR, type ReaderCastEntry } from "./types";

/** A stand-in row while `/cast` is still loading — enough for the modal's preview. */
function rowFromEntry(entry: ReaderCastEntry): BudgetCharacterRow {
  return toBudgetRow({
    character_id: entry.character_id,
    char_map_id: "",
    name: entry.name,
    is_narrator: entry.name === NARRATOR,
    actor_name: entry.actor,
    mine: false,  // a stand-in for the palette preview; «мои роли» is the finder's question
    race: "",
    temperament: "",
    note: "",
    appears_in_chapters: [],
    lines_count: entry.lines,
    character_color: entry.color,
    character_text_color: entry.text_color,
    character_font_weight: entry.weight,
    character_font_style: entry.font_style,
  });
}

function savePalette(characterId: string, palette: PaletteDraft, row: BudgetCharacterRow) {
  const payload: SaveBudgetCharacterPayload = {
    character_color: normalizeHexColor(palette.background, row.character_color || "#13333B"),
    character_text_color: normalizeHexColor(palette.text, row.character_text_color || "#D7FFE9"),
    character_font_weight: palette.weight,
    character_font_style: palette.style,
  };
  // colours go to the colours endpoint: the budget one carries rates and totals with
  // them, and a dictor who may repaint a role may not move money
  return apiPostJson<SaveBudgetCharacterResponse>(`/api/v2/characters/${encodeURIComponent(characterId)}/palette`, payload);
}

type LegendEditorProps = {
  bookId: string;
  entry: ReaderCastEntry;
  onClose: () => void;
};

export function LegendEditor({ bookId, entry, onClose }: LegendEditorProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const cast = useBookCast(bookId, true);

  const rows = useMemo(() => (cast.data?.characters ?? []).map(toBudgetRow), [cast.data]);
  const row = useMemo(() => rows.find((item) => item.character_id === entry.character_id) ?? rowFromEntry(entry), [rows, entry]);
  // the legend entry carries the same colours as /cast, so the draft can start before the cast arrives
  const [draft, setDraft] = useState<PaletteDraft>(() => paletteFromRow(rowFromEntry(entry)));

  const save = useMutation({
    mutationFn: () => savePalette(entry.character_id, draft, row),
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Палитра не сохранена", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      pushToast({ tone: "success", title: `${entry.name}: палитра сохранена`, detail: "Реплики в тексте перекрасятся сейчас." });
      onClose();
      await invalidateReader(queryClient, bookId, ["chapters", "cast"]);
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Палитра не сохранена", detail: describeApiError(error, "Не удалось сохранить палитру.") });
    },
  });

  // A warning, computed as the colour is picked; the save is never blocked by it.
  const peers = useMemo(() => palettePeers(row, draft, rows), [row, draft, rows]);

  return (
    <CharacterPaletteModal
      row={row}
      draft={draft}
      isSaving={save.isPending}
      onChange={setDraft}
      onClose={onClose}
      onSave={() => save.mutate()}
      warning={<PaletteWarning conflicts={peers} />}
      saveLabel={peers.length ? "Всё равно сохранить" : undefined}
    />
  );
}

type LegendConflictsProps = {
  bookId: string;
  /** the open chapter: its pairs are listed first */
  chapterIndex: number;
  /** fetch only while the legend is expanded */
  enabled: boolean;
};

/**
 * «Конфликты палитры»: only pairs that can confuse an actor (both roles speak,
 * and meet in a chapter), this chapter's first; the raw pair count of the whole
 * book stays as a faint aside. Auto-fix touches the meaningful set only.
 */
export function LegendConflicts({ bookId, chapterIndex, enabled }: LegendConflictsProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const cast = useBookCast(bookId, enabled);
  const [preview, setPreview] = useState(false);

  const voicedRows = useMemo(() => (cast.data?.characters ?? []).map(toBudgetRow).filter((row) => !row.is_narrator), [cast.data]);
  const conflicts = useMemo(() => findMeaningfulPaletteConflicts(voicedRows), [voicedRows]);
  const here = useMemo(() => {
    const chaptersOf = new Map(voicedRows.map((row) => [row.character_id, new Set(row.appears_in_chapters || [])] as const));
    return conflicts.filter((item) => chaptersOf.get(item.leftId)?.has(chapterIndex) && chaptersOf.get(item.rightId)?.has(chapterIndex));
  }, [conflicts, voicedRows, chapterIndex]);

  const autoFix = useMutation({
    mutationFn: async (resolution: PaletteResolutionEntry[]) => {
      for (const entry of resolution) {
        await savePalette(entry.row.character_id, entry.palette, entry.row);
      }
      return resolution.length;
    },
    onSuccess: async (changed) => {
      setPreview(false);
      pushToast({ tone: "success", title: `Перекрашено ролей: ${changed}` });
      await invalidateReader(queryClient, bookId, ["chapters", "cast"]);
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Цвета не разведены", detail: describeApiError(error, "Не удалось сохранить палитру.") });
    },
  });

  if (!enabled || !cast.data) return null;
  const total = conflicts.length;
  const shown = here.length > 0 ? here : conflicts;
  const pairs = shown.slice(0, 3).map((item) => `${item.leftName} ↔ ${item.rightName}`).join(" · ");
  const rest = shown.length > 3 ? ` · ещё ${shown.length - 3}` : "";
  return (
    <div className={total > 0 ? "v2r-legend-conflicts is-bad" : "v2r-legend-conflicts"}>
      <span title="Две роли одного цвета, которые говорят в одной главе: на распечатке их не различить">
        {total > 0 ? (
          <>
            Похожие цвета: <strong className="num">{here.length}</strong>{" "}
            {here.length === 1 ? "пара" : "пар"} в этой главе
            {total > here.length ? (
              <span className="v2r-legend-conflicts-total">
                {" "}· <span className="num">{total}</span> в книге
              </span>
            ) : null}
          </>
        ) : (
          <>Похожие цвета: <strong className="num">нет</strong></>
        )}
      </span>
      {total > 0 ? (
        <>
          <span className="v2r-legend-conflicts-list">
            {pairs}
            {rest}
          </span>
          <button type="button" className="btn btn-sm v2r-btn" onClick={() => setPreview(true)}>
            Развести цвета
          </button>
        </>
      ) : null}
      {preview ? (
        <PaletteFixSheet
          rows={voicedRows}
          conflicts={conflicts}
          applying={autoFix.isPending}
          onApply={(resolution) => autoFix.mutate(resolution)}
          onClose={() => setPreview(false)}
        />
      ) : null}
    </div>
  );
}
