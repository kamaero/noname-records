---
name: Noname Records
description: Director's console for turning a book into a colour-coded multi-voice script; dark-first, dense, IBM Plex, one green accent.
colors:
  accent: "oklch(0.68 0.13 154)"
  accent-press: "oklch(0.62 0.13 154)"
  accent-soft: "oklch(0.68 0.13 154 / 0.16)"
  accent-line: "oklch(0.68 0.13 154 / 0.40)"
  on-accent: "#07120d"
  bg: "#0c1210"
  surface-1: "#121917"
  surface-2: "#18211e"
  surface-3: "#1e2825"
  border: "rgba(255,255,255,0.07)"
  border-strong: "rgba(255,255,255,0.13)"
  text: "#e7efe9"
  text-dim: "#9aaaa1"
  text-faint: "#66766d"
  amber: "oklch(0.78 0.12 78)"
  amber-soft: "oklch(0.78 0.12 78 / 0.15)"
  blue: "oklch(0.72 0.11 250)"
  blue-soft: "oklch(0.72 0.11 250 / 0.15)"
  danger: "oklch(0.68 0.16 25)"
  danger-soft: "oklch(0.68 0.16 25 / 0.15)"
  light-accent: "oklch(0.58 0.13 154)"
  light-accent-press: "oklch(0.52 0.13 154)"
  light-accent-soft: "oklch(0.58 0.13 154 / 0.13)"
  light-accent-line: "oklch(0.58 0.13 154 / 0.42)"
  light-on-accent: "#ffffff"
  light-bg: "#eef1ee"
  light-surface-1: "#ffffff"
  light-surface-2: "#f5f7f5"
  light-surface-3: "#ecefec"
  light-border: "rgba(18,28,23,0.11)"
  light-border-strong: "rgba(18,28,23,0.20)"
  light-text: "#16201b"
  light-text-dim: "#51635a"
  light-text-faint: "#8a9890"
  light-amber: "oklch(0.62 0.13 70)"
  light-blue: "oklch(0.55 0.13 250)"
  light-danger: "oklch(0.55 0.18 25)"
typography:
  prose:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.7
    letterSpacing: "normal"
  headline:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  control:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "normal"
  label:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.09em"
  numeric:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "25px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
rounded:
  span: "3px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "8px"
  md: "10px"
  pad: "14px"
  gap: "14px"
  content: "22px"
  row: "42px"
components:
  button-secondary:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 14px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-3}"
  button-secondary-on:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 14px"
  button-primary-hover:
    backgroundColor: "{colors.accent-press}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-dim}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 14px"
  button-ghost-hover:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
  button-sm:
    height: "30px"
    padding: "0 11px"
  input:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 11px"
  chip:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-dim}"
    rounded: "{rounded.pill}"
    height: "24px"
    padding: "0 9px"
  status-done:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    rounded: "{rounded.pill}"
    height: "22px"
    padding: "0 9px 0 8px"
  status-processing:
    backgroundColor: "{colors.blue-soft}"
    textColor: "{colors.blue}"
    rounded: "{rounded.pill}"
    height: "22px"
  status-needs-fix:
    backgroundColor: "{colors.amber-soft}"
    textColor: "{colors.amber}"
    rounded: "{rounded.pill}"
    height: "22px"
  status-failed:
    backgroundColor: "{colors.danger-soft}"
    textColor: "{colors.danger}"
    rounded: "{rounded.pill}"
    height: "22px"
  panel:
    backgroundColor: "{colors.surface-1}"
    rounded: "{rounded.lg}"
    padding: "{spacing.pad}"
  role-chip:
    rounded: "{rounded.pill}"
    height: "26px"
    padding: "0 10px"
  tab:
    backgroundColor: "transparent"
    textColor: "{colors.text-dim}"
    typography: "{typography.control}"
    height: "42px"
    padding: "0 13px"
  tab-active:
    textColor: "{colors.text}"
  segment:
    backgroundColor: "{colors.surface-2}"
    rounded: "{rounded.md}"
    padding: "3px"
  segment-on:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.text}"
---

# Design System: Noname Records

## Overview

**Creative North Star: "The Director's Console"**

Noname Records is a working console, not a showcase: the kind of dark editing surface a producer sits behind for eight hours. Every screen is built from the same few materials: a near-black green-tinted canvas (`bg`), one step lighter panels (`surface-1`), quiet hairline borders, and IBM Plex Sans at 13–14px for controls. Colour is data. A single moss-green accent (hue 154) marks the primary action and the current selection; amber, blue and red carry status; and the loud, saturated colours on screen belong to cast roles, which come from the book's palette rather than from the design system.

