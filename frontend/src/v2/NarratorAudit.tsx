import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ApiError, apiGet } from "../api/client";
import "./NarratorAudit.css";

type Chapter = { chapter_id: string; chapter_index: number };
type Finding = {
  index: number;
  text: string;
  missing_words: string[];
  note: string;
  confirm_note: string;
  markers: Array<{ start: number; end: number; approximate: true; word: string }>;
  marker_unavailable?: boolean;
};
type Audit = {
  ok: boolean;
  audit_id: string | null;
  fingerprint: string | null;
  items: Array<{ audio_file_id: string; filename: string; findings: Finding[]; download_url: string; labels_download_url?: string }>;
  instructions: string;
};

function timecode(seconds: number): string {
  const tenths = Math.round(Math.max(0, seconds) * 10);
  const minutes = Math.floor(tenths / 600);
  return `${minutes}:${String(Math.floor(tenths / 10) % 60).padStart(2, "0")}.${tenths % 10}`;
}

export function NarratorAudit({ bookId, chapters }: { bookId: string; chapters: Chapter[] }) {
  const [params, setParams] = useSearchParams();
  const requestedChapter = params.get("audit_chapter");
  const chapterId = requestedChapter ?? chapters[0]?.chapter_id ?? "";
  const selected = chapters.find((chapter) => chapter.chapter_id === chapterId);
  const section = useRef<HTMLElement>(null);
  const audit = useQuery({
    queryKey: ["v2", "narrator-audit", bookId, chapterId],
    queryFn: async () => {
      const result = await apiGet<Audit>(`/api/v2/chapters/${encodeURIComponent(chapterId)}/narrator-audit`);
      if (!result.ok) throw new Error("audit_unavailable");
      return result;
    },
    enabled: Boolean(selected),
    retry: false,
    // Результаты и ссылки относятся к текущей записи: после возврата на экран
    // снова проверяем доступ, а не показываем сохранённые чужие или старые данные.
    staleTime: 0,
    gcTime: 0,
  });

  useEffect(() => {
    if (requestedChapter) section.current?.scrollIntoView({ block: "start" });
  }, [requestedChapter]);

  const forbidden = audit.error instanceof ApiError && [403, 404].includes(audit.error.status);
  const result = !audit.isFetching && !audit.isError ? audit.data : undefined;

  return (
    <section ref={section} className="v2r-narrator-audit" aria-labelledby="narrator-audit-title">
      <div className="v2r-narrator-audit-heading">
        <h2 id="narrator-audit-title">Recording check</h2>
        {selected ? <button type="button" className="btn btn-sm" disabled={audit.isFetching} onClick={() => void audit.refetch()}>Refresh check</button> : null}
      </div>
      <p className="v2r-narrator-audit-hint">Passages in the narrator recording to listen to and compare with the script.</p>
      {chapters.length ? (
        <label className="v2r-narrator-audit-chapter">Chapter
          <select className="v2r-select" aria-label="Chapter to check" value={selected ? chapterId : ""} onChange={(event) => {
            const next = new URLSearchParams(params);
            next.set("audit_chapter", event.target.value);
            setParams(next, { replace: true });
          }}>
            {!selected ? <option value="" disabled>Choose a chapter</option> : null}
            {chapters.map((chapter) => <option key={chapter.chapter_id} value={chapter.chapter_id}>Chapter {chapter.chapter_index}</option>)}
          </select>
        </label>
      ) : null}
      <div aria-live="polite" aria-busy={audit.isFetching}>
        {!selected ? <p>{requestedChapter ? "The linked chapter is unavailable. Choose a chapter for your role." : "No chapters are available for this role yet."}</p> : audit.isFetching ? (
          <p role="status">Loading chapter check…</p>
        ) : audit.isError ? (
          <div>
            <p>{forbidden ? "This chapter check is unavailable for your account." : "Could not load the check. Try refreshing it."}</p>
            <button type="button" className="btn btn-sm" onClick={() => void audit.refetch()}>Retry</button>
          </div>
        ) : result && !result.items.length ? (
          <p>This chapter has no available recording findings. New results will appear here after the recording is checked.</p>
        ) : result ? (
          <>
            <p className="v2r-narrator-audit-hint">Times are approximate listening guides, not exact word boundaries.</p>
            <p className="v2r-narrator-audit-hint">TXT contains labels for your original recording, without audio. Times are measured from the start of the named file.</p>
            {result.items.map((item, sourceIndex) => (
              <div className="v2r-narrator-audit-source" key={item.audio_file_id}>
                <div className="v2r-narrator-audit-source-heading">
                  <h3>{result.items.length > 1 ? `Recording ${sourceIndex + 1} · ` : ""}{item.filename}</h3>
                  <div className="v2r-narrator-audit-downloads">
                    {item.labels_download_url ? <a className="btn btn-sm" href={item.labels_download_url} download>Download labels (TXT)</a> : null}
                    <a className="btn btn-sm" href={item.download_url} download>Download WAV and Audacity labels (ZIP)</a>
                  </div>
                </div>
                <ol className="v2r-narrator-audit-findings">
                  {item.findings.map((finding, index) => (
                    <li key={`${finding.index}:${index}`}>
                      <p><strong>Line {finding.index + 1}</strong></p>
                      <blockquote>{finding.text}</blockquote>
                      {finding.missing_words.length ? <p><strong>Check words:</strong> {finding.missing_words.join(", ")}</p> : null}
                      {finding.note ? <p>{finding.note}</p> : null}
                      {finding.confirm_note && finding.confirm_note !== finding.note ? <p>{finding.confirm_note}</p> : null}
                      {finding.markers.length ? (
                        <ul className="v2r-narrator-audit-times" aria-label="Approximate time in the original recording">
                          {finding.markers.map((marker, markerIndex) => <li key={markerIndex}>Around <span className="num">{timecode(marker.start)}–{timecode(marker.end)}</span>{marker.word ? ` · near “${marker.word}”` : ""}</li>)}
                        </ul>
                      ) : <p className="v2r-narrator-audit-hint">Time unavailable. Find this passage in the original recording by its text.</p>}
                    </li>
                  ))}
                </ol>
              </div>
            ))}
            <details className="v2r-narrator-audit-instructions" open>
              <summary>How to check and send a correction</summary>
              <ol>
                <li>Open your local original recording in Audacity, or extract the ZIP and open source.wav. Each recording needs its own labels file.</li>
                <li>Choose <strong>File &gt; Import &gt; Labels</strong> and the downloaded TXT file (<strong>labels.txt</strong> in the ZIP). Labels appear after import.</li>
                <li>Listen to the marked passages and compare them with the full script lines above. If a label is unavailable, find the passage by its text.</li>
                <li>Save a full corrected chapter recording as a new WAV or FLAC with the <strong>_fix</strong> suffix. For a short correction, record the whole affected line. Keep the original recording.</li>
                <li>Return to Script and use “Upload take”. Choose this chapter and select “Full chapter replacement” for the whole chapter or “Short correction only” for a few corrected lines.</li>
              </ol>
            </details>
          </>
        ) : null}
      </div>
    </section>
  );
}
