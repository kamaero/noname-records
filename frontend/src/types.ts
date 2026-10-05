export type WorkspaceTab = {
  id: string;
  label: string;
};

export type MeResponse = {
  authenticated: boolean;
  uid: string;
  login: string;
  display_name: string;
  roles: string[];
  auth_source: string;
  is_owner_telegram: boolean;
  workspace_tabs: WorkspaceTab[];
  full_access: boolean;
  /** может ли бот написать человеку: null — вход по паролю или Telegram не ответил */
  bot_reachable?: boolean | null;
  /** сервер решил открыть приветственное окно само */
  show_onboarding?: boolean;
};

export type MeBotReachResponse = {
  ok: boolean;
  bot_reachable: boolean | null;
};

export type BookProgress = {
  percent: number;
  chapters_done: number;
  chapters_total: number;
  effective_status: string;
  effective_status_label?: string;
  action_hint: string;
  char_extraction_failed: number;
  polishing_skipped: number;
  pipeline_elapsed_seconds: number;
  draft_elapsed_seconds: number;
  final_elapsed_seconds: number;
  polishing_elapsed_seconds: number;
};

export type BackgroundRunItem = {
  job_kind: string;
  status: string;
  started_at: string;
  finished_at: string;
  heartbeat_at: string;
  error_message: string;
  thread_name: string;
};

export type BookProgressDetail = BookProgress & {
  book_id: string;
  book_title: string;
  status: string;
  status_label: string;
  status_detail: string;
  stop_requested: boolean;
  draft_provider: string;
  draft_model: string;
  final_provider: string;
  final_model: string;
  polishing_provider: string;
  polishing_model: string;
  current_job: {
    stage: string;
    provider: string;
    model: string;
    chapter_index: number;
    updated_at: string;
    age_seconds: number;
    overdue_after_seconds: number;
    is_overdue: boolean;
  };
  processing_jobs: Array<{
    stage: string;
    provider: string;
    model: string;
    chapter_index: number;
    updated_at: string;
    age_seconds: number;
  }>;
  operations: {
    stale_requeued_count: number;
    last_continue_log: string;
    recent_recovery_events: string[];
  };
  background_runs: BackgroundRunItem[];
};

export type BookListItem = {
  id: string;
  title: string;
  display_title: string;
  source_filename: string;
  status: string;
  status_label?: string;
  stop_requested: boolean;
  chapter_count: number;
  character_count: number;
  book_annotation: string;
  /** автор для витрины «Автор - "Название"»; пусто — книга подписана одним названием */
  author: string;
  pipeline_mode: string;
  validation_profile: string;
  total_chars: number;
  created_at: string;
  updated_at: string;
  progress: BookProgress | null;
  urls: {
    workspace: string;
    validation: string;
    recording: string;
  };
};

export type BooksResponse = {
  items: BookListItem[];
};

export type OpsDailySummaryResponse = {
  date_utc: string;
  runs_count: number;
  manual_interventions_count: number;
  top_failure_codes: Array<{ code: string; count: number }>;
};

export type PrepQuality = {
  chapter_count: number;
  graded_count: number;
  avg_score: number;
  grade: string;
  grade_counts: Record<string, number>;
  punctuation_drifts: number;
  fallback_chapters: number;
};

export type PrepPolishing = {
  summary?: string;
  generated_at?: string;
  provider?: string;
  model?: string;
  global_issues_count?: number;
  chapter_patch_suggestions_count?: number;
  character_identity_map_count?: number;
  safe_auto_patches_count?: number;
  applied_safe_auto_patches_count?: number;
  top_global_issues?: string[];
};

export type PrepCharMemory = {
  preview: string;
  character_count: number;
  alias_count: number;
  groups: Array<{ title: string; items: string[] }>;
  url: string;
};

export type PrepPreflightStage = {
  model: string;
  part_chars?: number;
  request_count: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost_rub?: number;
  seconds?: number;
  whole_book_limit_chars?: number;
  route?: string;
};

export type PrepPreflightChapter = {
  chapter_index: number;
  chapter_title: string;
  char_count: number;
  estimated_tokens: number;
  draft_parts: number;
  final_parts: number;
  risk: string;
};

export type PrepPreflightReport = {
  ok: boolean;
  total_chars: number;
  estimated_tokens: number;
  chapter_count: number;
  character_count: number;
  pipeline_mode: string;
  validation_profile: string;
  recommended_pipeline_mode: string;
  risk_level: string;
  tokenizer_note: string;
  char_extraction: PrepPreflightStage;
  draft: PrepPreflightStage;
  final: PrepPreflightStage;
  total_cost_rub: number;
  total_seconds: number;
  largest_chapters: PrepPreflightChapter[];
  warnings: string[];
};

export type PrepCharacterBible = {
  character_count: number;
  alias_count: number;
  ambiguous_alias_count: number;
  unresolved_speakers: number;
  replica_count: number;
  ambiguous_aliases: Array<{ alias: string; owners: string[]; normalized: string }>;
  scene_roster: Array<{
    chapter_index: number;
    chapter_title: string;
    character_count: number;
    characters: string[];
    speaker_count: number;
    replica_count: number;
    unresolved_speakers: number;
  }>;
};

