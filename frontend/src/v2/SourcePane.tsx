/**
 * The left half of the split view: the chapter exactly as the author wrote it —
 * no roles, no colours, no stress marks. Its job is to be the thing the script
 * is checked against, so it deliberately shows nothing the pipeline added.
 *
 * Two states are worth a word to the reader: a `gap` block (prose the script does
 * not cover — the one real loss this screen can catch), and `aligned: false`,
 * where the stored offsets no longer fit the source and the segments' own text is
 * shown instead, which can no longer prove that nothing was lost.
 */
import { memo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../api/client";
import { SkeletonPanel } from "../components/Skeleton";
import type { ChapterSourceResponse, SourceBlock } from "./types";

/** DOM anchor of a block in the source pane; the script pane uses `seg-<id>`. */
export const sourceDomId = (segmentId: string) => `src-${segmentId}`;

function highlight(text: string, query: string): ReactNode {
  const needle = query.trim().toLowerCase();
  if (needle.length < 2) return text;
  const out: ReactNode[] = [];
  const hay = text.toLowerCase();
  let cursor = 0;
  for (let at = hay.indexOf(needle); at >= 0; at = hay.indexOf(needle, cursor)) {
    if (at > cursor) out.push(text.slice(cursor, at));
    out.push(
      <mark key={at} className="v2r-hit">
        {text.slice(at, at + needle.length)}
      </mark>,
    );
    cursor = at + needle.length;
  }
  if (!out.length) return text;
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

const Block = memo(function Block({ block, query }: { block: SourceBlock; query: string }) {
  const body = highlight(block.text, query);
  if (block.kind === "gap") {
    return (
      <p className="v2r-src-p v2r-src-p--gap" title="This text is missing from the script">
        <span className="v2r-src-gap-tag">missing from script</span>
        {body}
      </p>
    );
  }
  const anchor = block.segment_id ? { id: sourceDomId(block.segment_id), seg: block.segment_id } : null;
  if (block.kind === "heading") {
    return (
      <h2 id={anchor?.id} data-seg={anchor?.seg} className="v2r-src-h">
        {body}
      </h2>
    );
  }
  return (
    <p id={anchor?.id} data-seg={anchor?.seg} className="v2r-src-p">
      {body}
    </p>
  );
});

export function SourcePane({ chapterId, query }: { chapterId: string; query: string }) {
  const sourceQuery = useQuery({
    queryKey: ["v2", "chapter-source", chapterId],
    queryFn: () => apiGet<ChapterSourceResponse>(`/api/v2/chapters/${encodeURIComponent(chapterId)}/source`),
    enabled: Boolean(chapterId),
  });

  if (sourceQuery.isLoading) return <SkeletonPanel label="Loading original…" lines={5} />;
  if (sourceQuery.isError || !sourceQuery.data) {
    return <p className="v2r-notice">{describeApiError(sourceQuery.error, "Could not load the chapter's original text.")}</p>;
  }

  const { blocks, counts, aligned } = sourceQuery.data;
  return (
    <>
      {!aligned ? (
        <p className="v2r-notice">
          The chapter text changed after annotation. Stored segment text is shown instead of the original file.
        </p>
      ) : counts.gaps > 0 ? (
        <p className="v2r-notice">
          {counts.gaps === 1 ? "One original paragraph is missing from the script" : `Original paragraphs missing from script: ${counts.gaps}`} — marked on the left.
        </p>
      ) : null}
      {blocks.length === 0 ? <p className="v2r-src-empty">The original chapter text was not saved.</p> : null}
      {blocks.map((block, index) => (
        <Block key={block.segment_id || `gap-${index}`} block={block} query={query} />
      ))}
    </>
  );
}
