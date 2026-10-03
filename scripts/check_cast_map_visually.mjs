#!/usr/bin/env node
/**
 * Проверка объединённого экрана «Каст» (роли + карта персонажей в одной
 * таблице) по НАСТОЯЩЕМУ собранному бандлу (`frontend/dist`), с ответами
 * `/api/**` из заглушек. У фронта нет ни одного теста — ни vitest, ни
 * `*.test.tsx` — поэтому это единственная честная проверка.
 *
 * Приём такой же, как в reference_frontend_visual_check из памяти проекта:
 * Playwright перехватывает КАЖДЫЙ запрос страницы (`page.route("**\/*")`) и
 * отдаёт либо файл из `dist/`, либо заготовленный JSON — без дев-сервера,
 * без логина, без сети.
 *
 * Пять проверок из брифа задачи 10 плюс шестая (по ревью: признанное
 * пересечение не должно ни гореть чипом, ни считаться заново) и седьмая (по
 * финальному ревью: пустая заявка извлечения — не расхождение), каждая
 * печатает свою строку и по провалу заваливает процесс ненулевым кодом —
 * это НЕ декоративный скрипт.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DIST = path.join(ROOT, "frontend", "dist");
/* Скриншоты — во временный каталог, вычисляемый на месте: абсолютный путь чужой
   сессии в коммите означает, что завтра скрипт не запустится ни у кого, включая
   автора. Переопределяется `CAST_CHECK_SHOTS`, если смотреть их хочется рядом. */
const SHOTS = process.env.CAST_CHECK_SHOTS || path.join(os.tmpdir(), "cast-map-check");

if (!existsSync(path.join(DIST, "index.html"))) {
  console.error("дист не собран: `cd frontend && npm run build` перед проверкой");
  process.exit(1);
}
mkdirSync(SHOTS, { recursive: true });

/* ------------------------------------------------------------------ */
/* Поиск playwright и chromium. Ни того, ни другого нет в зависимостях  */
/* репозитория — оба ставятся через `npx playwright`, а каталог npx-кэша */
/* назван хэшем, который меняется от установки к установке. Поэтому не  */
/* жёсткая строка, а перебор: явная переменная окружения → обычное      */
/* разрешение модулей → кэш npx → глобальные node_modules.             */
/* ------------------------------------------------------------------ */

function* candidateModuleDirs() {
  if (process.env.PLAYWRIGHT_MODULE) yield process.env.PLAYWRIGHT_MODULE;
  const roots = [
    path.join(os.homedir(), ".npm", "_npx"),
    process.env.npm_config_cache ? path.join(process.env.npm_config_cache, "_npx") : "",
  ].filter(Boolean);
  for (const root of roots) {
    let entries = [];
    try { entries = readdirSync(root); } catch { continue; }
    for (const entry of entries) yield path.join(root, entry, "node_modules", "playwright");
  }
  for (const root of ["/usr/local/lib/node_modules", "/usr/lib/node_modules"]) {
    yield path.join(root, "playwright");
  }
}

async function loadChromium() {
  const require = createRequire(import.meta.url);
  try {
    return (await import("playwright")).chromium;
  } catch { /* в репозитории playwright не зависимость — ищем дальше */ }
  const tried = [];
  for (const dir of candidateModuleDirs()) {
    tried.push(dir);
    let entry = dir;
    try {
      if (statSync(dir).isDirectory()) entry = require.resolve(path.join(dir, "index.mjs"));
    } catch { continue; }
    try {
      return (await import(pathToFileURL(entry).href)).chromium;
    } catch { /* следующий кандидат */ }
  }
  console.error(
    "playwright не найден. Поставьте его (`npx playwright@latest install chromium`) "
    + "или укажите путь к модулю в PLAYWRIGHT_MODULE.\nСмотрел: " + tried.join(", "),
  );
  process.exit(1);
}

/** Исполняемый chromium из кэша playwright: имя каталога содержит номер ревизии,
    который меняется с каждой версией — берём самую свежую из найденных. */
