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
      <div className="books-palette-modal" role="dialog" aria-modal="true" aria-label={`Character palette ${row.name}`} onClick={(event) => event.stopPropagation()}>
        <div className="books-palette-modal-header">
          <div>
            <h3>Character palette</h3>
            <p>{row.name}. Choose colors and typography for this role.</p>
          </div>
          <button type="button" className="books-palette-close" onClick={onClose}>×</button>
        </div>

        <div className="books-palette-preview" style={characterSwatchStyleFromPalette(draft)}>
          <span>Preview</span>
          <strong>[{row.name}] — This is how the role will appear in the script.</strong>
        </div>

        <div className="books-palette-grid">
          <label className="books-palette-field">
            <span>Background</span>
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
            <span>Text color</span>
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
            <span>Font weight</span>
            <select value={draft.weight} onChange={(event) => onChange({ ...draft, weight: event.target.value as FontWeightOption })}>
              {FONT_WEIGHT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>

          <label className="books-palette-field">
            <span>Style</span>
            <select value={draft.style} onChange={(event) => onChange({ ...draft, style: event.target.value as FontStyleOption })}>
              {FONT_STYLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
        </div>

        {warning ? <div className="books-palette-warning">{warning}</div> : null}

        <div className="books-palette-actions">
          <button type="button" className="books-palette-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="books-palette-primary" disabled={isSaving} onClick={onSave}>
            {isSaving ? "Saving..." : saveLabel || "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