The system is dark by default and has a full, equal light theme (`data-theme="light"`) that re-derives every token rather than inverting. Density is a first-class dial: `--row-h`, `--pad`, `--gap` are switched by `data-density="compact|spacious"` and nothing else needs to change. The v2 script reader (`frontend/src/v2/`) is the reference surface: the prose is the main object at 17px/1.7 on a 74ch measure, and every other piece of chrome (toolbar, chapter rail, sticky cast legend, stress dock, side sheets) is a panel that steps aside from it.

**Key Characteristics:**
- Dark-first, green-tinted neutrals; light theme is a peer, not an afterthought.
- One accent, used for the primary action, the active tab/row/chip, and focus rings; status colours are semantic, role colours are data.
- Flat tonal layering (`bg` → `surface-1` → `surface-2` → `surface-3`) with hairline borders; shadows are small and only two.
- Dense 13–14px control type, 11px uppercase tracked labels, IBM Plex Mono for every number and id.
- 120ms state transitions, no decorative motion, no glow.
- 40px minimum targets on the reader and on mobile.

## Colors

A near-black green-tinted neutral ramp, one moss-green accent, three semantic status hues, and role colours injected per span from the book's cast palette.

### Primary
- **Moss Accent** (`accent`, `oklch(0.68 0.13 154)`; light `oklch(0.58 0.13 154)`): primary button fill, active tab underline, active chapter/book row tint, stress-mark underline, focus ring hue, brand mark. Every derived tone is built from `--accent-h: 154`, so shifting the hue re-tints the whole system.
- **Moss Press** (`accent-press`): hover/active fill of the primary button only.
- **Moss Wash** (`accent-soft`, 16% alpha; light 13%): selected-row background, "is-on" toggle fill, avatar background, text selection, focus halo.
- **Moss Line** (`accent-line`, 40% alpha): border of any selected/active element and of focused inputs; also the border of the stress dock.
- **On Accent** (`on-accent`, `#07120d`; light `#ffffff`): text on the accent fill.

### Secondary (status, semantic)
- **Signal Amber** (`amber`, `oklch(0.78 0.12 78)` + `amber-soft`): needs-attention states (`needs_fix`, `stalled`, `partially_approved`), the unsure-role dashed outline, search hits, the reader's warning notice.
- **Working Blue** (`blue`, `oklch(0.72 0.11 250)` + `blue-soft`): in-progress and awaiting-review states (`processing`, `author_review`, `pending_review`); info toasts.
- **Fault Red** (`danger`, `oklch(0.68 0.16 25)` + `danger-soft`): `failed`, `stopped`, `rejected_audio`; error toasts and sheet error blocks; palette-conflict counts.

### Neutral
- **Canvas** (`bg`, `#0c1210`; light `#eef1ee`): page background.
- **Panel** (`surface-1`, `#121917`; light `#ffffff`): panels, topbar, tab bar, rails, sheets, legend.
- **Well** (`surface-2`, `#18211e`; light `#f5f7f5`): inputs, secondary buttons, chips, table header, segment tracks, hover on rows.
- **Well Deep** (`surface-3`, `#1e2825`; light `#ecefec`): button hover, progress track, count badges.
- **Hairline** (`border`, 7% white; light 11% ink) and **Hairline Strong** (`border-strong`, 13% / 20%): all borders and dividers. Buttons use the strong hairline; panels and inputs use the plain one.
- **Ink** (`text`, `#e7efe9`; light `#16201b`), **Ink Dim** (`text-dim`, `#9aaaa1` / `#51635a`), **Ink Faint** (`text-faint`, `#66766d` / `#8a9890`): three text levels. Faint is for labels, indices, placeholders; dim for metadata and secondary copy.

### Role colours (data, not tokens)
Cast colours arrive as `--role-color` / `--role-text` / `--role-weight` / `--role-style` on each span and chip, from `app/services/character_colors.py` (Google-Docs-style hex like `#b7b7b7`, `#4a86e8`, `#a64d79`). The reader renders the chip at full `--role-color` and the spoken line at `color-mix(in oklab, var(--role-color) 28%, transparent)`. The `?` unsure role has no fill: a dashed amber outline.

