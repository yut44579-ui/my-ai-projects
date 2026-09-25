// sort_range_cdp.mjs · 排序状态机 + 时间范围提示的实测（真 Edge + CDP，零依赖）
//
//   node scripts/sort_range_cdp.mjs http://127.0.0.1:8520
//
// 为什么必须打真浏览器：这一批修的是**交互状态**——点表头、切指标、翻页、敲日期框，
// 每一步之后"排序有没有被悄悄改掉"只有真点一遍才知道（静态检查只能看到代码里有没有那行字）。
//
// 量什么（对应评审冻结规格的四条 Rule + 时间范围）：
//   R1 按日首次加载：默认按日期升序（时间序列语义），页面上写着"当前排序"
//   R2 默认态切指标：按日**仍然是日期升序**（不许被改成指标降序 —— 最容易搞反的一条）
//   R3 点表头 → 人工排序；再切指标 → ★人工排序不许被覆盖（本次最重要的回归）
//   R4 翻页 → 排序完整保留（第 1 页按订单数、第 2 页也得按订单数）
//   R5 切维度 → 清掉人工排序，回到新维度的默认排序（按国家 = 当前指标降序）
//   R6 时间范围：不再是"更新于 X"；日期框钉住数据边界；越界即时提示 + 一键用数据范围

const BASE = process.argv[2] || "http://127.0.0.1:8520";
const PORT = 9334;
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

