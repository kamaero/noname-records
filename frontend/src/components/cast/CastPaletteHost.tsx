/**
 * The one modal on the cast page: `CharacterPaletteModal` for a single row, posting to
 * `POST /api/budget/character/{id}`.
 *
 * The v1 guard that refused a colour clashing with any of the book's roles is gone —
 * it made violet unavailable to Пупип because of a role from chapter 57. The roles the
 * colour could be confused with are listed in the modal instead.
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPostJson, describeApiError } from "../../api/client";
import type { SaveBudgetCharacterPayload, SaveBudgetCharacterResponse } from "../../types";
import { CharacterPaletteModal } from "../books/CharacterPaletteModal";
import { useToast } from "../ToastProvider";
import {
  normalizeHexColor,
  paletteFromRow,
  palettePeers,
  type CastRow,
  type PaletteDraft,
} from "../../viewModels/booksCast";
import { PaletteWarning } from "../books/PaletteWarning";

type CastPaletteHostProps = {
  bookId: string;
  row: CastRow;
  allRows: CastRow[];
  onClose: () => void;
};

export function CastPaletteHost({ bookId, row, allRows, onClose }: CastPaletteHostProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [draft, setDraft] = useState<PaletteDraft>(() => paletteFromRow(row));

  const save = useMutation({
    mutationFn: () => {
      const payload: SaveBudgetCharacterPayload = {
        character_color: normalizeHexColor(draft.background, row.character_color || "#13333B"),
        character_text_color: normalizeHexColor(draft.text, row.character_text_color || "#D7FFE9"),
        character_font_weight: draft.weight,
        character_font_style: draft.style,
      };
      return apiPostJson<SaveBudgetCharacterResponse>(
        `/api/v2/characters/${encodeURIComponent(row.character_id)}/palette`,
        payload,
      );
    },
    onSuccess: async (result) => {
      if (!result.ok) {
        pushToast({ tone: "error", title: "Палитра не сохранена", detail: result.error || "Сервер ответил без подробностей." });
        return;
      }
      pushToast({ tone: "success", title: `${row.name}: палитра сохранена` });
      onClose();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["book-budget", bookId] }),
        queryClient.invalidateQueries({ queryKey: ["v2", "book-cast", bookId] }),
      ]);
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Палитра не сохранена", detail: describeApiError(error, "Не удалось сохранить палитру.") });
    },
  });

  const peers = useMemo(() => palettePeers(row, draft, allRows), [row, draft, allRows]);

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
