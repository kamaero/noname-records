import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import { ApiError, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { Sheet } from "../ui";
import { sceneTitle } from "./sound";
import type { ReaderSegment, SoundMarker, SoundPlace } from "./types";
import {
  changeSoundtrack, getSoundtrack, searchKey, searchSoundtrack, soundtrackKey, soundtrackPath,
  type SoundtrackEntry, type SoundtrackLayer, type SoundtrackWork,
} from "./soundtrackApi";
import { getYouTubeJobs, searchYouTube, youtubeJobsKey, youtubeSearchKey } from "./youtubeApi";
import { YouTubeJobStatus, YouTubeResults } from "./YouTubeResults";
import "./SoundtrackPanel.css";

const LAYERS: Array<{ id: SoundtrackLayer; title: string }> = [
  { id: "music", title: "Music" }, { id: "ambience", title: "Environment" }, { id: "fx", title: "Effects" },
];
const SOURCES = { youtube: "YouTube", openverse: "Openverse", collection: "Personal collection", elevenlabs: "AI / ElevenLabs" };
const safeUrl = (url?: string) => url && /^https?:\/\//i.test(url) ? url : undefined;
const playUrl = (url?: string) => url?.startsWith("/api/v2/ambient/") ? url : safeUrl(url);
const externalId = (work: SoundtrackWork) => work.external_id || work.id;
function duration(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
function errorText(error: unknown) {
  if (error instanceof ApiError && error.errorCode === "youtube_download_pending") return "Wait for YouTube downloads to finish before finalizing the chapter.";
  if (error instanceof ApiError && error.errorCode === "soundtrack_locked") return "The chapter list is finalized. Unlock it in Chapter selection to make changes.";
  if (error instanceof ApiError && error.status === 429) return "Openverse has limited requests. Wait a moment and search again.";
  if (error instanceof ApiError && error.status === 409) return "The list changed in another window. Review the updated list and try again. Your input is preserved.";
  return describeApiError(error, "Network unavailable. Try again.");
}
function Metadata({ work }: { work: SoundtrackWork }) {
  return <>
    <p className="stk-meta">{work.author || "Author not listed"} · {work.provider || SOURCES[work.source]} {duration(work.duration_seconds) ? <>· <span className="num">{duration(work.duration_seconds)}</span></> : null}</p>
    <p className="stk-meta">
      {safeUrl(work.license_url) ? <a href={safeUrl(work.license_url)} target="_blank" rel="noreferrer">{work.license || "License"} {work.license_version}</a> : work.license || "Terms unknown"}
      {safeUrl(work.source_url) ? <> · <a href={safeUrl(work.source_url)} target="_blank" rel="noreferrer">Original</a></> : null}
    </p>
    {work.rights_status === "share_alike" ? <p className="stk-warning">ShareAlike: check that the terms are compatible with your project.</p> : null}
    {work.rights_warning ? <p className="stk-warning">{work.rights_warning}</p> : null}
    {work.source !== "openverse" ? <p className="stk-meta">Rights and usage terms have not been verified.</p> : null}
  </>;
}

type Props = {
  chapterId: string; markers: SoundMarker[]; places: SoundPlace[]; segments: ReaderSegment[];
  article: HTMLElement | null; selectedMarkerId: string | null; scrollHost?: RefObject<HTMLDivElement>;
};
export function SoundtrackPanel({ chapterId, markers, places, segments, article, selectedMarkerId }: Props) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [mobileOpen, setMobileOpen] = useState(false);
  const mobileToggle = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const [wide, setWide] = useState(() => window.matchMedia("(min-width: 1251px)").matches);
  useEffect(() => { const media = window.matchMedia("(min-width: 1251px)"); const update = () => setWide(media.matches); media.addEventListener("change", update); return () => media.removeEventListener("change", update); }, []);
  useEffect(() => {
    const element = panel.current;
    if (!wide || !element) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      // Шапка Читалки меняет высоту: нижний плеер должен оставаться в окне и до прилипания панели.
      element.style.setProperty("--stk-top", `${Math.max(12, element.getBoundingClientRect().top)}px`);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    document.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    const resize = new ResizeObserver(schedule);
    if (element.parentElement) resize.observe(element.parentElement);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("scroll", schedule, true); window.removeEventListener("resize", schedule); resize.disconnect(); };
  }, [wide]);
  useEffect(() => {
    if (!mobileOpen || wide) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const sheet = document.querySelector(".stk-mobile-content")?.closest("aside");
      const targets = sheet?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]');
      if (!targets?.length) return;
      const first = targets[0]; const last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", trap);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", trap); mobileToggle.current?.focus({ preventScroll: true }); };
  }, [mobileOpen, wide]);
  const [readingScene, setReadingScene] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [layer, setLayer] = useState<SoundtrackLayer>("music");
  const [queries, setQueries] = useState<Record<string, { value: string; searched: string; page: number }>>({});
  const [source, setSource] = useState<"openverse" | "youtube">("youtube");
  const [queuedYoutubeJobs, setQueuedYoutubeJobs] = useState<Array<{ id: string; title: string; scene: string; layer: string; chapterId: string }>>([]);
  const [showRejected, setShowRejected] = useState(false);
  const [showDownloads, setShowDownloads] = useState(false);
  const [tab, setTab] = useState<"search" | "selection">("search");
  const [playing, setPlaying] = useState<{ title: string; url: string; sourceUrl?: string } | null>(null);
  const [youtubePlaying, setYoutubePlaying] = useState<{ videoId: string; title: string } | null>(null);
  const [audioFailed, setAudioFailed] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const [editing, setEditing] = useState<SoundtrackEntry | null>(null);
  const [manual, setManual] = useState({ source: "collection", title: "", author: "", source_url: "" });
  const [mutationError, setMutationError] = useState("");
  const requests = useRef(new Map<string, string>());
  const scenes = useMemo(() => markers.filter((marker) => marker.kind === "scene"), [markers]);
  const segmentOrder = useMemo(() => new Map(segments.map((segment, index) => [segment.id, index])), [segments]);
  const scene = scenes.find((item) => item.id === (pinned || readingScene)) ?? scenes[0] ?? null;
  const sceneIndex = scenes.findIndex((item) => item.id === scene?.id);
  const titleOf = (item: SoundMarker) => sceneTitle(item, places.find((place) => place.id === item.place_id), scenes.indexOf(item) + 1);
  const ownSounds = useMemo(() => {
    const start = scene ? segmentOrder.get(scene.segment_id) ?? 0 : 0;
    const next = scenes[sceneIndex + 1];
    const end = next ? segmentOrder.get(next.segment_id) ?? Infinity : Infinity;
    return markers.filter((marker) => marker.kind === "sound" && (segmentOrder.get(marker.segment_id) ?? -1) >= start && (segmentOrder.get(marker.segment_id) ?? Infinity) < end);
  }, [scene, sceneIndex, scenes, markers, segmentOrder]);
  const suggestions = layer === "music" ? scene?.payload.music_queries ?? [] : layer === "fx"
    ? ownSounds.flatMap((marker) => marker.payload.queries ?? []) : places.find((place) => place.id === scene?.place_id)?.ambience_queries ?? [];
  const queryId = `${scene?.id ?? "chapter"}:${layer}`;
  const queryState = queries[queryId] ?? { value: suggestions[0] ?? "", searched: suggestions[0] ?? "", page: 1 };
  const setQuery = (value: string, submit = false) => setQueries((old) => ({ ...old, [queryId]: { ...queryState, value, ...(submit ? { searched: value.trim(), page: 1 } : {}) } }));
  const draft = useQuery({ queryKey: soundtrackKey(chapterId), queryFn: () => getSoundtrack(chapterId) });
  const data = draft.data;
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => {
      const payload = { version: data?.version, ...body };
      const signature = JSON.stringify(payload);
      const requestId = requests.current.get(signature) ?? crypto.randomUUID();
      requests.current.set(signature, requestId);
      return changeSoundtrack(chapterId, { ...payload, request_id: requestId });
    },
    onSuccess: (response, body) => {
      queryClient.setQueryData(soundtrackKey(chapterId), response);
      requests.current.clear();
      setMutationError("");
      if (body.action === "select" && body.source !== "openverse") setManual((old) => ({ ...old, title: "", author: "", source_url: "" }));
      if (body.action === "update") setEditing(null);
    },
    onError: (error) => {
      setMutationError(errorText(error));
      if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries({ queryKey: soundtrackKey(chapterId) });
    },
  });
  const busy = mutation.isPending || !data;
  const locked = Boolean(data?.locked);
  const act = (body: Record<string, unknown>) => { setMutationError(""); mutation.mutate(body); };
  const search = useQuery({
    queryKey: searchKey(chapterId, layer, queryState.searched, queryState.page),
    queryFn: ({ signal }) => searchSoundtrack(chapterId, layer, queryState.searched, queryState.page, signal),
    enabled: source === "openverse" && Boolean(queryState.searched), staleTime: 10 * 60_000, retry: false,
  });

  const youtube = useQuery({
    queryKey: youtubeSearchKey(chapterId, queryState.searched),
    queryFn: () => searchYouTube(chapterId, queryState.searched),
    enabled: source === "youtube" && Boolean(queryState.searched), staleTime: 10 * 60_000, retry: false,
  });
  const youtubeHistory = useQuery({ queryKey: youtubeJobsKey(chapterId), queryFn: () => getYouTubeJobs(chapterId), retry: false });
  const youtubeJobs = [...new Map([
    ...queuedYoutubeJobs.filter((job) => job.chapterId === chapterId),
    ...(youtubeHistory.data?.jobs ?? []).map((job) => ({ id: job.job_id, title: job.title || "YouTube track", scene: scenes.find((item) => item.id === job.scene_id) ? titleOf(scenes.find((item) => item.id === job.scene_id)!) : "Scene no longer exists in the script", layer: LAYERS.find((item) => item.id === job.layer)?.title ?? job.layer })),
  ].map((job) => [job.id, job])).values()];

  useEffect(() => { setPinned(null); setReadingScene(null); setEditing(null); setQueries({}); setMobileOpen(false); setShowDownloads(false); }, [chapterId]);
  // Измеряем положение полос, а не направление скролла: короткие сцены и прыжки не теряются.
  useEffect(() => {
    if (!article || !scenes.length) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const scroll = article.closest(".v2r-pane-scroll");
      const line = Math.max(140, (scroll?.getBoundingClientRect().top ?? 0) + 40);
      let active = scenes[0].id;
      for (const item of scenes) {
        const band = [...article.querySelectorAll<HTMLElement>(".snd-scene[data-sound]")].find((element) => element.dataset.sound === item.id);
        if (band && band.getBoundingClientRect().top <= line) active = item.id;
      }
      setReadingScene(active);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    document.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    const resize = new ResizeObserver(schedule); resize.observe(article);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("scroll", schedule, true); window.removeEventListener("resize", schedule); resize.disconnect(); };
  }, [article, scenes]);
  useEffect(() => {
    const marker = markers.find((item) => item.id === selectedMarkerId);
    if (!marker) return;
    const position = segmentOrder.get(marker.segment_id) ?? -1;
    const context = [...scenes].reverse().find((item) => (segmentOrder.get(item.segment_id) ?? Infinity) <= position);
    if (context) { setReadingScene(context.id); if (pinned) setPinned(context.id); }
    if (marker.kind === "sound") { setLayer("fx"); const value = marker.payload.queries?.[0]; if (value && context) setQueries((old) => ({ ...old, [`${context.id}:fx`]: { value, searched: value, page: 1 } })); }
  // Закрепление не отменяет явно выбранный пользователем контекст маркера.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedMarkerId, markers, scenes, segmentOrder]);
  // Поиск готовится по одному: сначала текущая/следующая сцена, затем остальная глава.
  useEffect(() => {
    const controller = new AbortController();
    const ordered = [...scenes.slice(Math.max(0, sceneIndex)), ...scenes.slice(0, Math.max(0, sceneIndex))];
    const run = async () => {
      for (const item of ordered) {
        if (controller.signal.aborted) break;
        for (const query of (item.payload.music_queries ?? []).slice(0, 1)) {
          if (controller.signal.aborted) break;
          await queryClient.prefetchQuery({ queryKey: searchKey(chapterId, "music", query, 1), queryFn: ({ signal }) => searchSoundtrack(chapterId, "music", query, 1, signal), staleTime: 10 * 60_000, retry: false });
        }
      }
    };
    const timer = window.setTimeout(() => void run(), 500);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [chapterId, sceneIndex, scenes, queryClient]);
  useEffect(() => { if (playing) { setAudioFailed(false); void audio.current?.play().catch(() => setAudioFailed(true)); } }, [playing, mobileOpen, wide]);
  const play = (work: SoundtrackWork) => {
    setYoutubePlaying(null);
    const url = playUrl(work.preview_url);
    if (url) setPlaying({ title: work.title, url, sourceUrl: safeUrl(work.source_url) });
  };
  const selectionFor = (work: SoundtrackWork) => data?.entries.find((entry) => entry.source === "openverse" && entry.external_id === externalId(work) && entry.scene_id === (scene?.id ?? null) && entry.layer === layer);
  const rejected = (work: SoundtrackWork) => data?.rejections.some((item) => item.scene_id === (scene?.id ?? null) && item.layer === layer && item.openverse_id === externalId(work));
  const selection = <>
    <p className="stk-note">Save candidates, then mark only the tracks actually used in Audition. Downloading does not mark a track as used.</p>
    {data?.entries.length === 0 ? <p className="stk-note">No tracks selected for this chapter yet.</p> : null}
    {data?.entries.map((entry) => <div className="stk-entry" key={entry.id}>
      <strong>{entry.title}</strong><Metadata work={entry} />
      <p className="stk-meta">{entry.scene_missing ? "Scene removed from script · " : ""}{entry.scene_title || "Whole chapter"} · {LAYERS.find((item) => item.id === entry.layer)?.title}</p>
      <label className="stk-check"><input type="checkbox" checked={entry.used} disabled={busy || locked} onChange={(event) => act({ action: "update", entry_id: entry.id, used: event.target.checked })} />Used in Audition</label>
      <div className="stk-actions"><button className="btn btn-sm" disabled={busy || locked} onClick={() => setEditing({ ...entry })}>Edit details</button><button className="btn btn-sm btn-ghost" disabled={busy || locked} onClick={() => act({ action: "remove", entry_id: entry.id })}>Remove selection</button></div>
      <details className="stk-meta"><summary>Original metadata</summary><dl>{Object.entries(entry.original ?? {}).filter(([, value]) => typeof value === "string" && value).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl></details>
    </div>)}
    {editing ? <form className="stk-form" onSubmit={(event) => { event.preventDefault(); const original = data?.entries.find((entry) => entry.id === editing.id);
      const fields = ["title", "author", "source_url", "license", "license_version", "license_url", "attribution", "scene_id", "layer"] as const;
      const changed = Object.fromEntries(fields.filter((field) => (editing[field] ?? "") !== (original?.[field] ?? "")).map((field) => [field, editing[field]]));
      if (Object.keys(changed).length) act({ action: "update", entry_id: editing.id, ...changed });
      else setEditing(null); }}>
      <h3>Track details</h3>
      {(["title", "author", "source_url", "license", "license_version", "license_url", "attribution"] as const).map((field, index) => <label key={field}>{["Title", "Author", "Source page", "License", "License version", "License URL", "Attribution"][index]}<input className="ui-input" required={field === "title"} value={editing[field] ?? ""} onChange={(event) => setEditing({ ...editing, [field]: event.target.value })} /></label>)}
      <label>Scene<select className="ui-input" value={editing.scene_id ?? ""} onChange={(event) => setEditing({ ...editing, scene_id: event.target.value || null })}><option value="">Whole chapter</option>{scenes.map((item) => <option key={item.id} value={item.id}>{titleOf(item)}</option>)}{editing.scene_missing && editing.scene_id ? <option value={editing.scene_id}>Removed scene</option> : null}</select></label>
      <label>Layer<select className="ui-input" value={editing.layer} onChange={(event) => setEditing({ ...editing, layer: event.target.value as SoundtrackLayer })}>{LAYERS.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      <div className="stk-actions"><button className="btn btn-sm" disabled={busy || locked}>Save details</button><button type="button" className="btn btn-sm btn-ghost" onClick={() => setEditing(null)}>Cancel</button></div>
    </form> : null}
    <details open={!data?.entries.length}><summary>Add your own track</summary>
      <form className="stk-form" onSubmit={(event) => { event.preventDefault(); if (manual.title.trim()) act({ action: "select", ...manual, scene_id: scene?.id ?? null, layer, used: false }); }}>
        <label>Source<select className="ui-input" value={manual.source} disabled={locked} onChange={(event) => setManual({ ...manual, source: event.target.value })}><option value="collection">Personal collection</option></select></label>
        <label>Title<input className="ui-input" value={manual.title} required maxLength={300} disabled={locked} onChange={(event) => setManual({ ...manual, title: event.target.value })} /></label>
        <label>Author · optional<input className="ui-input" value={manual.author} disabled={locked} onChange={(event) => setManual({ ...manual, author: event.target.value })} /></label>
        <label>Reference URL · optional<input type="url" className="ui-input" value={manual.source_url} disabled={locked} onChange={(event) => setManual({ ...manual, source_url: event.target.value })} /></label>
        <p className="stk-note">The file stays on your computer. URLs, including YouTube links, are references; rights are not verified.</p>
        <button className="btn btn-sm" disabled={busy || locked || !manual.title.trim()}>Add to selection</button>
      </form>
    </details>
    {data?.legacy_tracks.length ? <details><summary>Existing ElevenLabs tracks</summary>{data.legacy_tracks.map((track) => <div key={track.ambient_track_id} className="stk-entry"><strong>{track.title}</strong><p className="stk-meta">{track.status}</p><div className="stk-actions"><button className="btn btn-sm" disabled={!playUrl(track.preview_url)} onClick={() => { const url = playUrl(track.preview_url); if (url) { setYoutubePlaying(null); setPlaying({ title: track.title, url }); } }}>Listen</button><button className="btn btn-sm" disabled={busy || locked} onClick={() => act({ action: "select", source: "elevenlabs", ambient_track_id: track.ambient_track_id, scene_id: scene?.id ?? null, layer, used: false })}>Save candidate</button></div></div>)}</details> : null}
    <div className="stk-final"><strong>{locked ? "Chapter finalized" : "Chapter draft"}</strong><p className="stk-note">{locked ? "Unlock the list to edit. Previous snapshots are preserved." : "Finalization includes only tracks marked as used."}</p>
      <div className="stk-actions">{locked ? <><button className="btn btn-sm" disabled={busy} onClick={() => act({ action: "unlock" })}>Unlock</button><a className="btn btn-sm" href={`${soundtrackPath(chapterId)}/credits`}>Download credits</a></> : <button className="btn btn-primary btn-sm" disabled={busy || Boolean(editing)} onClick={() => { const empty = !data?.entries.some((entry) => entry.used); if (!empty || window.confirm("Finalize this chapter without any used tracks? The export will be empty.")) act({ action: "finalize", ...(empty ? { confirm_empty: true } : {}) }); }}>Finalize chapter</button>}</div>
    </div>
    {data?.snapshots.length ? <details><summary>Finalization history <span className="num">({data.snapshots.length})</span></summary>{data.snapshots.map((snapshot) => <div className="stk-entry" key={snapshot.id}><span className="num">Version {snapshot.version}</span> · {new Date(snapshot.created_at).toLocaleString("en-US")}<ul>{(snapshot.entries ?? []).map((entry) => <li key={entry.id}>{entry.title}</li>)}</ul></div>)}</details> : null}
  </>;
  const player = playing ? <section className="stk-player" aria-label="Soundtrack preview">
    <div className="stk-player-head"><strong>{playing.title}</strong><button type="button" className="btn btn-sm btn-ghost" onClick={() => setPlaying(null)}>Close player</button></div>
    <audio key={playing.url} ref={audio} src={playing.url} controls preload="none" onError={() => setAudioFailed(true)} />
    {audioFailed ? <p className="stk-warning" role="alert">Playback failed. {playing.sourceUrl ? <a href={playing.sourceUrl} target="_blank" rel="noreferrer">Open the original</a> : "Check that the file is available."}</p> : null}
  </section> : youtubePlaying ? <section className="stk-player stk-youtube-player" aria-label="YouTube preview">
    <div className="stk-player-head"><strong>{youtubePlaying.title}</strong><button type="button" className="btn btn-sm btn-ghost" onClick={() => setYoutubePlaying(null)}>Close player</button></div>
    <div className="stk-youtube-preview"><iframe key={youtubePlaying.videoId} title={`YouTube: ${youtubePlaying.title}`} src={`https://www.youtube-nocookie.com/embed/${youtubePlaying.videoId}?autoplay=1`} allow="autoplay; encrypted-media; picture-in-picture" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" /><p className="stk-note">Player unavailable? <a href={`https://www.youtube.com/watch?v=${youtubePlaying.videoId}`} target="_blank" rel="noreferrer">Open YouTube</a>.</p></div>
  </section> : null;
  const content = <>
    <div className="stk-controls">
      <div className="stk-head"><h2>Sound search</h2><span className="stk-scene-badge">{scene ? `Scene ${String(sceneIndex + 1).padStart(2, "0")}` : "Whole chapter"}</span></div>
      <div className="stk-context">
        <span className="stk-context-label">{pinned ? "Scene pinned" : "Following the scene"}</span>
        {scene ? <button type="button" className={`stk-pin ${pinned ? "is-on" : ""}`} aria-pressed={Boolean(pinned)} onClick={() => setPinned(pinned ? null : scene.id)}>{pinned ? "Unpin" : "Pin"}</button> : null}
      </div>
      <p className="stk-scene-description" title={scene ? [titleOf(scene), scene.payload.mood, scene.payload.ambience].filter(Boolean).join(" · ") : undefined}>{scene ? titleOf(scene) : "Chapter has no scenes"}{scene?.payload.mood ? ` · ${scene.payload.mood}` : ""}</p>
      {tab === "search" ? <>
        <div className="stk-layer-tabs" role="tablist" aria-label="Sound layer">{LAYERS.map((item, index) => <button type="button" role="tab" id={`stk-layer-${chapterId}-${item.id}`} aria-controls={`stk-results-${chapterId}`} className={layer === item.id ? "is-on" : ""} key={item.id} aria-selected={layer === item.id} tabIndex={layer === item.id ? 0 : -1} onClick={() => setLayer(item.id)} onKeyDown={(event) => {
          const next = event.key === "ArrowRight" ? (index + 1) % LAYERS.length : event.key === "ArrowLeft" ? (index + LAYERS.length - 1) % LAYERS.length : event.key === "Home" ? 0 : event.key === "End" ? LAYERS.length - 1 : null;
          if (next === null) return;
          event.preventDefault(); setLayer(LAYERS[next].id); event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
        }}>{item.title}</button>)}</div>
        <div className="stk-layer-head"><h3>{LAYERS.find((item) => item.id === layer)?.title}</h3><span className="stk-meta">In chapter: <span className="num">{data?.entries.filter((entry) => entry.layer === layer).length ?? 0}</span></span></div>
        <p className="stk-query-label">Queries from sound annotations</p>
        {suggestions.length ? <div className="stk-suggestions" role="group" aria-label="Scene queries">{[...new Set(suggestions)].map((query) => <button type="button" className={`stk-chip ${queryState.searched === query ? "is-on" : ""}`} key={query} aria-pressed={queryState.searched === query} onClick={() => setQuery(query, true)}>{query}</button>)}</div> : <p className="stk-note">No suggested queries — enter your own.</p>}
        <form className="stk-search" onSubmit={(event) => { event.preventDefault(); setQuery(queryState.value, true); }}><label className="stk-search-label" htmlFor={`stk-search-${chapterId}`}>Search query · English usually works best</label><div className="stk-search-row"><input id={`stk-search-${chapterId}`} className="ui-input" value={queryState.value} onChange={(event) => setQuery(event.target.value)} placeholder={layer === "music" ? "fantasy instrumental ambient" : "Search sounds"} /><button className="btn btn-primary btn-sm" disabled={!queryState.value.trim()}>Search</button></div></form>
        <div className="stk-search-source"><span>Source:</span><div role="group" aria-label="Search source"><button type="button" className={source === "youtube" ? "is-on" : ""} aria-pressed={source === "youtube"} onClick={() => setSource("youtube")}>YouTube</button><span aria-hidden="true">·</span><button type="button" className={source === "openverse" ? "is-on" : ""} aria-pressed={source === "openverse"} onClick={() => setSource("openverse")}>Openverse</button></div><span className="stk-results-label">Results</span></div>
      </> : <button type="button" className="stk-back" onClick={() => setTab("search")}>← Back to sound search</button>}
    </div>
    <div className="stk-scroll" id={`stk-results-${chapterId}`} role={tab === "search" ? "tabpanel" : "region"} aria-labelledby={tab === "search" ? `stk-layer-${chapterId}-${layer}` : undefined} tabIndex={0} aria-label={tab === "search" ? "Search results" : "Chapter selection"}>
      {draft.isPending ? <p role="status">Loading chapter selection…</p> : null}
      {draft.isError ? <div role="alert" className="stk-warning">{errorText(draft.error)} <button className="btn btn-sm" onClick={() => void draft.refetch()}>Try again</button></div> : null}
      {mutationError ? <p role="alert" className="stk-warning">{mutationError}</p> : null}
      {youtubeHistory.isError ? <p className="stk-warning" role="alert">Could not load YouTube history. <button className="btn btn-sm" onClick={() => void youtubeHistory.refetch()}>Try again</button></p> : null}
      {tab === "selection" ? selection : <>
        {layer === "ambience" ? <p className="stk-note">Environment intervals have not been confirmed. Search using the script; scene weather does not establish a sound duration.</p> : null}
        {layer === "fx" ? <details><summary>Sounds in this scene <span className="num">({ownSounds.length})</span></summary><div className="stk-note">Review these suggestions against the script.{ownSounds.filter((item) => item.quote).map((item) => <blockquote key={item.id}>{item.quote}</blockquote>)}</div></details> : null}
      {source === "youtube" ? <>
        {locked ? <p className="stk-warning">Unlock Chapter selection before downloading.</p> : null}
        {!scene ? <p className="stk-note">Add a scene to the chapter before downloading.</p> : null}
        {youtube.isFetching ? <p role="status">Searching YouTube…</p> : null}
        {youtube.isError ? <div className="stk-warning" role="alert">{describeApiError(youtube.error, "YouTube search is unavailable.")} <button className="btn btn-sm" onClick={() => void youtube.refetch()}>Search again</button></div> : null}
        {!queryState.searched ? <p className="stk-note">Enter a search query.</p> : youtube.data?.results.length === 0 ? <p className="stk-note">No results. Try another query.</p> : null}
        <YouTubeResults results={youtube.data?.results ?? []} chapterId={chapterId} sceneId={scene?.id ?? null} layer={layer} disabled={busy || locked || !scene} onPlay={(videoId, title) => { setPlaying(null); setYoutubePlaying({ videoId, title }); }} onQueued={(id, title) => { setQueuedYoutubeJobs((old) => old.some((job) => job.id === id) ? old : [...old, { id, title, chapterId, scene: scene ? titleOf(scene) : "Whole chapter", layer: LAYERS.find((item) => item.id === layer)?.title ?? layer }]); setShowDownloads(true); void queryClient.invalidateQueries({ queryKey: youtubeJobsKey(chapterId) }); pushToast({ tone: "success", title: "Download queued", detail: "Status is shown in YouTube downloads below the results." }); }} />
      </> : <>
      {search.isFetching ? <p role="status">Searching Openverse…</p> : null}
      {search.isError ? <div className="stk-warning" role="alert">{errorText(search.error)} <button className="btn btn-sm" onClick={() => void search.refetch()}>Search again</button></div> : null}
      {!queryState.searched ? <p className="stk-note">No prepared query. Enter your own search.</p> : search.data?.results.length === 0 ? <p className="stk-note">No results. Try another query or add your own track in Chapter selection.</p> : null}
      {data?.rejections.length ? <label className="stk-check"><input type="checkbox" checked={showRejected} onChange={(event) => setShowRejected(event.target.checked)} />Show rejected</label> : null}
      {search.data?.results.filter((work) => showRejected || !rejected(work)).map((work) => {
        const selected = selectionFor(work); const isRejected = rejected(work);
        return <div className={`stk-result ${isRejected ? "is-rejected" : ""}`} key={work.id}><strong>{work.title || "Untitled"}</strong><Metadata work={work} /><div className="stk-actions">
          <button className="btn btn-sm" disabled={!playUrl(work.preview_url)} onClick={() => play(work)}>Preview</button>
          <button className={`btn btn-sm ${selected ? "is-on" : ""}`} disabled={busy || locked} onClick={() => act(selected ? { action: "remove", entry_id: selected.id } : { action: "select", source: "openverse", openverse_id: externalId(work), scene_id: scene?.id ?? null, layer, used: false })}>{selected ? "Remove selection" : "Select"}</button>
          <button className="btn btn-sm btn-ghost" disabled={busy || locked} onClick={() => act({ action: isRejected ? "restore" : "reject", scene_id: scene?.id ?? null, layer, openverse_id: externalId(work) })}>{isRejected ? "Restore" : "Reject"}</button>
          {safeUrl(work.download_url) ? <a className="btn btn-sm" href={safeUrl(work.download_url)} target="_blank" rel="noreferrer">Download from source</a> : safeUrl(work.source_url) ? <a className="btn btn-sm" href={safeUrl(work.source_url)} target="_blank" rel="noreferrer">Open source</a> : null}
        </div>{!work.preview_url ? <p className="stk-note">Preview unavailable. Check the source page.</p> : null}<p className="stk-note">The preview may differ from the original. If downloading fails, open the original.</p></div>;
      })}
      {search.data && search.data.page_count > 1 ? <div className="stk-actions"><button className="btn btn-sm" disabled={queryState.page <= 1 || search.isFetching} onClick={() => setQueries((old) => ({ ...old, [queryId]: { ...queryState, page: queryState.page - 1 } }))}>Previous</button><span className="num">{queryState.page} / {search.data.page_count}</span><button className="btn btn-sm" disabled={queryState.page >= search.data.page_count || search.isFetching} onClick={() => setQueries((old) => ({ ...old, [queryId]: { ...queryState, page: queryState.page + 1 } }))}>Next</button></div> : null}
      </>}
      <p className="stk-note stk-rights-note">{source === "youtube" ? "Confirm rights for each track before downloading. Add files to Audition manually." : "Commercial work: CC0 / CC BY. Review CC BY-SA separately for compatibility. Verify attribution and the license at the original source."}</p>
      {data?.entries.some((entry) => entry.scene_id !== (scene?.id ?? null)) ? <details><summary>Saved in other scenes</summary>{data.entries.filter((entry) => entry.scene_id !== (scene?.id ?? null)).map((entry) => <div className="stk-entry" key={entry.id}><strong>{entry.title}</strong><p className="stk-meta">{entry.scene_title || "Whole chapter"}</p>{entry.source === "openverse" ? <button className="btn btn-sm" disabled={busy || locked} onClick={() => act({ action: "select", source: "openverse", openverse_id: entry.external_id, scene_id: scene?.id ?? null, layer, used: false })}>Select for this scene</button> : null}</div>)}</details> : null}

      </>}
      {youtubeJobs.length ? <details className="stk-downloads" open={showDownloads} onToggle={(event) => setShowDownloads(event.currentTarget.open)}><summary>YouTube downloads <span className="num">({youtubeJobs.length})</span></summary>{youtubeJobs.map((job) => <div className="stk-entry" key={job.id}><strong>{job.title}</strong><p className="stk-meta">{job.scene} · {job.layer}</p><YouTubeJobStatus chapterId={chapterId} jobId={job.id} /></div>)}</details> : null}
    </div>
    <div className="stk-chapter-footer"><button type="button" aria-expanded={tab === "selection"} onClick={() => setTab(tab === "selection" ? "search" : "selection")}><span>{tab === "selection" ? "Chapter selection" : "Selected for chapter →"}</span><span className="stk-count num">{data?.entries.length ?? 0}</span></button><p>Files, rights and credits</p></div>
    {player}
  </>;
  return <>
    <button ref={mobileToggle} type="button" className="btn stk-mobile-toggle" aria-expanded={mobileOpen} onClick={() => setMobileOpen(true)}>Soundtrack</button>
    {wide ? <aside ref={panel} className="stk-panel" aria-label="Soundtrack selection">{content}</aside> : null}
    {mobileOpen && !wide ? createPortal(<Sheet title="Soundtrack" className="stk-sheet" onClose={() => setMobileOpen(false)}><div className="stk-mobile-content">{content}</div></Sheet>, document.body) : null}
    {!wide && !mobileOpen && player ? <div className="stk-player-floating">{player}</div> : null}
  </>;
}
