// stepa_topbar_cdp.mjs · A-08 顶栏实测（真 Edge + CDP，零依赖：Node 内置 fetch + WebSocket）
//
//   node scripts/stepa_topbar_cdp.mjs http://127.0.0.1:8520
//
// 为什么要有这一条：A-08 说的是"窄窗口下按钮文字不被截断、不重叠、不横向溢出"，
// 这是**像素级**的事 —— 静态检查看 HTML/CSS 看不出来，只有真浏览器量 `scrollWidth` /
// `clientWidth` / `getBoundingClientRect()` 才算数（用户反馈的正是"按钮被压成单字"）。
//
// 量什么（每个宽度一遍）：
//   ① 顶栏自身不横向溢出（scrollWidth <= clientWidth）；
//   ② 五件套（数据源 / 提问框 / 分析 / ☰ / 用户区）都真实可见（宽度 > 0）；
//   ③ 「分析」按钮的 scrollWidth <= clientWidth（文字没被裁）+ 高度是单行 + white-space: nowrap；
//   ④ 提问框吃满剩余宽度（越宽越大），且不小于 80px；
//   ⑤ 任何可见元素的右边界都不越出视口；页面本身不出现横向滚动条；
//   ⑥ 顶栏里没有能力清单那种长文案（id="nl-note" 已挪到首页）。

const BASE = process.argv[2] || "http://127.0.0.1:8520";
const PORT = 9333;
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
const WIDTHS = [1600, 1440, 1280, 1180, 1024, 900, 820];

const { spawn } = await import("node:child_process");
const os = await import("node:os");
const path = await import("node:path");
const fs = await import("node:fs");

const edge = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
if (!edge) {
  console.log("❌ 没找到 Edge，无法实测");
  process.exit(1);
}

