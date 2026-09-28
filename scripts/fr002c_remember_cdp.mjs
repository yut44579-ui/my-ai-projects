// fr002c_remember_cdp.mjs · 「记住账号」在**真浏览器**里的实测（真 Edge + CDP，零依赖）
//
//   node scripts/fr002c_remember_cdp.mjs http://127.0.0.1:8536
//
// 跑之前先起一个真服务（沙箱目录只是别把本次注册/登录写进 state/）：
//   SRA_STATE_DIR=outputs/_cdp_run_fr002c/state SRA_DOC_DIR=outputs/_cdp_run_fr002c/documents \
//   SRA_UPLOAD_DIR=outputs/_cdp_run_fr002c/uploads SRA_OUTPUT_DIR=outputs/_cdp_run_fr002c/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8536
//
// 为什么要有这一条：FR-002C 的三件事都是**浏览器里的事**，静态检查看不出来 ——
//   ① 勾了「记住账号」→ 登录成功之后，账号名真的进了本机那条记录；
//   ② **重新打开页面**（真的 Page.reload）→ 账号框真的被填上、勾选框真的还勾着；
//   ③ 点「清除」→ 记录真的没了，再打开页面账号框真的是空的。
// 顺带把 B6/B7/B8 一起验了：本机记录里逐个键看**没有密码**、重开页面照样要登录、只有一个账号名键。
//
// 量什么（每一步都读**真值**，不是「看源码猜」）：
//   ① 真注册一个账号（第一个账号自动是管理员）→ 退出；
//   ② 勾上「记住账号」登录 → localStorage 逐键读出来 + 全量 value 搜密码明文（0 命中）；
//   ③ 重开页面 → 登录页在（没自动登录）、账号框 = 那个账号、勾选框 = 勾上、那一行「已记住账号 xxx 清除」真的显示出来；
//   ④ 点「清除」→ 键没了、账号框空、勾选框取消、那一行收起来 + 一句人话回执；
//   ⑤ 再重开页面 → 账号框仍然空、勾选框仍然未勾（清除是真的）；
//   ⑥ 不勾登录一次 → 记录里没有这个键（未勾选不保存）；
//   ⑦ 勾上登录、再**把勾摘掉** → 记录当场就没了（不用等下次登录）；
//   ⑧ 全程没有 JS 报错。
// 截图落在 outputs/_cdp_shots/：那一行「已记住账号…清除」的样子 + 重开页面回填的样子。

const BASE = process.argv[2] || "http://127.0.0.1:8536";
const PORT = 9336;
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
const ACCOUNT = "13900000001";
const PASSWORD = "abc12345";

const { spawn } = await import("node:child_process");
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

// 先等进程就绪（不猜固定秒数；端口没起时这里会拿到 502 空 body，所以只认 200）
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

// 每次跑都要是干净的一次：清掉**本脚本沙箱**里的账号表（真实 state/ 永远不碰）
const sandbox = "outputs/_cdp_run_fr002c/state";
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const accountsPath = path.join(process.cwd(), sandbox, "accounts.json");
  if (fs.existsSync(accountsPath)) {
    fs.rmSync(accountsPath);
    console.log(`已清空沙箱账号表（每次跑都是干净的一次）：${accountsPath}`);
  }
}

// 等数据真的读进内存（登录页那几个数字靠它），等到了再开浏览器
console.log("等数据预热（首次要读那张大表，可能要一两分钟）…");
try {
  const warm = await fetch(BASE + "/api/chat/capabilities", { signal: AbortSignal.timeout(900000) });
  if (!warm.ok) { console.log(`❌ 数据预热失败（${warm.status}）`); process.exit(1); }
  console.log("数据已就绪，开始走查。");
} catch (err) {
  console.log(`❌ 等数据预热超时/失败：${err}`);
  process.exit(1);
}