### Named Rules
**The One Green Rule.** The accent belongs to the primary action, the current selection, and focus. Anything that is neither an action nor a selection is a neutral or a status hue. Two accent-filled buttons in one toolbar is a defect.

**The Colour Is Data Rule.** Saturated colour on the page encodes a role or a status. Decorative colour, gradients, and glows are out; the only gradient in the system is the login-side radial wash of `accent-soft`.

**The Soft/Line Pair Rule.** A selected element is `*-soft` fill plus `*-line` border of the same hue, never a solid fill. Solid accent is reserved for the primary button, the pressed stress letter, and the "done" pipeline node.

## Typography

**Display Font:** none. Headlines use the body face.
**Body Font:** IBM Plex Sans (with system-ui, sans-serif)
**Label/Mono Font:** IBM Plex Mono (with ui-monospace, monospace) for numbers, ids, counts, timestamps, and the brand subtitle

**Character:** Plex Sans at small sizes and medium weight reads like a control surface; Plex Mono with tabular figures turns every count and price into a readout. There is no display face and no decorative weight: hierarchy is carried by size, weight 600, and the tracked uppercase label.

### Hierarchy
- **Prose** (400, 17px, 1.7; S=15px/1.6, L=20px/1.75; mobile +1px; print 12pt/1.55): the script text in the reader, on a 74ch measure with `hyphens: auto`. Chapter headings inside the prose are 1.35em/1.3, weight 600, -0.01em. Spoken lines inherit `--role-weight` (700 by default from the palette).
- **Headline** (600, 22px, -0.01em): page and book titles (`Крылья полумрака`, `Библиотека`). One per screen.
- **Title** (600, 16–17px, -0.01em): chapter title in the reader toolbar, card titles, sheet titles (16px), the stress dock's word preview (20px).
- **Subtitle** (600, 13.5–15px): rail headings, row titles, role names in table cells.
- **Body** (400, 14px, 1.5): default. Sheet key/value rows and table cells run at 13–13.5px.
- **Control** (500, 13px; small 12.5px): buttons, tabs, inputs, segment buttons.
- **Meta** (400, 11.5–12.5px, `text-dim`/`text-faint`): actor names under a role chip, counts, hints, timestamps.
- **Label** (600, 11px, uppercase, 0.09em; table headers 0.04em, stat labels 0.06em): section titles like `КАСТ ГЛАВЫ`, `УДАРЕНИЕ`, table headers, stat labels. Always `text-faint`.
- **Numeric** (600, 25px, Plex Mono, -0.02em, `tnum`): stat values. Smaller readouts (17px in budget bars and card stats) keep the mono face.

### Named Rules
**The Mono Number Rule.** Every number that can be compared — counts, prices, indices, times, chapter numbers — is set in IBM Plex Mono with tabular figures (`.num` / `.mono`). Numbers in Plex Sans are a defect.

**The Label Is Quiet Rule.** Uppercase tracked labels are always 11px, weight 600, `text-faint`. They name a section or a column; they never introduce a headline as a kicker.

**The Reader Owns Size Rule.** Only the reader exposes a font-size switch (S/M/L). Chrome type does not scale with it.

## Layout

The shell is a three-row grid: a 56px topbar (brand, search, theme toggle, user pill; on a phone one row with a menu sheet, search behind an icon), a 42px tab bar with a 2px accent underline on the active tab, and a `content` area padded 22px with an optional 1320px `content-wide` container. Pages inside are grids of panels separated by `--gap` (14px; compact 10px; spacious 18px). Two-column layouts are `290px 1fr` for a rail plus stage (books shelf), `1.4fr 1fr` for content plus sidebar, and `repeat(4, 1fr)` for stat rows; all collapse to one column at 1080px.

The reader adds its own grid: an optional 250px sticky chapter rail beside a `minmax(0, 1fr)` main column; the cast legend is sticky at top 0 (z-index 5) and the stress dock at top 8px (z-index 8) above it. Prose sits centred on a 74ch measure. At 760px the rail becomes a fixed left drawer (`min(86vw, 340px)`, slides in over a 45% black backdrop), the toolbar wraps, the search takes the full row, and every control grows to a 40px minimum, with the bottom prev/next buttons stretched to 44px full width. Side sheets (queue, profile) are fixed right panels `min(92vw, 440px)` wide, full width on mobile.

Rhythm: 14px is the unit (`--pad`, `--gap`). Inside panels, 10–12px between rows, 6–8px between chips and inline controls, 2px between list items. Table rows are `--row-h` (42px; compact 36; spacious 50). Density modes change only these three variables.

