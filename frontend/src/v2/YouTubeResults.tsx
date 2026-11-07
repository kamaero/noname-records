import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, describeApiError } from "../api/client";
import { soundtrackKey, type SoundtrackLayer } from "./soundtrackApi";
import { downloadYouTube, getYouTubeDetail, getYouTubeJob, type YouTubeResult } from "./youtubeApi";

function downloadError(error: unknown) {
  if (error instanceof ApiError && error.errorCode === "soundtrack_locked") return "The chapter list is finalized. Unlock it in Chapter selection and try downloading again.";
  if (error instanceof ApiError && error.errorCode === "youtube_download_pending") return "This track is already downloading. Wait for it to finish.";
  return describeApiError(error, "Could not queue the download.");
}
function jobError(code: string) {
  if (code === "youtube_worker_interrupted") return "The download was interrupted. Confirm your rights again before retrying.";
  if (code === "youtube_audition_format_unavailable") return "This video has no audio format suitable for Audition. Choose another video or obtain a file from the rights holder.";
  if (code === "youtube_local_disk_full") return "Local storage is full. Free some space and download again.";
  if (code === "queue_unavailable") return "The download queue is unavailable. Try again later.";
  if (code === "youtube_download_failed") return "Audio download failed. Check the video and confirm your rights again before retrying.";
  return "Could not download the track. Check that the YouTube video is available and try again.";
}

export function YouTubeJobStatus({ chapterId, jobId }: { chapterId: string; jobId: string }) {
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: ["v2", "youtube-job", chapterId, jobId],
    queryFn: () => getYouTubeJob(chapterId, jobId),
    // Локальный файл и зеркало заканчиваются независимо: продолжаем ждать NAS после загрузки.
    refetchInterval: (query) => query.state.status === "error" || query.state.data?.status === "failed" || (query.state.data?.status === "done" && query.state.data?.mirror_state !== "pending" && query.state.data?.mirror_state !== "") ? false : 3000,
    retry: false,
  });
  const result = job.data;
  useEffect(() => { if (result?.status === "done") void queryClient.invalidateQueries({ queryKey: soundtrackKey(chapterId) }); }, [result?.status, chapterId, queryClient]);
  const local = result?.status === "done" && result.audio_file_id ? "Saved" : result?.status === "failed" ? "Download failed" : result?.status === "running" ? "Downloading…" : result?.status === "done" ? "Save not confirmed" : "Queued";
  const mirror = result?.mirror_state === "ok" ? "Copy verified" : result?.mirror_state === "pending" || result?.mirror_state === "" ? "Awaiting copy" : result?.mirror_state === "mismatch" ? "Copy verification failed" : result?.mirror_state === "error" ? "Copy failed" : result?.mirror_state === "disabled" ? "Not configured" : "Status unconfirmed";
  return <div className="stk-youtube-job" role="status">
    <p className="stk-meta">Local: {local}<br />NAS mirror: {mirror}</p>
    {result?.error ? <p className="stk-warning">{jobError(result.error)}</p> : null}
    {job.isError ? <p className="stk-warning">Could not check the status. <button className="btn btn-sm" onClick={() => void job.refetch()}>Check again</button></p> : null}
    {result?.status === "done" ? <p className="stk-note">Add the file to Audition manually. Mark it as used in Chapter selection after editing.</p> : null}
  </div>;
}