export type PrepAliasResolution = {
  total: number;
  review_count: number;
  counts: Record<string, number>;
  items: Array<{
    alias: string;
    aliases: string[];
    normalized: string;
    classification: string;
    confidence: number;
    reason: string;
    owners: string[];
    evidence_chapters: number[];
  }>;
};

export type PrepBookSurvey = {
  ok: boolean;
  book_id: string;
  range_count: number;
  target_range_chars: number;
  known_character_count: number;
  top_known_characters: Array<{ name: string; count: number }>;
  top_new_name_candidates: Array<{ name: string; count: number }>;
  ranges: Array<{
    range_index: number;
    chapter_start: number;
    chapter_end: number;
    char_count: number;
    risk: string;
    known_character_hits: Array<{
      name: string;
      count: number;
      aliases: Array<{ alias: string; count: number }>;
    }>;
    new_name_candidates: Array<{ name: string; count: number }>;
    evidence: Array<{
      character: string;
      alias: string;
      chapter_index: number;
      excerpt: string;
    }>;
  }>;
  warnings: string[];
};

export type PrepLargeCastQuality = {
  ok: boolean;
  readiness: string;
  score: number;
  fail_count: number;
  review_count: number;
  checks: Array<{
    code: string;
    severity: string;
    status: string;
    message: string;
    value: unknown;
    threshold: unknown;
  }>;
};

export type QueueOccupancyRow = {
  user_id: string;
  user_name: string;
  books: number;
  queued_jobs: number;
  processing_jobs: number;
  waiting_jobs: number;
  total_chars: number;
  book_titles: string[];
};

export type PrepLogRow = {
  id: string;
  level: string;
  message: string;
  created_at: string | null;
};

export type PrepJobRow = {
  id: string;
  chapter_index: number;
  stage: string;
  status: string;
  provider: string;
  model: string;
  pipeline_run_id?: string;
  error_code?: string;
  error_message: string;
  updated_at: string | null;
};

export type PrepStageSummary = {
  counts: Record<string, number>;
  latest_status: string;
  last_error_message: string;
  last_job_updated_at: string | null;
  last_provider: string;
  last_model: string;
  last_chapter_index: number;
};

export type BookPrepResponse = {
  book: BookListItem;
  quality: PrepQuality;
  polishing: PrepPolishing;
  preflight?: PrepPreflightReport;
  character_bible?: PrepCharacterBible;
  alias_resolution?: PrepAliasResolution;
  book_survey?: PrepBookSurvey;
  large_cast_quality?: PrepLargeCastQuality;
  char_memory: PrepCharMemory;
  queue_occupancy: QueueOccupancyRow[];
  stage_summary: Record<string, PrepStageSummary>;
  warnings?: string[];
  recent_logs: PrepLogRow[];
  recent_jobs: PrepJobRow[];
  pipeline_runs?: {
    current: {
      id: string;
      status: string;
      trigger_type: string;
      started_at: string;
      ended_at: string;
      ended_reason_code: string;
      ended_reason_text: string;
    } | null;
    previous: {
      id: string;
      status: string;
      trigger_type: string;
      started_at: string;
      ended_at: string;
      ended_reason_code: string;
      ended_reason_text: string;
    } | null;
  };
  operator_interventions?: Array<{
    id: string;
    action_type: string;
    actor_name: string;
    reason: string;
    pipeline_run_id: string;
    created_at: string;
  }>;
  pipeline_timeline?: Array<{
    id: string;
    event_type: string;
    level: string;
    code: string;
    message: string;
    stage: string;
    chapter_index: number;
    pipeline_run_id: string;
    created_at: string;
  }>;
  ops_daily_report?: {
    date_utc: string;
    runs_count: number;
    manual_interventions_count: number;
    top_failure_codes: Array<{ code: string; count: number }>;
  };
};

export type ValidationBookItem = {
  id: string;
  title: string;
  display_title: string;
  status: string;
};

export type ValidationChapterItem = {
  id: string;
  chapter_index: number;
  chapter_title: string;
  status: string;
  status_label?: string;
  unresolved_before_count: number;
  unresolved_after_count: number;
  resolved_postpass_count: number;
};

export type ReviewQueueItem = {
  chapter_id: string;
  chapter_index: number;
  chapter_title: string;
  status: string;
  status_label?: string;
  score: number;
  priority: number;
  primary_issue: string;
  issues: string[];
};

export type ValidationResponse = {
  selected_book: {
    id: string;
    title: string;
    display_title: string;
    status: string;
    pipeline_mode?: string;
    validation_profile?: string;
  } | null;
  char_memory_needs_rerun: boolean;
  chapters: ValidationChapterItem[];
  selected_chapter: {
    id: string;
    chapter_index: number;
    chapter_title: string;
    status: string;
    status_label?: string;
  } | null;
  review_queue: ReviewQueueItem[];
  chapter_summary: Record<string, number>;
  quality_summary: Record<string, unknown>;
  technical_report: Record<string, unknown>;
  consistency_summary: {
    summary: string;
    global_issues_count: number;
    safe_auto_patches_count: number;
    applied_safe_auto_patches_count: number;
  };
  chapter_consistency_counts: {
    chapter_patch_suggestions: number;
    safe_auto_patches: number;
    applied_safe_auto_patches: number;
  };
  selected_final_job: {
    stage: string;
    status: string;
    provider: string;
    model: string;
    error_message: string;
    updated_at: string | null;
  } | null;
  unsure_pass_metrics: {
    before: number;
    after_step1: number;
    after_step2: number;
    resolved_step1: number;
    resolved_step2: number;
    left_for_operator: number;
    step1_provider: string;
    step1_model: string;
    step2_provider: string;
    step2_model: string;
    step2_used: boolean;
  };
  review_mode: string;
  review_position: number;
  review_queue_size: number;
  review_prev_id: string;
  review_next_id: string;
  publish_gate_issues: string[];
  manual_decisions_log: Array<Record<string, unknown>>;
  validation_workspace_url: string;
};

