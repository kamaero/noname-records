/** Wire types for `GET /api/v2/chapters/{id}/script` and `GET /api/v2/books/{id}/chapters`. */

export const NARRATOR = "Рассказчик";
export const UNSURE = "UNSURE";

export type ReaderSpan = {
  start: number;
  end: number;
  speaker: string;
  confidence: number;
  source: string;
};

export type ReaderStress = {
  /** word bounds inside the segment text */
  start: number;
  end: number;
  /** offset of the stressed vowel inside the word */
  vowel: number;
  /** where the mark came from: dict | author | context | operator | text | rule | … */
  source: string;
  /** a word rare enough that a reader in flow may stress it wrong («только редкие») */
  rare?: boolean;
};

/** [start, end) inside the segment text — a fragment that looks like the author's own words. */
export type ReaderRemarkCandidate = { start: number; end: number };

export type ReaderSegment = {
  id: string;
  ordinal: number;
  kind: string;
  text: string;
  spans: ReaderSpan[];
  stress: ReaderStress[];
  /** author-words candidates inside a replica, computed server-side (see `app/v2/remark_candidates.py`) */
  remark_candidates: ReaderRemarkCandidate[];
};

export type ReaderCastEntry = {
  /** "" for the synthetic narrator */
  character_id: string;
  name: string;
  actor: string;
  color: string;
  text_color: string;
  weight: string;
  font_style: string;
  lines: number;
  /** «фархеррим (квартерон) · Холоден, расчётлив…» — what the pipeline learned */
  about?: string;
  /** the author's own word on the voice */
  note?: string;
};

export type ReaderChapterRef = {
  id: string;
  index: number;
  title: string;
  has_v2: boolean;
  /** the author has marked this chapter checked */
  approved: boolean;
};

/** `POST /api/v2/books/{id}/characters` */
export type CreateRoleResponse = {
  ok: boolean;
  /** false when the cast already had this name (or one of its aliases) */
  created: boolean;
  character_id: string;
  name: string;
  error?: string;
};

/** `POST /api/v2/chapters/{id}/approve` */
export type ApproveChapterResponse = {
  ok: boolean;
  chapter_id: string;
  chapter_index: number;
  status: string;
  approved: boolean;
  error?: string;
};

export type ReaderBookRef = { id: string; title: string };

export type ReaderChapterPayload = {
  book: ReaderBookRef;
  chapter: {
    id: string;
    index: number;
    title: string;
    segments_count: number;
    attributed: boolean;
    /** the author's «проверено» — publishing waits for it */
    approved: boolean;
    status: string;
  };
  chapters: ReaderChapterRef[];
  cast: ReaderCastEntry[];
  segments: ReaderSegment[];
  /** the viewer may edit stress / palette / profile (operator or author) */
  can_edit: boolean;
  /** stress and palette — open to dictors, unlike the markup layer */
  can_voice: boolean;
};

/** one block of the author's original text — a segment's own slice, or prose no segment covers */
export type SourceBlock = {
  /** "" for a gap: text present in the original that the script does not cover */
  segment_id: string;
  ordinal: number;
  kind: "paragraph" | "heading" | "gap";
  text: string;
};

/** `GET /api/v2/chapters/{id}/source` */
export type ChapterSourceResponse = {
  ok: boolean;
  chapter: { id: string; index: number; title: string };
  /** false when the stored offsets no longer fit the source text — segments are shown instead */
  aligned: boolean;
  blocks: SourceBlock[];
  counts: { blocks: number; segments: number; gaps: number; gap_chars: number };
};

export type ReaderBookChapters = {
  book: ReaderBookRef;
  chapters: ReaderChapterRef[];
};

export type RoleStyle = Pick<ReaderCastEntry, "color" | "text_color" | "weight" | "font_style">;

/* ---------- editor endpoints (operators / authors only) ---------- */

/** `GET /api/v2/books/{id}/stress-queue` */
export type StressQueueItem = {
  word: string;
  count: number;
  sample_segment_id: string;
  sample_chapter_index: number;
};

