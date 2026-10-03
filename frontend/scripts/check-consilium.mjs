// Проверка чистого модуля консилиума: у фронта нет тестового раннера, а правила кнопок —
// та часть, где ошибка ставит чужой голос одним кликом. Запуск: node scripts/check-consilium.mjs
import { buildSync } from "esbuild";
import assert from "node:assert/strict";

const out = buildSync({
  entryPoints: [new URL("../src/v2/consilium.ts", import.meta.url).pathname],
  bundle: true, format: "esm", write: false, platform: "neutral",
});
const m = await import("data:text/javascript;base64," + Buffer.from(out.outputFiles[0].text).toString("base64"));

const cast = new Set(["Бапдиг", "Безымянный алхимик", "Хор", "Гамук", "Тупуг"]);
const base = {
  id: "f", chapter_index: 33, chapter_id: "c33", ordinal: 63, segment_id: "s", span_start: 0, span_end: 5,
  excerpt: "", kind: "wrong_voice", current_speaker: "Бапдиг", readers_speaker: "Безымянный алхимик",
  reader_opus: "Безымянный алхимик", reader_sol: "Безымянный алхимик", arbiter_verdict: "change",
  arbiter_speaker: "Безымянный алхимик", evidence_para: 62, evidence_quote: "тот сказал", evidence_proven: true,
  reason: "", status: "new", decided_by: "", decided_at: "", decided_speaker: "",
};

// доказанная смена: кандидат арбитра первый, выделен, дубли слиты; сценарий последний
let c = m.buildCandidates(base, cast);
assert.deepEqual(c.map((x) => [x.name, x.backers, x.primary, x.keep]), [
  ["Безымянный алхимик", ["arbiter", "readers"], true, false],
  ["Бапдиг", ["script"], false, true],
]);

// не доказано: имя арбитра не выдаётся за его голос, никто не выделен
c = m.buildCandidates({ ...base, arbiter_verdict: "undecidable", evidence_proven: false }, cast);
assert.deepEqual(c.map((x) => [x.name, x.backers, x.primary]), [
  ["Безымянный алхимик", ["readers"], false],
  ["Бапдиг", ["script"], false],
]);

// сценарий подтверждён: арбитр стоит за именем сценария
c = m.buildCandidates({ ...base, arbiter_verdict: "keep_current", arbiter_speaker: "Бапдиг" }, cast);
assert.deepEqual(c.map((x) => [x.name, x.backers, x.primary, x.keep]), [
  ["Бапдиг", ["arbiter", "script"], true, true],
  ["Безымянный алхимик", ["readers"], false, false],
]);

// чтецы врозь: по кнопке на каждого; мусорный ответ и UNSURE кнопкой не становятся
c = m.buildCandidates({ ...base, kind: "readers_split", readers_speaker: "", reader_opus: "Гамук",
  reader_sol: "Гамук, Тупуг, Бапдиг", arbiter_verdict: "undecidable", evidence_proven: false }, cast);
assert.deepEqual(c.map((x) => x.name), ["Гамук", "Бапдиг"]);
assert.equal(m.readerAnswer("Гамук, Тупуг, Бапдиг", cast), "ответ невнятный");
assert.equal(m.readerAnswer("UNSURE", cast), "не уверен");
assert.equal(m.readerAnswer("Рассказчик", cast), "Рассказчик");

// порядок: род, затем доказанная смена → сценарий подтверждён → не доказано, затем глава/абзац
const items = [
  { ...base, id: "a", kind: "narrator_border", arbiter_verdict: "undecidable", evidence_proven: false, chapter_index: 1 },
  { ...base, id: "b", arbiter_verdict: "undecidable", evidence_proven: false, chapter_index: 2 },
  { ...base, id: "c", arbiter_verdict: "keep_current", chapter_index: 9 },
  { ...base, id: "d", chapter_index: 40 },
  { ...base, id: "e", chapter_index: 3 },
];
assert.deepEqual(m.sortFindings(items).map((x) => x.id), ["e", "d", "c", "b", "a"]);

// следующая: по порядку, только нерешённые, по кругу, не сама себя
const withDone = items.map((x) => (x.id === "c" ? { ...x, status: "accepted" } : x));
assert.equal(m.nextFinding(withDone, "d")?.id, "b");
assert.equal(m.nextFinding(withDone, "a")?.id, "e");
assert.equal(m.nextFinding([{ ...base, id: "z" }], "z"), null);

