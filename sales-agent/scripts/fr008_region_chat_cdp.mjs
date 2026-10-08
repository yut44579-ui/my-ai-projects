// fr008_region_chat_cdp.mjs · FR-008「把按地区接进聊天」在**真浏览器**里的实测（真 Edge + CDP，零依赖）
//
//   node scripts/fr008_region_chat_cdp.mjs http://127.0.0.1:8538
//
// 跑之前先起一个真服务（沙箱目录只是别把本次走查写进真实 state/ 与真实 data/app.db）：
//   SRA_STATE_DIR=outputs/_cdp_run_fr008/state SRA_DB_PATH=outputs/_cdp_run_fr008/app.db \
//   SRA_ORIGINAL_DIR=outputs/_cdp_run_fr008/original SRA_UPLOAD_DIR=outputs/_cdp_run_fr008/uploads \
//   SRA_DOC_DIR=outputs/_cdp_run_fr008/documents SRA_OUTPUT_DIR=outputs/_cdp_run_fr008/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8538
//
// 为什么要真开浏览器（静态检查证明不了的）：
//   ① 用户要的是「**在对话里**直接问地区」，不是只有数据管理页那张卡能点 ——
//      所以必须在真页面的输入框里真打一句、真看它渲染出什么；
//   ② 没有带地区字段的数据源时，页面要说清"没有这个维度 + 怎么获得"，
//      而不是弹个错、更不是拿国家数字顶上；
//   ③ 导入带省份的数据后，同一句话要真的出 广东/浙江/江苏 三条 + 合计。
//
// 量什么（每一步都读**真 DOM**，不是看源码猜）：
//   ⓐ 空库问「按地区看销售额」→ 页面正文说"当前没有任何带地区字段的数据源"+ 怎么获得，
//      且**一个金额数字都没有**（截图存证）；
//   ⓑ 数据管理页导入带「省份」的 CSV → 回执说清落成了数据集、地区字段=省份；
//   ⓒ 回到首页在对话里真打「各省份的销售额」→ 结果表与正文出现 广东 100 / 浙江 200 / 江苏 300
//      与合计 600（截图存证）。
//
// ★ 提速纪律（本 TASK 的硬要求）：**只跑一轮**，不反复刷绿。
//   只调一次需要主数据集预热的接口（/api/chat/capabilities，超时 900s 一次等完），
//   其余都是登录 / 页面渲染 / 导入 / 对话 —— 这些不用等预热。
//
// ★ 关于 CDP 连接（踩过的坑，别再改回去 —— 与 fr003_import_cdp.mjs 同一套）：
//   ① `DOM.getDocument` 必须带 `depth: 1`；
//   ② 一段流程里"命令越做越多"之后渲染进程会整体不再应答 → **每段换一个全新浏览器进程 + 全新 profile**；
//   ③ 调试端口每换一个浏览器进程也要换一个（老进程的监听还没释放，新进程会静默绑不上）；
//   ④ `DOM.setFileInputFiles` **必须传绝对路径**（相对路径静默失效）。

const BASE = process.argv[2] || "http://127.0.0.1:8538";
let PORT = 0;
const pickPort = () => 9400 + Math.floor(Math.random() * 400);
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
const ACCOUNT = "13900000008";
const PASSWORD = "abc12345";
const SANDBOX = "outputs/_cdp_run_fr008";
const FIXTURE = `${SANDBOX}/地区销售.csv`;

const { spawn, spawnSync } = await import("node:child_process");
const os = await import("node:os");
const path = await import("node:path");
const fs = await import("node:fs");

const edge = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
if (!edge) {
  console.log("❌ 没找到 Edge，无法实测");
  process.exit(1);
}
const SHOT_DIR = process.argv[3] || path.join(process.cwd(), "outputs");
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

// 每次跑都要是干净的一次：重置**本脚本沙箱**（真实 state/ 与真实 data/app.db 永远不碰）。
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const python = process.env.SRA_PYTHON || path.join(process.cwd(), ".venv", "Scripts", "python.exe");
  const reset = spawnSync(python, [path.join("scripts", "fr008_cdp_reset.py")],
    { cwd: process.cwd(), encoding: "utf-8" });
  if (reset.status !== 0) {
    console.log("❌ 沙箱重置失败：", reset.stdout, reset.stderr);
    process.exit(1);
  }
  console.log(reset.stdout.trim());
}
if (!fs.existsSync(FIXTURE)) {
  console.log(`❌ 缺少样例文件：${FIXTURE} —— 先跑 .venv/Scripts/python.exe scripts/fr008_cdp_reset.py`);
  process.exit(1);
}