## Elevation & Depth

Depth is tonal first. Layers are built from `bg` → `surface-1` → `surface-2` → `surface-3` plus a hairline border; a panel reads as a panel because it is one step lighter and outlined, not because it floats. Two shadows exist and are used deliberately: the small one on resting panels, the large one on anything that overlays the page.

### Shadow Vocabulary
- **Resting** (`shadow-1`, `0 1px 2px rgba(0,0,0,0.3)`; light `0 1px 2px rgba(20,32,27,0.06)`): panels, toolbar, chapter rail, the "on" segment button.
- **Overlay** (`shadow-2`, `0 8px 28px rgba(0,0,0,0.38)`; light `0 10px 30px rgba(20,32,27,0.10)`): sticky legend, stress dock, side sheets, mobile rail drawer, card hover.
- **Focus** (`0 0 0 3px accent-soft`, plus `accent-line` border): inputs and the dock; `outline: 2px solid accent` with 1–2px offset on buttons, letters, and chips.

### Named Rules
**The Two Shadows Rule.** `shadow-1` at rest, `shadow-2` when something sits above the page. There is no third shadow, no coloured shadow, and no glow (`--glow: 0px` in light theme exists precisely to kill legacy halos).

**The Scrim Rule.** Anything modal (rail drawer, sheet) is backed by `rgba(0,0,0,0.45)`. Nothing blurs.

## Shapes

Gently rounded, tighter as things get smaller. Panels and cards are 12px (`lg`); buttons, inputs, rail items, and notices are 8px (`md`); segment buttons, cell inputs, and the stress letters' inner elements are 5–6px (`sm`); inline role spans and clickable words are 3px. Anything that is a token of a person or a state is a pill (999px): chips, status badges, role chips, the user box, count badges, tab badges. The brand mark is a 28px square at 7px. Borders are always 1px hairlines; the only dashed borders are the unsure-role outline (1.5px dashed amber), the paragraph gap button, and the palette-conflicts divider. The legend's edit affordance is a pill split in two: the role button keeps the left cap, the `⋯` edit button takes the right cap.

## Components

Character: quiet, precise, the same everywhere. Every control is 36px tall on desktop, 40px on the reader's mobile layout, with 120ms colour transitions.

### Buttons
- **Shape:** 8px radius, 36px tall, 0 14px padding, 13px/500, inline-flex with 7px icon gap; icons 15px.
- **Secondary (default `.btn`):** `surface-2` fill, `border-strong` hairline, `text`. Hover `surface-3`; active translates down 1px over 80ms. This is the workhorse (`Пред.`, `След.`, `Главы`, `PDF / печать`).
- **Toggle on (`.is-on`):** `accent-soft` fill, `accent-line` border, `accent` text (`Ударе́ния`, `Очередь`, `Профиль`). `aria-pressed` carries the state.
- **Primary (`.btn-primary`):** accent fill, `on-accent` text, weight 600, no border; hover `accent-press`. One per view (`Для этой книги` in the dock).
- **Ghost (`.btn-ghost`):** transparent, `text-dim`; hover `surface-2` + `text` (`Показать всё`).
- **Small (`.btn-sm`):** 30px tall, 0 11px, 12.5px; the reader's toolbar uses small buttons stretched to `min-height: 36px`.
- **Icon (`.btn-icon`):** 32px square.
- **Disabled:** opacity 0.45, default cursor, hover suppressed.
- **Focus:** `outline: 2px solid accent; outline-offset: 1–2px`.

### Segmented control
`surface-2` track with hairline and 8px radius, 3px inner padding; segments 28px tall, 5px radius, `text-dim`, weight 600; the selected segment is `surface-1` with `shadow-1` and `text` (S/M/L, theme toggle, subtabs, the stress display switch «нет · редкие · все»). Same pattern at 42px for the tab bar minus the track.

