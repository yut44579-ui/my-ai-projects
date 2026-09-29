// fr001a_reset_cdp.mjs · FR-001A「忘记密码」四步流程 + 改密那一屏的 CDP 实测（真 Edge，零依赖）
//
//   node scripts/fr001a_reset_cdp.mjs http://127.0.0.1:8536
//
// 跑之前先起一个真服务（沙箱目录只是别把本次操作写进 state/）：
//   SRA_STATE_DIR=outputs/_cdp_run/state SRA_DOC_DIR=outputs/_cdp_run/documents \
//   SRA_UPLOAD_DIR=outputs/_cdp_run/uploads SRA_OUTPUT_DIR=outputs/_cdp_run/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8536
// （本脚本自己会等 /api/health 就绪；数据预热那 100 多秒由它去等，不用手动。）
//
// 为什么要有这一条：`tests/` 里那些静态检查只能证明"按钮绑了事件、四张卡在页面上"，
// 证明不了**真的点得动、真的能走完**。本脚本用真浏览器把用户那条路走一遍：
//   ① 「忘记密码？」点下去真的进了四步流程（不再是弹一句"本机没法找回"）；
//   ② 第①步的验证码是**页面上那张图**（探针从 <img> 的 data: URL 里把字符读出来，
//      与用户"看图为题"走的是同一张图），填对 → 进第②步；
//   ③ 第①步对**不存在的账号**说的仍是同一句话（不泄露账号在不在）；
//   ④ 第②步恢复码按用户抄下来的样子填（小写 + 空格）也能过 —— 规整前后端都在做；
//      填错一次 → 就地人话提示，且能接着改（不销毁正确的码）；
//   ⑤ 第③步两次密码不一致 → 就地拦下；一致 → 第④步"完成"，且显示改的是哪个账号；
//   ⑥ 回登录页：旧密码登不上、**新密码能登**（这是"真能重置"的最终证据）；
//   ⑦ 登录后到「系统设置 → 账号安全」点「生成恢复码」：页面上真的出现一串码；
//   ⑧ 管理员在「账号管理」里能看到「临时密码」按钮，点下去弹出一串一次性密码；
//   ⑨ 用那串临时密码在登录页登录 → **落在"先设置新密码"那一屏**（不是直接进系统），
//      改完之后才进去，而且新会话是好的；
//   ⑩ 整个过程没有 JS 报错。
// 截图落在 outputs/_cdp_shots/（第二个参数可改目录）。