export type ValidationMutateResponse = {
  ok: boolean;
  error?: string;
  issues?: string[];
  changed_lines?: number;
};

export type IdentityConflictItem = {
  alias: string;
  normalized: string;
  owners: Array<{ name: string; char_map_id: string; appears_in: number[] }>;
  owner_names: string[];
  evidence_chapters: number[];
  suggested_decision: string;
};

export type IdentityConflictsResponse = {
  ok: boolean;
  book_id: string;
  book_title: string;
  total: number;
  resolved_count: number;
  items: IdentityConflictItem[];
};

export type IdentityConflictDecisionPayload = {
  alias: string;
  decision: string;
  primary_name?: string;
  comment?: string;
};

export type SurveyIdentityProposalItem = {
  proposal_key: string;
  proposal_type: string;
  alias: string;
  classification: string;
  confidence: number;
  owners: string[];
  primary_name: string;
  evidence_chapters: number[];
  reason: string;
  suggested_decision: string;
  count?: number;
};

export type SurveyIdentityProposalsResponse = {
  ok: boolean;
  book_id: string;
  book_title: string;
  total: number;
  resolved_count: number;
  items: SurveyIdentityProposalItem[];
  latest_llm_run: {
    id: string;
    action_type: string;
    created_at: string;
    ok: boolean | null;
    provider: string;
    model: string;
    proposal_count: number;
    error: string;
    raw_output_preview: string;
  } | null;
};

export type SurveyProposalDecisionPayload = {
  proposal_key: string;
  alias?: string;
  decision: string;
  primary_name?: string;
  comment?: string;
};

export type UserListItem = {
  id: string;
  login: string;
  display_name: string;
  is_active: boolean;
  roles: string[];
  /** how this account gets in: a password, or the row Telegram login makes for itself */
  auth: "password" | "telegram";
  /** все пути входа сразу: учётка может быть и паролем, и телеграмом */
  ways_in: Array<"password" | "telegram">;
  /** привязанный телеграм, если он есть */
  telegram_user_id: string;
  /** the same person's other account: `twin_id` is what «Объединить» merges with */
  twin_id: string;
  twin_login: string;
  created_at: string | null;
  updated_at: string | null;
};

/** Может ли бот написать этому человеку. `reachable: null` — не смогли спросить. */
export type BotReachItem = {
  telegram_user_id: string;
  display_name: string;
  linked_login: string;
  telegram_name: string;
  username: string;
  reachable: boolean | null;
  reason: string;
};

export type BotReachResponse = {
  ok: boolean;
  items: BotReachItem[];
  checked: number;
  unreachable: number;
  error: string;
};

export type TelegramWhitelistItem = {
  id: string;
  telegram_user_id: string;
  display_name: string;
  /** учётка, к которой привязана запись; пусто — запись ничья */
  linked_login: string;
  linked_name: string;
  role: string;
  access_scope: string;
  is_active: boolean;
};

export type UsersResponse = {
  users: UserListItem[];
  owner_whitelist_accounts: TelegramWhitelistItem[];
};

export type SaveTelegramWhitelistPayload = {
  entry_id?: string;
  telegram_user_id: string;
  display_name: string;
  role: string;
  access_scope: string;
  is_active: boolean;
};

export type UsageRow = {
  created_by_name: string;
  book_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
};

