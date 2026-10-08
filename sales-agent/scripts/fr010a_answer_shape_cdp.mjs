// fr010a_answer_shape_cdp.mjs · FR-010-A「回复形态收口」在**真浏览器**里的实测（真 Edge + CDP，零依赖）
//
//   node scripts/fr010a_answer_shape_cdp.mjs http://127.0.0.1:8539
//
// 跑之前先起一个真服务（沙箱目录只是别把本次导入写进真实 state/ 与真实 data/app.db）：
//   .venv/Scripts/python.exe scripts/fr010a_cdp_reset.py      # 先把沙箱建好（表要建在服务启动前）
//   SRA_STATE_DIR=outputs/_cdp_run_fr010a/state SRA_DB_PATH=outputs/_cdp_run_fr010a/app.db \
//   SRA_ORIGINAL_DIR=outputs/_cdp_run_fr010a/original SRA_UPLOAD_DIR=outputs/_cdp_run_fr010a/uploads \
//   SRA_DOC_DIR=outputs/_cdp_run_fr010a/documents SRA_OUTPUT_DIR=outputs/_cdp_run_fr010a/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8539
//
// 为什么必须真开浏览器：派单 §四 第 11 条要求"三条问题真浏览器走一遍 + 截图"。
// 网页上要看的不是"接口返回了什么"，而是：
//   ① 轻量看板**真的画出来了**（`.board-kpi` 的几何，不是 DOM 里有几个字符串）；
//   ② 「11-26 当天无成交」在页面上是**一块警示条**、而**不是一张 0.00 元的卡**；
//   ③ 元信息回答在页面上是**一句话**，不是一屏分析模板。
// 量几何只能用 CDP 的 `getBoundingClientRect` —— `dump-dom` 量不了（这是踩过的坑）。
//
// ★ 提速（派单 §五）：
//   · 本走查只跑**一轮**，两条提问在**同一个浏览器进程**里做完前一条再问后一条；
//   · 页面一打开就会 `refreshAll` 去读主数据集（实测预热 ~300 秒，这期间 GIL 被占满、
//     别的请求全被饿着）—— 用**一次长超时**等它过去（判据是页面 banner 收起来），
//     而不是 sleep 240/420 分段干等；
//   · 需要主数据集的那两条提问只发**一次**（11-25 与 11-26 各一次，绝不轮询）。
// 截图落在 outputs/_cdp_shots/。
//
// ★ CDP 的两条坑（从 fr003/fr009a 那两份探针抄来的实测经验，别再踩一遍）：
//   ① `DOM.getDocument` 必须带 `depth: 1`，否则整棵 DOM 树回来会把连接噎住；
//   ② 一段流程里命令做多了，渲染进程会整体不再应答 → 每段换**全新浏览器进程 + 全新 profile**。
//      文件框必须传**绝对路径**（相对路径会静默地什么都不设）。

const BASE = process.argv[2] || "http://127.0.0.1:8539";
let PORT = 0;
const pickPort = () => 9900 + Math.floor(Math.random() * 400);
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
const ACCOUNT = "13900000005";
const PASSWORD = "abc12345";
const SANDBOX = "outputs/_cdp_run_fr010a";
const FIXTURE = `${SANDBOX}/季度复盘资料.pdf`;
const FILENAME = "季度复盘资料.pdf";

const { spawn, spawnSync } = await import("node:child_process");
const os = await import("node:os");
const path = await import("node:path");
const fs = await import("node:fs");