export type StressQueueScope = "all" | "names" | "common";

export type StressQueueResponse = {
  ok: boolean;
  scope: StressQueueScope;
  unresolved: StressQueueItem[];
  homographs: StressQueueItem[];
  has_more: { unresolved: boolean; homographs: boolean };
  counts: {
    marked: number;
    unresolved: number;
    homographs: number;
    unresolved_words: number;
    homograph_words: number;
    /** unresolved author names and inventions (the `names` partition) */
    author_words?: number;
    /** unresolved ordinary words (the `common` partition) */
    common_words?: number;
    /** words that only ever appear capitalised and no dictionary knows — the author's own */
    name_words: number;
    skipped_words: number;
  };
};

/** `GET /api/v2/books/{id}/disputed` — one line the model would not settle */
export type DisputedItem = {
  segment_id: string;
  chapter_id: string;
  chapter_index: number;
  chapter_title: string;
  ordinal: number;
  span: { start: number; end: number };
  /** "" when the model refused to name anyone */
  speaker: string;
  confidence: number;
  source: string;
  /** unsure = no speaker named; low = named with a hedge */
  kind: "unsure" | "low";
  excerpt: string;
};

export type DisputedResponse = {
  ok: boolean;
  counts: { total: number; unsure: number; low: number; chapters: number };
  /** chapter index (as a string key) → disputed lines in it */
  by_chapter: Record<string, number>;
  items: DisputedItem[];
  truncated: boolean;
  threshold: number;
};

export type StressScope = "book" | "author";

/** `POST /api/v2/books/{id}/stress-term` */
export type StressTermResponse = {
  ok: boolean;
  word: string;
  stressed: string;
  scope: StressScope;
  segments_updated: number;
  occurrences: number;
  skipped: number;
  error?: string;
};

/** `GET /api/v2/books/{id}/cast` — the subset of `BudgetCharacterRow` the palette helpers need. */
export type BookCastCharacter = {
  character_id: string;
  char_map_id: string;
  name: string;
  is_narrator: boolean;
  actor_name: string;
  /** the role is cast to whoever is logged in (`names_match` on the server) */
  mine: boolean;
  /** what the pipeline learned about the character, and the author's word on the voice */
  race: string;
  temperament: string;
  note: string;
  appears_in_chapters: number[];
  lines_count: number;
  character_color: string;
  character_text_color: string;
  character_font_weight: string;
  character_font_style: string;
};

export type BookCastResponse = {
  ok: boolean;
  book_id: string;
  characters: BookCastCharacter[];
};

/** `GET /api/v2/books/{id}/profile` */
export type BookProfileResponse = {
  ok: boolean;
  book: { id: string; title: string; pipeline_mode: string; validation_profile: string; author_id: string };
  author: { id: string; name: string; slug: string } | null;
  /** every author in the system — the book can be pointed at one of them */
  authors: Array<{ id: string; name: string }>;
  /** for an unbound book: the author whose roster its roles match */
  suggested: { author_id: string; name: string; matched: number } | null;
  counts: {
    author_characters: number;
    author_characters_confirmed: number;
    linked_characters: number;
    author_pronunciations: number;
    book_pronunciation_terms: number;
    v2_segments: number;
    v2_attributed_segments: number;
    v2_stress_marks: number;
  };
  last_sync: { at: string; summary: unknown } | null;
};

/** `POST /api/v2/books/{id}/author` */
export type BindAuthorResponse = {
  ok: boolean;
  author_id: string;
  author_name: string;
  applied: { actors_applied: number; colours_applied: number; characters_linked: number } | null;
  error?: string;
};

/** `POST .../profile/sync` and `POST .../profile/apply` */
export type ProfileActionResponse = { ok: boolean; summary: unknown; error?: string };

/** `POST /api/v2/segments/{id}/attribution` — what the reader sends and gets back. */
export type ReassignSpan = { start: number; end: number; speaker: string; confidence?: number };

