// Запуск со сверх-лимитом: 409 over_limit → подтверждение с цифрами → повтор с ?override_limit=1.
// node scripts/check-limit-confirm.mjs
import { buildSync } from "esbuild";
import assert from "node:assert/strict";
const out = buildSync({ entryPoints: [new URL("../src/utils/limitConfirm.ts", import.meta.url).pathname], bundle: true, format: "esm", write: false, platform: "neutral" });
const m = await import("data:text/javascript;base64," + Buffer.from(out.outputFiles[0].text).toString("base64"));
const calls = [];
function reply(status, body) { return { ok: status < 300, status, headers: new Map([["content-type", "application/json"]]), json: async () => body, text: async () => JSON.stringify(body) }; }
const over = { error: "over_limit", estimate_rub: 900, left_rub: 600, limit_rub: 5000 };
// 1) нехватка → подтверждение → повтор с override
globalThis.window = { confirm: (t) => { calls.push(["confirm", t]); return true; } };
globalThis.fetch = async (url) => { calls.push(["fetch", url]); return url.includes("override_limit=1") ? reply(200, { ok: true }) : reply(409, over); };
assert.deepEqual(await m.postWithLimit("/api/v2/books/b1/consilium/run", { mode: "reread" }), { ok: true });
assert.match(calls[1][1], /≈ 900 ₽.*осталось 600 ₽/);
assert.equal(calls[2][1], "/api/v2/books/b1/consilium/run?override_limit=1");
// 2) отказ в подтверждении → ошибка, второго запроса нет
calls.length = 0; globalThis.window.confirm = () => false;
await assert.rejects(m.postWithLimit("/x", {})); assert.equal(calls.filter((c) => c[0] === "fetch").length, 1);
// 3) не админ → понятная 403
globalThis.window.confirm = () => true;
globalThis.fetch = async (url) => url.includes("override") ? reply(403, { error: "override_admin_only" }) : reply(409, over);
await assert.rejects(m.postWithLimit("/x", {}), /только администратор/);
// 4) другая ошибка не трогается
globalThis.fetch = async () => reply(409, { error: "already_running" });
await assert.rejects(m.postWithLimit("/x", {}), (e) => e.status === 409 && e.payload.error === "already_running");
// 5) запущено, но модель без цены — предупреждение показано
const alerts = [];
globalThis.window.alert = (t) => alerts.push(t);
globalThis.fetch = async () => reply(200, { ok: true, spend_warning: "траты не войдут в лимит" });
await m.postWithLimit("/x", {});
assert.deepEqual(alerts, ["траты не войдут в лимит"]);
console.log("postWithLimit: 5 scenarios ok");