### Chips
- **Neutral chip (`.chip`):** 24px pill, `surface-2`, hairline, `text-dim`, 11.5px/500.
- **Status badge (`.status`):** 22px pill with a 6px `currentColor` dot; neutral by default, then `accent`/`blue`/`amber`/`danger` text over the matching `*-soft` fill by pipeline state. Done states also get an `accent-line` border; others drop the border.
- **Role chip (`.v2r-role-chip`):** 26px pill filled with `--role-color`, text `--role-text`, weight `--role-weight` (700), 12.5px; inline in prose it is 0.7em, 999px, bold, sans, non-selectable, with 0.35em right margin.
- **Legend role button (`.v2r-role`):** 40px pill, `surface-2`, hairline; contains the role chip plus actor name (12px dim) and line count (11px faint, mono). Hover `border-strong`; selected gets `accent-line` border plus a 2px `accent-soft` ring; in role mode unselected roles drop to 0.55 opacity.
- **Action chip (`.v2r-chip-btn`):** 40px pill, 14px text, hover `accent-soft` + `accent-line` (queue words).
- **Unsure:** transparent with 1.5px dashed amber and amber text, both in the legend and inline.

### Cards / Containers
- **Panel:** `surface-1`, hairline, 12px radius, `shadow-1`, 14px padding (`.panel-pad`). Toolbar, rail, legend, sheets, stat tiles all derive from it.
- **Stat tile:** panel with an 11px uppercase label, a 25px mono value, and an 11.5px dim sub-line.
- **Card link (command center):** panel with 16px 18px padding; hover lifts 2px, borders `accent-line`, takes `shadow-2`, and slides the arrow 3px.
- **Sticky overlays:** the legend and stress dock are panels promoted to `shadow-2`; the dock additionally uses an `accent-line` border to read as "editing".
- **Notice:** 8px radius, `amber-soft` fill, 1px amber border, 13.5px; errors swap to `danger-soft`/`danger`.

### Inputs / Fields
- **Style:** 36px, `surface-2`, hairline, 8px radius, 0 11px padding, `text`; placeholder `text-faint`; label above at 12px/500 `text-dim` with 6px gap. Search boxes wrap a 15px icon and an unstyled input in the same shell; the topbar search shows a `↵` kbd.
- **Focus:** border `accent-line` + `0 0 0 3px accent-soft` halo (`:focus-within` on search shells).
- **Cell input:** transparent 30px input inside tables; hairline appears on hover, accent ring on focus.
- **Select:** same shell as input, 0 8px padding.
- **Checkbox:** native, `accent-color: accent`, 16px, in a 40px-tall label.

### Navigation
- **Topbar:** 56px `surface-1` with bottom hairline; brand mark 28px accent square with the wave icon, `NONAME RECORDS` 13px/700 tracked 0.12em, `студия озвучки книг` 10px mono faint.
- **Tab bar:** 42px tabs, 13px/500 `text-dim`, 15px icon, hover `text`, active `text` with 2px accent bottom border; admin tabs (`Пользователи`, `Лог`, `Помощь`) are `text-faint` and pushed right. Badges are 10.5px mono pills.
- **Rail item (books, chapters):** 8px radius, transparent, hover `surface-2`, active `accent-soft` + `accent-line`; chapter index 28px wide right-aligned faint, turning accent when active.
- **Mobile:** the topbar collapses to one row with the tabs in a menu sheet; the chapter rail becomes a left drawer at 760px with a 40px close button.

### Side sheet
Fixed right panel, `surface-1` with left hairline and `shadow-2`, over a 45% scrim; header 14px padding with 16px/600 title, 12px dim subtitle, 40px close; body sections separated by 18px with 12px uppercase `text-faint` headings; footer `surface-2` with top hairline. Key/value lists are 28px rows with hairline dividers, keys 13px dim, values 13.5px right-aligned.

### Stress dock (signature)
Sticky panel under the toolbar with `accent-line` border and `shadow-2`; `УДАРЕНИЕ` label, the word at 20px/600, `сейчас: нет` source note. Letters are 40px squares (`surface-2`, hairline, 8px, 18px/600); vowels hover to `accent-soft`, the chosen vowel fills solid accent, consonants sit at 0.4 opacity. Actions: one primary (`Для этой книги`), one secondary (`Автору`), then a 12px faint keyboard hint (`← → гласная · Enter — в книгу · Esc — закрыть`).

### Prose marks (signature)
- **Spoken line:** 3px-radius span tinted 28% of the role colour with `box-decoration-break: clone`; paragraph gets a 3px left rule in the role colour (`--p-color`) and 0.7em inset.
- **Stress:** 2px accent underline offset 0.12em under the vowel, in addition to the acute glyph in the text.
- **Search hit:** `amber-soft` fill with a 1px amber ring.
- **Dimmed (role mode):** 0.45 opacity, 0.8 on hover; headings dim the same way.
- **Paragraph gap:** dashed `border-strong` 40px block, 0.8em faint text.

