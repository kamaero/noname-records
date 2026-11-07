import { apiGet, apiPostJson } from "../api/client";
import type { SoundtrackLayer } from "./soundtrackApi";

export type YouTubeResult = {
  id: string; title: string; url: string; channel: string;
  duration_seconds: number | null; thumbnail: string; license: string;
};
export type YouTubeJob = {
  status: "queued" | "running" | "done" | "failed";
  error?: string; audio_file_id?: string | null; mirror_state?: string | null;
};
export type YouTubeJobEntry = YouTubeJob & { job_id: string; title: string; scene_id: string; layer: SoundtrackLayer };
const youtubePath = (chapter: string) => `/api/v2/chapters/${encodeURIComponent(chapter)}/youtube`;
export const youtubeSearchKey = (chapter: string, query: string) => ["v2", "youtube", chapter, query] as const;
export const searchYouTube = (chapter: string, query: string) => apiGet<{ results: YouTubeResult[] }>(`${youtubePath(chapter)}/search?${new URLSearchParams({ q: query })}`);
export const getYouTubeDetail = (chapter: string, videoId: string) => apiGet<{ result: YouTubeResult }>(`${youtubePath(chapter)}/${encodeURIComponent(videoId)}/detail`);
export const downloadYouTube = (chapter: string, body: {
  video_id: string; scene_id: string | null; layer: SoundtrackLayer;
  rights_confirmed: true; license_note: string; request_id: string;
}) => apiPostJson<{ ok: true; job_id: string }>(`${youtubePath(chapter)}/download`, body);
export const getYouTubeJob = (chapter: string, jobId: string) => apiGet<YouTubeJob>(`${youtubePath(chapter)}/jobs/${encodeURIComponent(jobId)}`);
export const youtubeJobsKey = (chapter: string) => ["v2", "youtube-jobs", chapter] as const;
export const getYouTubeJobs = (chapter: string) => apiGet<{ jobs: YouTubeJobEntry[] }>(`${youtubePath(chapter)}/jobs`);