export type RecordingImpactState =
  | "not_recorded" | "borrow" | "borrow_no_file" | "rerecord" | "not_in_audio" | "new_role_unrecorded";

/** Что станет со звуком реплики, если отдать её этой роли. */
export type RecordingImpactEntry = { state: RecordingImpactState; level: "info" | "warn"; text: string };

/** `GET /api/v2/consilium/{id}/recording-impact` */
export type RecordingImpact = { recorded: boolean; by_speaker: Record<string, RecordingImpactEntry> };

export type ReassignImpact = RecordingImpactEntry & {
  span_start: number;
  span_end: number;
  from_role: string;
  to_role: string;
};

export type ReassignResponse = {
  ok: boolean;
  segment_id: string;
  /** spans stored */
  spans: number;
  /** attribution rows written */
  rows: number;
  /** звук главы, задетый этой правкой — пусто, когда глава не записана или сказать нечего */
  recording_impact?: ReassignImpact[];
  error?: string;
};

/** Проба на роль: файл, который прислали до того, как роль назначили. */
export type AuditionItem = {
  id: string;
  role: string;
  /** необязательна: пробу не требуют привязывать к главе */
  chapter: string;
  actor_name: string;
  original_filename: string;
  canonical_filename: string;
  mime_type: string;
  size_bytes: number;
  uploaded_at: string;
  /** для подтверждения удаления — экран называет цену, а не спрашивает голое «удалить файл?» */
  duration_seconds: number;
  /** 👍 — 1, 👎 — -1; null — реакции нет или смотрящему её не видно */
  author_reaction: 1 | -1 | null;
  /** судьба отказа по паре «актёр + роль»; null — отказа нет или не видно */
  rejection: AuditionRejection | null;
};

export type AuditionRejectionStatus = "pending" | "sent" | "no_account" | "ambiguous" | "failed";

export type AuditionRejection = {
  status: AuditionRejectionStatus;
  /** ISO в UTC с `Z` */
  due_at: string;
  sent_at: string;
};

export type AuditionReactionResponse = {
  ok: boolean;
  author_reaction: 1 | -1 | null;
  rejection: AuditionRejection | null;
};

export type AuditionsResponse = {
  ok: boolean;
  items: AuditionItem[];
  /** утверждать на роль могут владелец и автор; слушают все */
  can_approve: boolean;
  /** ставить 👍/👎 может только автор */
  can_react: boolean;
};

export type ConsiliumKind = "wrong_voice" | "identity_play" | "narrator_border" | "readers_split";

/** `GET /api/v2/books/{id}/consilium` — одна находка */
export type ConsiliumItem = {
  id: string;
  chapter_index: number;
  /** "" когда абзаца уже нет в разметке */
  chapter_id: string;
  ordinal: number;
  segment_id: string;
  span_start: number;
  span_end: number;
  excerpt: string;
  kind: ConsiliumKind;
  current_speaker: string;
  /** общее мнение чтецов; "" у readers_split */
  readers_speaker: string;
  reader_opus: string;
  reader_sol: string;
  /** change · keep_current · undecidable · "" (арбитраж не гоняли) */
  arbiter_verdict: string;
  arbiter_speaker: string;
  evidence_para: number;
  evidence_quote: string;
  evidence_proven: boolean;
  reason: string;
  status: "new" | "accepted" | "dismissed" | "gone";
  decided_by: string;
  decided_at: string;
  decided_speaker: string;
};

export type ConsiliumResponse = {
  ok: boolean;
  counts: Record<ConsiliumKind | "new" | "accepted" | "dismissed" | "gone", number> & {
    new_by_kind: Record<ConsiliumKind, number>;
  };
  items: ConsiliumItem[];
  run: ConsiliumRun | null;
};

/** `POST /api/v2/consilium/{id}/accept` */
export type ConsiliumDecision = {
  ok: boolean;
  segment_id: string;
  speaker: string;
  status: "accepted" | "dismissed";
};

export type ConsiliumMode = "recheck" | "reread";