function findChromium() {
  if (process.env.PLAYWRIGHT_CHROMIUM_PATH) return process.env.PLAYWRIGHT_CHROMIUM_PATH;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(os.homedir(), ".cache", "ms-playwright");
  let entries = [];
  try { entries = readdirSync(root); } catch { return ""; }
  const found = [];
  for (const entry of entries) {
    if (!/^chromium/.test(entry)) continue;
    for (const relative of [
      ["chrome-headless-shell-linux64", "chrome-headless-shell"],
      ["chrome-linux", "chrome"],
      ["chrome-linux64", "chrome"],
    ]) {
      const candidate = path.join(root, entry, ...relative);
      if (existsSync(candidate)) found.push({ entry, candidate });
    }
  }
  // «chromium_headless_shell-1223» → 1223; свежая ревизия впереди старой
  const revision = (name) => Number((name.match(/(\d+)$/) || [0, 0])[1]);
  found.sort((a, b) => revision(b.entry) - revision(a.entry));
  return found.length ? found[0].candidate : "";
}

const chromium = await loadChromium();

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".json": "application/json; charset=utf-8",
  ".woff2": "font/woff2",
  ".ico": "image/x-icon",
};

/* ------------------------------------------------------------------ */
/* Заглушки: книга «Крылья полумрака», книга с id "test".              */
/* ------------------------------------------------------------------ */

const BOOK_ID = "test";
const NOW = "2026-09-09T10:00:00Z";

// Гамук × Тупуг — одна пара, один актёр («Гончаров Иван»), диалог в 1
// абзаце, одна раса. Именно эта пара должна дать флажок «диалог» в таблице.
const TAKIL_CHAPTERS = [12, 13, 14, 25, 26, 40, 41, 42, 55, 56];
const ROKIL_CHAPTERS = [26, 40];
// Дгарнин — расхождение глав 45 (разметка) против 46 (извлечение).
const DZIMVEL_MARKUP_CHAPTERS = Array.from({ length: 45 }, (_, i) => i + 1);
const DZIMVEL_CLAIMED_CHAPTERS = Array.from({ length: 46 }, (_, i) => i + 1);
const NARRATOR_CHAPTERS = Array.from({ length: 70 }, (_, i) => i + 1);

function budgetCharacter(over) {
  return {
    character_id: over.character_id,
    char_map_id: over.character_id,
    name: over.name,
    is_narrator: Boolean(over.is_narrator),
    race: over.race || "",
    temperament: over.temperament || "",
    note: over.note || "",
    actor_name: over.actor_name || "",
    appears_in_chapters: over.chapters,
    chapter_count: over.chapters.length,
    lines_count: over.lines_count,
    approx_seconds: over.lines_count * 6,
    fact_seconds: over.lines_count * 6,
    calc_mode: "rate",
    manual_rate_rub_per_min: over.manual_rate ?? null,
    manual_fixed_rub: null,
    character_color: over.character_color || "#13333B",
    character_text_color: over.character_text_color || "#D7FFE9",
    character_font_weight: "700",
    character_font_style: "normal",
    total_rub: over.total_rub,
  };
}

const CHARACTERS = [
  {
    character_id: "char-narrator", name: "Рассказчик", is_narrator: true,
    actor_name: "Широкова Анна", chapters: NARRATOR_CHAPTERS, lines_count: 3120,
    total_rub: 748800, race: "", temperament: "",
  },
  {
    character_id: "char-dgarnin", name: "Дгарнин",
    actor_name: "Ким Наталья", chapters: DZIMVEL_MARKUP_CHAPTERS, lines_count: 612,
    total_rub: 293760, race: "фархеррим (низший демон)", temperament: "въедливый, торопится договорить за собеседника",
  },
  {
    character_id: "char-gamuk", name: "Гамук",
    actor_name: "Гончаров Иван", chapters: TAKIL_CHAPTERS, lines_count: 214,
    total_rub: 128400, manual_rate: 1500,
    race: "фархеррим (высший демон)", temperament: "холоден, говорит мало и веско",
    note: "тембр ниже среднего, без надрыва",
  },
  {
    character_id: "char-vetziog", name: "Ветциог",
    actor_name: "Дроздов Пётр", chapters: [3, 4, 5, 26], lines_count: 150,
    total_rub: 90000, race: "фархеррим (низший демон)", temperament: "",
  },
  {
    character_id: "char-tupug", name: "Тупуг",
    actor_name: "Гончаров Иван", chapters: ROKIL_CHAPTERS, lines_count: 58,
    total_rub: 34800, race: "фархеррим (высший демон)", temperament: "младший, оттого дерзкий",
  },
  {
    character_id: "char-ueldo", name: "Уэлдо",
    actor_name: "", chapters: [3], lines_count: 2,
    total_rub: 0, race: "", temperament: "",
  },
  // Вторая пара одного актёра — признанная («близнецы»), чтобы отличать
  // «пересечение есть, но решено» от «пересечения нет». char-vetziog делит
  // с ней и актёра, и главу 4 — иначе пары не из чего считать.
  {
    character_id: "char-yasmina", name: "Ясмина",
    actor_name: "Дроздов Пётр", chapters: [4, 61], lines_count: 9,
    total_rub: 5400, race: "фархеррим (низший демон)", temperament: "",
  },
];

