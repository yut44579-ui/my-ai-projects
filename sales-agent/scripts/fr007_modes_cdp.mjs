// fr007_modes_cdp.mjs · FR-007「响应路由解耦」的真浏览器模式矩阵（真 Edge，零依赖）
//
//   node scripts/fr007_modes_cdp.mjs http://127.0.0.1:8537
//
// 跑之前先起一个真服务（沙箱目录只是别把本次操作写进 state/）：
//   SRA_STATE_DIR=outputs/_cdp_fr007/state SRA_DOC_DIR=outputs/_cdp_fr007/documents \
//   SRA_UPLOAD_DIR=outputs/_cdp_fr007/uploads SRA_OUTPUT_DIR=outputs/_cdp_fr007/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8537
// （本脚本自己等 /api/health + /api/chat/capabilities 就绪，那张大表的预热不用手动等。）
//
// 为什么要有这一条（评审原话：**比截图验收重要得多**）：
// 施工指令 §八G 要求的是"**模式矩阵**"—— 七句话，各自落到哪一档、有没有调销售工具、
// 页面上是单值还是分析还是报告。这件事只有真浏览器 + 真后端能验：
//   M1 你好           → general  → 无销售工具 → 普通回答
//   M2 1+1等于多少？   → direct   → 无销售工具 → 单值
//   M3 什么是毛利率    → general  → 无销售工具 → 概念说明
//   M4 本期销售额     → direct   → 有销售工具 → 单值（事实表只有一行）
//   M5 为什么本周比上周高 → analysis → 有销售工具 → 分析（事实 + 推断 + 建议）
//   M6 帮我生成上周周报 → report   → 有销售工具 → 报告（文档面板 + 可下载）
//   M7 怎么导出数据    → help     → 无销售工具 → 帮助
// 外加一条：**界面上不出现实现细节**（意图名 / 档位名 / 工具名 / 路由字段 / 接口路径）。
//
// 截图落在 outputs/_cdp_shots_fr007/。

const BASE = process.argv[2] || "http://127.0.0.1:8537";
const PORT = 9339;
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];

const { spawn } = await import("node:child_process");
const os = await import("node:os");
const path = await import("node:path");
const fs = await import("node:fs");

const edge = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
if (!edge) {
  console.log("❌ 没找到 Edge，无法实测");
  process.exit(1);
}
const SHOT_DIR = process.argv[3] || path.join(process.cwd(), "outputs", "_cdp_shots_fr007");
fs.mkdirSync(SHOT_DIR, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

// 本脚本会真注册一个账号 → 每次跑都要干净的一次：只清**沙箱**里的账号表，真实 state/ 不碰
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const sandboxAccounts = path.join(process.cwd(), "outputs", "_cdp_fr007", "state", "accounts.json");
  if (fs.existsSync(sandboxAccounts)) {
    fs.rmSync(sandboxAccounts);
    console.log(`已清空沙箱账号表：${sandboxAccounts}`);
  }
}

console.log("等数据预热（首次要读那张大表，可能要一两分钟）…");
try {
  const warm = await fetch(BASE + "/api/chat/capabilities", { signal: AbortSignal.timeout(900000) });
  if (!warm.ok) {
    console.log(`❌ 数据预热失败（${warm.status}）`);
    process.exit(1);
  }
  console.log("数据已就绪，开始走查。");
} catch (err) {
  console.log(`❌ 等数据预热超时/失败：${err}`);
  process.exit(1);
}

