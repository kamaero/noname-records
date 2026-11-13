import type { NasDelivery } from "../../types";

const labels = { delivered: "Verified", pending: "Pending", error: "Error", unknown: "Unknown" };

export function NasDeliveryDetails({ delivery, id }: { delivery: NasDelivery; id: string }) {
  const groups = new Map<string, NasDelivery["files"]>();
  for (const file of delivery.files) {
    const files = groups.get(file.role) ?? [];
    files.push(file);
    groups.set(file.role, files);
  }
  return <div id={id} className="asr-nas-details" aria-label="NAS files by role">
    {delivery.reason === "nas_not_configured" ? <p className="cast-muted">NAS mirroring is not configured.</p> : null}
    {!delivery.files.length ? <p className="cast-muted">No recordings for speaking roles.</p> : null}
    {Array.from(groups, ([role, files]) => <section key={role}>
      <strong>{role}</strong>
      <ul>
        {files.map((file) => <li key={file.audio_id}>
          <span className="asr-nas-filename">{file.filename || "Unnamed file"}</span>
          <span className={file.state === "error" ? "asr-broken" : "cast-muted"}>
            {labels[file.state]}{file.error ? `: ${file.error}` : ""}
          </span>
          {file.verified_at ? <time className="cast-muted" dateTime={file.verified_at}>
            {new Date(file.verified_at).toLocaleString()}
          </time> : null}
        </li>)}
      </ul>
    </section>)}
  </div>;
}