const BUDGET_RESPONSE = {
  ok: true,
  book_id: BOOK_ID,
  book_title: "Крылья полумрака",
  book_annotation: "",
  default_rate_rub_per_min: 1200,
  usd_rub_rate: 90,
  urls: { recording: `/recording?book=${BOOK_ID}` },
  budget: {
    narrative_cost_rub: 45000,
    sound_engineer_cost_rub: 20000,
    extra_cost_rub: 5000,
    notes: "",
    narrator_actor_name: "Широкова Анна",
    updated_at: NOW,
  },
  characters: CHARACTERS.map(budgetCharacter),
  totals: {
    character_total_rub: CHARACTERS.filter((c) => !c.is_narrator).reduce((a, c) => a + c.total_rub, 0),
    narrator_total_rub: 748800,
    sound_engineer_cost_rub: 20000,
    extra_cost_rub: 5000,
    grand_total_rub: CHARACTERS.reduce((a, c) => a + c.total_rub, 0) + 20000 + 5000 + 45000,
  },
};

const CAST_RESPONSE = {
  ok: true,
  book_id: BOOK_ID,
  characters: CHARACTERS.map((c) => ({
    character_id: c.character_id,
    char_map_id: c.character_id,
    name: c.name,
    is_narrator: Boolean(c.is_narrator),
    actor_name: c.actor_name || "",
    mine: false,
    race: c.race || "",
    temperament: c.temperament || "",
    note: c.note || "",
    appears_in_chapters: c.chapters,
    lines_count: c.lines_count,
    character_color: "#13333B",
    character_text_color: "#D7FFE9",
    character_font_weight: "700",
    character_font_style: "normal",
  })),
};

function mapRow(over) {
  const claimed = over.claimedChapters || over.chapters;
  return {
    character_id: over.character_id,
    name: over.name,
    in_cast: true,
    in_markup: true,
    lines: over.lines_count,
    chapters: over.chapters.length,
    chapter_numbers: over.chapters,
    has_audio: false,
    is_placeholder: false,
    actor_name: over.actor_name || "",
    actor_tentative: false,
    claimed_chapters: claimed,
    canon_status: over.canon_status ?? "known",
    race: over.race || "",
    age: over.age || "",
    temperament: over.temperament || "",
    voice: "",
    aliases: over.aliases || "",
    updated_at: NOW,
  };
}

const CHARACTER_MAP_RESPONSE = {
  ok: true,
  rows: [
    // У рассказчика заявки нет вовсе (`appears_in` пуст) — на живой книге так у
    // 58 ролей из 201, и это НЕ расхождение: извлечение про роль промолчало, а не
    // назвало ноль глав. Проверка 7 держит именно этот случай.
    mapRow({ ...CHARACTERS[0], age: "", claimedChapters: [] }),
    mapRow({
      ...CHARACTERS[1],
      age: "неизвестен, выглядит немолодым",
      claimedChapters: DZIMVEL_CLAIMED_CHAPTERS,
      canon_status: "known",
    }),
    mapRow({ ...CHARACTERS[2], age: "около трёх тысяч лет", aliases: "Гамук-полумрак" }),
    mapRow({ ...CHARACTERS[3], age: "", canon_status: "new" }),
    mapRow({ ...CHARACTERS[4], age: "младше Гамука на пару веков" }),
    mapRow({ ...CHARACTERS[5], age: "", canon_status: "" }),
    mapRow({ ...CHARACTERS[6], age: "", canon_status: "new" }),
  ],
  intersections: [
    {
      actor: "Гончаров Иван",
      role_a: "Гамук",
      role_b: "Тупуг",
      distance: 1,
      chapter: 26,
      chapters: [26, 40],
      severity: "dialogue",
      same_race: true,
      acknowledged: false,
      reason: "",
      acknowledged_by: "",
      acknowledged_at: "",
    },
    // Признанная пара: владелец уже решил, что Ветциог и Ясмина — «близнецы»,
    // и это решение не должно ни гореть флажком в таблице, ни попадать в
    // счётчик непризнанных пересечений.
    {
      actor: "Дроздов Пётр",
      role_a: "Ветциог",
      role_b: "Ясмина",
      distance: 4,
      chapter: 4,
      chapters: [4],
      severity: "scene",
      same_race: true,
      acknowledged: true,
      reason: "близнецы",
      acknowledged_by: "Max Ray",
      acknowledged_at: "2026-09-09T07:12:51Z",
    },
  ],
  checked_at: "",
  checked_by: "",
};

