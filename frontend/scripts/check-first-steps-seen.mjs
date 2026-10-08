// Отметка «результат открыт»: сбой сети не съедает её, успех и 403 не повторяются.
// node scripts/check-first-steps-seen.mjs
import { buildSync } from "esbuild";
import assert from "node:assert/strict";
const out = buildSync({ entryPoints: [new URL("../src/utils/firstStepsSeen.ts", import.meta.url).pathname], bundle: true, format: "esm", write: false, platform: "neutral" });
const m = await import("data:text/javascript;base64," + Buffer.from(out.outputFiles[0].text).toString("base64"));
const sent = [];
let next = [];
function reply(status) { return { ok: status < 300, status, headers: new Map([["content-type", "application/json"]]), json: async () => ({ ok: true }), text: async () => "{}" }; }
globalThis.fetch = async (url, init) => { sent.push(JSON.parse(init.body).book_id); const r = next.shift(); if (r === "net") throw new TypeError("network"); return reply(r); };
const tick = () => new Promise((r) => setTimeout(r, 0));
// 1) сбой сети → следующее открытие отправляет снова
next = ["net", 200];
m.markFirstStepsSeen("b1"); await tick(); await tick();
m.markFirstStepsSeen("b1"); await tick(); await tick();
assert.deepEqual(sent, ["b1", "b1"]);
// 2) после успеха — тишина
m.markFirstStepsSeen("b1"); await tick();
assert.deepEqual(sent, ["b1", "b1"]);
// 3) 403 (диктор) — не повторяем на каждой главе
sent.length = 0; next = [403];
m.markFirstStepsSeen("b2"); await tick(); await tick();
m.markFirstStepsSeen("b2"); await tick();
assert.deepEqual(sent, ["b2"]);
// 4) пока запрос в пути — второго нет
sent.length = 0; next = [200];
m.markFirstStepsSeen("b3"); m.markFirstStepsSeen("b3"); await tick(); await tick();
assert.deepEqual(sent, ["b3"]);
console.log("check-first-steps-seen: 4 сценария ок");