const udd = fs.mkdtempSync(path.join(os.tmpdir(), "sra_topbar_"));
// headless Edge 不会自己退出 → 必须 unref（否则 node 事件循环被它吊住，脚本跑完不返回）
const browser = spawn(edge, [
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${udd}`,
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--window-size=1600,1000", BASE + "/",
], { stdio: "ignore" });
browser.unref();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let passed = 0;
let failed = 0;
function check(ok, label, extra = "") {
  if (ok) passed += 1; else failed += 1;
  console.log(`   ${ok ? "✅" : "❌"} ${label}${extra ? " | " + extra : ""}`);
}

let ws;
try {
  let targets = null;
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      targets = await r.json();
      if (targets.some((t) => t.type === "page")) break;
    } catch { /* 还没起来 */ }
    await sleep(500);
  }
  const page = targets?.find((t) => t.type === "page" && t.url.startsWith(BASE));
  if (!page) throw new Error("没找到页面 target：" + JSON.stringify(targets?.map((t) => t.url)));

  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let seq = 0;
  const pending = new Map();
  const consoleErrors = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
      consoleErrors.push((msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "));
    }
    if (msg.method === "Runtime.exceptionThrown") {
      consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const id = ++seq;
    pending.set(id, (m) => res(m.result ?? m.error));
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r?.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval failed");
    return r?.result?.value;
  };

  await send("Runtime.enable");
  await send("Page.enable");

  const MEASURE = `(() => {
    const q = (s) => document.querySelector(s);
    const box = (el) => { const r = el.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left), right: Math.round(r.right),
               sw: el.scrollWidth, cw: el.clientWidth, text: (el.textContent || "").trim() }; };
    const top = q(".topbar");
    const ask = q("#nl-ask"), input = q("#nl-input"), chip = q("#datasource-chip");
    const menu = q("#btn-menu"), user = q(".user-area");
    const visible = (el) => Boolean(el) && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
    const missing = [["datasource-chip", chip], ["nl-input", input], ["nl-ask", ask],
                     ["btn-menu", menu], ["user-area", user]].filter(([, el]) => !visible(el)).map(([n]) => n);
    const spill = [...top.querySelectorAll("*")]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1)
      .map((el) => el.id || el.className || el.tagName).slice(0, 5);
    return {
      innerWidth: window.innerWidth,
      docScrollWidth: document.documentElement.scrollWidth,
      topScrollWidth: top.scrollWidth, topClientWidth: top.clientWidth,
      topChildren: [...top.children].map((el) => el.id || el.className || el.tagName),
      hasNlNote: Boolean(q("#nl-note")) || Boolean(top.querySelector(".nl-note")),
      missing,
      spill,
      ask: ask ? box(ask) : null,
      askWhiteSpace: ask ? getComputedStyle(ask).whiteSpace : null,
      askFontSize: ask ? getComputedStyle(ask).fontSize : null,
      input: input ? box(input) : null,
      chip: chip ? box(chip) : null,
      menu: menu ? box(menu) : null,
      user: user ? box(user) : null,
      brandText: q(".brand-text") ? getComputedStyle(q(".brand-text")).display : null,
    };
  })()`;

  console.log(`\n=== A-08 顶栏在 ${WIDTHS.join(" / ")} px 下的实测（真 Edge + CDP）===`);
  const inputWidths = [];
  for (const width of WIDTHS) {
    await send("Emulation.setDeviceMetricsOverride", {
      width, height: 900, deviceScaleFactor: 1, mobile: false,
    });
    await sleep(600);
    const m = await evalJs(MEASURE);
    console.log(`\n  ── ${width}px（innerWidth=${m.innerWidth}）`);
    check(m.topScrollWidth <= m.topClientWidth + 1,
      "顶栏自身不横向溢出", `scrollWidth=${m.topScrollWidth} ≤ clientWidth=${m.topClientWidth}`);
    check(m.missing.length === 0, "五件套都在（数据源 / 提问框 / 分析 / ☰ / 用户区）", "缺：" + m.missing);
    check(!m.hasNlNote, "顶栏里没有能力清单长文案（已挪到首页）");
    check(m.ask.sw <= m.ask.cw + 1, "「分析」按钮文字没被裁",
      `scrollWidth=${m.ask.sw} ≤ clientWidth=${m.ask.cw}`);
    check(m.ask.h <= 30, "「分析」按钮是单行高度（没被压成竖排）", `height=${m.ask.h}px`);
    check(m.askWhiteSpace === "nowrap", "「分析」按钮 white-space: nowrap", String(m.askWhiteSpace));
    check(m.ask.text === "分析", "「分析」按钮文字完整", JSON.stringify(m.ask.text));
    check(m.input.w >= 80, "提问框仍有可用宽度", `${m.input.w}px`);
    check(m.menu.w >= 28 && m.menu.h <= 34, "☰ 按钮没有被压扁",
      `w=${m.menu.w} h=${m.menu.h}`);
    check(m.user.w > 0, "用户区可见", `w=${m.user.w}`);
    check(m.spill.length === 0, "没有元素越出视口右边界", "越界：" + m.spill);
    check(m.docScrollWidth <= m.innerWidth + 1, "页面没有横向滚动条",
      `doc=${m.docScrollWidth} ≤ ${m.innerWidth}`);
    // 数据源名称允许用省略号收尾（设计如此），但必须还能看见一部分
    check(m.chip.w > 0, "数据源标签还在（收窄而不是隐藏）", `w=${m.chip.w}（允许「…」）`);
    inputWidths.push(m.input.w);
  }
  // ④ 提问框"吃满剩余宽度"：窗口越窄它越窄（说明它在拉伸），而不是固定宽度把别人挤走
  check(inputWidths[0] >= 300, "宽窗口下提问框吃满剩余宽度", `1600px 时 ${inputWidths[0]}px`);
  check(inputWidths[0] >= inputWidths[inputWidths.length - 1],
    "窗口变窄时提问框跟着收（它在拉伸，不是固定宽度）",
    `${inputWidths[0]}px → ${inputWidths[inputWidths.length - 1]}px`);
  check(consoleErrors.length === 0, "调窗口尺寸过程中没有 JS 报错", consoleErrors.slice(0, 2).join(" | "));

  // ── A-09：逐页扫"渲染出来的文字"（innerText，不是源码）────────────────
  const BANNED = ["TASK-", "/api/", "openapi", "白名单", "数字闸门", "sha256",
                  "file_id", "哈希", "版本：", "Repository", "Executor", "pytest",
                  "落盘", "dataset_id"];
  const ROUTES = ["overview", "sales", "customers", "products", "raw",
                  "data", "weekly", "settings", "anomaly"];
  console.log(`\n=== A-09 九个页面逐页扫技术词（innerText）===`);
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  for (const route of ROUTES) {
    await evalJs(`location.hash = "#/${route}"`);
    await sleep(2500);
    const text = await evalJs(`document.body.innerText`);
    const hits = BANNED.filter((t) => text.includes(t));
    check(hits.length === 0, `#/${route}：页面上没有技术实现信息`, hits.join(" , "));
    check(!/undefined|NaN/.test(text), `#/${route}：没有 undefined / NaN`);
  }
  await evalJs(`location.hash = "#/data"`);
  await sleep(2500);
  const dataText = await evalJs(`document.getElementById("datasets-body").innerText`);
  check(dataText.includes("销售数据"), "数据管理页的「数据源」表列出了内置数据源",
    dataText.replace(/\s+/g, " ").slice(0, 90));
  check(!/[\w.\-]{24,}/.test(dataText), "数据源表里没有长串哈希/编号");
  const importText = await evalJs(`document.body.innerText`);
  check(importText.includes("导入数据"), "数据管理页有「导入数据」入口");
  await evalJs(`location.hash = "#/overview"`);
  await sleep(3000);
  const ovText = await evalJs(`document.getElementById("ov-customers").innerText`);
  check(/\d/.test(ovText) && ovText.split("\n").length >= 3,
    "首页「客户 TOP5」渲染出真实表格", ovText.replace(/\s+/g, " ").slice(0, 90));
  check(consoleErrors.length === 0, "整个走查过程没有 JS 报错", consoleErrors.slice(0, 2).join(" | "));
  console.log(`\n通过 ${passed} 项，失败 ${failed} 项`);
  process.exitCode = failed ? 1 : 0;
} catch (err) {
  console.log(`❌ 探针失败：${err}`);
  process.exitCode = 1;
} finally {
  if (ws) ws.close();
  try { browser.kill(); } catch { /* 已经退出 */ }
  // Edge 可能还攥着临时 profile 目录（Windows 上常见）——清理失败不影响结论，别让它把退出码带坏
  try { fs.rmSync(udd, { recursive: true, force: true }); } catch { /* 留给系统清 temp */ }
}
