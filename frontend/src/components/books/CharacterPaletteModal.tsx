import "./CharacterPaletteModal.css";
import type { ReactNode } from "react";
import {
  characterSwatchStyleFromPalette,
  FONT_STYLE_OPTIONS,
  FONT_WEIGHT_OPTIONS,
  normalizeHexColor,
  type FontStyleOption,
  type FontWeightOption,
  type PaletteDraft,
} from "../../viewModels/booksCast";
import type { BudgetCharacterRow } from "../../types";

type CharacterPaletteModalProps = {
  row: BudgetCharacterRow;
  draft: PaletteDraft;
  isSaving: boolean;
  onChange: (next: PaletteDraft) => void;
  onClose: () => void;
  onSave: () => void;
  /** shown above the buttons: roles this colour could be confused with */
  warning?: ReactNode;
  /** the primary button's words, when a warning changes what pressing it means */
  saveLabel?: string;
};

export function CharacterPaletteModal({
  row,
  draft,
  isSaving,
  onChange,
  onClose,
  onSave,
  warning,
  saveLabel,
}: CharacterPaletteModalProps) {
  return (
    <div className="books-palette-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="books-palette-modal" role="dialog" aria-modal="true" aria-label={`Палитра персонажа ${row.name}`} onClick={(event) => event.stopPropagation()}>
        <div className="books-palette-modal-header">
          <div>
            <h3>Палитра персонажа</h3>
            <p>{row.name}. Тут уже можно играть в художника, но без масляных красок на мониторе.</p>
          </div>
          <button type="button" className="books-palette-close" onClick={onClose}>×</button>
        </div>

        <div className="books-palette-preview" style={characterSwatchStyleFromPalette(draft)}>
          <span>Превью</span>
          <strong>[{row.name}] — Вот так будет выглядеть роль в сценарии.</strong>
        </div>

        <div className="books-palette-grid">
          <label className="books-palette-field">
            <span>Фон</span>
            <div className="books-palette-color-row">
              <input
                type="color"
                value={draft.background}
                onChange={(event) => onChange({ ...draft, background: normalizeHexColor(event.target.value, draft.background) })}
              />
              <input
                type="text"
                value={draft.background}
                onChange={(event) => onChange({ ...draft, background: event.target.value.toUpperCase() })}
                onBlur={(event) => onChange({ ...draft, background: normalizeHexColor(event.target.value, draft.background) })}
              />
            </div>
          </label>

          <label className="books-palette-field">
            <span>Шрифт</span>
            <div className="books-palette-color-row">
              <input
                type="color"
                value={draft.text}
                onChange={(event) => onChange({ ...draft, text: normalizeHexColor(event.target.value, draft.text) })}
              />
              <input
                type="text"
                value={draft.text}
                onChange={(event) => onChange({ ...draft, text: event.target.value.toUpperCase() })}
                onBlur={(event) => onChange({ ...draft, text: normalizeHexColor(event.target.value, draft.text) })}
              />
            </div>
          </label>

          <label className="books-palette-field">
            <span>Насыщенность</span>
            <select value={draft.weight} onChange={(event) => onChange({ ...draft, weight: event.target.value as FontWeightOption })}>
              {FONT_WEIGHT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>

          <label className="books-palette-field">
            <span>Стиль</span>
            <select value={draft.style} onChange={(event) => onChange({ ...draft, style: event.target.value as FontStyleOption })}>
              {FONT_STYLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
        </div>

        {warning ? <div className="books-palette-warning">{warning}</div> : null}

        <div className="books-palette-actions">
          <button type="button" className="books-palette-secondary" onClick={onClose}>Отмена</button>
          <button type="button" className="books-palette-primary" disabled={isSaving} onClick={onSave}>
            {isSaving ? "Сохраняю..." : saveLabel || "OK, сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}