const edge = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
if (!edge) {
  console.log("❌ 没找到 Edge，无法实测");
  process.exit(1);
}
const SHOT_DIR = process.argv[3] || path.join(process.cwd(), "outputs", "_cdp_shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const listUrl = () => `http://127.0.0.1:${PORT}/json/list`;

let ready = false;
for (let i = 0; i < 300; i++) {
  try {
    const r = await fetch(BASE + "/api/health");
    if (r.status === 200) { ready = true; break; }
  } catch { /* 还没起来 */ }
  await sleep(1000);
}
if (!ready) {
  console.log(`❌ 服务没起来（${BASE}/api/health 一直没返回 200），先起服务再跑本脚本`);
  process.exit(1);
}
if (!fs.existsSync(FIXTURE)) {
  console.log(`❌ 缺少样例文件：${FIXTURE}（先跑 scripts/fr010a_cdp_reset.py）`);
  process.exit(1);
}

// 每次跑都要是干净的一次：重置**本脚本沙箱**（真实 state/ 与真实 data/app.db 永远不碰）。
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const python = process.env.SRA_PYTHON || path.join(process.cwd(), ".venv", "Scripts", "python.exe");
  const reset = spawnSync(python, [path.join("scripts", "fr010a_cdp_reset.py")],
    { cwd: process.cwd(), encoding: "utf-8" });
  if (reset.status !== 0) {
    console.log("❌ 沙箱重置失败：", reset.stdout, reset.stderr);
    process.exit(1);
  }
  console.log(reset.stdout.trim());
}