const udd = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr007_cdp_"));
const browser = spawn(edge, [
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${udd}`,
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--window-size=1600,1000", BASE + "/",
], { stdio: "ignore" });
browser.unref();

let passed = 0;
let failed = 0;
function check(ok, label, extra = "") {
  if (ok) passed += 1; else failed += 1;
  console.log(`   ${ok ? "✅" : "❌"} ${label}${extra ? " | " + extra : ""}`);
}
function step(title) {
  console.log(`\n── ${title} ${"─".repeat(Math.max(2, 56 - title.length))}`);
}

// 界面上**不许出现**的实现细节（意图名 / 档位名 / 路由字段 / 工具名 / 接口路径）
const FORBIDDEN_ON_SCREEN = [
  "sales_analysis", "data_lookup", "general_qa", "report_generation", "system_help",
  "response_mode", "routing", "sales_summary", "sales_compare", "top_products",
  "sales_breakdown_by_country", "customer_analysis", "product_analysis",
  "/api/", "TASK-", "intent", "Intent",
];

// 七句话 → 期望的形态（分段标题由**后端**给，这里照抄后端当前的文案，等于顺带钉住它）
const MATRIX = [
  { id: "M1", question: "你好", mode: "general", salesTool: false,
    titles: ["【你好】"], note: "普通回答" },
  { id: "M2", question: "1+1等于多少？", mode: "direct", salesTool: false,
    titles: ["【回答】"], note: "单值" },
  { id: "M3", question: "什么是毛利率？", mode: "general", salesTool: false,
    titles: ["【什么是毛利率】"], note: "概念说明" },
  { id: "M4", question: "本期销售额是多少？", mode: "direct", salesTool: true,
    titles: ["【回答】"], note: "单值（事实表只有一行）" },
  { id: "M5", question: "2011年11月和10月的销售额对比", mode: "analysis", salesTool: true,
    titles: ["【发生了什么】", "【为什么】", "【建议行动】"], note: "分析" },
  { id: "M6", question: "帮我生成上周周报", mode: "report", salesTool: true,
    titles: ["【为什么】", "【建议行动】"], note: "报告" },
  { id: "M7", question: "怎么导出数据", mode: "help", salesTool: false,
    titles: ["【导出与下载】"], note: "帮助" },
];

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
  if (!page) throw new Error("没找到页面 target");

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
  const shot = async (name) => {
    const s = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(SHOT_DIR, name), Buffer.from(s.data, "base64"));
  };

  await send("Runtime.enable");
  await send("Page.enable");

  let domReady = false;
  for (let i = 0; i < 150; i++) {
    domReady = await evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate"))
      && Boolean(document.getElementById("register-form"))`);
    if (domReady) break;
    await sleep(200);
  }
  if (!domReady) throw new Error("页面 30 秒内没建出登录页的 DOM");

  // ══════════════════════════════════════════════════════════════════
  // ① 登录：注册一个本机账号（第一个账号 = 管理员，注册完直接进系统）
  // ══════════════════════════════════════════════════════════════════
  step("① 注册并进入系统");
  const setField = `(id, value) => {
    const el = document.getElementById(id);
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }`;
  const readCaptchaCode = (imgId) => evalJs(`(() => {
    const img = document.getElementById(${JSON.stringify(imgId)});
    if (!img || !img.src) return "";
    const svg = decodeURIComponent((img.src.split(",")[1] || ""));
    const found = [];
    svg.split("</text>").forEach((part) => {
      const at = part.lastIndexOf(">");
      if (at >= 0 && part.length - at === 2) found.push(part.slice(at + 1));
    });
    return found.join("");
  })()`);

  await evalJs(`document.getElementById("ltab-register").click()`);
  await sleep(300);
  await evalJs(`(${setField})("reg-name", "fr007")`);
  await evalJs(`(${setField})("reg-display", "路由验收员")`);
  await evalJs(`(${setField})("reg-pwd", "fr007pass123")`);
  await evalJs(`(${setField})("reg-pwd2", "fr007pass123")`);
  const code = await readCaptchaCode("reg-captcha-img");
  await evalJs(`(${setField})("reg-captcha", ${JSON.stringify(code)})`);
  await evalJs(`document.getElementById("reg-name").dispatchEvent(new Event("blur", { bubbles: true }))`);
  await sleep(900);
  await evalJs(`document.getElementById("btn-register").click()`);
  await sleep(2500);
  const gate = await evalJs(`(() => {
    const g = document.getElementById("login-gate");
    return { hidden: g.hidden, pending: !document.getElementById("register-pending").hidden,
             msg: (document.querySelector("#register-form .field-msg:not([hidden])") || {}).textContent || "" };
  })()`);
  check(gate.hidden || gate.pending, "注册入口能用（进系统或如实说待批准）", JSON.stringify(gate));
  if (!gate.hidden) {
    console.log("❌ 没进系统，后面的模式矩阵无法进行");
    ws.close(); browser.kill(); process.exit(1);
  }
  await shot("00-登录后.png");

  // ══════════════════════════════════════════════════════════════════
  // ② 七句话逐一提问（每句都等回答渲染完再量）
  // ══════════════════════════════════════════════════════════════════
  const askAndRead = async (question) => {
    // 提交前先记下"回答区长什么样"：只等"问题文本对上"是不够的 ——
    // 提交那一瞬间上一个回答还挂在页面上，会读到**上一条**的分段（这里真踩过：
    // 「你好」的【回答】被当成了「什么是毛利率？」的结果）。
    const before = await evalJs(`(() => {
      const a = document.getElementById("chat-answer");
      return { html: a ? a.innerHTML : "", q: (document.getElementById("chat-question") || {}).textContent || "" };
    })()`);
    await evalJs(`(() => {
      const input = document.getElementById("hero-nl-input") || document.getElementById("nl-input");
      input.value = ${JSON.stringify(question)};
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    await evalJs(`(document.getElementById("hero-nl-btn") || document.getElementById("nl-ask")).click()`);
    // 等"这一次"真的回来：问题文本对上 **且** 回答区内容已经换过
    for (let i = 0; i < 400; i++) {
      const state = await evalJs(`(() => {
        const q = (document.getElementById("chat-question") || {}).textContent || "";
        const a = document.getElementById("chat-answer");
        return { q, html: a ? a.innerHTML : "", answered: Boolean(a && a.children.length) };
      })()`);
      if (state.q.trim() === question.trim() && state.answered && state.html !== before.html) {
        await sleep(400);                       // 让渲染落定（报告面板/事实表都是同步渲染）
        break;
      }
      await sleep(500);
    }
    return evalJs(`(() => {
      const text = (el) => (el ? el.textContent : "");
      const sections = Array.from(document.querySelectorAll("#chat-answer .answer-section"));
      const factsBox = document.getElementById("chat-facts");
      const factRows = factsBox ? factsBox.querySelectorAll("table tr").length : 0;   // 含表头行
      const panel = document.querySelector("#chat-answer .report");
      return {
        question: text(document.getElementById("chat-question")),
        badge: text(document.getElementById("chat-status-badge")),
        notice: text(document.getElementById("chat-notice")),
        sections: sections.map((s) => text(s.querySelector(".answer-head b"))),
        answerText: text(document.getElementById("chat-answer")),
        factsText: text(factsBox),
        factRows,
        hasReportPanel: Boolean(panel),
        reportTitle: panel ? text(panel.querySelector(".report-bar b")) : "",
        bodyText: text(document.body),
      };
    })()`);
  };

  step("② 模式矩阵：七句话各走各的档");
  for (const item of MATRIX) {
    const got = await askAndRead(item.question);
    const salesTool = got.factRows > 1;         // 事实表多于表头 = 真的算了销售指标
    const noSalesText = !/销售额[:：]/.test(got.factsText || "");

    check(got.sections.join("|") === item.titles.join("|"),
      `${item.id} 「${item.question}」落在 ${item.note} 档（分段完全一致）`,
      `实际=${JSON.stringify(got.sections)} 期望=${JSON.stringify(item.titles)}`);
    if (item.salesTool) {
      check(salesTool, `${item.id} 确实调了销售工具（事实表有内容）`, `行数=${got.factRows}`);
    } else {
      check(!salesTool && noSalesText, `${item.id} 没有调销售工具（事实区没有指标）`,
        `行数=${got.factRows} 事实区=「${(got.factsText || "").slice(0, 40)}」`);
    }
    if (item.mode === "direct") {
      check(got.factRows <= 2, `${item.id} 单值档：事实表只有一行`, `行数=${got.factRows}`);
      check(!/【主要贡献】|【为什么】|【建议行动】/.test(got.answerText || ""),
        `${item.id} 单值档：没有贡献/为什么/建议三段`);
    }
    if (item.mode === "report") {
      check(got.hasReportPanel, `${item.id} 报告档：页面上有报告面板`, `标题=「${got.reportTitle}」`);
      check(/【为什么】/.test(got.answerText || ""), `${item.id} 报告档：保留结论段`);
    }
    if (item.mode === "analysis") {
      check(/【发生了什么】/.test(got.answerText || ""), `${item.id} 分析档：有事实段`);
    }
    // 界面上不出现实现细节（意图名 / 档位名 / 路由字段 / 工具名 / 接口路径）
    const leaked = FORBIDDEN_ON_SCREEN.filter((word) => (got.bodyText || "").includes(word));
    check(leaked.length === 0, `${item.id} 界面上没有实现细节`, leaked.join(","));
    await shot(`${item.id}-${item.mode}.png`);
  }

  // ══════════════════════════════════════════════════════════════════
  // ③ 历史列表：非销售问答也能点回来（记录是完整的一条）
  // ══════════════════════════════════════════════════════════════════
  step("③ 历史列表与刷新后复查");
  const history = await evalJs(`(() => {
    const rows = document.querySelectorAll("#chat-history [data-conversation-id]");
    return { count: rows.length,
             first: rows.length ? (rows[0].textContent || "").slice(0, 60) : "" };
  })()`);
  check(history.count >= 7, "七次提问都进了历史列表", `条数=${history.count}`);
  check(!/sales_|data_lookup|general_qa|system_help|arithmetic/.test(history.first),
    "历史列表里没有意图键名", `首条=「${history.first}」`);

  await evalJs(`location.reload()`);
  await sleep(3500);
  const afterReload = await evalJs(`(() => {
    const body = document.body.textContent || "";
    const leaked = ${JSON.stringify(FORBIDDEN_ON_SCREEN)}.filter((w) => body.includes(w));
    return { leaked, history: document.querySelectorAll("#chat-history [data-conversation-id]").length };
  })()`);
  check(afterReload.leaked.length === 0, "刷新后界面上依然没有实现细节", afterReload.leaked.join(","));
  check(afterReload.history >= 7, "刷新后历史仍在（记录真的落盘了）", `条数=${afterReload.history}`);
  await shot("99-刷新后.png");

  const realErrors = consoleErrors.filter((line) => !/favicon|Failed to load resource/i.test(line));
  check(realErrors.length === 0, "控制台没有真错误", realErrors.slice(0, 3).join(" | "));
} catch (err) {
  console.log(`\n❌ 走查中断：${err?.message || err}`);
  failed += 1;
} finally {
  try { ws?.close(); } catch { /* 无所谓 */ }
  try { browser.kill(); } catch { /* 无所谓 */ }
}

console.log(`\n═══ FR-007 模式矩阵：${passed} 项通过 / ${failed} 项失败 ═══`);
console.log(`截图目录：${SHOT_DIR}`);
process.exit(failed === 0 ? 0 : 1);
