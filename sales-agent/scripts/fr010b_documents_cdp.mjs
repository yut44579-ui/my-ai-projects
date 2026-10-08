// fr010b_documents_cdp.mjs · FR-010-B「外部资料可检索 + 带来源回答」在**真浏览器**里的实测
//                            （真 Edge + CDP，零依赖）
//
//   node scripts/fr010b_documents_cdp.mjs http://127.0.0.1:8541
//
// 跑之前先起一个真服务（沙箱目录只是别把本次导入写进真实 state/ 与真实 data/app.db）：
//   .venv/Scripts/python.exe scripts/fr010b_cdp_reset.py      # 先把沙箱建好（表要建在服务启动前）
//   SRA_STATE_DIR=outputs/_cdp_run_fr010b/state SRA_DB_PATH=outputs/_cdp_run_fr010b/app.db \
//   SRA_ORIGINAL_DIR=outputs/_cdp_run_fr010b/original SRA_UPLOAD_DIR=outputs/_cdp_run_fr010b/uploads \
//   SRA_DOC_DIR=outputs/_cdp_run_fr010b/documents SRA_OUTPUT_DIR=outputs/_cdp_run_fr010b/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8541
//
// 为什么必须真开浏览器（派单 §四 第 14 条：真浏览器走一遍 1/3/4/5/7 并截图）：
// 网页上要看的不是"接口返回了什么"，而是：
//   ① 答案里的**出处**长什么样（《文件名》第 N 节 是不是真的画在正文里）；
//   ② 「没有找到」那句在页面上是**警示态**，而不是一段看起来像答案的话；
//   ③ 提到图片型资料的答案明说读不出文字；
//   ④ 单值回答的**提示条**（相对时间那条）真的出现在回答下方。
// 量几何只能用 CDP 的 `getBoundingClientRect` —— `dump-dom` 量不了（这是踩过的坑）。
//
// ★ 提速（派单 §五）：本走查只跑一轮；需要主数据集的那一条提问只发**一次**；
//   页面一打开会 refreshAll 去读主数据集（实测预热 ~300 秒，这期间 GIL 被占满）——
//   用**一次长超时**等它过去（判据是页面 banner 收起来），而不是 sleep 240/420 分段干等。
// 截图落在 outputs/_cdp_shots/。
//
// ★ CDP 的两条坑（从 fr003/fr009a/fr010a 那几份探针抄来的实测经验，别再踩一遍）：
//   ① `DOM.getDocument` 必须带 `depth: 1`，否则整棵 DOM 树回来会把连接噎住；
//   ② 一段流程里命令做多了，渲染进程会整体不再应答 → 每段换**全新浏览器进程 + 全新 profile**。
//      文件框必须传**绝对路径**（相对路径会静默地什么都不设）。

const BASE = process.argv[2] || "http://127.0.0.1:8541";
let PORT = 0;
const pickPort = () => 9900 + Math.floor(Math.random() * 400);
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
const ACCOUNT = "13900000006";
const PASSWORD = "abc12345";
const SANDBOX = "outputs/_cdp_run_fr010b";
const GAME_DOC = "游戏资料.md";
const STRATEGY_DOC = "某游戏2026海外战略.md";
const INJECTION_DOC = "注入测试.md";
const SCANNED_PDF = "扫描件.pdf";
const MOJIBAKE_FILE = "AI游戏制作知识库.pdf";
const MOJIBAKE_TITLE = "AI 游戏制作知识库";
const FIXTURES = [GAME_DOC, STRATEGY_DOC, INJECTION_DOC, SCANNED_PDF].map((n) => `${SANDBOX}/${n}`);

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
for (const file of FIXTURES) {
  if (!fs.existsSync(file)) {
    console.log(`❌ 缺少样例文件：${file}（先跑 scripts/fr010b_cdp_reset.py）`);
    process.exit(1);
  }
}

// 每次跑都要是干净的一次：重置**本脚本沙箱**（真实 state/ 与真实 data/app.db 永远不碰）。
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const python = process.env.SRA_PYTHON || path.join(process.cwd(), ".venv", "Scripts", "python.exe");
  const reset = spawnSync(python, [path.join("scripts", "fr010b_cdp_reset.py")],
    { cwd: process.cwd(), encoding: "utf-8" });
  if (reset.status !== 0) {
    console.log("❌ 沙箱重置失败：", reset.stdout, reset.stderr);
    process.exit(1);
  }
  console.log(reset.stdout.trim());
}

