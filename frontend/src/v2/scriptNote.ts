// Ссылки на абзацы в отдельной строке заметки выводятся рядом с кнопкой
// редактирования. Ссылками становятся только адреса абзацев внутри Читалки.
const SCRIPT_REFERENCE = /^\[([^\]]+)\]\((\/reader\/[A-Za-z0-9_-]+\?segment=[A-Za-z0-9_:%-]+)\)$/;

export function splitScriptNote(note: string) {
  const lines: string[] = [];
  const links: { label: string; href: string }[] = [];
  for (const line of note.split("\n")) {
    const reference = SCRIPT_REFERENCE.exec(line.trim());
    if (reference) links.push({ label: reference[1], href: reference[2] });
    else lines.push(line);
  }
  return { text: lines.join("\n").trim(), links };
}