function Result({ work, chapterId, sceneId, layer, disabled, onQueued, onPlay }: {
  work: YouTubeResult; chapterId: string; sceneId: string | null; layer: SoundtrackLayer; disabled: boolean;
  onQueued: (jobId: string, title: string) => void;
  onPlay: (videoId: string, title: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const queryClient = useQueryClient();
  const requestKey = (licenseNote: string) => ["v2", "youtube-request", chapterId, sceneId, layer, work.id, licenseNote];
  const download = useMutation({
    mutationFn: () => {
      const licenseNote = note.trim();
      // При потере ответа смена вкладки не должна превращать повтор в ещё одну загрузку.
      queryClient.setQueryDefaults(["v2", "youtube-request"], { gcTime: Infinity });
      const requestId = queryClient.getQueryData<string>(requestKey(licenseNote)) ?? crypto.randomUUID();
      queryClient.setQueryData(requestKey(licenseNote), requestId);
      return downloadYouTube(chapterId, { video_id: work.id, scene_id: sceneId, layer, rights_confirmed: true, license_note: licenseNote, request_id: requestId });
    },
    onSuccess: (response) => {
      queryClient.removeQueries({ queryKey: requestKey(note.trim()), exact: true });
      setConfirming(false); setConfirmed(false); setNote(""); onQueued(response.job_id, work.title);
    },
    retry: false,
  });
  const jobId = download.data?.job_id;
  const latestJob = useQuery({ queryKey: ["v2", "youtube-job", chapterId, jobId], queryFn: () => getYouTubeJob(chapterId, jobId!), enabled: Boolean(jobId), staleTime: Infinity, retry: false });
  const canRetry = latestJob.data?.status === "failed";
  const detail = useQuery({ queryKey: ["v2", "youtube-detail", chapterId, work.id], queryFn: () => getYouTubeDetail(chapterId, work.id), enabled: confirming, staleTime: 10 * 60_000, retry: false });
  const videoId = /^[\w-]{11}$/.test(work.id) ? work.id : null;
  const url = videoId ? `https://www.youtube.com/watch?v=${videoId}` : null;
  const seconds = work.duration_seconds;
  const length = seconds != null && Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}` : "";
  return <div className="stk-result">
    <strong>{work.title || "Untitled"}</strong>
    <p className="stk-meta">{work.channel || "Channel not listed"}{length ? <> · <span className="num">{length}</span></> : null}</p>
    <p className="stk-meta">Declared license: {work.license || "Not listed"}. Rights are not checked automatically.</p>
    <div className="stk-actions">
      {videoId ? <button className="btn btn-sm" onClick={() => onPlay(videoId, work.title)}>Preview</button> : null}
      {url ? <a className="btn btn-sm" href={url} target="_blank" rel="noreferrer">Watch on YouTube</a> : null}
      <button className="btn btn-primary btn-sm" disabled={disabled || download.isPending || (download.isSuccess && !canRetry) || !videoId} onClick={() => { download.reset(); setConfirmed(false); setNote(""); setConfirming(true); }}>{canRetry ? "Retry download" : download.isSuccess ? "Queued" : "Download"}</button>
    </div>
    {confirming ? <form className="stk-form" onSubmit={(event) => { event.preventDefault(); if (confirmed && note.trim() && !disabled && !download.isPending) download.mutate(); }}>
      {detail.isFetching ? <p className="stk-note" role="status">Loading video details…</p> : <p className="stk-meta">License declared on the page: {detail.data?.result?.license || "Not listed"}. Check the terms with the rights holder.</p>}
      {detail.isError ? <p className="stk-warning">Video details are unavailable. Check the page and permission manually.</p> : null}
      <p className="stk-note">Download only with the rights holder's permission or a suitable license. Availability on YouTube does not grant download or commercial use rights.</p>
      <label>Permission or license for this track<textarea className="ui-input stk-prompt" required rows={3} maxLength={1000} value={note} placeholder="License terms, URL or permission details" disabled={download.isPending} onChange={(event) => setNote(event.target.value)} /></label>
      <label className="stk-check"><input type="checkbox" checked={confirmed} disabled={download.isPending} onChange={(event) => setConfirmed(event.target.checked)} />I have verified permission to download and use this specific track in this project.</label>
      <div className="stk-actions"><button className="btn btn-primary btn-sm" disabled={disabled || download.isPending || !confirmed || !note.trim()}>{download.isPending ? "Submitting…" : "Confirm and download"}</button><button type="button" className="btn btn-sm btn-ghost" disabled={download.isPending} onClick={() => { setConfirming(false); setConfirmed(false); setNote(""); }}>Cancel</button></div>
    </form> : null}
    {download.isError ? <p className="stk-warning" role="alert">{downloadError(download.error)}</p> : null}
    {download.isSuccess ? <p className="stk-note">Download queued. Status is shown in YouTube downloads below the results.</p> : null}
  </div>;
}

export function YouTubeResults({ results, chapterId, sceneId, layer, disabled, onQueued, onPlay }: {
  results: YouTubeResult[]; chapterId: string; sceneId: string | null; layer: SoundtrackLayer; disabled: boolean;
  onQueued: (jobId: string, title: string) => void;
  onPlay: (videoId: string, title: string) => void;
}) {
  // Новая сцена и слой требуют нового согласия, даже если найден тот же ролик.
  return <>{results.map((work) => <Result key={`${chapterId}:${sceneId}:${layer}:${work.id}`} work={work} chapterId={chapterId} sceneId={sceneId} layer={layer} disabled={disabled} onQueued={onQueued} onPlay={onPlay} />)}</>;
}
