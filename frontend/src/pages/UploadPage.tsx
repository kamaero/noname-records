import { PageHeader } from "../ui";
import { UploadPanel } from "../components/upload/UploadPanel";

/** /upload — the same inline form the Library opens, on its own page. */
export function UploadPage() {
  return (
    <div className="lib">
      <PageHeader title="Upload book" subtitle="Turn a book text into an annotated script." back={{ to: "/", label: "Library" }} />
      <section className="ui-card ui-card-pad" aria-label="Book upload">
        <UploadPanel autoFocus />
      </section>
    </div>
  );
}