export type ActivityRow = {
  action: string;
  action_label: string;
  entity_type: string;
  entity_id: string;
  actor_name: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type UsageStageRow = {
  stage: string;
  request_count: number;
  book_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  runtime_ms: number;
};

export type LogResponse = {
  usage_by_actor: UsageRow[];
  usage_by_stage: UsageStageRow[];
  recent_activity: ActivityRow[];
};

export type WorkspaceInitResponse = {
  ok: boolean;
  app_name: string;
  active_tab: string;
  tabs: Array<{ id: string; label: string }>;
  workspace_full_access: boolean;
  display_name: string;
  status_labels: Record<string, string>;
  script_stage_labels: Record<string, string>;
  legacy_urls: Record<string, string>;
  prep: unknown;
  validation: unknown;
  recording: unknown;
};

export type UploadBookResponse = {
  ok: boolean;
  book_id?: string;
  book_title?: string;
  source_filename?: string;
  status?: string;
  chapter_count?: number;
  total_chars?: number;
  pipeline_mode?: string;
  validation_profile?: string;
  error?: string;
};

export type BookActionResponse = {
  ok: boolean;
  error?: string;
  deleted_count?: number;
};

export type BudgetCharacterRow = {
  character_id: string;
  char_map_id: string;
  name: string;
  is_narrator?: boolean;
  race: string;
  temperament: string;
  /** the author's own word on how the role should sound (`Character.operator_note`) */
  note?: string;
  actor_name: string;
  appears_in_chapters: number[];
  chapter_count: number;
  lines_count: number;
  approx_seconds: number;
  fact_seconds: number;
  calc_mode: string;
  manual_rate_rub_per_min: number | null;
  manual_fixed_rub: number | null;
  character_color: string;
  character_text_color: string;
  character_font_weight: string;
  character_font_style: string;
  total_rub: number;
  /** открытый срок пробы или роли; нет — null или поле отсутствует */
  deadline?: RoleDeadlineView | null;
};

export type BookBudgetResponse = {
  ok: boolean;
  book_id: string;
  book_title: string;
  book_annotation: string;
  /** the studio's rate: what a role costs per minute when it has no rate of its own */
  default_rate_rub_per_min: number;
  /** rubles per dollar, for model prices billed in dollars */
  usd_rub_rate: number;
  urls: {
    recording: string;
  };
  budget: {
    narrative_cost_rub: number;
    sound_engineer_cost_rub: number;
    extra_cost_rub: number;
    notes: string;
    narrator_actor_name?: string;
    updated_at: string | null;
  };
  characters: BudgetCharacterRow[];
  totals: {
    character_total_rub: number;
    narrator_total_rub?: number;
    sound_engineer_cost_rub?: number;
    extra_cost_rub?: number;
    grand_total_rub: number;
  };
  summary?: {
    source_chapter_count: number;
    processing_chapter_count: number;
    dialogue_lines_count: number;
    dialogue_seconds: number;
    audiobook_seconds: number;
    narrator_pricing_mode: string;
    narrator_source_lines_count: number;
    narrator_source_seconds: number;
    cast_only_count: number;
    priced_roles_count: number;
    unpriced_playable_roles_count: number;
    calculation_warnings: string[];
  };
};

export type PreflightStage = {
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_rub: number;
  seconds: number;
};

export type PreflightChapterItem = {
  chapter_index: number;
  chapter_title: string;
  char_count: number;
  draft_cost_rub: number;
  final_cost_rub: number;
  draft_seconds: number;
  final_seconds: number;
  is_heavy: boolean;
};

export type PreflightEstimateResponse = {
  ok: boolean;
  title: string;
  source_format: string;
  total_chars: number;
  chapter_count: number;
  draft: PreflightStage;
  final: PreflightStage;
  total_cost_rub: number;
  total_seconds: number;
  chapters: PreflightChapterItem[];
  error?: string;
};

export type ChapterViewResponse = {
  ok: boolean;
  chapter_id: string;
  chapter_index: number;
  chapter_title: string;
  status: string;
  user_hint: string;
  can_edit_hint: boolean;
  book_id: string;
  book_title: string;
  selected_artifact_id: string;
  selected_artifact_stage: string;
  selected_artifact_status: string;
  effective_artifact_id: string;
  artifact_timeline: Array<{
    id: string;
    stage: string;
    status: string;
    provider: string;
    model: string;
    pipeline_run_id: string;
    source_artifact_id: string;
    is_effective: boolean;
    note: string;
    created_at: string;
    updated_at: string;
    diff: {
      added_lines: number;
      removed_lines: number;
      changed_lines: number;
      current_lines: number;
      previous_lines: number;
    };
  }>;
  narrative_owner_name: string;
  narrative_owner_confidence: number;
  narrative_owner_reason: string;
  pronunciation_notes: string;
  source_lines: string[];
  script_lines: string[];
  lines: string[];
  segments_map?: Record<string, { speaker?: string; segments?: Array<{ type: "speech" | "narration"; text: string }> }>;
  cast_color_map: Record<string, string>;
  cast_text_color_map: Record<string, string>;
  cast_weight_map: Record<string, string>;
  cast_font_style_map: Record<string, string>;
  cast_actor_map?: Record<string, string>;
  error?: string;
};

export type SaveChapterHintPayload = {
  user_hint: string;
  pronunciation_notes: string;
};

export type SaveChapterHintResponse = {
  ok: boolean;
  error?: string;
};

export type SaveBookBudgetPayload = {
  narrative_cost_rub: number;
  sound_engineer_cost_rub: number;
  extra_cost_rub: number;
  notes: string;
  narrator_actor_name?: string;
};

/** Готовность записи по главам: у роли есть дубль или нет. */
export type BookRecordingChapter = {
  chapter_id: string;
  chapter_index: number;
  chapter_title: string;
  total_roles: number;
  recorded_roles: number;
  ready: boolean;
  /** null — распознавание не запускали; ноль означал бы «ничего не произнесено» */
  asr_coverage: number | null;
  asr_files: number;
  /** все реплики главы — знаменатель покрытия, включая роли, которых ещё не записали */
  total_lines: number;
  matched_lines: number;
  missing_lines: number;
  /** пусто — главу ещё не забирали в сведение */
  delivered_at: string;
  /** «Файл есть» и «файл цел» — разные вещи: обрыв на середине даёт запись
   * в базе и обрезок на диске. */
  integrity_ok: number;
  integrity_bad: number;
  integrity_checked: boolean;
  /** дубли без суммы приёма (приняты до миграции 0020): сверять их не с чем */
  integrity_skipped: number;
  /** всего дублей главы; `integrity_skipped === integrity_files` — сверять нечего */
  integrity_files: number;
  mirrored_files: number;
  /** вторая копия ещё не подтверждена на NAS зеркалированием */
  unmirrored_files: number;
  /** ждут копии дольше суток — или сумма разошлась и ждать уже бессмысленно */
  stale_unmirrored: number;
  mirror_broken: number;
  /** файл проекта (.sesx) уже лежит на NAS, в папке главы */
  session_archived: boolean;
  /** файл проекта на NAS есть, но сверка с тех пор изменилась — фон пересоберёт его сам */
  session_outdated: boolean;
  /** маркеры звукорежиссёра, чей абзац ещё не записан — записано архивацией, не посчитано здесь */
  session_markers_skipped: number;
  /** Эмбиент главы — только редактору (дикторам нули и пустое состояние).
   * `ambient_todo` — активные сцены записанной главы без готового трека, среди них
   * `ambient_skipped` — те, что прошлый прогон пропустил (нет места на таймлайне). */
  ambient_todo: number;
  ambient_done: number;
  /** треков, что не вышли в последнем прогоне */
  ambient_failed: number;
  ambient_skipped: number;
  /** готовых треков, чей файл не прошёл сверку: архив главы из-за них не соберётся */
  ambient_broken: number;
  ambient_state: AmbientState;
};

/** Состояние эмбиента главы: `''` — не запускали или нечего сказать. */
export type AmbientState = "" | "running" | "quota" | "bad_key" | "disk_full" | "failed" | "done";

/** `GET /api/v2/chapters/{id}/ambient/plan` — смета для подтверждения запуска. */
export type AmbientPlan = { ok: boolean; tracks: number; minutes: number; skipped: number };

/** Один дубль в описи главы — тем, чем его опознают на слух и на диске. */
export type ChapterTake = {
  id: string;
  role: string;
  actor_name: string;
  original_filename: string;
  canonical_filename: string;
  mime_type: string;
  size_bytes: number;
  duration_seconds: number;
  uploaded_at: string;
  /** путь на диске — чтобы дотянуться до файла терминалом или Audition */
  stored_key: string;
  /** `nas` — легаси-запись, чья единственная копия так и осталась на NAS */
  location: string;
  mirror_state: string;
  /** пусто — копии на NAS ещё нет */
  mirrored_at: string;
  /** пусто — суммы приёма нет (запись старше миграции 0020), сверять не с чем */
  md5: string;
};

export type ChapterRecordingRole = {
  role: string;
  actor_name: string;
  lines: number;
  /** сколько дублей у роли — длина `takes` */
  files: number;
  asr_coverage: number | null;
  matched_lines: number;
  missing_lines: number;
  duration_seconds: number;
  size_bytes: number;
  takes: ChapterTake[];
};

export type ChapterRecordingResponse = {
  ok: boolean;
  chapter_id: string;
  chapter_index: number;
  chapter_title: string;
  roles: ChapterRecordingRole[];
  /** дубли, чья роль ушла из сценария: переименовали в карте персонажей или слили с
   * другой. В свою строку они уже не попадут, а на диске остались. */
  orphan_takes: ChapterTake[];
  total_roles: number;
  recorded_roles: number;
  ready: boolean;
  duration_seconds: number;
  size_bytes: number;
  can_download: boolean;
};

export type BookRecordingResponse = {
  ok: boolean;
  chapters: BookRecordingChapter[];
  total_chapters: number;
  ready_chapters: number;
  started_chapters: number;
  recognised_chapters: number;
  delivered_chapters: number;
  missing_lines: number;
  can_download: boolean;
};

/** Что вышло из вашего голоса за актёра: голос автора весит два, владельца — один. */
export type RoleVoteResult = {
  /** кто в итоге играет роль */
  actor_name: string;
  changed: boolean;
  /** чей голос перевесил ваш; пусто — решение ваше */
  overridden_by: string;
  votes: Array<{ voter_name: string; actor_name: string; weight: number }>;
};

/** Дошло ли до диктора «ты утверждён на роль» или «вас зовут на пробу» — приходит
    только когда актёра назначили или предложили (имя со знаком вопроса). */
export type RoleApproval = {
  notified: boolean;
  /** `tentative` больше не возвращается сервером (имя со знаком вопроса теперь
      шлёт приглашение на пробу, `kind: "audition"`); значение оставлено в типе
      для совместимости со старыми ответами. `ambiguous` — имени подошло больше
      одной учётки: письмо никому не ушло, переслано владельцу и агентам. */
  reason: "sent" | "no_actor" | "no_telegram" | "not_sent" | "ambiguous" | "tentative";
  actor_name: string;
  /** `approval` — назначение на роль, `audition` — имя со знаком вопроса: приглашение на пробу. */
  kind: "approval" | "audition";
};

export type SaveBookBudgetResponse = {
  ok: boolean;
  error?: string;
  approval?: RoleApproval;
};

export type SaveBookCardPayload = {
  book_annotation: string;
};

export type SaveBookCardResponse = {
  ok: boolean;
  error?: string;
};

export type SaveBudgetCharacterPayload = {
  name?: string;
  appears_in?: string;
  actor_name?: string;
  race?: string;
  temperament?: string;
  lines_count?: number;
  duration_seconds?: number;
  manual_rate_rub_per_min?: number;
  manual_fixed_rub?: number;
  character_color?: string;
  character_text_color?: string;
  character_font_weight?: string;
  character_font_style?: string;
};

export type SaveBudgetCharacterResponse = {
  ok: boolean;
  error?: string;
  approval?: RoleApproval;
  vote?: RoleVoteResult;
};

export type DictorChapterItem = {
  id: string;
  chapter_index: number;
  chapter_title: string;
};

export type DictorRecentFile = {
  id: string;
  uploaded_at: string;
  chapter: string;
  role: string;
  actor_name: string;
  canonical_filename: string;
  /** для подтверждения удаления — экран называет цену, а не спрашивает голое «удалить файл?» */
  duration_seconds: number;
};

export type DictorCastMember = {
  name: string;
  lines: number;
  color: string;
  text_color: string;
  actor_name: string;
};

export type DictorMyRole = {
  name: string;
  actor_name: string;
  lines: number;
};

export type DictorWorkspaceResponse = {
  ok: boolean;
  selected_book?: {
    id: string;
    title: string;
    display_title: string;
    /** derived from the title's initials; the actor never types it */
    code: string;
  } | null;
  selected_chapter?: {
    id: string;
    chapter_index: number;
    chapter_title: string;
  } | null;
  chapters: DictorChapterItem[];
  /** the cast of the selected chapter, with the author's colours */
  cast: DictorCastMember[];
  /** roles whose character carries this actor's name */
  my_roles: DictorMyRole[];
  recent_files: DictorRecentFile[];
  error?: string;
};

export type BatchValidateFileItem = { name: string; size: number };

export type BatchValidateOverride = {
  book_code: string;
  chapter: string;
  role: string;
  actor_name: string;
  /** диктор подтвердил: глава выбрана верно, номер в имени файла — просто другой */
  confirm_chapter?: boolean;
};

export type BatchValidatePayload = {
  book_code: string;
  actor_name: string;
  files: BatchValidateFileItem[];
  overrides: BatchValidateOverride[];
};

export type BatchValidatedItem = {
  ok: boolean;
  original_filename: string;
  book_code: string;
  chapter: string;
  role: string;
  actor_name: string;
  canonical_filename: string;
  errors?: string[];
  warnings?: string[];
};

export type BatchValidateResponse = {
  ok: boolean;
  items: BatchValidatedItem[];
  summary: { valid: number; invalid: number };
  error?: string;
};

export type BatchUploadResponse = {
  ok: boolean;
  saved_count: number;
  error?: string;
  /** отказ словами, для экрана: «KP_Ch11_Gamuk.wav: этот же файл уже загружен…» */
  message?: string;
};

export type AuthorBookItem = {
  id: string;
  title: string;
  status: string;
  status_label?: string;
};

export type AuthorChapterItem = {
  id: string;
  chapter_index: number;
  chapter_title: string;
  status: string;
  status_label?: string;
  char_count: number;
};

export type AuthorSelectedBook = {
  id: string;
  title: string;
  display_title?: string;
  status: string;
  status_label?: string;
  pipeline_mode?: string;
  validation_profile?: string;
};

export type AuthorSelectedChapter = {
  id: string;
  chapter_index: number;
  chapter_title: string;
  status: string;
  status_label?: string;
};

export type AuthorUnknownWord = { word: string; count: number };
export type AuthorConfidenceItem = { speaker: string; text: string; confidence: number };
export type AuthorUnresolvedItem = { quote: string };
export type AuthorLexiconCandidate = { word: string };
export type AuthorPronunciationEntry = { term: string; stressed: string; variants?: string[]; source?: string };

export type AuthorWorkspaceResponse = {
  ok: boolean;
  books: AuthorBookItem[];
  selected_book: AuthorSelectedBook | null;
  chapters: AuthorChapterItem[];
  selected_chapter: AuthorSelectedChapter | null;
  original_source_text: string;
  lines: string[];
  pronunciation_notes?: string;
  author_pronunciations?: AuthorPronunciationEntry[];
  cast_names: string[];
  cast_color_map: Record<string, string>;
  cast_text_color_map: Record<string, string>;
  cast_weight_map: Record<string, string>;
  cast_font_style_map: Record<string, string>;
  cast_actor_map?: Record<string, string>;
  script_insertions?: Record<string, number[]>;
  // Words of the source missing from the script, anchored to the script word they
  // follow (-1 = before the line's first word). See app/services/script_fidelity.py.
  script_omissions?: Record<string, { after_ordinal: number; words: string[] }[]>;
  // Per-line hashes from the backend. NEVER compute these client-side: one byte of
  // difference makes every relabel a stale locator.
  line_hashes?: string[];
  cast_picker?: { name: string; line_count: number; in_cast: boolean }[];
  unknown_words: AuthorUnknownWord[];
  confidence_items: AuthorConfidenceItem[];
  unresolved_items: AuthorUnresolvedItem[];
  lexicon_candidates: AuthorLexiconCandidate[];
  error?: string;
};

export type AuthorSavePayload = {
  book_id: string;
  fountain_text: string;
  pronunciation_notes?: string;
  domain_lexicon?: string;
  user_hint?: string;
};

export type AuthorSaveResponse = { ok: boolean; error?: string };
export type AuthorApprovePayload = { comment: string };
export type AuthorApproveResponse = { ok: boolean; error?: string };
export type AuthorNeedsFixPayload = { comment: string };
export type AuthorNeedsFixResponse = { ok: boolean; error?: string };
export type AddPronTermPayload = {
  term: string;
  stressed: string;
  chapter_id: string;
  fountain_text: string;
};
export type AddPronTermResponse = {
  ok: boolean;
  skipped?: boolean;
  error?: string;
  forms_applied?: number;
  chapters_updated?: number;
  replacements?: number;
  updated_chapter_text?: string;
};

export type AsrJobAudioMeta = {
  id: string;
  chapter: string;
  role: string;
  actor_name: string;
  canonical_filename: string;
  status: string;
  uploaded_at: string | null;
} | null;

export type AsrJobItem = {
  id: string;
  audio_file_id: string;
  status: string;
  provider: string;
  model: string;
  decision: string;
  confidence_score: number;
  similarity: number;
  coverage: number;
  timing_fit: number;
  review_reason: string;
  error_message: string;
  chapter_id: string;
  chapter_index: number;
  chapter_title: string;
  expected_role: string;
  detected_role: string;
  expected_chapter_index: number;
  detected_chapter_index: number;
  audio: AsrJobAudioMeta;
  updated_at: string | null;
  created_at: string | null;
};

export type AsrJobsResponse = {
  ok: boolean;
  items: AsrJobItem[];
  summary: {
    queued: number;
    processing: number;
    done: number;
    failed: number;
  };
  error?: string;
};

export type AsrActionResponse = {
  ok: boolean;
  queued?: boolean;
  processed?: number | boolean;
  requested?: number;
  job_id?: string;
  status?: string;
  error?: string;
};

export type ReadinessChapterRow = {
  chapter_id: string;
  chapter_index: number;
  chapter_title: string;
  planned: number;
  covered: number;
  percent: number;
};

export type ReadinessCastRow = {
  role: string;
  planned: number;
  covered: number;
  missing: number;
};

export type ReadinessHoleRow = {
  chapter_id: string;
  chapter_index: number;
  chapter_title: string;
  role: string;
  missing: number;
  planned: number;
  covered: number;
};

export type BookReadinessResponse = {
  ok: boolean;
  book_id: string;
  book_title: string;
  total_planned: number;
  total_covered: number;
  coverage_percent: number;
  chapter_rows: ReadinessChapterRow[];
  cast_rows: ReadinessCastRow[];
  holes_by_chapter: ReadinessHoleRow[];
  is_ready_for_mix: boolean;
  asr_summary: {
    queued: number;
    processing: number;
    done: number;
    failed: number;
  };
  error?: string;
};

export type DawJobItem = {
  id: string;
  format: string;
  status: string;
  cancel_requested: boolean;
  progress: number;
  processed_items: number;
  total_items: number;
  output_name: string;
  error_message: string;
  created_at: string;
  download_url: string;
};

export type DawJobsResponse = {
  ok: boolean;
  items: DawJobItem[];
  error?: string;
};

export type ReadyForMixResponse = {
  ok: boolean;
  status?: string;
  message?: string;
  readiness?: BookReadinessResponse;
  error?: string;
};

export type PublicAuthConfigResponse = {
  ok: boolean;
  app_name: string;
  telegram_bot_username: string;
  studio_name?: string;
  setup_missing: boolean;
};

/* ---------- v2 pipeline progress (GET /api/v2/books/{id}/progress) ---------- */
export type V2StepState = "done" | "running" | "pending" | "failed" | "skipped";

export type V2ProgressStep = {
  key: string;
  label: string;
  state: V2StepState;
  detail?: string;
};

export type V2ProgressRun = {
  status: string;
  step: string;
  chapters_total: number;
  chapters_done: number;
  tokens?: number;
  started_at?: string;
  updated_at?: string;
  error?: string | null;
};

export type V2ProgressResponse = {
  mode: string;
  run: V2ProgressRun | null;
  steps: V2ProgressStep[];
  counts: Record<string, number>;
};

/* ---------- character map (GET /api/v2/books/{id}/character-map) ---------- */
// Спикер в атрибуции хранится строкой, не ссылкой на карту — поэтому имя может
// говорить, не будучи в карте, или быть в карте, ни разу не заговорив. Строка
// показывает оба факта сразу, отсюда четыре независимых флага вместо статуса.
export interface CharacterMapRow {
  /** идентификатор строки `Character`; пусто у имени, которое говорит в разметке, но роли для него нет — сводить такую строку не с чем */
  character_id: string;
  name: string;
  in_cast: boolean;
  in_markup: boolean;
  lines: number;
  chapters: number;
  /** Номера глав, где роль реально звучит по разметке — не только их количество. */
  chapter_numbers: number[];
  has_audio: boolean;
  is_placeholder: boolean;
  /** Диктор, назначенный строке карты; пусто, если строки карты нет или диктор не назначен. */
  actor_name: string;
  /** Хвостовой «?» в исходной записи — «Химера — Натали Ким?»: назначение предварительное. */
  actor_tentative: boolean;
  /** Главы «по мнению извлечения» (`appears_in`) — подсказка для сверки, не источник правды. */
  claimed_chapters: number[];
  /** «known» — канон автора знает эту роль, «new» — нет, пусто — корпус не подключён */
  canon_status: "" | "known" | "new";
  race: string;
  age: string;
  temperament: string;
  /** На экране — «голос», в базе — историческое поле `operator_note`: заполнено кастинговым заданием. */
  voice: string;
  aliases: string;
  /** ISO-время последней правки строки карты; пусто, если строки карты нет. */
  updated_at: string;
}

export type CharacterMapDeleteResponse = {
  ok: boolean;
  error?: string;
  deleted?: number;
  name?: string;
};

export type CharacterMapAdoptResponse = {
  ok: boolean;
  error?: string;
  created?: number;
  name?: string;
};

export type CharacterMapMergeResponse = {
  ok: boolean;
  error?: string;
  source?: string;
  target?: string;
  segments?: number;
  /** Сколько реплик реально переехало — то же число, что лист обещал до подтверждения. */
  lines?: number;
  rows?: number;
};

/* ---------- role intersections (внутри GET .../character-map) ---------- */
// Две роли одного актёра, которые встречаются в тексте — арифметика; допустимость
// пары решает человек (см. app/v2/role_intersections.py). severity — расстояние
// в абзацах, переведённое в слова: dialogue ближе всего, chapter — дальше всего.
export type RoleIntersectionSeverity = "dialogue" | "scene" | "chapter";

export interface RoleIntersection {
  actor: string;
  role_a: string;
  role_b: string;
  /** расстояние в абзацах между ближайшими репликами пары */
  distance: number;
  /** номер главы самой близкой встречи */
  chapter: number;
  /** номера всех общих глав пары, по возрастанию */
  chapters: number[];
  severity: RoleIntersectionSeverity;
  /** одна ли раса у пары — худший случай: два похожих голоса */
  same_race: boolean;
  acknowledged: boolean;
  /** причина «так задумано», как её записал владелец; пусто у непризнанных пар */
  reason: string;
  /** кто признал пару; пусто для непризнанных пар */
  acknowledged_by: string;
  /** ISO-время признания; пусто для непризнанных пар */
  acknowledged_at: string;
}

/** Полный ответ GET .../character-map — карта персонажей и пересечения ролей одним запросом. */
export type CharacterMapResponse = {
  ok: boolean;
  rows: CharacterMapRow[];
  intersections: RoleIntersection[];
  /** ISO-время последней отметки «автор проверил карту»; пусто, если карту ещё не проверяли. */
  checked_at: string;
  checked_by: string;
};

export type CharacterMapUpdateResponse = {
  ok: boolean;
  error?: string;
  name?: string;
  fields?: string[];
};

export type CharacterMapAcknowledgeResponse = {
  ok: boolean;
  error?: string;
  created?: number;
  role_a?: string;
  role_b?: string;
};

export type CharacterMapForgetResponse = {
  ok: boolean;
  error?: string;
  removed?: number;
  role_a?: string;
  role_b?: string;
};

export type CharacterMapCheckedResponse = {
  ok: boolean;
  error?: string;
  checked_at?: string;
  checked_by?: string;
};

/** Раздел «Дикторы» (`/api/dictors`). */
export type DictorDemoBrief = { id: string; title: string; duration_seconds: number };

export type DictorListItem = {
  user_id: string;
  name: string;
  telegram_user_id: string;
  username: string;
  demos: number;
  main_demo: DictorDemoBrief | null;
  roles: number;
  books: number;
  note: string;
  /** может ли бот писать: null — не знаем (кэш ещё пуст или нет Telegram) */
  reachable: boolean | null;
};

export type DictorsListResponse = { ok: boolean; items: DictorListItem[] };

export type DictorCard = {
  ok: boolean;
  user_id: string;
  name: string;
  login: string;
  telegram_user_id: string;
  username: string;
  note: string;
  main_demo_id: string;
  reachable: boolean | null;
  demos: (DictorDemoBrief & { trimmed: boolean; source: string; created_at: string | null })[];
  links: { id: string; url: string; title: string }[];
  roles: { book_id: string; book: string; role: string; state: string; recorded: boolean; deadline: RoleDeadlineView | null }[];
  recasts: { role_name: string; from_actor: string; to_actor: string; reason: string; comment: string; created_at: string | null }[];
};

/** Открытый срок пробы или роли (спека 2026-10-01-role-deadlines). */
export type RoleDeadlineView = {
  id: string;
  kind: "audition" | "role";
  due_at: string | null;
  overdue: boolean;
  /** глав с дублем / всего глав роли; у пробы — 0/0 */
  done: number;
  total: number;
};

/** «Настройки → Нейросети»: ключ провайдера — без значения, только откуда и последние 4 знака. */
export type AiKeySource = "site" | "env" | "none";
export type AiCheckStatus = "" | "ok" | "bad_key" | "no_money" | "unreachable" | "error";

export interface AiProvider {
  provider: string;
  label: string;
  source: AiKeySource;
  last4: string;
  unreadable: boolean;
  check_status: AiCheckStatus;
  check_detail: string;
  checked_at: string;
}

export interface AiStep {
  step: string;
  title: string;
  provider: string;
  model: string;
  source: "site" | "default";
  default: { provider: string; model: string };
  providers: string[];
  options: AiStepOption[];
}

export interface AiStepOption {
  provider: string;
  model: string;
  label: string;
  price: string;
  trains_on_text: boolean;
  note: string;
}

export interface AiSettings {
  providers: AiProvider[];
  steps: AiStep[];
}

export interface AiBalance {
  provider: string;
  available: boolean;
  amount: number | null;
  unit: string;
  detail: string;
}