/** `run` inside `ConsiliumResponse` — null for non-editors. */
export type ConsiliumRun = {
  id: string;
  status: "queued" | "running" | "done" | "stopped" | "failed" | string;
  phase: string;
  mode: ConsiliumMode | "";
  chapters_total: number;
  chapters_done: number;
  chapters_skipped: number;
  /** главы, где чтец ответил не на все абзацы, — пересверка их дочитает */
  chapters_incomplete: number;
  arbiter_total: number;
  arbiter_done: number;
  spent_rub: number;
  estimate_rub: number;
  stop_requested: boolean;
  started_at: string;
  finished_at: string;
  /** причина остановки или сбоя — по-русски */
  error: string;
  result: {
    findings: number; added: number; updated: number; gone: number; arbitrated: number; spent_rub: number;
    chapters_incomplete: number;
  } | null;
};

export type ConsiliumBlock = "" | "no_credits" | "book_busy" | "already_running";

/** `GET /api/v2/books/{id}/consilium/estimate` */
export type ConsiliumEstimate = {
  ok: boolean;
  chapters_total: number;
  chapters_to_read: Record<ConsiliumMode, number>;
  arbiter_places: Record<ConsiliumMode, number>;
  estimate_rub: Record<ConsiliumMode, number>;
  estimate_calls: Record<ConsiliumMode, number>;
  credits: number | null;
  blocked: Record<ConsiliumMode, ConsiliumBlock>;
};

// ── Звуковой слой (сцены, переходы, значимые звуки, места) — только редактор ──

export type SoundKind = "scene" | "transition" | "sound";

export type SoundMarker = {
  id: string;
  kind: SoundKind;
  segment_id: string;
  status: "active" | "lost";
  source: "llm" | "human";
  place_id: string;
  quote: string;
  payload: {
    time_of_day?: string; weather?: string; ambience?: string; mood?: string; music_queries?: string[];
    what?: string; description?: string; queries?: string[];
  };
  /** Эмбиент сцены (только у сцен): последний не заменённый трек; `null` — не генерировали. */
  ambient?: SoundAmbient | null;
};

/** Трек эмбиента сцены. Файл — готового трека, даже если последняя попытка упала. */
export type SoundAmbient = {
  /** строка трека и её время — снимок меняется и от сбоя с тем же текстом */
  id: string;
  updated_at: string;
  status: "pending" | "done" | "failed" | string;
  prompt: string;
  audio_file_id: string;
  file_name: string;
  error: string;
};

/** Место книги: подложка и главы (номера), где оно встречается сейчас. */
export type SoundPlace = { id: string; name: string; description: string; ambience_queries: string[]; chapters: number[] };

/** `GET /api/v2/chapters/{id}/sound` */
export type SoundChapterResponse = { ok: boolean; markers: SoundMarker[]; lost: SoundMarker[]; places: SoundPlace[] };

/** Кандидат на склейку двух мест — решает человек. */
export type SoundPair = { id: string; a: { id: string; name: string }; b: { id: string; name: string }; reason: string };

export type SoundRun = {
  id: string; status: string; phase: string; mode: string; chapters_total: number; chapters_done: number;
  chapters_skipped: number; chapters_incomplete: number; spent_rub: number; estimate_rub: number;
  stop_requested: boolean; started_at?: string; finished_at?: string; error: string;
  result: Record<string, number | string> | null;
};

/** `GET /api/v2/books/{id}/sound` */
export type SoundBookResponse = { ok: boolean; places: SoundPlace[]; pairs: SoundPair[]; run: SoundRun | null };

export type SoundMode = "rest" | "all";

/** `GET /api/v2/books/{id}/sound/estimate` */
export type SoundEstimate = {
  ok: boolean;
  chapters_total: number; chapters_to_read: Record<SoundMode, number>; estimate_rub: Record<SoundMode, number>;
  credits: number | null; blocked: Record<SoundMode, "" | "no_credits" | "book_busy" | "already_running">;
};