### Feedback
- **Toast:** fixed top-right (18px), `surface-2`, `border-strong`, 8px radius, 14px 16px padding, 14px/600 title + 13px dim detail; error border `danger`, info border `blue`; enters with `toast-in` (8px rise, 0.98 scale), auto-dismisses at 4200ms. Lives in `styles.legacy.css` today.
- **Skeleton:** `border-strong` blocks (22px title, 13px lines) at 8px radius with a 1.3s shimmer; `aria-busy` + `aria-live="polite"` and a `Загрузка…` label at 0.6 opacity.
- **Empty state:** centred 34px icon at 0.5 opacity, `text-faint`, 60px vertical padding; reader variant 48px padding with a 15px dim line.
- **Progress bar:** 6px pill track `surface-3`, accent fill; amber variant for warnings.

### Iconography
Inline SVG only (`components/Icon.tsx`): 24-grid, `stroke: currentColor`, thin stroke, no fill. Rendered at 15px in buttons and tabs, 13px in stat labels, 17px in the brand mark, 32–34px in empty states. No icon fonts, no emoji, no glyph characters as icons except the legend caret (`▾`/`▸`) and the sheet close `×`, which the build carries as text.

### Motion
State changes only. Colour, border, and opacity transition at 120ms; the word hover in editor mode and the stress letters at 100ms; press translate at 80ms. The chapter rail slides in over 160ms ease-out. Continuous motion is limited to the skeleton shimmer and the hub's running-step pulse. `prefers-reduced-motion` is honoured by the shell, the `ui/` primitives, the reader, the hub, the cast table, recording and upload.

## Do's and Don'ts

### Do:
- **Do** build every new surface from `styles.css` tokens only. The v2 reader has one non-token colour family (`--role-color`), and that is the ceiling.
- **Do** keep controls at 36px on desktop and 40px on the reader and on mobile; stretch bottom-of-page navigation to 44px full width.
- **Do** mark selection with `*-soft` fill plus `*-line` border, and carry the state in `aria-pressed` / `aria-expanded`.
- **Do** set every number in Plex Mono with tabular figures.
- **Do** put secondary tools in a side sheet or a sticky dock; the script stays visible underneath.
- **Do** give focus a visible 2px accent outline or a 3px `accent-soft` halo, in both themes.
- **Do** keep text contrast at WCAG AA in both themes; the three ink levels are tuned for that and role colours supply their own `--role-text`.
- **Do** use the theme's `shadow-1` at rest and `shadow-2` only for overlays and hover-lift.

### Don't:
- **Don't** add a third button style, a second accent, or a per-page colour. The palette has one green.
- **Don't** use glow, neon, gradients, or blur for emphasis; `--glow: 0px` exists to remove them.
- **Don't** put an uppercase kicker above a headline; uppercase 11px labels name sections and columns only.
- **Don't** open a modal as the first answer to a task; the system has drawers, sheets, and docks.
- **Don't** scale chrome type with the reader's S/M/L switch.
- **Don't** hard-code hex in page CSS; even legacy page files reference tokens.
- **Don't** encode meaning by colour alone: the stress mark is glyph plus underline, the unsure role is dashed plus `?`, the status badge is dot plus text.

## Debt

Recorded so future work retires it rather than inherits it. None of this is system. Checked against the code on 2026-09-22 (`a800d46`).

- **`frontend/src/styles.legacy.css` (514 lines):** imported before `styles.css`; its header lists the survivors that still depend on it: `.shell`/`.grid`/`.content`, legacy `.panel` rules for Users, Log, Help and CharMemory pages, the toast (`ToastProvider`) and skeleton styles, `.list`, `.form-grid`. Retire a block when its survivor moves onto `ui/` and `styles.css`.
- **Per-page CSS:** `CastPage.css` (340 lines), `BookCommandCenterPage.css` (305), `AsrCoveragePage.css` (258), `CharacterPaletteModal.css` (183) and smaller files. They reference tokens; check new rules against the radius and type ramps above rather than adding page-local variants.
- **Text glyphs as icons:** the legend caret and sheet close are characters; the icon set has `chevron` and should supply them.
- **Prototype leftovers:** the density comment «overridable by tweaks» in `styles.css` and `.tweaks-fab`/`#tweaks` in the reader's print rules refer to a tweaks panel that no longer exists in the markup.
- **No frontend tests:** visual checks are done by building `dist/` and driving it with Playwright over stubbed `/api/**` responses.