const BASE = process.argv[2] || "http://127.0.0.1:8536";
const PORT = 9335;
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
const SHOT_DIR = process.argv[3] || path.join(process.cwd(), "outputs", "_cdp_shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── 先用真 HTTP 把前置条件摆好（探针的准备工作，不是被测对象）──────────────
// 被测的是**浏览器里那四步**；账号怎么来的不在这条链上，所以用 fetch 摆好即可。
const PLAIN = "Sup3r-秘密-2026";
const NEW_PLAIN = "N3w-密码-2026x";
const BOSS = "老板";
const MEMBER = "小李";

async function api(pathname, options = {}) {
  const response = await fetch(BASE + pathname, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  try { payload = JSON.parse(text); } catch { /* 不是 JSON 就留着原文 */ }
  return { status: response.status, payload, text };
}

function readCodeFromSvg(svgText) {
  return [...svgText.matchAll(/>([0-9A-Z])<\/text>/g)].map((m) => m[1]).join("");
}

async function freshCaptcha() {
  const { payload } = await api("/api/auth/captcha");
  return { id: payload.captcha_id, code: readCodeFromSvg(payload.image_svg) };
}

// 前置条件摆不上就直接停：那说明**沙箱目录不是干净的**（比如复用了上一次跑留下的 state/），
// 继续往下跑只会得到一堆看不懂的失败。这里把话说清楚。
async function must(step, result) {
  if (result.status !== 200) {
    throw new Error(`前置准备失败：${step} → ${result.status} ${result.text.slice(0, 200)}\n`
      + "（多半是沙箱 state/ 里已经有账号了：换一个空的 SRA_STATE_DIR 再起服务）");
  }
  return result.payload;
}

async function setup() {
  let cap = await freshCaptcha();
  await must("注册老板（第一个账号必须是管理员）", await api("/api/auth/register", {
    method: "POST", body: JSON.stringify(
      { username: BOSS, password: PLAIN, captcha_id: cap.id, captcha_text: cap.code }) }));
  cap = await freshCaptcha();
  const login = await must("老板登录", await api("/api/auth/login", { method: "POST", body: JSON.stringify(
    { username: BOSS, password: PLAIN, captcha_id: cap.id, captcha_text: cap.code }) }));
  const bossSession = login.account.session_id;

  cap = await freshCaptcha();
  await must("注册小李", await api("/api/auth/register", { method: "POST", body: JSON.stringify(
    { username: MEMBER, password: PLAIN, captcha_id: cap.id, captcha_text: cap.code }) }));
  // FR-002A 之后本机是**单用户模式：注册即生效**，不再需要管理员批准。
  // 所以这一步在现在的机器上会回 400 `review_no_change`（"这个账号已经是正常了"）——
  // 那不是失败：本脚本真正需要的前置只是"小李这个账号能用"，批不批都达到了。
  // 只对**这一种**回执放行，其余错误照旧停下（不许把真问题也吞掉）。
  const approved = await api(`/api/auth/accounts/${MEMBER}/review`, {
    method: "POST", body: JSON.stringify({ action: "approve", session_id: bossSession }) });
  if (approved.status !== 200 && !/review_no_change/.test(approved.text)) {
    throw new Error(`前置准备失败：老板批准小李 → ${approved.status} ${approved.text.slice(0, 200)}`);
  }

  cap = await freshCaptcha();
  const memberLogin = await must("小李登录", await api("/api/auth/login", { method: "POST", body: JSON.stringify(
    { username: MEMBER, password: PLAIN, captcha_id: cap.id, captcha_text: cap.code }) }));
  const memberSession = memberLogin.account.session_id;
  const made = await must("小李生成恢复码", await api("/api/auth/recovery-code", { method: "POST",
    body: JSON.stringify({ session_id: memberSession }) }));

  // ⚠️ 临时密码**不在这里发**：第③步那次"用恢复码重置密码"会把它清掉
  //   （改过密码的账号上不该留着一张先前发出去的一次性凭证）——
  //   所以它要等到第⑥步、重置完成之后再发。见那一段的注释。
  return { recoveryCode: made.recovery_code };
}

console.log("等数据预热（首次要读那张大表，可能要一两分钟）…");
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

const prepared = await setup();
console.log(`准备就绪：恢复码 ${prepared.recoveryCode}`);

const udd = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr001a_cdp_"));
const browser = spawn(edge, [
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${udd}`,
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--window-size=1440,960", BASE + "/",
], { stdio: "ignore" });
browser.unref();

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
  let lastDialog = "";
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.method === "Page.javascriptDialogOpening") {
      lastDialog = msg.params.message || "";          // 接住它（否则页面会卡在弹窗上）
      send("Page.handleJavaScriptDialog", { accept: true });
      return;
    }
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
      consoleErrors.push((msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "));
    }
    if (msg.method === "Runtime.exceptionThrown") {
      consoleErrors.push(msg.params.exceptionDetails.exception?.description
        || msg.params.exceptionDetails.text);
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
  const shoot = async (name) => {
    const shot = await send("Page.captureScreenshot", { format: "png" });
    if (shot?.data) fs.writeFileSync(path.join(SHOT_DIR, name), Buffer.from(shot.data, "base64"));
  };
  // 填一个输入框：设值 + 派发 input 事件（让页面的监听器真的跑一遍）
  const fill = async (selector, value) => evalJs(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return false;
    el.value = ${JSON.stringify(value)};
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`);
  // 点一个元素。★ 判据必须是**真实可见性**（`getBoundingClientRect().height > 0`）：
  //   只看 `el.hidden` 会漏掉"自己在显示、但祖先那块是隐藏的"——
  //   那样的按钮压根不在屏幕上，点它等于伪造一次用户操作（探针自己会骗自己）。
  const click = async (selector) => evalJs(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return "missing";
    if (el.getBoundingClientRect().height <= 0) return "not-visible";
    if (el.disabled) return "disabled";
    el.click();
    return "clicked";
  })()`);
  const textOf = async (selector) => evalJs(
    `(document.querySelector(${JSON.stringify(selector)}) || {}).textContent || ""`);
  const visible = async (selector) => evalJs(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return false;
    const style = getComputedStyle(el);
    return !el.hidden && style.display !== "none" && el.getBoundingClientRect().height > 0;
  })()`);
  const waitFor = async (selector, timeoutMs = 8000) => {
    for (let i = 0; i < Math.ceil(timeoutMs / 100); i += 1) {
      if (await visible(selector)) return true;
      await sleep(100);
    }
    return false;
  };
  // 等登录页收起（= 真的进来了）。异步请求要时间，点完立刻读结论会误判成失败。
  const waitGateHidden = async (timeoutMs = 8000) => {
    for (let i = 0; i < Math.ceil(timeoutMs / 100); i += 1) {
      if (await evalJs(`document.getElementById("login-gate").hidden`)) return true;
      await sleep(100);
    }
    return false;
  };
  // 页面上那张验证码图里的字符（与用户"看图填字"是同一张图、同一段 SVG）。
  // ★ 必须等**图真的换了一张**再读：同一张图用一次就作废，读早了拿的是上一张的字符，
  //   提交上去只会得到"验证码已过期"—— 那是探针的错，不是产品的错。
  const lastSrc = {};
  const captchaOnPage = async (imgId, timeoutMs = 6000) => {
    const previous = lastSrc[imgId] || "";
    for (let i = 0; i < Math.ceil(timeoutMs / 150); i += 1) {
      const src = await evalJs(`(document.getElementById(${JSON.stringify(imgId)}) || {}).src || ""`);
      if (src && src !== previous) {
        lastSrc[imgId] = src;
        return readCodeFromSvg(decodeURIComponent(src.split(",")[1] || ""));
      }
      await sleep(150);
    }
    return "";
  };

  await send("Runtime.enable");
  await send("Page.enable");
  for (let i = 0; i < 150; i += 1) {
    if (await evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate"))`)) break;
    await sleep(200);
  }

  // ── ① 点「忘记密码？」真的进流程 ────────────────────────────────────────
  console.log("\n【1】忘记密码？→ 四步流程");
  check(await click("#link-forgot"), "点得到「忘记密码？」");
  check(await waitFor("#reset-pane"), "点完落在找回流程那张卡上");
  check(await visible("#reset-step-1") && !(await visible("#reset-step-2")),
    "第①步是当前这一步（第②步还没出现）");
  check((await textOf("#login-sub")).includes("第 1 步"), "标题栏写着走到第几步",
    await textOf("#login-sub"));
  check(await visible("#reset-note-1"), "第①步下方有一句「恢复码要在登录后自己生成」");
  await shoot("fr001a-01-reset-step1.png");

  // ── ② 第①步：不存在的账号 → 同一句话 ──────────────────────────────────
  console.log("\n【2】第①步：不存在的账号也给同一句话（不泄露存在性）");
  await fill("#reset-name", "查无此人");
  const ghostCode = await captchaOnPage("reset-captcha-img");
  check(ghostCode.length === 4, "探针从页面那张图里读到了 4 个字符", ghostCode);
  await fill("#reset-captcha", ghostCode);
  await click("#btn-reset-step1");
  check(await waitFor("#reset-step-2"), "不存在的账号**照样进第②步**（第①步不回答「在不在」）");
  check(!(await textOf("#reset-pane")).includes("不存在"), "页面上没有「账号不存在」这类字");
  await click("#btn-reset-back");

  // ── ③ 真的走一遍：账号 + 验证码 → 恢复码 → 新密码 → 完成 ────────────────
  console.log("\n【3】四步走完（小李忘记密码 → 用恢复码设新密码）");
  await click("#link-forgot");
  await waitFor("#reset-pane");
  await fill("#reset-name", MEMBER);
  const code1 = await captchaOnPage("reset-captcha-img");
  await fill("#reset-captcha", code1);
  await click("#btn-reset-step1");
  check(await waitFor("#reset-step-2"), "第①步过了 → 进第②步");

  // 第②步先故意填错一次：要能看到人话提示，而且**不销毁**正确的码
  await fill("#reset-code", "AAAA-AAAA-AAAA-AAAA-AAAA");
  await click("#btn-reset-step2");
  check(await waitFor("#reset-code-msg"), "填错恢复码 → 就地人话提示", await textOf("#reset-code-msg"));
  check(await visible("#reset-step-2"), "填错之后还在第②步（可以接着改）");

  // 用"抄下来的样子"填：小写 + 空格（服务端与前端都会规整）
  const written = prepared.recoveryCode.toLowerCase().replace(/-/g, " ");
  await fill("#reset-code", written);
  check((await evalJs(`document.getElementById("reset-code").value`)) === written,
    "输入框里就是用户抄下来的那个样子（小写 + 空格）", written);
  await click("#btn-reset-step2");
  check(await waitFor("#reset-step-3"), "小写 + 空格的恢复码也过了 → 进第③步");

  await fill("#reset-pwd", "12345678");
  await fill("#reset-pwd2", "87654321");
  await click("#btn-reset-step3");
  check(await visible("#reset-pwd2-msg"), "两次不一致 → 就地拦下", await textOf("#reset-pwd2-msg"));
  check(await visible("#reset-step-3"), "拦下之后还在第③步");

  await fill("#reset-pwd", NEW_PLAIN);
  await fill("#reset-pwd2", NEW_PLAIN);
  await click("#btn-reset-step3");
  check(await waitFor("#reset-step-4"), "两次一致 → 进第④步（完成）");
  check((await textOf("#reset-done-title")).includes(MEMBER),
    "完成那一屏写清了改的是哪个账号", await textOf("#reset-done-title"));
  await shoot("fr001a-02-reset-done.png");
  await click("#btn-reset-done");
  check(await visible("#login-form"), "「返回登录」回到登录页");

  // ── ④ 旧密码登不上、新密码能登（"真能重置"的最终证据）──────────────────
  console.log("\n【4】登录页：旧密码失败、新密码成功");
  // 登录一次：返回 `{ok, message}`。验证码那一路的问题（图换了、读早了）自动重试，
  // **不**把它算成"账号密码的结论"—— 这两件事混在一起，探针就会给出假结论。
  const tryLogin = async (name, password) => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      // 把上一次留下的验证码提示清掉，好分辨这次的失败到底是哪一类
      await evalJs(`(() => { const el = document.getElementById("login-captcha-msg");
        if (el) { el.textContent = ""; el.hidden = true; } })()`);
      await fill("#login-name", name);
      await fill("#login-pwd", password);
      await fill("#login-captcha", await captchaOnPage("login-captcha-img"));
      await click("#btn-login");
      await sleep(1500);
      const state = await evalJs(`(() => ({
        hidden: document.getElementById("login-gate").hidden,
        pwd: (document.getElementById("login-pwd-msg") || {}).textContent || "",
        cap: (document.getElementById("login-captcha-msg") || {}).textContent || "",
      }))()`);
      if (state.hidden) return { ok: true, message: "" };
      if (/验证码/.test(state.cap)) continue;         // 换一张再来一次
      return { ok: false, message: state.pwd || state.cap || "没登进去" };
    }
    return { ok: false, message: "连着几次都卡在验证码上（探针侧问题）" };
  };
  const oldPwd = await tryLogin(MEMBER, PLAIN);
  check(oldPwd.ok === false, "旧密码登不上（还停在登录页）", oldPwd.message);
  const newPwd = await tryLogin(MEMBER, NEW_PLAIN);
  check(newPwd.ok === true, "★ 新密码登进去了（登录页收起）", newPwd.message);
  await shoot("fr001a-03-login-with-new.png");

  // ── ⑤ 登录后：系统设置里的「账号安全」能生成恢复码 ────────────────────
  console.log("\n【5】系统设置 → 账号安全 → 生成恢复码");
  await evalJs(`location.hash = "#/settings"`);
  await sleep(600);
  check(await visible("#set-security-card"), "登录的账号看得到「账号安全」那张卡");
  await click("#btn-make-recovery");
  check(await waitFor("#set-recovery-box", 6000), "点「生成恢复码」后页面上出现了结果区");
  const shownCode = (await textOf("#set-recovery-code")).trim();
  check(/^([0-9A-Z]{4}-){4}[0-9A-Z]{4}$/.test(shownCode),
    "显示的是 20 位、4-4-4-4-4 的恢复码", shownCode);
  await shoot("fr001a-04-recovery-code.png");

  // ── ⑥ 管理员：账号管理里的「临时密码」按钮 ─────────────────────────
  console.log("\n【6】管理员：账号管理 → 临时密码");
  // 先退出，改用管理员登录
  await evalJs(`document.getElementById("btn-user").click()`);
  await sleep(200);
  await evalJs(`document.getElementById("um-logout").click()`);
  await sleep(800);
  const bossLogin = await tryLogin(BOSS, PLAIN);
  check(bossLogin.ok, "管理员登录成功（要看账号管理那张卡）", bossLogin.message);
  await evalJs(`location.hash = "#/settings"`);
  await sleep(1200);
  const buttons = await evalJs(`[...document.querySelectorAll("#set-approved-body button")]
    .map((b) => b.textContent)`);
  check(buttons.includes("临时密码"), "已批准账号那一行有「临时密码」按钮", buttons.join(" / "));
  // 真的点一下：管理员脚下那一行的「临时密码」按钮 → 页面把明文弹出来给人转交
  lastDialog = "";
  const tempClicked = await evalJs(`(() => {
    const target = [...document.querySelectorAll("#set-approved-body tr")].find((row) =>
      row.textContent.includes(${JSON.stringify(MEMBER)}));
    if (!target) return "no-row";
    const button = [...target.querySelectorAll("button")].find((b) => b.textContent === "临时密码");
    if (!button) return "no-button";
    button.click();
    return "clicked";
  })()`);
  check(tempClicked === "clicked", "点得到那一行的「临时密码」按钮", tempClicked);
  for (let i = 0; i < 40 && !lastDialog; i += 1) await sleep(150);
  const issued = (lastDialog.split(/\r?\n/).map((line) => line.trim())
    .find((line) => /^[A-Za-z0-9]{12}$/.test(line))) || "";
  check(/^[A-Za-z0-9]{12}$/.test(issued), "弹出来的明文是 12 位临时密码", lastDialog.slice(0, 80));
  await shoot("fr001a-06-temp-password.png");

  // ── ⑦ 用临时密码登录 → 落在"先设置新密码"那一屏 ──────────────────────
  console.log("\n【7】临时密码登录 → 必须先改密");
  await evalJs(`document.getElementById("btn-user").click()`);
  await sleep(200);
  await evalJs(`document.getElementById("um-logout").click()`);
  await sleep(800);
  const tempLogin = await tryLogin(MEMBER, issued);
  const onChangeScreen = await waitFor("#pwd-change-form", 8000);
  check(onChangeScreen, "★ 临时密码登录**没有**把人直接放进系统，而是落在「先设置新密码」那一屏",
    onChangeScreen ? "" : tempLogin.message);
  check(await evalJs(`!document.getElementById("login-gate").hidden`),
    "这一屏还在登录页上（不算已经进来）");
  await shoot("fr001a-05-must-change.png");

  await fill("#pc-old", issued);
  await fill("#pc-new", "F1nal-密码-2026");
  await fill("#pc-new2", "F1nal-密码-2026");
  await click("#btn-pwd-change");
  // 点完要**等请求回来**（改密要算一次 PBKDF2），不能立刻读结论
  const entered = await waitGateHidden(8000);
  check(entered, "改完密码 → 真的进系统了",
    entered ? "" : `pc-old-msg=${await textOf("#pc-old-msg")} pc-new-msg=${await textOf("#pc-new-msg")}`);
  check((await textOf("#user-name")).includes(MEMBER), "顶栏显示的是小李", await textOf("#user-name"));

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
