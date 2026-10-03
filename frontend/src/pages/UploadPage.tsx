import { PageHeader } from "../ui";
import { UploadPanel } from "../components/upload/UploadPanel";

/** /upload — the same inline form the Library opens, on its own page. */
export function UploadPage() {
  return (
    <div className="lib">
      <PageHeader title="Загрузить книгу" subtitle="Текст книги станет размеченным сценарием." back={{ to: "/", label: "Библиотека" }} />
      <section className="ui-card ui-card-pad" aria-label="Загрузка книги">
        <UploadPanel autoFocus />
      </section>
    </div>
  );
}
