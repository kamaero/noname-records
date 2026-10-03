# v2 API — contract notes (backend → frontend)

Checked against the code on 2026-09-22 (`a800d46`). Full route list:
`docs/reference/API_ROUTES.md` (generated). This file keeps only the clarifications
the route list cannot show.

No deviations from the agreed endpoint list or response shapes. Clarifications and
additive fields only:

## `POST /api/v2/books/{book_id}/stress-term`
- `skipped` is an **integer** (occurrences the rule could not place); the same
  response also carries `notes: string[]` — one Russian line per skipped occurrence
  (`"<segment_id>: «форма» — не удалось перенести ударение на эту форму"`).
- `word` in the response is the word lowercased and stripped of stress marks (that
  is how the notes line and the marks are keyed); `stressed` is echoed as sent.
- 400 error codes (`{"ok": false, "error": <code>}`): `bad_payload` (not a JSON
  object), `word_and_stressed_required`, `stressed_does_not_match_word`,
  `no_vowel_marked`, `bad_scope`, `book_has_no_author`. 404: `book_not_found`.
- `scope="author"` does not add a notes line to the book; if the book already had a
  line for the same word it is refreshed so the two layers agree.

## `GET /api/v2/books/{book_id}/stress-queue`
- `counts` has the agreed `marked / unresolved / homographs` (occurrence totals) plus
  `unresolved_words` and `homograph_words` (distinct words, before the `limit` cut of
  200 per list). `author_words` and `common_words` are an additive partition of
  `unresolved_words`; legacy `name_words` remains an alias of `author_words`.
  `word` in both lists is lowercased.
- `scope=all` remains the default and returns every unresolved word. Legacy
  `scope=names` returns only author names/inventions (words that occur only
  capitalised and are unknown to the dictionary); `scope=common` returns the
  complementary ordinary-word partition. The homograph list is unchanged by scope.

## `GET /api/v2/books/{book_id}/cast`
- Rows as agreed. `character_id == ""` and `char_map_id == ""` only for a synthetic
  narrator (no `Рассказчик` Character row). The colour fields are the ones the
  reader shows (unset ones filled the same way). Colours are saved on
  `POST /api/v2/characters/{character_id}/palette` (colour fields only, `_can_voice`);
  rates and money stay on `POST /api/budget/character/{id}`.

## `GET /api/v2/books/{book_id}/profile`
- As agreed; `author` is `null` when the book has no author, and the author-side
  counts are then `0`. `last_sync` is `{at: ISO string, summary: {...}}` or `null`
  and reflects only `profile/sync`, not `profile/apply`.
- `profile/apply` summary carries `overwrite` echoed back.

## Chapter payload (`GET /api/v2/chapters/{chapter_id}/script`)
- `can_edit: bool` at the top level (admin | author) and `can_voice: bool` (admin |
  author | dictor — stress and palette; always true when `can_edit` is).
- every `cast[]` entry has `character_id` (`""` when there is no Character row).
- every `segments[].stress[]` entry has `source` (`text | author | rule | dict |
  wiktionary | context | residue | operator`; `residue` is reserved — the LLM layer
  is not wired).

## Auth
- Two gates in `app/v2/api.py`: `_can_edit` = `admin | author` (runs, publishing,
  roles, profile, character map, consilium, sound) and `_can_voice` = `admin | author
  | dictor` (stress and palette). No session → 401; wrong role → 403.
- `stress-term`, `stress-skip`, palette saves: `_can_voice`. `profile/sync`,
  `profile/apply`: `_can_edit`.
- Chapter reads (`/script`, `/source`): an unpublished chapter is served to editors
  only; others get 404 `chapter_not_published`.
- `disputed` and `consilium` lists: any session. `consilium` returns `run` (money,
  progress) only to editors, `null` to others.