// ── CDP 基建（与 fr009a 探针同一套）────────────────────────────────────────
const UDDS = [];
function spawnBrowser() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr010a_cdp_"));
  UDDS.push(dir);
  PORT = pickPort();
  const child = spawn(edge, [
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${dir}`,
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--window-size=1440,1100", BASE + "/",
  ], { stdio: "ignore" });
  child.unref();
  return child;
}
let browser = spawnBrowser();

let passed = 0, failed = 0;
function check(ok, label, extra = "") {
  if (ok) passed += 1; else failed += 1;
  console.log(`   ${ok ? "✅" : "❌"} ${label}${extra ? " | " + extra : ""}`);
}

const consoleErrors = [];
const pageTarget = async () => {
  let last = "（一次都没连上调试端口）";
  for (let i = 0; i < 120; i += 1) {
    try {
      const list = await (await fetch(listUrl())).json();
      last = JSON.stringify(list.map((t) => `${t.type}:${t.url}`));
      const found = list.find((t) => t.type === "page" && t.url.startsWith(BASE));
      if (found) return found;
    } catch (err) { last = `（连不上调试端口：${err.message}）`; }
    await sleep(500);
  }
  throw new Error(`没找到页面 target —— 调试端口上最后看到的是：${last}`);
};

async function openSession(onEvent) {
  const target = await pageTarget();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { socket.onopen = res; socket.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  socket.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
    if (msg.method && typeof onEvent === "function") onEvent(msg.method, msg.params || {});
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
      consoleErrors.push((msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "));
    }
    if (msg.method === "Runtime.exceptionThrown") {
      consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    }
  };
  const send = (method, params = {}, ms = 20000) => new Promise((res, rej) => {
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); rej(new Error(`CDP 调用超时：${method}`)); }, ms);
    pending.set(id, (m) => { clearTimeout(timer); res(m.result ?? m.error); });
    socket.send(JSON.stringify({ id, method, params }));
  });
  await send("Runtime.enable");
  await send("Page.enable");
  await send("DOM.enable");
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r?.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval failed");
    return r?.result?.value;
  };
  return { send, evalJs, close: () => { try { socket.close(); } catch { /* 忽略 */ } } };
}

let SESSION = null;
async function currentSession() {
  if (!SESSION) SESSION = await openSession();
  return SESSION;
}
async function step(fn) {
  try {
    return await fn(await currentSession());
  } catch (err) {
    if (!String(err.message).includes("CDP 调用超时")) throw err;
    console.log(`   （连接哑掉，换一条继续：${err.message}）`);
    SESSION = null;
    return await fn(await currentSession());
  }
}

async function restartBrowser() {
  SESSION = null;
  const pid = browser.pid;
  try { browser.kill(); } catch { /* 已经退了 */ }
  try { spawnSync("taskkill", ["/F", "/T", "/PID", String(pid)], { stdio: "ignore" }); } catch { /* 忽略 */ }
  await sleep(1200);
  browser = spawnBrowser();
  await pageTarget();
  await sleep(600);
}

const click = (session, id) =>
  session.evalJs(`document.getElementById(${JSON.stringify(id)}).click()`);

const textOf = (session, id) =>
  session.evalJs(`(() => {
    const el = document.getElementById(${JSON.stringify(id)});
    return el ? el.textContent.replace(/\\s+/g, " ").trim() : null;
  })()`);

/** 等某张验证码图**真的有内容**（`src` 是 data: URI 那种）。先等症状出现，再断言它。 */
async function waitForCaptcha(session, imageId, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ready = await session.evalJs(`(() => {
      const img = document.getElementById(${JSON.stringify(imageId)});
      return Boolean(img && img.src && img.src.length > 64);
    })()`).catch(() => false);
    if (ready) return true;
    await sleep(300);
  }
  return false;
}

async function readCaptcha(session, imageId) {
  return session.evalJs(`(() => {
    const img = document.getElementById(${JSON.stringify(imageId)});
    if (!img || !img.src) return "";
    const svg = decodeURIComponent((img.src.split(",")[1] || ""))
      .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
      .replace(/&#(\\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
      .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
    const chars = [];
    svg.replace(/<text[^>]*>([\\s\\S]*?)<\\/text>/g, (_, inner) => {
      const t = inner.replace(/<[^>]*>/g, "").trim();
      if (t) chars.push(t);
      return "";
    });
    return chars.join("");
  })()`);
}

/** 已登录就直接返回；被踢回登录页就把验证码读出来、照表单登一次（最多试 3 次）。 */
async function ensureLoggedIn(session) {
  const locked = () => session.evalJs(`document.body ? document.body.classList.contains("is-locked") : true`);
  if (!(await locked())) return true;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    if (attempt > 1) {
      await session.send("Page.reload", { ignoreCache: true });
      await sleep(2500);
      for (let i = 0; i < 60; i += 1) {
        const ok = await session.evalJs(`Boolean(document.getElementById("login-captcha-img"))`)
          .catch(() => false);
        if (ok) break;
        await sleep(250);
      }
    }
    await waitForCaptcha(session, "login-captcha-img");
    const code = await readCaptcha(session, "login-captcha-img");
    if (!code) console.log("   ⚠ 读不出登录验证码");
    await session.evalJs(`(() => {
      document.getElementById("ltab-account").click();
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("login-name", ${JSON.stringify(ACCOUNT)});
      set("login-pwd", ${JSON.stringify(PASSWORD)});
      set("login-captcha", ${JSON.stringify(code)});
      return true;
    })()`);
    await click(session, "btn-login");
    await sleep(3000);
    if (!(await locked())) {
      console.log(`   （换进程后重新登录：ok${attempt > 1 ? `，第 ${attempt} 次` : ""}）`);
      return true;
    }
    console.log(`   （登录第 ${attempt} 次没成，换张验证码重来）`);
  }
  console.log("   ⚠ 登录连续 3 次都没成功");
  return false;
}

/** 统一的开段动作：重启浏览器 → 等骨架 → 补登录 → 等页面自己的请求全部落地。 */
async function beginSegment(label, hash = "#/overview") {
  console.log(`   ${label}`);
  await restartBrowser();
  const session = await currentSession();
  for (let i = 0; i < 150; i += 1) {
    const ok = await session.evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate")) && Boolean(document.getElementById("btn-register"))`);
    if (ok) break;
    await sleep(200);
  }
  await ensureLoggedIn(session);
  await session.send("Page.navigate", { url: BASE + "/" + hash });
  for (let i = 0; i < 120; i += 1) {
    const ok = await session.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("nl-input"))`).catch(() => false);
    if (ok) break;
    await sleep(250);
  }
  await waitForWarmup(session);
}

/** 等服务端把主数据集预热完（判据是页面 banner 收起来）——一次长超时，不分段干等。 */
async function waitForWarmup(session) {
  const deadline = Date.now() + 600000;
  let announced = false;
  while (Date.now() < deadline) {
    const busy = await session.evalJs(`(() => {
      const banner = document.getElementById("banner");
      return Boolean(banner) && !banner.hidden;
    })()`).catch(() => false);
    if (!busy) break;
    if (!announced) {
      console.log("   （服务端正在预热主数据集，等它读完再提问 —— 实测 300 秒上下）");
      announced = true;
    }
    await sleep(2000);
  }
  await sleep(500);
}

/** 提一个问题，等**这一条**的回答渲染出来（先认问题、再认回答，避免读到上一条）。 */
async function ask(session, question, timeoutMs = 900000) {
  await session.evalJs(`(() => {
    const el = document.getElementById("nl-input");
    el.value = ${JSON.stringify(question)};
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  })()`);
  await click(session, "nl-ask");
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const seen = await session.evalJs(`(() => {
      const q = document.getElementById("chat-question");
      const a = document.getElementById("chat-answer");
      return { q: q ? q.textContent : "", a: a ? a.textContent.trim().length : 0 };
    })()`).catch(() => null);
    if (seen && seen.q.includes(question) && seen.a > 0) {
      await sleep(400);
      return true;
    }
    await sleep(500);
  }
  return false;
}

/** 看板的几何（**量真 DOM**：卡片数量/位置/字号，不是数源码里的字符串）。 */
const boardGeometry = (session) => session.evalJs(`(() => {
  const board = document.querySelector("#chat-answer .board");
  if (!board) return null;
  const kpis = Array.from(board.querySelectorAll(".board-kpi"));
  const table = board.querySelector(".board table");
  const warning = board.querySelector(".board-warning");
  const rect = (el) => { const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) }; };
  const trs = table ? Array.from(table.querySelectorAll("tr")) : [];
  const valueEls = kpis.map((k) => k.querySelector(".board-kpi-value"));
  return {
    board: rect(board),
    labels: kpis.map((k) => (k.querySelector(".board-kpi-label") || {}).textContent || ""),
    values: valueEls.map((el) => (el ? el.textContent : "")),
    sizes: kpis.map((k) => rect(k)),
    valueFont: valueEls[0] ? getComputedStyle(valueEls[0]).fontSize : "",
    tableRows: trs.length ? trs.length - 1 : 0,
    tableHead: table ? Array.from(table.querySelectorAll("th")).map((th) => th.textContent) : [],
    firstRow: trs.length > 1 ? Array.from(trs[1].children).map((td) => td.textContent) : [],
    missRows: table ? Array.from(table.querySelectorAll("tr.miss td")).map((td) => td.textContent) : [],
    warning: warning ? warning.textContent.replace(/\\s+/g, " ").trim() : null,
    warningVisible: Boolean(warning) && warning.getBoundingClientRect().height > 0,
    text: board.textContent.replace(/\\s+/g, " ").trim(),
  };
})()`);

/** 回答里出现过的分段标题（`<b>` 在 `.answer-head` 里）—— 用来看有没有套分析模板。 */
const sectionHeads = (session) => session.evalJs(
  `Array.from(document.querySelectorAll("#chat-answer .answer-head b")).map((el) => el.textContent.trim())`);

async function shot(name, id) {
  const session = await openSession();
  try {
    let clip = null;
    if (id) {
      const box = await session.evalJs(`(() => {
        const el = document.getElementById(${JSON.stringify(id)});
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      })()`);
      if (box && box.w > 0) {
        clip = { x: Math.max(0, box.x - 16), y: Math.max(0, box.y - 16),
                 width: box.w + 32, height: box.h + 32, scale: 2 };
      }
    }
    const image = await session.send("Page.captureScreenshot", clip ? { format: "png", clip } : { format: "png" });
    const file = path.join(SHOT_DIR, name);
    fs.writeFileSync(file, Buffer.from(image.data, "base64"));
    return file;
  } finally {
    session.close();
  }
}

/** 往文件选择框里塞文件（真事件，走页面自己的 change 监听）。必须传**绝对路径**。 */
async function pickFiles(session, selector, files) {
  const { root } = await session.send("DOM.getDocument", { depth: 1 });
  const { nodeId } = await session.send("DOM.querySelector", { nodeId: root.nodeId, selector });
  if (!nodeId) throw new Error(`页面上没有这个元素：${selector}`);
  await session.send("DOM.setFileInputFiles", { nodeId, files: files.map((f) => path.resolve(f)) });
  await sleep(200);
}

try {
  // 等页面骨架就绪
  for (let i = 0; i < 150; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate")) && Boolean(document.getElementById("btn-register"))`));
    if (ok) break;
    await sleep(200);
  }

  // ── ① 注册一个本机账号 ─────────────────────────────────────────────────
  console.log("\n=== ① 注册一个本机账号（第一个账号，直接进系统）===");
  await step(async (s) => {
    await click(s, "link-register");
    await sleep(300);
    await waitForCaptcha(s, "reg-captcha-img");
    await s.evalJs(`(() => {
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("reg-name", ${JSON.stringify(ACCOUNT)});
      set("reg-display", "回复形态用例");
      set("reg-pwd", ${JSON.stringify(PASSWORD)});
      set("reg-pwd2", ${JSON.stringify(PASSWORD)});
      return true;
    })()`);
    const code = await readCaptcha(s, "reg-captcha-img");
    await s.evalJs(`(() => {
      const el = document.getElementById("reg-captcha");
      el.value = ${JSON.stringify(code)};
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    await click(s, "btn-register");
  });
  await sleep(3500);
  check(!(await step((s) => s.evalJs(`document.body ? document.body.classList.contains("is-locked") : true`))),
    "注册后进入系统（登录页收起）");

  // ── ② A3 空库 + A5/A6 身份：一句话答完，不编文件名、不套模板 ─────────────
  console.log("\n=== ② 空库时问「我刚刚导入的文件在哪里」+「你是什么」===");
  await beginSegment("重开浏览器，进首页…");
  check(await step((s) => ask(s, "我刚刚导入的文件在哪里")), "问「我刚刚导入的文件在哪里」有回答");
  const emptyAnswer = await step((s) => textOf(s, "chat-answer"));
  check(/还没有导入过任何资料/.test(emptyAnswer || ""), "空库时明说「还没有导入过任何资料」", (emptyAnswer || "").slice(0, 60));
  check(!/《[^》]+》/.test(emptyAnswer || ""), "★ 空库时没有编出任何文件名");
  check(!/\S+\.(pdf|xlsx|csv|docx|pptx)/.test(emptyAnswer || ""), "★ 也没有编出任何扩展名");
  const emptyBoard = await step((s) => boardGeometry(s));
  check(emptyBoard === null, "元信息回答没有看板（不是分析结果，不该配卡片）");
  const emptyHeads = await step((s) => sectionHeads(s));
  check(!(emptyHeads || []).some((t) => /发生了什么|为什么|建议行动/.test(t)),
    "★ 元信息回答不套分析模板（没有【发生了什么】/【为什么】/【建议行动】）", JSON.stringify(emptyHeads));
  check(!(emptyHeads || []).some((t) => /口径|数据范围|字段清单/.test(t)),
    "★ 元信息回答里没有口径/字段清单那类「拒绝式」内容", JSON.stringify(emptyHeads));
  check(!/为了更好地|首先，|很高兴为您/.test(emptyAnswer || ""), "★ 回答不以「为了更好地帮助您」这类开场白起头");
  check(!/BM25|intent|document_qa|response_mode|置信度|\/api\//.test(emptyAnswer || ""),
    "★ 回答正文里零内部名词");
  console.log("   截图：", await shot("fr010a_01_空库导入记录.png", "chat-answer"));

  check(await step((s) => ask(s, "你是什么")), "问「你是什么」有回答");
  const identity = await step((s) => textOf(s, "chat-answer"));
  check(/我是你的销售分析助手/.test(identity || ""), "身份回答是那句话", (identity || "").slice(0, 80));
  check((identity || "").length <= 130, "★ 身份回答是一句话（不是十几项能力清单）", `${(identity || "").length} 字`);
  check(!/①|②|③|本版支持/.test(identity || ""), "★ 身份回答没有倒长清单");
  console.log("   截图：", await shot("fr010a_02_你是什么.png", "chat-answer"));

  // 记下这一条元信息回答里的"内部名词"检查（页面上看到的正文）
  const bannedInPage = ["BM25", "intent", "document_qa", "response_mode", "置信度", "耗时", "/api/"];
  console.log("   ", (bannedInPage.every((w) => !(identity || "").includes(w)) ? "✅" : "❌"),
    "★ 身份回答里零内部名词");
  if (bannedInPage.some((w) => (identity || "").includes(w))) failed += 1; else passed += 1;

  // ── ③ 真导入一份 PDF → A3 直接给出真实文件名/字数/时间 ─────────────────
  console.log("\n=== ③ 真导入一份 PDF（文件名与标题故意不同）→ 再问同一句 ===");
  await step((s) => s.send("Page.navigate", { url: BASE + "/#/data" }));
  for (let i = 0; i < 120; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("imports-body"))`).catch(() => false));
    if (ok) break;
    await sleep(250);
  }
  await step((s) => pickFiles(s, "#imp-file-input", [FIXTURE]));
  await step((s) => click(s, "btn-imp-run"));
  await sleep(7000);
  const rows = await step((s) => s.evalJs(`(() => {
    const body = document.getElementById("imports-body");
    return body ? Array.from(body.children).map((tr) =>
      Array.from(tr.children).map((td) => td.textContent.replace(/\\s+/g, " ").trim())) : null;
  })()`));
  check((rows || []).length === 1, "导入记录里出现了这一条", JSON.stringify(rows));
  const recordRow = (rows || [])[0] || [];
  check(recordRow[0] === FILENAME, "记录里的文件名是**真文件名**（不是标题）", recordRow[0]);

  await step((s) => s.send("Page.navigate", { url: BASE + "/#/overview" }));
  for (let i = 0; i < 120; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("nl-input"))`).catch(() => false));
    if (ok) break;
    await sleep(250);
  }
  check(await step((s) => ask(s, "我刚刚导入的文件在哪里")), "导入之后再问同一句，有回答");
  const recordAnswer = await step((s) => textOf(s, "chat-answer"));
  check((recordAnswer || "").includes(FILENAME), "★ 回答里给出了刚才那份文件的真名字", (recordAnswer || "").slice(0, 90));
  check(/已成功保存/.test(recordAnswer || ""), "说了这份文件已保存");
  check(/PDF/.test(recordAnswer || ""), "给了格式（PDF 文档）");
  check(/[\d,]+ 字/.test(recordAnswer || ""), "给了字数");
  check(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(recordAnswer || ""), "给了入库时间");
  check(/数据管理/.test(recordAnswer || "") && /导入记录/.test(recordAnswer || ""), "说了去哪儿看");
  console.log("   截图：", await shot("fr010a_03_导入记录问答.png", "chat-answer"));

  // ── ④ A1/A2 真实数据：11-25 单日看板 + 11-26 空档日不补 0 ──────────────
  console.log("\n=== ④ 真数据单日看板：「2011年11月25日销售额是多少」===");
  check(await step((s) => ask(s, "2011年11月25日销售额是多少")), "11-25 的提问拿到了回答（真算了一遍）");
  const okBoard = await step((s) => boardGeometry(s));
  check(Boolean(okBoard), "★ 单值档的回答下面**真的画出了看板**");
  if (okBoard) {
    console.log("      看板几何：", JSON.stringify({ board: okBoard.board, kpi: okBoard.sizes[0], 字号: okBoard.valueFont }));
    check(okBoard.labels.length === 4, "看板有 4 张卡", JSON.stringify(okBoard.labels));
    check(okBoard.labels.slice(0, 3).join(",") === "销售额,订单数,客户数", "★ 前三张卡是销售额/订单数/客户数", JSON.stringify(okBoard.labels));
    check(/前一天/.test(okBoard.labels[3] || ""), "★ 第四张卡是前一天对比", okBoard.labels[3]);
    check(okBoard.values[0].includes("50,822.73"), "★ 11-25 销售额与确定性结果一致", okBoard.values[0]);
    check(okBoard.tableRows === 6, "最近 6 天的小表是 6 行", String(okBoard.tableRows));
    check((okBoard.tableHead || []).join(",") === "日期,销售额,订单数,较前一日", "小表表头", JSON.stringify(okBoard.tableHead));
    check(/本月累计/.test(okBoard.text), "有本月累计");
    check(!/成本|毛利|商品数/.test(okBoard.text), "★ 数据里没有的字段（成本/毛利/商品数）没出现在看板上");
    check(okBoard.sizes.every((r) => r.w > 100 && r.h > 40), "每张卡都真的占位（不是被压成 0 高）");
  }
  console.log("   截图：", await shot("fr010a_04_11-25单日看板.png", "chat-answer"));

  console.log("\n=== ⑤ 空档日：「2011年11月26日卖了多少」===");
  check(await step((s) => ask(s, "2011年11月26日卖了多少")), "11-26 的提问拿到了回答");
  const emptyBoard2 = await step((s) => boardGeometry(s));
  check(Boolean(emptyBoard2), "看板块还在（这一档有话说，只是没有卡片）");
  if (emptyBoard2) {
    check(emptyBoard2.labels.length === 0, "★ 一张卡都没画（不补 0）", JSON.stringify(emptyBoard2.values));
    check(emptyBoard2.tableRows === 0, "★ 没有趋势小表");
    check(emptyBoard2.warningVisible && /没有任何成交记录/.test(emptyBoard2.warning || ""), "★ 警示条明说「这一天没有任何成交记录」", emptyBoard2.warning);
    check(!/0\.00/.test(emptyBoard2.text), "★ 看板上没有 0.00 元这种补出来的数");
    check(/11-25|11-27/.test(emptyBoard2.text), "给了相邻两天的真实值");
  }
  console.log("   截图：", await shot("fr010a_05_11-26无成交.png", "chat-answer"));

  // ── ⑥ 全程没有 JS 报错 ──────────────────────────────────────────────────
  console.log("\n=== ⑥ 全程没有 JS 报错 ===");
  check(consoleErrors.length === 0, "页面没有 JS 报错", consoleErrors.slice(0, 3).join(" | "));
} catch (err) {
  failed += 1;
  console.log(`\n❌ 走查中断：${err && err.message ? err.message : err}`);
} finally {
  try { browser.kill(); } catch { /* 忽略 */ }
  try { spawnSync("taskkill", ["/F", "/T", "/PID", String(browser.pid)], { stdio: "ignore" }); } catch { /* 忽略 */ }
  for (const dir of UDDS) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* 忽略 */ }
  }
  console.log(`\n=== 结果：${passed} 通过 / ${failed} 失败 ===`);
  if (consoleErrors.length) {
    console.log("页面 console 错误（前 5 条）：");
    consoleErrors.slice(0, 5).forEach((line) => console.log("   -", String(line).slice(0, 200)));
  }
  process.exit(failed ? 1 : 0);
}