const AUDITIONS_RESPONSE = {
  ok: true,
  items: [
    {
      id: "aud-1",
      role: "Гамук",
      actor_name: "Гончаров Иван",
      original_filename: "gamuk_proba_01.mp3",
      canonical_filename: "aud-1.mp3",
      mime_type: "audio/mpeg",
      size_bytes: 245_760,
      uploaded_at: NOW,
    },
  ],
  can_approve: true,
};

const ME_RESPONSE = {
  authenticated: true,
  uid: "u1",
  login: "director",
  display_name: "Директор студии",
  roles: ["admin"],
  auth_source: "session",
  is_owner_telegram: false,
  workspace_tabs: [],
  full_access: true,
};

const API_ROUTES = new Map([
  ["/api/me", ME_RESPONSE],
  [`/api/budget/${BOOK_ID}`, BUDGET_RESPONSE],
  [`/api/v2/books/${BOOK_ID}/cast`, CAST_RESPONSE],
  [`/api/v2/books/${BOOK_ID}/character-map`, CHARACTER_MAP_RESPONSE],
  [`/api/v2/books/${BOOK_ID}/auditions`, AUDITIONS_RESPONSE],
]);

/* ------------------------------------------------------------------ */

async function serveRoute(route) {
  const request = route.request();
  const url = new URL(request.url());
  const pathname = url.pathname;

  if (pathname.startsWith("/api/")) {
    if (API_ROUTES.has(pathname)) {
      return route.fulfill({ status: 200, contentType: "application/json; charset=utf-8", body: JSON.stringify(API_ROUTES.get(pathname)) });
    }
    // POST'ы (переименование, признание пересечения и т.п.) в этой проверке не
    // нужны — печатаем и отвечаем нейтральным успехом, чтобы не завалить UI.
    if (request.method() !== "GET") {
      let body = "";
      try { body = request.postData() || ""; } catch { /* no body */ }
      console.log(`  [POST ${pathname}] ${body}`);
      return route.fulfill({ status: 200, contentType: "application/json; charset=utf-8", body: JSON.stringify({ ok: true }) });
    }
    console.log(`  [неизвестная ручка] ${pathname}`);
    return route.fulfill({ status: 404, contentType: "application/json; charset=utf-8", body: JSON.stringify({ ok: false, error: "not_found" }) });
  }

  // Всё остальное — файлы собранного бандла. `base: "/app/"` в vite.config.ts,
  // поэтому и статика, и SPA-маршруты живут под /app/.
  let relative = pathname.startsWith("/app/") ? pathname.slice("/app/".length) : pathname.replace(/^\//, "");
  let filePath = path.join(DIST, relative);
  const ext = path.extname(filePath);
  if (!ext || !existsSync(filePath)) {
    filePath = path.join(DIST, "index.html");
  }
  const body = readFileSync(filePath);
  const contentType = MIME[path.extname(filePath)] || "application/octet-stream";
  return route.fulfill({ status: 200, contentType, body });
}

/* ------------------------------------------------------------------ */

const results = [];
function record(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? "ЗЕЛЁНО" : "КРАСНО"} · ${name}${detail ? ` — ${detail}` : ""}`);
}

async function main() {
  // Сначала — то, что playwright считает своим браузером; если его версия не
  // совпала с тем, что лежит в кэше, берём найденный вручную. Не нашлось ничего —
  // говорим об этом словами, а не падаем стеком из недр playwright.
  const executablePath = findChromium();
  let browser;
  try {
    browser = await chromium.launch();
  } catch (error) {
    if (!executablePath) {
      console.error(
        "chromium для playwright не найден. Поставьте его (`npx playwright install chromium`) "
        + "или укажите PLAYWRIGHT_CHROMIUM_PATH.\nОшибка запуска: " + error.message,
      );
      process.exit(1);
    }
    browser = await chromium.launch({ executablePath });
  }
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: "dark" });
    await context.route("**/*", serveRoute);
    const page = await context.newPage();

    await page.goto("https://noname.local/app/books/test/cast", { waitUntil: "networkidle" });
    await page.waitForSelector("text=Гамук", { timeout: 15000 });

    /* --- Проверка 1: 1440×1000, тёмная — флажок «диалог» виден ------- */
    const theme1 = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
    const crossBadge = page.locator(".cast-cross:has-text(\"диалог\")").first();
    const crossVisible = (await crossBadge.count()) > 0 && (await crossBadge.isVisible());
    await page.screenshot({ path: path.join(SHOTS, "1-dark-1440.png") });
    record(
      "1440×1000 тёмная: флажок «диалог» виден",
      theme1 === "dark" && crossVisible,
      `data-theme=${theme1}, badge visible=${crossVisible}`,
    );

    /* --- Проверка 2: развёрнутая строка -------------------------------- */
    const toggle = page.getByRole("button", { name: "Развернуть роль Гамук" });
    await toggle.click();
    const detail = page.locator(".rdr").first();
    await detail.waitFor({ state: "visible", timeout: 5000 });
    const detailTextRaw = await detail.innerText();
    // `toLocaleString("ru-RU")` разделяет тысячи неразрывным пробелом (U+00A0) —
    // нормализуем, чтобы сравнение не зависело от вида пробела в разметке.
    const detailText = detailTextRaw.replace(/[\u00A0\u202F]/g, " ");
    await page.screenshot({ path: path.join(SHOTS, "2-expanded-gamuk.png") });
    const hasRace = detailText.includes("фархеррим (высший демон)");
    const hasAge = detailText.includes("около трёх тысяч лет");
    const hasRate = detailText.includes("1500 ₽/мин");
    const hasSum = detailText.includes("128 400");
    const hasIntersection = detailText.includes("Тупуг") && detailText.includes("диалог") && detailText.includes("одна раса");
    record(
      "развёрнутая строка: раса, возраст, ставка, сумма, карточка пересечения",
      hasRace && hasAge && hasRate && hasSum && hasIntersection,
      `race=${hasRace} age=${hasAge} rate=${hasRate} sum=${hasSum} intersection=${hasIntersection}`,
    );

    /* --- Проверка 3: светлая тема -------------------------------------- */
    await page.getByTitle("Светлая тема").click();
    await page.waitForTimeout(300); // переживает 120ms CSS-переход темы, чтобы скрин не попал в полутон
    const theme3 = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
    await page.screenshot({ path: path.join(SHOTS, "3-light-1440.png") });
    record("1440×1000 светлая тема применена", theme3 === "light", `data-theme=${theme3}`);

    /* --- Проверка 4: 390×844 — нет горизонтальной прокрутки ------------ */
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(200);
    const { scrollWidth, clientWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    await page.screenshot({ path: path.join(SHOTS, "4-mobile-390.png") });
    record(
      "390×844: без горизонтальной прокрутки",
      scrollWidth <= clientWidth + 1,
      `scrollWidth=${scrollWidth} clientWidth=${clientWidth}`,
    );

    /* --- Проверка 5: /roles → /cast ------------------------------------- */
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("https://noname.local/app/books/test/roles", { waitUntil: "networkidle" });
    await page.waitForSelector("text=Гамук", { timeout: 15000 });
    const finalPath = await page.evaluate(() => window.location.pathname);
    await page.screenshot({ path: path.join(SHOTS, "5-redirect.png") });
    record("/books/test/roles перенаправляет на /books/test/cast", finalPath === "/app/books/test/cast", `path=${finalPath}`);

    /* --- Проверка 6: признанная пара не горит и не считается заново ----- */
    // Ветциог × Ясмина уже признаны («близнецы») — в отличие от Гамук × Тупуг,
    // которая ещё ждёт решения. Признанная пара обязана: (а) не рисовать чип
    // в своей строке, (б) не расти счётчик кнопки «Пересечения», (в) не
    // попадать под фильтр по этой кнопке.
    const vetziogCrossCell = page.locator("tr").filter({ hasText: "Ветциог" }).first().locator("td.cast-col-cross");
    const vetziogCrossText = (await vetziogCrossCell.innerText()).trim();
    const vetziogHasChip = (await vetziogCrossCell.locator(".cast-cross").count()) > 0;

    // Не просто «Пересечения» — так называются ещё и чипы в ячейках таблицы
    // (`aria-label="Пересечения роли …"`); нужна именно кнопка-тумблер в
    // тулбаре (`.cast-toggle`), а не любой элемент с этим словом в имени.
    const crossButton = page.locator("button.cast-toggle").filter({ hasText: "Пересечения" });
    const crossLabel = (await crossButton.textContent() || "").replace(/\s+/g, " ").trim();

    await crossButton.click();
    await page.waitForTimeout(150);
    const roleCellsAfterFilter = await page.locator("td.cast-col-role").allTextContents();
    await page.screenshot({ path: path.join(SHOTS, "6-acknowledged-pair.png") });
    await crossButton.click(); // возвращаем фильтр, чтобы не путать следующий прогон

    const dashNotChip = vetziogCrossText === "—" && !vetziogHasChip;
    const counterUnaffected = crossLabel === "Пересечения · 1";
    const filterExcludesAcknowledged =
      roleCellsAfterFilter.some((text) => text.includes("Гамук"))
      && roleCellsAfterFilter.some((text) => text.includes("Тупуг"))
      && !roleCellsAfterFilter.some((text) => text.includes("Ветциог"))
      && !roleCellsAfterFilter.some((text) => text.includes("Ясмина"));
    record(
      "признанная пара «Ветциог × Ясмина»: без чипа в строке, без прироста счётчика, вне фильтра «Пересечения»",
      dashNotChip && counterUnaffected && filterExcludesAcknowledged,
      `cross-cell="${vetziogCrossText}" chip=${vetziogHasChip} counter="${crossLabel}" filteredRows=${JSON.stringify(roleCellsAfterFilter.map((t) => t.split("\n")[0]))}`,
    );

    /* --- Проверка 7: пустая заявка извлечения — не расхождение --------- */
    // Колонка «Глав» показывает «размечено/заявлено» янтарём. `claimed_chapters`
    // пуст в двух РАЗНЫХ случаях: «назвали ноль глав» и «про роль не говорили».
    // Второе — 58 ролей из 201 на живой книге, включая рассказчика первой
    // строкой; зажигать на них янтарь значит утопить настоящие расхождения.
    // Поэтому проверяются обе стороны сразу: рассказчик без заявки — голое
    // число, Дгарнин с заявкой 46 против размеченных 45 — янтарь и номер главы.
    const narratorChapters = page.locator("tr").filter({ hasText: "Рассказчик" }).first().locator("td.cast-col-chapters");
    const narratorText = (await narratorChapters.innerText()).trim();
    const narratorAmber = (await narratorChapters.locator(".is-mismatch").count()) > 0;
    const dgarninChapters = page.locator("tr").filter({ hasText: "Дгарнин" }).first().locator("td.cast-col-chapters");
    const dgarninText = (await dgarninChapters.innerText()).trim();
    const dgarninAmber = (await dgarninChapters.locator(".is-mismatch").count()) > 0;
    const dgarninTitle = (await dgarninChapters.locator("span").first().getAttribute("title")) || "";
    record(
      "колонка «Глав»: пустая заявка — не расхождение, настоящее расхождение называет главу",
      narratorText === "70" && !narratorAmber
      && dgarninText === "45/46" && dgarninAmber && dgarninTitle.includes("нет разметки в главе 46"),
      `рассказчик="${narratorText}" янтарь=${narratorAmber} · Дгарнин="${dgarninText}" янтарь=${dgarninAmber} title="${dgarninTitle}"`,
    );

    await context.close();
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log("");
  console.log(`Итог: ${results.length - failed.length}/${results.length} зелёных`);
  if (failed.length) {
    console.log("Провалено:");
    for (const item of failed) console.log(`  - ${item.name}`);
    process.exit(1);
  }
  process.exit(0);
}

main().catch((error) => {
  console.error("Скрипт упал:", error);
  process.exit(1);
});