// ★ 只调**一次**需要主数据集预热的接口（首次要读那张大表，机器慢时可能要几分钟）：
//   一次等完（900s），不 60s 一次地反复轮询。
console.log("等数据预热（首次要读那张大表；本脚本只等这一次，超时 900s）…");
try {
  const warm = await fetch(BASE + "/api/chat/capabilities", { signal: AbortSignal.timeout(900000) });
  if (!warm.ok) { console.log(`❌ 数据预热失败（${warm.status}）`); process.exit(1); }
  console.log("数据已就绪，开始走查。");
} catch (err) {
  console.log(`❌ 等数据预热超时/失败：${err}`);
  process.exit(1);
}

const UDDS = [];
function spawnBrowser() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr008_cdp_"));
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

const visible = (session, id) => session.evalJs(`(() => {
  const el = document.getElementById(${JSON.stringify(id)});
  if (!el || el.hidden) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
})()`);

const textOf = (session, id) =>
  session.evalJs(`document.getElementById(${JSON.stringify(id)}).textContent.replace(/\\s+/g, " ")`);

const click = (session, id) =>
  session.evalJs(`document.getElementById(${JSON.stringify(id)}).click()`);

async function pickFiles(session, selector, files) {
  const { root } = await session.send("DOM.getDocument", { depth: 1 });
  const { nodeId } = await session.send("DOM.querySelector", { nodeId: root.nodeId, selector });
  if (!nodeId) throw new Error(`页面上没有这个元素：${selector}`);
  // ★ 必须传**绝对路径**（见文件头 ④）
  await session.send("DOM.setFileInputFiles", { nodeId, files: files.map((f) => path.resolve(f)) });
  await sleep(200);
}

async function shot(name, id) {
  const session = await openSession();
  try {
    let clip;
    if (id) {
      await session.evalJs(`document.getElementById(${JSON.stringify(id)}).scrollIntoView({ block: "center" })`);
      await sleep(250);
      const box = await session.evalJs(`(() => {
        const r = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();
        return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
      })()`);
      clip = { x: Math.max(0, box.x - 16), y: Math.max(0, box.y - 16),
               width: box.w + 32, height: box.h + 32, scale: 2 };
    }
    const image = await session.send("Page.captureScreenshot", clip ? { format: "png", clip } : { format: "png" });
    const file = path.join(SHOT_DIR, name);
    fs.writeFileSync(file, Buffer.from(image.data, "base64"));
    return file;
  } finally {
    session.close();
  }
}

/** 已登录就直接返回；被踢回登录页就把验证码读出来、照表单登一次（最多 3 次）。 */
async function ensureLoggedIn(session) {
  const locked = () => session.evalJs(`document.body.classList.contains("is-locked")`);
  if (!(await locked())) return true;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    if (attempt > 1) {
      await session.send("Page.reload", { ignoreCache: true });
      await sleep(2500);
      for (let i = 0; i < 60; i += 1) {
        const ok = await session.evalJs(`Boolean(document.getElementById("login-captcha-img"))`).catch(() => false);
        if (ok) break;
        await sleep(250);
      }
    }
    const info = await session.evalJs(`(() => {
      const img = document.getElementById("login-captcha-img");
      if (!img || !img.src) return { svg: "", code: "" };
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
      return { svg: svg.slice(0, 120), code: chars.join("") };
    })()`);
    if (!info.code) console.log(`   ⚠ 读不出登录验证码，SVG 头 120 字：${info.svg}`);
    await session.evalJs(`(() => {
      document.getElementById("ltab-account").click();
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("login-name", ${JSON.stringify(ACCOUNT)});
      set("login-pwd", ${JSON.stringify(PASSWORD)});
      set("login-captcha", ${JSON.stringify(info.code)});
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

/** 等在首页（问答面板所在的那一页）就绪。 */
async function waitOverview(session, hash = "#/overview") {
  await session.send("Page.navigate", { url: BASE + "/" + hash });
  for (let i = 0; i < 150; i += 1) {
    const ok = await session.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("hero-nl-input"))
      && Boolean(document.getElementById("chat-panel"))`).catch(() => false);
    if (ok) break;
    await sleep(250);
  }
  await sleep(1200);        // 让能力清单那几个 GET 落地
}