const udd = fs.mkdtempSync(path.join(os.tmpdir(), "sra_sort_"));
// 截图落在 outputs/_cdp_shots/（命令行第二个参数可改）——给验收留可看的证据
const SHOT_DIR = process.argv[3] || path.join(process.cwd(), "outputs", "_cdp_shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });
const browser = spawn(edge, [
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${udd}`,
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--window-size=1600,1000", BASE + "/#/sales",
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
  const waitFor = async (expr, ms = 90000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (await evalJs(expr)) return true;
      await sleep(400);
    }
    return false;
  };

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false,
  });

  // ── 先进主界面（登录门是真实存在的：未登录时 app.js 的初始化会被挂住）──
  // 用「游客」入口：业务表格游客能看，受限的只有导出/任务/导入（那几个不在本次范围内）。
  let domReady = false;
  for (let i = 0; i < 150; i += 1) {
    domReady = await evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate"))
      && Boolean(document.getElementById("ltab-guest"))
      && Boolean(document.getElementById("btn-guest"))`);
    if (domReady) break;
    await sleep(200);
  }
  if (!domReady) throw new Error("页面 30 秒内没建出登录页的 DOM");
  await evalJs(`document.getElementById("ltab-guest").click()`);
  await sleep(200);
  await evalJs(`document.getElementById("btn-guest").click()`);
  const loggedIn = await waitFor(`document.getElementById("login-gate").hidden === true`, 20000);
  if (!loggedIn) throw new Error("游客身份没进主界面");
  console.log("   （以游客身份进入主界面：业务表格可见，导出等受限入口不在本次范围内）");

  // 页面上的读取器：只读 DOM 上**画出来的东西**（innerText / 属性），不碰任何内部变量
  const READ = `(() => {
    const root = document.querySelector("#tbl-sales");
    if (!root) return null;
    const tds = (tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent.trim());
    const rows = [...root.querySelectorAll("tbody tr")].map(tds);
    const selects = [...root.querySelectorAll("select")];
    const dates = [...root.querySelectorAll('input[type="date"]')];
    const hint = root.querySelector(".range-hint");
    const btn = [...root.querySelectorAll("button")].find((b) => b.textContent.includes("使用数据范围"));
    return {
      sortLine: (root.querySelector(".sort-line") || {}).textContent || "",
      head: [...root.querySelectorAll("thead th")]
        .filter((th) => th.classList.contains("sorted-asc") || th.classList.contains("sorted-desc"))
        .map((th) => th.textContent.trim()
          + ":" + (th.classList.contains("sorted-asc") ? "asc" : "desc")),
      rows: rows.slice(0, 3),
      rowCount: rows.length,
      pager: (root.querySelector(".pager") || {}).textContent || "",
      note: (document.querySelector("#sales-scope-note") || {}).textContent || "",
      footNote: (document.querySelector("#tbl-sales-note") || {}).textContent || "",
      dimensions: selects[0] ? selects[0].value : "",
      metrics: selects.length > 1 ? selects[1].value : "",
      start: dates[0] ? { value: dates[0].value, min: dates[0].min, max: dates[0].max } : null,
      end: dates[1] ? { value: dates[1].value, min: dates[1].min, max: dates[1].max } : null,
      hintHidden: hint ? hint.hidden : null,
      hintText: hint ? hint.textContent.trim() : "",
      hasRangeButton: Boolean(btn),
      visibleRows: (root.querySelector(".table-wrap") || {}).hidden === false ? rows.length : 0,
    };
  })()`;

  // 页面上的操作器（点按钮 / 选下拉 / 敲日期，全部走真实事件）
  const ACT = {
    clickHeader: (label) => `(() => {
      const th = [...document.querySelectorAll("#tbl-sales thead th")]
        .find((el) => el.textContent.trim() === ${JSON.stringify(label)});
      if (!th) return false; th.click(); return true;
    })()`,
    setSelect: (index, value) => `(() => {
      const sel = document.querySelectorAll("#tbl-sales select")[${index}];
      if (!sel) return false; sel.value = ${JSON.stringify(value)};
      sel.dispatchEvent(new Event("change")); return true;
    })()`,
    clickButton: (label) => `(() => {
      // 按钮文字要去掉那个 ⚠ 记号再比（游客态下受限入口带记号，业务按钮不带）
      const btn = [...document.querySelectorAll("#tbl-sales button")]
        .find((b) => b.textContent.replace(/⚠/g, "").trim() === ${JSON.stringify(label)});
      if (!btn) return false; btn.click(); return true;
    })()`,
    typeSearch: (value) => `(() => {
      const input = document.querySelector('#tbl-sales input[type="search"]');
      if (!input) return false; input.value = ${JSON.stringify(value)}; return true;
    })()`,
    typeDate: (index, value) => `(() => {
      const input = document.querySelectorAll('#tbl-sales input[type="date"]')[${index}];
      if (!input) return false; input.value = ${JSON.stringify(value)};
      input.dispatchEvent(new Event("input")); input.dispatchEvent(new Event("change")); return true;
    })()`,
    lastOrderOfPage: (columnIndex) => `(() => {
      const rows = [...document.querySelectorAll("#tbl-sales tbody tr")];
      const last = rows[rows.length - 1];
      if (!last) return null;
      const cell = last.querySelectorAll("td")[${columnIndex}];
      return cell ? Number(cell.textContent.replace(/[^0-9.-]/g, "")) : null;
    })()`,
  };

  const clickHeader = async (label) => evalJs(ACT.clickHeader(label));
  const setSelect = async (i, v) => evalJs(ACT.setSelect(i, v));
  const clickButton = async (label) => evalJs(ACT.clickButton(label));
  const typeDate = async (i, v) => evalJs(ACT.typeDate(i, v));
  const read = () => evalJs(READ);
  const shot = async (name) => {
    const r = await send("Page.captureScreenshot", { format: "png" });
    if (r?.data) fs.writeFileSync(path.join(SHOT_DIR, name), Buffer.from(r.data, "base64"));
  };
  // 删除线：页面正在读取时不读数（status 那一格就是它的进度）
  const settle = async () => {
    await sleep(500);
    await waitFor(`document.querySelector("#tbl-sales-status").textContent === ""`, 150000);
  };

  console.log(`\n=== 排序状态机 + 时间范围 实测（真 Edge + CDP @ ${BASE}）===`);

  await evalJs(`location.hash = "#/sales"`);
  const ready = await waitFor(`(() => {
    const t = document.querySelector("#tbl-sales tbody");
    return Boolean(t && t.querySelectorAll("tr").length > 0);
  })()`, 180000);
  if (!ready) throw new Error("销售表一直没渲染出行（先确认服务已预热）");
  await settle();

  // ── R1 按日首次加载：日期升序（时间序列语义）──────────────────────────
  let s = await read();
  console.log(`\n  ── R1 按日默认排序`);
  check(s.dimensions === "day" && s.metrics === "sales_amount", "首次加载是「按日 + 销售额」",
    `dimension=${s.dimensions} metric=${s.metrics}`);
  check(s.sortLine.trim() === "当前排序：期间（按日） ↑", "页面上写出了「当前排序」", JSON.stringify(s.sortLine.trim()));
  check(s.head.some((h) => h.startsWith("期间") && h.endsWith("asc")), "表头高亮标在「期间」列上（升序）", s.head.join(" , "));
  const dates1 = s.rows.map((r) => r[0]);
  check(dates1[0] < dates1[1], "第一列确实是日期升序（时间序列的阅读顺序）", dates1.join(" → "));
  console.log(`     证据（前 3 行：期间 / 销售额 / 订单数）：${s.rows.map((r) => r.slice(0, 3).join(" ")).join(" | ")}`);
  await shot("sort-01-default-day.png");

  // ── R2 默认态切指标：按日仍是日期升序（★ 不许改成指标降序）────────────
  console.log(`\n  ── R2 默认态切指标 = 订单数（按日）`);
  await setSelect(1, "order_count");
  await waitFor(`document.querySelector("#tbl-sales .pager-summary").textContent.includes("订单数合计")`);
  await settle();
  s = await read();
  check(s.sortLine.trim() === "当前排序：期间（按日） ↑", "按日切指标后仍然是「期间（按日）↑」",
    JSON.stringify(s.sortLine.trim()));
  const dates2 = s.rows.map((r) => r[0]);
  check(dates2[0] < dates2[1], "日期仍然升序（没有被改成订单数降序）", dates2.join(" → "));
  console.log(`     证据（前 3 行：期间 / 销售额 / 订单数）：${s.rows.map((r) => r.slice(0, 3).join(" ")).join(" | ")}`);

  // ── R3 点表头 → 人工排序；切指标 → ★ 不许被覆盖 ───────────────────────
  console.log(`\n  ── R3 点表头（人工排序）→ 再切指标`);
  check(await clickHeader("订单数"), "点到了「订单数」表头");
  await waitFor(`document.querySelector("#tbl-sales .sort-line").textContent.includes("订单数 ↓")`);
  await settle();
  s = await read();
  const orders3 = s.rows.map((r) => Number(r[2].replace(/[^\d.-]/g, "")));
  check(s.sortLine.trim() === "当前排序：订单数 ↓", "点表头后：当前排序 = 订单数 ↓", JSON.stringify(s.sortLine.trim()));
  check(orders3[0] >= orders3[1], "订单数确实降序", orders3.join(" ≥ "));
  console.log(`     证据（前 3 行：期间 / 订单数）：${s.rows.map((r) => `${r[0]} ${r[2]}`).join(" | ")}`);

  await setSelect(1, "customer_count");
  await waitFor(`document.querySelector("#tbl-sales .pager-summary").textContent.includes("客户数合计")`);
  await settle();
  s = await read();
  const orders3b = s.rows.map((r) => Number(r[2].replace(/[^\d.-]/g, "")));
  check(s.sortLine.trim() === "当前排序：订单数 ↓", "★ 切指标后人工排序**没被覆盖**（仍是订单数 ↓）",
    JSON.stringify(s.sortLine.trim()));
  check(orders3b[0] >= orders3b[1], "★ 数据顺序也没变（仍按订单数降序）", orders3b.join(" ≥ "));
  console.log(`     证据（前 3 行：期间 / 订单数 / 客户数）：${s.rows.map((r) => `${r[0]} ${r[2]} ${r[3]}`).join(" | ")}`);
  await shot("sort-02-user-sort-kept.png");

  // ── R4 翻页：排序完整保留 ────────────────────────────────────────────
  console.log(`\n  ── R4 翻页（第 1 页 → 第 2 页）`);
  const lastOfPage1 = await evalJs(ACT.lastOrderOfPage(2));   // 第 1 页**最后一行**的订单数
  check(await clickButton("下一页"), "点到了「下一页」");
  await waitFor(`document.querySelector("#tbl-sales .pager").textContent.includes("第 2 /")`);
  await settle();
  s = await read();
  const orders4 = s.rows.map((r) => Number(r[2].replace(/[^\d.-]/g, "")));
  check(s.sortLine.trim() === "当前排序：订单数 ↓", "翻页后排序状态完整保留", JSON.stringify(s.sortLine.trim()));
  check(orders4[0] <= lastOfPage1, "第 2 页接着第 1 页排（没有回到默认排序）",
    `第1页末 ${lastOfPage1} ≥ 第2页首 ${orders4[0]}`);
  console.log(`     证据（第 2 页前 3 行）：${s.rows.map((r) => `${r[0]} ${r[2]}`).join(" | ")}`);

  // ── R5 切维度：清掉人工排序，用新维度默认排序 ────────────────────────
  console.log(`\n  ── R5 切维度到「按国家」`);
  await setSelect(0, "country");
  await waitFor(`document.querySelector("#tbl-sales-note").textContent.includes("按国家")`);
  await settle();
  s = await read();
  check(!/^\d{4}-\d{2}-\d{2}$/.test(s.rows[0][0]), "第一列真的换成了国家", s.rows[0][0]);
  check(s.sortLine.trim() === "当前排序：客户数 ↓", "换维度后回到新维度的默认排序（当前指标降序）",
    JSON.stringify(s.sortLine.trim()));
  const cust5 = s.rows.map((r) => Number(r[3].replace(/[^\d.-]/g, "")));
  check(cust5[0] >= cust5[1], "数据确实按客户数降序", cust5.join(" ≥ "));

  console.log(`\n  ── R5b 按国家 + 默认态切指标 = 销售额`);
  await setSelect(1, "sales_amount");
  await waitFor(`document.querySelector("#tbl-sales .pager-summary").textContent.includes("销售额合计")`);
  await settle();
  s = await read();
  check(s.sortLine.trim() === "当前排序：销售额 ↓", "按国家切指标 → 排序跟着切到新指标降序",
    JSON.stringify(s.sortLine.trim()));
  console.log(`     证据（前 3 行：国家 / 销售额）：${s.rows.map((r) => `${r[0]} ${r[1]}`).join(" | ")}`);

  // ── R6 时间范围 ──────────────────────────────────────────────────────
  console.log(`\n  ── R6 时间范围（覆盖范围 / 边界 / 越界提示）`);
  check(s.note.includes("数据覆盖：2010-12-01 ~ 2011-12-09"), "页面上写的是「数据覆盖」",
    JSON.stringify(s.note));
  check(!s.note.includes("更新于"), "业务表上不再出现数据源的登记时间");
  check(s.start && s.start.min === "2010-12-01" && s.start.max === "2011-12-09",
    "起始日期框钉住了数据边界", JSON.stringify(s.start));
  check(s.end && s.end.min === "2010-12-01" && s.end.max === "2011-12-09",
    "结束日期框钉住了数据边界", JSON.stringify(s.end));

  await typeDate(0, "2026-09-01");
  await sleep(300);
  s = await read();
  check(s.hintHidden === false, "敲进 2026-09-01 的**那一刻**就弹出提示（不用等查询）");
  check(s.hintText.includes("超出数据范围") && s.hintText.includes("2010-12-01 ~ 2011-12-09"),
    "提示文案写明了数据只覆盖到哪一天", JSON.stringify(s.hintText));
  check(s.hasRangeButton, "提示旁边有「使用数据范围」按钮");
  console.log(`     证据（提示原文）：${s.hintText.replace(/\s+/g, " ")}`);
  await shot("sort-03-out-of-range.png");

  check(await clickButton("使用数据范围"), "点了「使用数据范围」");
  await waitFor(`document.querySelector("#tbl-sales-status").textContent === ""`);
  await sleep(1200);
  s = await read();
  check(s.start.value === "2010-12-01" && s.end.value === "2011-12-09",
    "起止被一键填成完整数据范围", `${s.start.value} ~ ${s.end.value}`);
  check(s.hintHidden === true, "填好之后提示自己消失");
  check(s.visibleRows > 0, "并且按完整范围重新查出了数据", `可见行 ${s.visibleRows}`);

  console.log(`\n  ── R6b 真正查到范围之外 → 如实 0 条 + 提示`);
  await typeDate(0, "2026-09-01");
  await typeDate(1, "2026-09-10");
  await clickButton("查询");
  await sleep(600);
  await waitFor(`document.querySelector("#tbl-sales-status").textContent === ""`, 60000);
  await sleep(600);
  s = await read();
  check(s.visibleRows === 0, "范围之外就是 0 条（不假装、不截断）", `可见行 ${s.visibleRows}`);
  check(s.hintHidden === false && s.hintText.includes("超出数据范围"),
    "0 条时说的是「超出数据范围」（为什么是空的）+ 快捷入口",
    JSON.stringify(s.hintText.replace(/\s+/g, " ")));
  check(s.hasRangeButton, "0 条时「使用数据范围」入口还在");
  check(s.footNote.includes("不在数据源范围内") && s.footNote.includes("没有记录"),
    "后端的说明原样保留在表下方（不美化、不吞掉）",
    JSON.stringify(s.footNote.slice(0, 60)));
  console.log(`     证据（表下说明）：${s.footNote.replace(/\s+/g, " ").slice(0, 120)}`);

  console.log(`\n  ── R6c 区间合法但筛不出东西 → 也要有说明 + 快捷入口`);
  await typeDate(0, "2010-12-01");
  await typeDate(1, "2011-12-09");
  await evalJs(ACT.typeSearch("zzzz"));
  await clickButton("查询");
  await sleep(600);
  await waitFor(`document.querySelector("#tbl-sales-status").textContent === ""`, 60000);
  await sleep(600);
  s = await read();
  check(s.visibleRows === 0, "没有匹配记录（时间范围合法，是筛选条件筛没了）", `可见行 ${s.visibleRows}`);
  check(s.hintHidden === false && s.hintText.includes("没有记录") && s.hasRangeButton,
    "这时给的是「本次条件下没有记录」+ 快捷入口", JSON.stringify(s.hintText.replace(/\s+/g, " ")));

  // ── R7 另外几张业务表：首次加载也是"销售额降序"+ 同一套覆盖范围文案 ────
  console.log(`\n  ── R7 客户表 / 产品表 / 原始数据（同一套面板代码）`);
  for (const [route, root, noteId] of [["#/customers", "tbl-customers", "cust-scope-note"],
                                       ["#/products", "tbl-products", "prod-scope-note"],
                                       ["#/raw", "tbl-raw", "raw-scope-note"]]) {
    await evalJs(`location.hash = ${JSON.stringify(route)}`);
    await waitFor(`(() => {
      const t = document.querySelector("#${root} tbody");
      return Boolean(t && t.querySelectorAll("tr").length > 0);
    })()`, 180000);
    await sleep(800);
    const one = await evalJs(`(() => {
      const root = document.querySelector("#${root}");
      return {
        sortLine: (root.querySelector(".sort-line") || {}).textContent || "",
        note: (document.getElementById("${noteId}") || {}).textContent || "",
        first: (root.querySelector("tbody tr td") || {}).textContent || "",
      };
    })()`);
    const expected = route === "#/raw" ? "当前排序：序号 ↑" : "当前排序：销售额 ↓";
    check(one.sortLine.trim() === expected, `${route} 的「当前排序」`, JSON.stringify(one.sortLine.trim()));
    check(one.note.includes("数据覆盖：2010-12-01 ~ 2011-12-09") && !one.note.includes("更新于"),
      `${route} 写的是数据覆盖（不是登记时间）`, JSON.stringify(one.note));
    console.log(`     证据：${one.sortLine.trim()} ｜ 首行 ${one.first}`);
  }

  check(consoleErrors.length === 0, "整个走查过程没有 JS 报错", consoleErrors.slice(0, 2).join(" | "));
  console.log(`\n通过 ${passed} 项，失败 ${failed} 项`);
  console.log(`截图目录：${SHOT_DIR}`);
  process.exitCode = failed ? 1 : 0;
} catch (err) {
  console.log(`❌ 探针失败：${err}`);
  process.exitCode = 1;
} finally {
  if (ws) ws.close();
  try { browser.kill(); } catch { /* 已经退出 */ }
  try { fs.rmSync(udd, { recursive: true, force: true }); } catch { /* 留给系统清 temp */ }
}
