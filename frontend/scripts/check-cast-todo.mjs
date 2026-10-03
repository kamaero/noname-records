// Проверка чистого модуля списка дел по касту: сортировка, фильтры и лимиты
// решают, что оператор видит первым, открыв книгу. Запуск: node scripts/check-cast-todo.mjs
import { buildSync } from "esbuild";
import assert from "node:assert/strict";
import fs from "node:fs";

const out = buildSync({
  entryPoints: [new URL("../src/viewModels/booksCast.ts", import.meta.url).pathname],
  bundle: true, format: "esm", write: false, platform: "neutral",
});
const m = await import("data:text/javascript;base64," + Buffer.from(out.outputFiles[0].text).toString("base64"));

function role(name, lines, actor, extra = {}) {
  return {
    character_id: name, char_map_id: name, name, is_narrator: false,
    race: "", temperament: "", actor_name: actor,
    appears_in_chapters: [], chapter_count: 0, lines_count: lines,
    approx_seconds: 0, fact_seconds: 0, calc_mode: "",
    character_color: "", character_text_color: "", character_font_weight: "700", character_font_style: "normal",
    rate_rub: 0, total_rub: 0,
    ...extra,
  };
}

function crossing(actor, roleA, roleB, chapter, acknowledged) {
  return {
    actor, role_a: roleA, role_b: roleB, distance: 1, chapter, chapters: [chapter],
    severity: "dialogue", same_race: false, acknowledged, reason: "", acknowledged_by: "", acknowledged_at: "",
  };
}

// сортировка по числу реплик, по убыванию
{
  const rows = [role("Ринора", 51, ""), role("Хаколва", 110, ""), role("Диона", 49, "")];
  const r = m.castTodo(rows, []);
  assert.deepEqual(r.freeRoles.map((x) => x.name), ["Хаколва", "Ринора", "Диона"]);
}

// рассказчик исключён, даже если у него нет актёра
{
  const rows = [role("Рассказчик", 9999, "", { is_narrator: true }), role("Бапдиг", 800, "")];
  const r = m.castTodo(rows, []);
  assert.deepEqual(r.freeRoles.map((x) => x.name), ["Бапдиг"]);
}

// роль с актёром не входит в список дел
{
  const rows = [role("Бапдиг", 800, "Михаил Пестов"), role("Гамук", 400, "")];
  const r = m.castTodo(rows, []);
  assert.deepEqual(r.freeRoles.map((x) => x.name), ["Гамук"]);
}

// лимит и счёт «ещё N»
{
  const rows = Array.from({ length: 8 }, (_, i) => role(`Роль ${i}`, 100 - i, ""));
  const r = m.castTodo(rows, [], { roles: 5, crossings: 3 });
  assert.equal(r.freeRoles.length, 5);
  assert.deepEqual(r.freeRoles.map((x) => x.name), ["Роль 0", "Роль 1", "Роль 2", "Роль 3", "Роль 4"]);
  assert.equal(r.freeMore, 3);
}

// признанные пересечения исключены, лимит и «ещё N» считаются по непризнанным
{
  const items = [
    crossing("Румянцев Константин", "Кимиуб", "Вубрекракник", 15, false),
    crossing("Филатов Павел", "Пазузу", "Дасда", 13, true),
    crossing("Сомов Роман", "Сатухух", "Куйбу Дегатти", 36, false),
    crossing("Иванов Иван", "Один", "Два", 5, false),
    crossing("Петров Пётр", "Три", "Четыре", 7, false),
  ];
  const r = m.castTodo([], items, { roles: 5, crossings: 3 });
  assert.equal(r.crossings.length, 3);
  assert.deepEqual(r.crossings.map((x) => x.actor), ["Румянцев Константин", "Сомов Роман", "Иванов Иван"]);
  assert.equal(r.crossingsMore, 1);
  // ровно те поля, которые нужны строке «<actor>: <role_a> ↔ <role_b> · гл. <chapter>»
  assert.deepEqual(Object.keys(r.crossings[0]).sort(), ["actor", "chapter", "role_a", "role_b"]);
}

// пустой случай: всё назначено, пересечений нет
{
  const rows = [role("Бапдиг", 800, "Михаил Пестов")];
  const r = m.castTodo(rows, []);
  assert.deepEqual(r, { freeRoles: [], freeMore: 0, crossings: [], crossingsMore: 0 });
}

console.log("booksCast.ts (castTodo): все проверки прошли");

// ---- реальный ответ /cast — не проверка, а печать первых пяти ролей без актёра ----
const real = JSON.parse(fs.readFileSync("/tmp/claude-0/castdbg/cast.json", "utf-8"));
const realTodo = m.castTodo(real.characters, [], { roles: 5, crossings: 3 });
console.log("Реальный payload — первые 5 ролей без актёра (ожидание: Хаколва 110, Ринора 51, Диона 49, Еома 43, Иббупус 33):");
for (const row of realTodo.freeRoles) {
  console.log(`  ${row.name} — ${row.lines_count}`);
}