/** 在对话输入框里真打一句、真点「开始分析」，等链路跑完。 */
async function askInChat(session, question) {
  await session.evalJs(`(() => {
    const el = document.getElementById("hero-nl-input");
    el.value = ${JSON.stringify(question)};
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  })()`);
  await click(session, "hero-nl-btn");
  // 等这条链路真的把结果渲染出来（或明确报错），最多 180s
  for (let i = 0; i < 360; i += 1) {
    const state = await session.evalJs(`(() => {
      const body = document.getElementById("chat-body");
      const badge = document.getElementById("chat-status-badge");
      return { done: Boolean(body) && !body.hidden, badge: badge ? badge.textContent.trim() : "" };
    })()`).catch(() => null);
    if (state && state.done && state.badge && state.badge !== "—") return state;
    await sleep(500);
  }
  return { done: false, badge: "" };
}

async function beginSegment(label, hash) {
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
  await waitOverview(session, hash);
}

try {
  // 等页面骨架就绪
  for (let i = 0; i < 150; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate")) && Boolean(document.getElementById("btn-register"))`));
    if (ok) break;
    await sleep(200);
  }

  // ── ① 真注册一个账号（沙箱是空的，第一个账号直接进系统）──────────────────
  console.log("\n=== ① 注册一个本机账号 ===");
  await step(async (s) => {
    await click(s, "link-register");
    await sleep(300);
    await s.evalJs(`(() => {
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("reg-name", ${JSON.stringify(ACCOUNT)});
      set("reg-display", "地区走查");
      set("reg-pwd", ${JSON.stringify(PASSWORD)});
      set("reg-pwd2", ${JSON.stringify(PASSWORD)});
      return true;
    })()`);
    const code = await s.evalJs(`(() => {
      const img = document.getElementById("reg-captcha-img");
      if (!img || !img.src) return "";
      const svg = decodeURIComponent((img.src.split(",")[1] || ""));
      const found = [];
      svg.split("</text>").forEach((part) => {
        const at = part.lastIndexOf(">");
        if (at >= 0 && part.length - at === 2) found.push(part.slice(at + 1));
      });
      return found.join("");
    })()`);
    await s.evalJs(`(() => {
      const el = document.getElementById("reg-captcha");
      el.value = ${JSON.stringify(code)};
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    await click(s, "btn-register");
  });
  await sleep(3500);
  check(!(await step((s) => s.evalJs(`document.body.classList.contains("is-locked")`))),
    "注册后进入系统（登录页收起）");

  // ── ⓐ 空库问「按地区看销售额」：明确说没有 + 怎么获得，一个金额都不给 ──────
  console.log("\n=== ⓐ 还没有带地区字段的数据源：对话里问地区，必须明说没有（不给国家数据）===");
  await beginSegment("重开浏览器，进首页…", "#/overview");
  const emptyState = await step((s) => askInChat(s, "按地区看销售额"));
  check(emptyState.done, "页面给出了结果（链路跑完）", `status=${emptyState.badge}`);
  const refusedFacts = await step((s) => textOf(s, "chat-facts"));
  const refusedAnswer = await step((s) => textOf(s, "chat-answer"));
  const refusedNotice = await step((s) => textOf(s, "chat-notice"));
  const refusedAll = `${refusedAnswer} ${refusedNotice}`;
  check(refusedAnswer.includes("回答不了") || refusedAll.includes("回答不了"),
    "正文明确说答不了", refusedAnswer.slice(0, 60));
  check(/没有[^。]{0,12}地区/.test(refusedAll) || refusedAll.includes("没有地区"),
    "说清了「没有带地区字段的数据源」", refusedAll.slice(0, 80));
  check(refusedAll.includes("导入"), "说清了怎么获得（导入带地区列的数据）");
  check(refusedFacts.includes("本次没有计算结果"), "没有任何计算结果块（也就没有国家数字）",
    refusedFacts);
  check(!/广东|浙江|江苏/.test(refusedAll), "响应里没有任何地区数字/地区名");
  const refusedShot = await shot("fr008-no-region-source.png", "chat-panel");
  console.log(`   📷 ${refusedShot}`);

  // ── ⓑ 数据管理页导入带「省份」的 CSV ────────────────────────────────────
  console.log("\n=== ⓑ 导入带「省份」列的 CSV ===");
  await beginSegment("重开浏览器，进「数据管理」…", "#/data");
  for (let i = 0; i < 120; i += 1) {
    const ok = await step((s) => s.evalJs(`Boolean(document.getElementById("sources-body"))`).catch(() => false));
    if (ok) break;
    await sleep(250);
  }
  await step((s) => pickFiles(s, "#imp-file-input", [FIXTURE]));
  await step((s) => click(s, "btn-imp-preview"));
  await sleep(4000);
  const preview = await step((s) => textOf(s, "imp-preview"));
  check(preview.includes("地区字段：省份"), "预览里认出「省份」是地区字段", preview.slice(0, 90));
  await step((s) => click(s, "btn-imp-run"));
  await sleep(6000);
  const receipt = await step((s) => textOf(s, "imp-result"));
  check(receipt.includes("已导入") && receipt.includes("省份"), "导入回执说明落成了什么 + 地区字段=省份",
    receipt.slice(0, 110));

  // ── ⓒ 回到首页，在对话里真打「各省份的销售额」──────────────────────────
  console.log("\n=== ⓒ 在对话里问「各省份的销售额」：必须出三条 + 合计 ===");
  await beginSegment("重开浏览器，进首页…", "#/overview");
  const regionState = await step((s) => askInChat(s, "各省份的销售额"));
  check(regionState.done, "页面给出了结果（链路跑完）", `status=${regionState.badge}`);
  const factsText = await step((s) => textOf(s, "chat-facts"));
  const answerText = await step((s) => textOf(s, "chat-answer"));
  const noticeText = await step((s) => textOf(s, "chat-notice"));
  const all = `${factsText} ${answerText} ${noticeText}`;
  for (const [region, amount] of [["广东", "100"], ["浙江", "200"], ["江苏", "300"]]) {
    check(all.includes(region) && all.includes(amount), `${region} ${amount} 出现在页面上`);
  }
  check(all.includes("600"), "合计 600 出现在页面上");
  // ★ 数据源归属渲染在**回答区**（web/app.js 的 renderChatAnswer → 事实段），不是指标表
  //   （chat-facts 只有 tool.display 那张「指标/数值/单位」表，按设计不含数据源名）。
  //   2026-09-29 修正：原先读 factsText 是假阴性 —— 截图证明这两行确实显示在页面上。
  check(answerText.includes("数据源") && answerText.includes("地区销售"),
    "回答里写明用的是哪份数据源",
    (answerText.match(/数据源：.{0,20}/) || [""])[0]);
  check(!/Python|Traceback|intent|tool_name|耗时/.test(all), "页面上没有技术细节（意图名/工具名/耗时）");
  const regionShot = await shot("fr008-region-chat-answer.png", "chat-panel");
  console.log(`   📷 ${regionShot}`);

  // ── 收尾 ────────────────────────────────────────────────────────────────
  console.log("\n=== 汇总 ===");
  check(consoleErrors.length === 0, "全程没有 JS 报错", consoleErrors.slice(0, 2).join(" | "));
  console.log(`\n${failed === 0 ? "✅" : "❌"} 通过 ${passed} 项 / 失败 ${failed} 项`);
  console.log(`截图目录：${SHOT_DIR}`);
} catch (err) {
  console.log(`\n❌ 走查中断：${err}`);
  failed += 1;
} finally {
  try { browser.kill(); } catch { /* 忽略 */ }
  try { spawnSync("taskkill", ["/F", "/T", "/PID", String(browser.pid)], { stdio: "ignore" }); } catch { /* 忽略 */ }
  for (const dir of UDDS) { try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* 忽略 */ } }
}

process.exit(failed === 0 ? 0 : 1);