const udd = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr002c_cdp_"));
const browser = spawn(edge, [
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${udd}`,
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--window-size=1440,900", BASE + "/",
], { stdio: "ignore" });
browser.unref();

let passed = 0, failed = 0;
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

  let domReady = false;
  for (let i = 0; i < 150; i++) {
    domReady = await evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate"))
      && Boolean(document.getElementById("btn-forget-account"))`);
    if (domReady) break;
    await sleep(200);
  }
  if (!domReady) throw new Error("页面 30 秒内没建出登录页的 DOM（先看服务是否正常返回 / 与 /session.js）");

  // ── 工具：本机存储逐键读出来（验收要「逐个键 + 每个键里存什么」）────────────
  const storageDump = () => evalJs(`(() => {
    const out = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      out.push({ key, value: localStorage.getItem(key) });
    }
    return out.sort((a, b) => (a.key < b.key ? -1 : 1));
  })()`);

  const loginForm = () => evalJs(`(() => {
    const box = document.getElementById("login-remember");
    const note = document.getElementById("remember-note");
    const r = note.getBoundingClientRect();
    return {
      name: document.getElementById("login-name").value,
      pwd: document.getElementById("login-pwd").value,
      checked: box.checked,
      noteHidden: note.hidden,
      noteText: note.textContent.replace(/\\s+/g, " ").trim(),
      whoText: document.getElementById("remember-who").textContent.trim(),
      noteVisible: !note.hidden && r.width > 0 && r.height > 0,
      gateHidden: document.getElementById("login-gate").hidden,
      locked: document.body.classList.contains("is-locked"),
      who: document.getElementById("user-name").textContent.trim(),
    };
  })()`);

  // 照着图把验证码填上（人看图抄字的机器版）
  const fillCaptcha = async (imgId, inputId) => {
    const code = await evalJs(`(() => {
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
    await evalJs(`(() => {
      const input = document.getElementById(${JSON.stringify(inputId)});
      input.value = ${JSON.stringify(code)};
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    return code;
  };

  // 登一次录：填账号密码 + 验证码，按需勾/不勾「记住账号」，点登录按钮，等结果
  const doLogin = async ({ remember, expectOk }) => {
    await evalJs(`(() => {
      document.getElementById("ltab-account").click();
      const a = document.getElementById("login-name");
      const p = document.getElementById("login-pwd");
      a.value = ${JSON.stringify(ACCOUNT)}; p.value = ${JSON.stringify(PASSWORD)};
      a.dispatchEvent(new Event("input", { bubbles: true }));
      p.dispatchEvent(new Event("input", { bubbles: true }));
      const box = document.getElementById("login-remember");
      if (box.checked !== ${remember}) box.click();      // 真点一下（走 change 事件）
      return true;
    })()`);
    await fillCaptcha("login-captcha-img", "login-captcha");
    await evalJs(`document.getElementById("btn-login").click()`);
    if (expectOk) await sleep(3500); else await sleep(800);
  };

  const logout = async () => {
    await evalJs(`(() => {
      const menu = document.getElementById("user-menu");
      if (menu && menu.hidden) document.getElementById("btn-user").click();
      return true;
    })()`);
    await sleep(150);
    await evalJs(`document.getElementById("um-logout").click()`);
    await sleep(600);
  };

  const shotClip = async (name, id, pad = 16) => {
    await evalJs(`document.getElementById(${JSON.stringify(id)}).scrollIntoView({ block: "center" })`);
    await sleep(250);
    const box = await evalJs(`(() => {
      const r = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();
      return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
    })()`);
    const shot = await send("Page.captureScreenshot", {
      format: "png",
      clip: { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad),
              width: box.w + pad * 2, height: box.h + pad * 2, scale: 3 },
    });
    const file = path.join(SHOT_DIR, name);
    fs.writeFileSync(file, Buffer.from(shot.data, "base64"));
    return file;
  };
  const shotPage = async (name) => {
    const shot = await send("Page.captureScreenshot", { format: "png" });
    const file = path.join(SHOT_DIR, name);
    fs.writeFileSync(file, Buffer.from(shot.data, "base64"));
    return file;
  };

  // ── ① 先真注册一个账号（注册/登录/验证码都当真功能走，不走后门）───────────
  console.log("\n=== ① 先真注册一个账号（第一个账号自动是管理员）===");
  console.log(`   （全新 profile → 本机没记着任何账号；账号框=「${(await loginForm()).name}」）`);
  await evalJs(`document.getElementById("link-register").click()`);
  await sleep(200);
  await evalJs(`(() => {
    const set = (id, v) => { const el = document.getElementById(id); el.value = v;
      el.dispatchEvent(new Event("input", { bubbles: true })); };
    set("reg-name", ${JSON.stringify(ACCOUNT)});
    set("reg-display", "记住账号用例");
    set("reg-pwd", ${JSON.stringify(PASSWORD)});
    set("reg-pwd2", ${JSON.stringify(PASSWORD)});
    return true;
  })()`);
  await fillCaptcha("reg-captcha-img", "reg-captcha");
  await evalJs(`document.getElementById("btn-register").click()`);
  await sleep(3500);
  const registered = await loginForm();
  check(registered.gateHidden && !registered.locked,
    "注册成功直接进系统（本机第一个账号，登录页收起）", `顶栏=「${registered.who}」`);
  await logout();
  let form = await loginForm();
  check(!form.gateHidden, "退出登录 → 回到登录页", `账号框=「${form.name}」`);
  check(await evalJs(`localStorage.getItem("sra.remembered-name") === null`),
    "还没勾「记住账号」→ 本机没有那条记录（未勾选不保存）");

  // ── ② 勾上「记住账号」登录一次 ────────────────────────────────────────
  console.log("\n=== ② 勾上「记住账号」→ 登录成功 → 本机记录里到底存了什么 ===");
  await doLogin({ remember: true, expectOk: true });
  form = await loginForm();
  check(form.gateHidden, "勾着「记住账号」登录成功", `顶栏=「${form.who}」`);
  const stored = await storageDump();
  console.log(`   本机存储逐键读出：${JSON.stringify(stored)}`);
  const remembered = stored.find((row) => row.key === "sra.remembered-name");
  check(Boolean(remembered) && remembered.value === ACCOUNT, "B1 勾选后登录成功 → 账号名被记住",
    `sra.remembered-name = 「${remembered ? remembered.value : "(没有这个键)"}」`);
  check(stored.every((row) => row.key === "sra.who" || row.key === "sra.remembered-name"),
    "B8 本机只有这两个键（没有账号列表 / 第二个账号名键）",
    stored.map((row) => row.key).join(", "));
  const valueTexts = stored.map((row) => row.value).join("\n");
  const banned = [PASSWORD, "abc12345", "password", "pwd", "token", "reset_token"];
  const hits = banned.filter((word) => valueTexts.includes(word));
  check(hits.length === 0, "B6 逐个键的 value 里**没有密码 / 令牌**（0 命中）", hits.join(", "));
  // 账号名键里存的就是那个账号名本身（明文、只有一个值）
  check(remembered && remembered.value.split(",").length === 1 && remembered.value === ACCOUNT,
    "B8 那个键里就是**一个**账号名（不是一串 / 不是列表）", `「${remembered ? remembered.value : ""}」`);
  await logout();

  // ── ③ 重开页面（真的 reload）→ 回填 + 勾选保持 ────────────────────────
  console.log("\n=== ③ 重新打开页面（Page.reload）→ 账号回填 + 勾选保持 ===");
  await send("Page.reload", { ignoreCache: false });
  await sleep(3000);
  form = await loginForm();
  check(!form.gateHidden, "B7 重开页面**仍然要登录**（记住账号 ≠ 记住登录状态）",
    `登录页在=「${!form.gateHidden}」顶栏=「${form.who}」`);
  check(form.name === ACCOUNT, "B2 账号框自动填充最近一次记住的账号", `「${form.name}」`);
  check(form.checked === true, "B3 「记住账号」勾选状态也被保持", `勾上=「${form.checked}」`);
  check(form.pwd === "", "密码框仍然是空的（记住账号 ≠ 记住密码）", `密码框=「${form.pwd}」`);
  check(form.noteVisible && form.whoText === ACCOUNT
        && form.noteText.includes("已记住账号") && form.noteText.includes("清除"),
    "「已记住账号 xxx 清除」那一行真的画出来了（看得见、点得到）",
    `「${form.noteText}」`);
  check(fs.existsSync(await shotClip("fr002c-remember-note.png", "remember-note", 24)),
    "留证：那一行「已记住账号…清除」的截图", "fr002c-remember-note.png");
  check(fs.existsSync(await shotPage("fr002c-reopen.png")), "留证：重开页面回填后的整屏截图",
    "fr002c-reopen.png");

  // ── ④ 点「清除」────────────────────────────────────────────────────────
  console.log("\n=== ④ 点「清除已记住账号」===");
  const noteBox = await evalJs(`(() => {
    const b = document.getElementById("btn-forget-account");
    const r = b.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
             x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
             text: (b.textContent || "").trim() };
  })()`);
  check(noteBox.w > 0 && noteBox.h > 0 && noteBox.text === "清除",
    "「清除」是个真按钮、真在页面上", `${noteBox.w}x${noteBox.h} 文字=「${noteBox.text}」`);
  // 用**真鼠标事件**点它（不是替它调 click()）
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: noteBox.x, y: noteBox.y,
                                           button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: noteBox.x, y: noteBox.y,
                                           button: "left", clickCount: 1 });
  await sleep(600);
  form = await loginForm();
  const afterClear = await storageDump();
  console.log(`   清完之后的本机存储：${JSON.stringify(afterClear)}`);
  check(!afterClear.some((row) => row.key === "sra.remembered-name"),
    "B5 「清除」真的把那条记录删掉了", afterClear.map((r) => r.key).join(", ") || "(空)");
  check(form.name === "" && form.checked === false && form.noteHidden,
    "B5 清完当场：账号框空、勾选取消、那一行收起",
    `账号框=「${form.name}」 勾=「${form.checked}」 那行收起=「${form.noteHidden}」`);
  const clearToast = await evalJs(`(() => { const t = document.getElementById("toast");
    return t.hidden ? "" : t.textContent.trim(); })()`);
  check(clearToast.includes("已清除记住的账号"), "清完给了一句人话回执", `「${clearToast}」`);

  // ── ⑤ 再重开一次：清掉是真的 ──────────────────────────────────────────
  console.log("\n=== ⑤ 再重开页面 → 账号框仍然空（清除不是「擦干净框子」）===");
  await send("Page.reload", { ignoreCache: false });
  await sleep(3000);
  form = await loginForm();
  check(form.name === "" && form.checked === false && form.noteHidden && !form.gateHidden,
    "B5 重开页面：账号框为空、勾选为未勾、那一行不出现、仍然要登录",
    `账号框=「${form.name}」 勾=「${form.checked}」 登录页在=「${!form.gateHidden}」`);

  // ── ⑥ 不勾登录一次 → 不保存 ───────────────────────────────────────────
  console.log("\n=== ⑥ 不勾「记住账号」登录一次 → 什么都不记 ===");
  await doLogin({ remember: false, expectOk: true });
  form = await loginForm();
  check(form.gateHidden, "不勾也能正常登录（这个勾只跟「记不记账号」有关）", `顶栏=「${form.who}」`);
  check(await evalJs(`localStorage.getItem("sra.remembered-name") === null`),
    "B4 未勾选 → 不保存（本机没有那条记录）");
  await send("Page.reload", { ignoreCache: false });
  await sleep(3000);
  form = await loginForm();
  check(form.name === "" && form.checked === false,
    "B4 重开页面：账号框空的、勾也没勾（不勾就是不记）",
    `账号框=「${form.name}」 勾=「${form.checked}」`);

  // ── ⑦ 勾上登录 → 把勾摘掉 → 当场清掉 ──────────────────────────────────
  console.log("\n=== ⑦ 先勾上登录（记住）→ 再把勾摘掉 → 记录当场就没了 ===");
  await doLogin({ remember: true, expectOk: true });
  check(await evalJs(`localStorage.getItem("sra.remembered-name") === ${JSON.stringify(ACCOUNT)}`),
    "勾上登录 → 又记住了", `sra.remembered-name=「${await evalJs(`localStorage.getItem("sra.remembered-name")`)}」`);
  await logout();
  form = await loginForm();
  check(form.name === ACCOUNT && form.checked === true && form.noteVisible,
    "退出回到登录页：账号照旧回填 + 勾上 + 那一行在（与本页刷新同一条路）",
    `账号框=「${form.name}」 勾=「${form.checked}」`);
  await evalJs(`document.getElementById("login-remember").click()`);   // 真点一下，把勾摘掉
  await sleep(400);
  form = await loginForm();
  check(await evalJs(`localStorage.getItem("sra.remembered-name") === null`),
    "B4 摘掉勾 → 上一次记住的当场被清掉（不用等下次登录）");
  check(form.noteHidden, "摘掉勾之后那一行也收起来了");
  await send("Page.reload", { ignoreCache: false });
  await sleep(3000);
  form = await loginForm();
  check(form.name === "" && form.checked === false,
    "重开页面确认：真的没记（账号框空 + 未勾）",
    `账号框=「${form.name}」 勾=「${form.checked}」`);

  // ── ⑧ 登录 / 验证码 / 游客 无回归 ─────────────────────────────────────
  console.log("\n=== ⑧ 别的地方没被碰坏（登录照样要验证码、游客照样能进）===");
  // 先不填验证码就提交：验证码这道题照样拦人（提示落在验证码那一格，不是密码那一格）
  await evalJs(`(() => {
    const a = document.getElementById("login-name"), p = document.getElementById("login-pwd");
    a.value = ${JSON.stringify(ACCOUNT)}; p.value = "wrong-one";
    a.dispatchEvent(new Event("input", { bubbles: true }));
    p.dispatchEvent(new Event("input", { bubbles: true }));
    const c = document.getElementById("login-captcha");
    c.value = ""; c.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("btn-login").click();
    return true;
  })()`);
  await sleep(800);
  const noCode = await evalJs(`(() => {
    const c = document.getElementById("login-captcha-msg");
    return { text: c.hidden ? "" : c.textContent.trim(),
             gateHidden: document.getElementById("login-gate").hidden };
  })()`);
  check(Boolean(noCode.text) && !noCode.gateHidden,
    "不填验证码照样被拦下（这道题没被绕过）", `「${noCode.text}」`);
  // 再走一次**完整**的失败登录：验证码填对、密码填错 → 401 的人话 + 清空密码框
  await fillCaptcha("login-captcha-img", "login-captcha");
  await evalJs(`(() => {
    const a = document.getElementById("login-name"), p = document.getElementById("login-pwd");
    a.value = ${JSON.stringify(ACCOUNT)}; p.value = "wrong-one";
    a.dispatchEvent(new Event("input", { bubbles: true }));
    p.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("btn-login").click();
    return true;
  })()`);
  await sleep(1500);
  const wrong = await evalJs(`(() => {
    const m = document.getElementById("login-pwd-msg");
    return { text: m.hidden ? "" : m.textContent.trim(),
             pwd: document.getElementById("login-pwd").value,
             name: document.getElementById("login-name").value,
             gateHidden: document.getElementById("login-gate").hidden,
             captchaImg: Boolean(document.getElementById("login-captcha-img").src) };
  })()`);
  check(wrong.text.includes("账号或密码不对") && !wrong.gateHidden,
    "密码不对那条路照旧拦人并给 401 的人话（改动没碰登录判定）", `「${wrong.text}」`);
  check(wrong.name === ACCOUNT && wrong.pwd === "",
    "失败后账号保留、密码清空（原样，没被记住账号那段改动影响）",
    `账号=「${wrong.name}」 密码=「${wrong.pwd}」`);
  check(wrong.captchaImg, "验证码图还在");
  await evalJs(`document.getElementById("ltab-guest").click()`);
  await sleep(300);
  check(await evalJs(`document.getElementById("guest-pane").hidden === false
    && document.getElementById("login-form").hidden === true`),
    "游客那一栏照旧能进（无回归）");

  check(consoleErrors.length === 0, "整个过程没有 JS 报错", consoleErrors.slice(0, 2).join(" | "));
  console.log(`\n通过 ${passed} 项，失败 ${failed} 项`);
  process.exitCode = failed ? 1 : 0;
} catch (err) {
  console.log(`❌ 探针失败：${err}`);
  process.exitCode = 1;
} finally {
  if (ws) ws.close();
  try { browser.kill(); } catch { /* 已经退出 */ }
  try { fs.rmSync(udd, { recursive: true, force: true }); } catch { /* 留给系统清 temp */ }
}