// ── CDP 基建（与 fr009a/fr010a 探针同一套）────────────────────────────────
const UDDS = [];
function spawnBrowser() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr010b_cdp_"));
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

/** 回答块的几何 + 正文（**量真 DOM**：出处是不是真的画出来了、提示条是不是真的有高度）。 */
const answerShape = (session) => session.evalJs(`(() => {
  const box = document.getElementById("chat-answer");
  const notice = document.getElementById("chat-notice");
  const head = box ? box.querySelector(".answer-head b") : null;
  const body = box ? box.querySelector(".answer-text") : null;
  const rect = (el) => { const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) }; };
  return {
    head: head ? head.textContent.trim() : "",
    text: body ? body.textContent : (box ? box.textContent : ""),
    textHeight: body ? Math.round(body.getBoundingClientRect().height) : 0,
    notice: notice ? notice.textContent.replace(/\\s+/g, " ").trim() : "",
    noticeVisible: Boolean(notice) && notice.getBoundingClientRect().height > 0,
    noticeRect: notice ? rect(notice) : null,
    answerRect: box ? rect(box) : null,
  };
})()`);

async function shot(name, id) {
  const session = await openSession();
  try {
    let clip = null;
    if (id) {
      // ★ 先把元素滚进视口再量：clip 用的是**视口坐标**（不带 captureBeyondViewport），
      //   元素在首屏之外时量出来的是屏幕外的坐标，截出来是一张空图
      //   （两版教训都是实测出来的：第一版全黑、第二版多加了 scrollY 结果偏上 400px）。
      //   目标若是 tbody，就往上一级量整张 table —— 否则表头被切在画面外，
      //   而「标题」这一列到底有没有，正是靠表头才看得懂的。
      const box = await session.evalJs(`(() => {
        let el = document.getElementById(${JSON.stringify(id)});
        if (!el) return null;
        if (el.tagName === "TBODY" && el.closest("table")) el = el.closest("table");
        el.scrollIntoView({ block: "center", inline: "nearest" });
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      })()`);
      await sleep(500);                       // 等滚动落定，别截到滚动中间态
      if (box && box.w > 0 && box.h > 0) {
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

/** 一行一行读某张 tbody（用于导入记录表 / 文档资料表）。 */
const rowsOf = (session, id) => session.evalJs(`(() => {
  const body = document.getElementById(${JSON.stringify(id)});
  return body ? Array.from(body.children).map((tr) =>
    Array.from(tr.children).map((td) => td.textContent.replace(/\\s+/g, " ").trim())) : null;
})()`);

/** 等某张表**攒够行数**再读（导入是同步请求，页面上多快出现取决于服务端解析速度 ——
 *  固定 sleep 会读到"还没刷出来的空表"，那是一次假红）。 */
async function waitForRows(session, id, count, timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  let rows = null;
  while (Date.now() < deadline) {
    rows = await session.evalJs(`(() => {
      const body = document.getElementById(${JSON.stringify(id)});
      return body ? Array.from(body.children).map((tr) =>
        Array.from(tr.children).map((td) => td.textContent.replace(/\\s+/g, " ").trim())) : null;
    })()`).catch(() => null);
    if (rows && rows.length >= count) return rows;
    await sleep(1000);
  }
  return rows;
}

/** 回答正文里"真正的事实行"（去掉出处行）—— 用来判"文件名暗示的内容有没有混进正文"。 */
const contentOnly = (text) => (text || "").split("\n")
  .filter((line) => !line.includes("《") && !line.trim().startsWith("出处")).join("\n");

try {
  // 等页面骨架就绪
  for (let i = 0; i < 150; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate")) && Boolean(document.getElementById("btn-register"))`));
    if (ok) break;
    await sleep(200);
  }

  // ── ① 注册 + 真上传四份资料 + 看导入记录与文档资料表 ────────────────────
  console.log("\n=== ① 注册 + 从页面真上传 4 份资料（3 份正文 + 1 份图片型 PDF）===");
  await step(async (s) => {
    await click(s, "link-register");
    await sleep(300);
    await waitForCaptcha(s, "reg-captcha-img");
    await s.evalJs(`(() => {
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("reg-name", ${JSON.stringify(ACCOUNT)});
      set("reg-display", "资料问答用例");
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

  await beginSegment("重开浏览器，进「数据管理」…", "#/data");
  for (let i = 0; i < 120; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("imp-file-input"))`).catch(() => false));
    if (ok) break;
    await sleep(250);
  }
  await step((s) => pickFiles(s, "#imp-file-input", FIXTURES));
  await step((s) => click(s, "btn-imp-run"));

  const importRows = await step((s) => waitForRows(s, "imports-body", FIXTURES.length));
  const names = (importRows || []).map((row) => row[0]);
  for (const file of FIXTURES) {
    check(names.includes(path.basename(file)), `导入记录里有《${path.basename(file)}》`, names.join(" / "));
  }
  const scannedRow = (importRows || []).find((row) => row[0] === SCANNED_PDF) || [];
  check(/失败/.test(scannedRow.join(" ")), "★ 图片型 PDF 如实记成「失败」（提不出文字不算导入成功）",
    scannedRow.join(" | "));
  console.log("   截图：", await shot("fr010b_01_导入记录.png", "imports-body"));

  // B8① 在页面上看标题：那份乱码标题的 PDF 现在显示的是干净标题
  // ★ 刷新一次再看文档资料表：那一列读的是 `GET /api/documents`（导入这几次请求不一定顺着刷新它），
  //   而我们要验的正是"**读取路径**上标题是不是干净的"—— 刷新后读到的才是读路径的真实样子。
  await step((s) => s.send("Page.reload", { ignoreCache: true }));
  await sleep(3000);
  for (let i = 0; i < 120; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("docs-table-body"))`).catch(() => false));
    if (ok) break;
    await sleep(250);
  }
  // （等表里攒够 4 行再读：那份 PDF 是重置脚本直接种进库的，另外 3 份是刚才真上传的）
  const docRows = await step((s) => waitForRows(s, "docs-table-body", 4));
  const mojibakeRow = (docRows || []).find((row) => row[0] === MOJIBAKE_FILE) || [];
  check(mojibakeRow.length > 1, "文档资料表里能看到那份 PDF", JSON.stringify(mojibakeRow));
  check(mojibakeRow[1] === MOJIBAKE_TITLE, "★ 已入库 PDF 的标题在页面上是干净的（读时修复）",
    `标题列=${JSON.stringify(mojibakeRow[1] || "")}`);
  check(!/\u0000/.test(JSON.stringify(mojibakeRow)), "★ 页面数据里零 NUL 字符");
  const gameRow = (docRows || []).find((row) => row[0] === GAME_DOC) || [];
  check(gameRow[1] === "游戏资料", "新导入的 Markdown 标题正常（写时修复那条路没被破坏）",
    `标题列=${JSON.stringify(gameRow[1] || "")}`);
  console.log("   截图：", await shot("fr010b_02_文档标题.png", "docs-table-body"));

  // ── ② 验收 1/2：概览与命中题（分节 + 每节带出处）───────────────────────
  console.log("\n=== ② 概览题：「这个资料讲了什么？」===");
  await beginSegment("重开浏览器，进问答页…", "#/overview");
  check(await step((s) => ask(s, "这个资料讲了什么？")), "概览题拿到了回答");
  const overview = await step((s) => answerShape(s));
  check(/出处：/.test(overview.text), "★ 回答里画出了出处行", overview.text.slice(0, 80));
  check((overview.text.match(/出处：/g) || []).length >= 3, "★ 分节列出且**每节都带出处**",
    `出处 ${(overview.text.match(/出处：/g) || []).length} 条`);
  check(overview.text.includes("第 1 节") && overview.text.includes("节"), "位置写的是「第 N 节」");
  check(!/\d{4}-\d{2}-\d{1,2} 销售额/.test(overview.text), "★ 概览回答里没有销售数字（不碰销售数据）");
  check(!/BM25|intent|document_qa|向量|置信度|\/api\//.test(overview.text), "★ 回答里零内部名词");
  console.log("   截图：", await shot("fr010b_03_概览分节带出处.png", "chat-answer"));

  console.log("\n=== ③ 命中题：「资料里提到的市场计划是什么？」===");
  check(await step((s) => ask(s, "资料里提到的市场计划是什么？")), "命中题拿到了回答");
  const hit = await step((s) => answerShape(s));
  check(hit.text.includes("《游戏资料.md》第 2 节"), "★ 出处准确（《游戏资料.md》第 2 节）");
  check(hit.text.includes("在明年开拓海外市场，优先东南亚地区"), "引的是**原文原句**");
  check(hit.text.includes("资料中计划"), "★ 属性保留：读作「资料中计划」，不是我们的结论");
  console.log("   截图：", await shot("fr010b_04_命中带出处.png", "chat-answer"));

  // ── ③ 验收 3/4/5/7：未命中 / 文件名不猜 / 属性 / 读不出文字 ─────────────
  console.log("\n=== ④ 未命中题：「资料里有没有写买量成本？」===");
  check(await step((s) => ask(s, "资料里有没有写买量成本？")), "未命中题拿到了回答");
  const miss = await step((s) => answerShape(s));
  check(/没有找到关于「买量成本」的内容/.test(miss.text), "★ 明确说没有找到", miss.text.slice(0, 70));
  check(miss.text.includes(GAME_DOC), "说明了资料里**实际**有什么（列出文件名与节标题）");
  check(!/买量[^\n]{0,12}\d/.test(miss.text), "★ 正文里没有任何「买量成本 + 数字」式的说法（不许补内容）");
  console.log("   截图：", await shot("fr010b_05_没有找到.png", "chat-answer"));

  console.log("\n=== ⑤ 文件名不猜：「《某游戏2026海外战略》里说了什么？」===");
  check(await step((s) => ask(s, "《某游戏2026海外战略》里说了什么？")), "点名文件的题拿到了回答");
  const named = await step((s) => answerShape(s));
  const namedBody = contentOnly(named.text);
  check(/国内的渠道合作与门店陈列安排/.test(namedBody), "★ 答的是**正文**里的内容");
  for (const word of ["海外", "欧美", "东南亚", "出海"]) {
    check(!namedBody.includes(word), `★ 正文部分没有文件名暗示的「${word}」`);
  }
  console.log("   截图：", await shot("fr010b_06_文件名不猜.png", "chat-answer"));

  console.log("\n=== ⑥ 属性保留：「资料说明年能增长多少？」===");
  check(await step((s) => ask(s, "资料说明年能增长多少？")), "增长题拿到了回答");
  const growth = await step((s) => answerShape(s));
  check(/资料中预计/.test(growth.text), "★ 用「资料中预计」表述（不是断言）", growth.text.slice(0, 70));
  check(/明年销售增长 50%/.test(growth.text), "引到了那句原文");
  check(!/一定增长|将会增长|肯定/.test(growth.text), "★ 没有写成我们自己的断言");
  console.log("   截图：", await shot("fr010b_07_属性保留.png", "chat-answer"));

  console.log("\n=== ⑦ 读不出文字：「《扫描件.pdf》里讲了什么？」===");
  check(await step((s) => ask(s, "《扫描件.pdf》里讲了什么？")), "图片型资料的问题拿到了回答");
  const scanned = await step((s) => answerShape(s));
  check(/读不出文字/.test(scanned.text) && /没有可提取的文本层/.test(scanned.text),
    "★ 明说读不出文字（没有文本层）", scanned.text.slice(0, 60));
  check(/不做图片识别/.test(scanned.text), "★ 明说不支持识别图片里的内容");
  check(!/图中|图片中|画面|截图里/.test(scanned.text), "★ 没有假装读过图");
  console.log("   截图：", await shot("fr010b_08_读不出文字.png", "chat-answer"));

  // ── ④ 验收 10：相对时间的可见提示（B8②）───────────────────────────────
  console.log("\n=== ⑧ 相对时间提示：「这个月卖了多少？」===");
  check(await step((s) => ask(s, "这个月卖了多少？")), "单值题拿到了回答");
  const single = await step((s) => answerShape(s));
  check(/销售额：/.test(single.text), "正文仍是「一个值」的形态", single.text.slice(0, 60));
  check(single.noticeVisible, "★ 回答下方的提示条**真的显示出来了**（有高度，不是隐藏的）");
  check(/「这个月」/.test(single.notice), "★ 提示条里把「这个月」这条假设说清楚了", single.notice);
  check(!/「这个月」/.test(single.text), "提示只在提示条里，没混进正文当作答案的一部分");
  console.log("   截图：", await shot("fr010b_09_相对时间提示.png", "chat-notice"));

  // ── ⑤ 全程没有 JS 报错 ──────────────────────────────────────────────────
  console.log("\n=== ⑨ 全程没有 JS 报错 ===");
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