// подписи
assert.equal(m.findingTag(base), "Бапдиг → Безымянный алхимик · доказано");
assert.equal(m.findingTag({ ...base, arbiter_verdict: "keep_current" }), "Бапдиг → Безымянный алхимик · сценарий подтверждён");
assert.equal(m.findingTag({ ...base, arbiter_verdict: "undecidable", evidence_proven: false }), "Бапдиг → Безымянный алхимик · не доказано");
assert.equal(m.decisionLine({ ...base, status: "accepted", decided_speaker: "Хор", decided_by: "Оператор", decided_at: "2026-09-13T10:00:00" }), "принято: Хор · Оператор, 13.09");
assert.equal(m.decisionLine({ ...base, status: "dismissed", decided_speaker: "Бапдиг", decided_by: "Оператор", decided_at: "2026-09-13T10:00:00" }), "оставлено как есть · Оператор, 13.09");

// ход прогона
const running = { status: "running", phase: "readers", chapters_total: 60, chapters_done: 23, arbiter_total: 0,
  arbiter_done: 0, spent_rub: 612.4, estimate_rub: 1850, mode: "reread", error: "", result: null };
assert.equal(m.isConsiliumRunActive(running), true);
assert.equal(m.isConsiliumRunActive({ ...running, status: "done" }), false);
assert.equal(m.isConsiliumRunActive(null), false);
assert.equal(m.runProgressLine(running), "Чтецы: гл. 23 из 60 · потрачено 612 ₽ из ≈ 1 850");
assert.equal(m.runProgressLine({ ...running, phase: "arbiter", arbiter_total: 157, arbiter_done: 40 }),
  "Арбитр: 40 из 157 · потрачено 612 ₽ из ≈ 1 850");
assert.equal(m.runProgressLine({ ...running, status: "queued", phase: "queued" }), "В очереди…");
assert.equal(m.runOutcomeLine({ ...running, status: "done", finished_at: "2026-09-14T10:00:00",
  result: { findings: 157, added: 0, updated: 0, gone: 0, arbitrated: 0, spent_rub: 3 } }, { new: 142 }),
  "Консилиум 14.09: 157 находок, нерешённых 142");
assert.equal(m.runOutcomeLine({ ...running, status: "done", finished_at: "2026-09-14T10:00:00", chapters_incomplete: 2,
  result: { findings: 157, added: 0, updated: 0, gone: 0, arbitrated: 0, spent_rub: 3, chapters_incomplete: 2 } }, { new: 142 }),
  "Консилиум 14.09: 157 находок, нерешённых 142 · неполных глав 2");
assert.equal(m.runOutcomeLine({ ...running, status: "done", finished_at: "2026-09-14T10:00:00", chapters_incomplete: 0,
  result: { findings: 1, added: 0, updated: 0, gone: 0, arbitrated: 0, spent_rub: 3, chapters_incomplete: 0 } }, { new: 1 }),
  "Консилиум 14.09: 1 находок, нерешённых 1");
assert.equal(m.runOutcomeLine({ ...running, status: "stopped", error: "превышена смета вдвое" }, { new: 1 }),
  "Консилиум остановлен: превышена смета вдвое");
assert.equal(m.decisionLine({ ...base, status: "gone" }), "снята: место больше не спорное");

console.log("consilium.ts: все проверки прошли");

// звук записанной главы: примечание у имени — только когда есть что сказать
const impacts = {
  recorded: true,
  by_speaker: {
    "Гамук": { state: "rerecord", level: "warn", text: "Глава записана — реплика лежит у роли «Тупуг»." },
    "Тупуг": { state: "borrow", level: "info", text: "Звук возьмём из файла роли «Бапдиг»." },
    "Хор": { state: "not_recorded", level: "info", text: "" },
  },
};
assert.deepEqual(m.impactNote(impacts, "Гамук"), { level: "warn", text: "Глава записана — реплика лежит у роли «Тупуг»." });
assert.deepEqual(m.impactNote(impacts, "Тупуг"), { level: "info", text: "Звук возьмём из файла роли «Бапдиг»." });
assert.equal(m.impactNote(impacts, "Хор"), null);
assert.equal(m.impactNote(impacts, "Нет такой"), null);
assert.equal(m.impactNote(undefined, "Гамук"), null);
assert.equal(m.impactNote({ recorded: false, by_speaker: {} }, "Гамук"), null);

// тост после ручной правки: самое тревожное первым, пусто — ничего
assert.equal(m.reassignImpactToast([]), null);
assert.deepEqual(
  m.reassignImpactToast([
    { state: "borrow", level: "info", text: "A", span_start: 0, span_end: 1, from_role: "Р", to_role: "Т" },
    { state: "rerecord", level: "warn", text: "B", span_start: 2, span_end: 3, from_role: "Р", to_role: "Х" },
  ]),
  { tone: "error", title: "Звук главы: нужна дозапись", detail: "B\nA" },
);
assert.deepEqual(
  m.reassignImpactToast([{ state: "borrow", level: "info", text: "A", span_start: 0, span_end: 1, from_role: "Р", to_role: "Т" }]),
  { tone: "info", title: "Звук главы", detail: "A" },
);
console.log("consilium impact ok");
