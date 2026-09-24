/* session.js · 登录页 + 用户区 + 在线状态（本机会话层）
 *
 * ════ 这一层是什么、不是什么（写清楚，免得被当成安全实现）════
 * 是：在本机记住"这次是谁在用"（一条本地记录），把名字与状态画到右上角，
 *     并让四种状态跟着**真实动作**走（登录 / 提问 / 长时间没操作 / 退出）。
 * 不是：任何身份校验。密码只检查"填了没有、够不够长"，**不保存、不比对、不做任何摘要运算**；
 *       名字与状态也只用于展示与记录。口令校验、令牌、权限、操作留痕属于服务端要做的事，
 *       本文件一行都没有 —— 所以界面上也不说"已安全登录"这种话。
 *
 * ════ 状态由什么驱动（不靠前端"假装忙"）════
 *   登录成功（或刷新时读回本机记录） → 在线
 *   分析请求发出 / 收回              → 忙碌中 / 在线   ← app.js 用它真实的请求生命周期调 trackRequest()
 *   连续 10 分钟没有点击或按键        → 离开（再有操作 → 回到在线）
 *   退出登录                         → 离线
 * 状态只是**本机视觉状态**：说"你在忙"的依据是"你刚发了一次分析请求"，
 * 不是"这个账号真的在线"（单机项目里本来也没有"账号在线"这回事）。
 *
 * ════ 载入顺序 ════
 * 本文件排在 app.js **之前**：先决定"要不要先给登录页"，再让 app.js 去读后端数据
 * （未登录时 app.js 的初始化会被 requireLogin 挂住，不会白读一遍数据）。
 */
window.Session = (() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const show = (el) => { if (el) el.hidden = false; };
  const hide = (el) => { if (el) el.hidden = true; };
  const setText = (id, value) => { const el = $(id); if (el) el.textContent = value; };

  // 本机那条"这次是谁"的记录：**只存名字与入口类型**，密码连碰都不碰
  const WHO_KEY = "sra.who";
  const NAME_KEY = "sra.remembered-name";

  const STATUS_ORDER = ["online", "busy", "away", "offline"];
  const STATUS_TEXT = { online: "在线", busy: "忙碌中", away: "离开", offline: "离线" };
  // 状态点的提示语：说清"这是本机状态、凭什么这么显示"
  const STATUS_WHY = {
    online: "有操作，或刚分析完",
    busy: "正在分析你提的问题",
    away: "有一段时间没操作了",
    offline: "已退出",
  };

  const AWAY_MINUTES = 10;                              // 默认阈值：10 分钟没操作算"离开"
  const GUEST_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789";   // 去掉容易看错的 0/o/1/l/i
  const TOAST_MS = 3400;                                // 人话提示停留多久
  const NOT_READY = "个人设置尚未开通。当前能改的只有登录时填的名字与右上角的状态。";

  const state = {
    who: null,          // {name, kind}；kind: account | guest
    status: "offline",
    manual: false,      // 用户手动选过状态（先听用户的，等下次自动事件再接管）
    lastActive: 0,
    toastUntil: 0,
    pending: null,      // 未登录时 app.js 交给我们的"登录后要干的事"
    guestName: "",
  };

  let factsPromise = null;

  const fmtCount = (value) => (typeof value === "number" && isFinite(value)
    ? value.toLocaleString("zh-CN")
    : "");

  // ══════════════════════════════════════════════════════════════════════
  // 本机记录（刷新不用重登；退出即清）
  // ══════════════════════════════════════════════════════════════════════
  function readWho() {
    try {
      const raw = localStorage.getItem(WHO_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.name) return null;
      return { name: String(parsed.name), kind: parsed.kind === "guest" ? "guest" : "account" };
    } catch (err) {
      return null;                       // 记录坏了就当没登录，不把页面搞挂
    }
  }

  function writeWho(who) {
    try {
      localStorage.setItem(WHO_KEY, JSON.stringify({ name: who.name, kind: who.kind }));
    } catch (err) { /* 本机不让写：这次登录仍然可用，只是刷新后要重登 */ }
  }

  function clearWho() {
    try {
      localStorage.removeItem(WHO_KEY);
    } catch (err) { /* 清不掉也要把界面回到登录页，不能卡住 */ }
  }

  function rememberedName() {
    try {
      return localStorage.getItem(NAME_KEY) || "";
    } catch (err) {
      return "";
    }
  }

  function rememberName(name) {
    try {
      if (name) localStorage.setItem(NAME_KEY, name);
      else localStorage.removeItem(NAME_KEY);
    } catch (err) { /* 同上：记不住只是下次要重填，不是错误 */ }
  }

  // ══════════════════════════════════════════════════════════════════════
  // 状态机
  // ══════════════════════════════════════════════════════════════════════
  function awayMs() {
    // 验收用：地址栏加 ?away=20 表示"20 秒没操作就算离开"（默认 10 分钟）
    let seconds = 0;
    try {
      const fromUrl = new URLSearchParams(location.search).get("away");
      seconds = fromUrl ? Number(fromUrl) : 0;
    } catch (err) {
      seconds = 0;
    }
    return seconds > 0 ? seconds * 1000 : AWAY_MINUTES * 60 * 1000;
  }

  function setStatus(next, source) {
    if (!STATUS_TEXT[next]) return;
    state.status = next;
    state.manual = source === "manual";
    renderStatus();
  }

  function markActive() {
    state.lastActive = Date.now();
    if (state.status === "away") setStatus("online", "activity");
  }

  function tick() {
    const now = Date.now();
    if (state.toastUntil && now >= state.toastUntil) {
      state.toastUntil = 0;
      hide($("toast"));
    }
    if (!state.who) return;                 // 没登录就没有"离开"一说
    if (state.manual) return;               // 手动选过：先听用户的，别立刻覆盖
    if (state.status !== "online") return;  // 忙碌中 / 已退出不往"离开"上盖
    if (now - state.lastActive >= awayMs()) setStatus("away", "idle");
  }

  // 分析请求的**真实生命周期**：发出 → 忙碌中；回来（成功或失败都一样）→ 回到在线。
  // app.js 只把请求交给这里，前端不猜"任务还在不在跑"。
  function trackRequest(promise) {
    markActive();
    setStatus("busy", "analysis");
    return Promise.resolve(promise).then(
      (value) => { setStatus("online", "analysis"); return value; },
      (err) => { setStatus("online", "analysis"); throw err; },
    );
  }

  // ══════════════════════════════════════════════════════════════════════
  // 画界面
  // ══════════════════════════════════════════════════════════════════════
  function renderStatus() {
    const dot = $("user-dot");
    if (dot) dot.className = `dot st-${state.status}`;
    setText("user-status-text", STATUS_TEXT[state.status]);
    const holder = $("user-status");
    if (holder) {
      holder.title = `本机状态：${STATUS_TEXT[state.status]}（${STATUS_WHY[state.status]}）。`
        + "状态只在这台机器上显示，用来提醒你系统在做什么。";
    }
    STATUS_ORDER.forEach((name) => {
      const item = $(`um-status-${name}`);
      if (!item) return;
      item.classList.toggle("is-on", name === state.status);
      item.setAttribute("aria-checked", name === state.status ? "true" : "false");
    });
  }

  function renderUserArea() {
    const who = state.who;
    const name = who ? who.name : "未登录";
    setText("avatar", name.slice(0, 1));
    setText("user-name", name);
    setText("um-name", who ? name : "未登录");
    setText("um-kind", who
      ? (who.kind === "guest" ? "游客（本机随机取名）" : "账号（名字由你自己填）")
      : "还没进来");
    renderStatus();
  }

  function renderPaneUser() {
    const who = state.who;
    setText("rp-user-name", who ? who.name : "未登录");
    setText("rp-user-kind", who
      ? (who.kind === "guest" ? "游客（本机随机取名）" : "账号（名字由你自己填）")
      : "—");
  }

  function toast(message, kind) {
    const box = $("toast");
    if (!box) return;
    box.textContent = message;
    box.className = kind ? `toast ${kind}` : "toast";
    box.hidden = false;
    state.toastUntil = Date.now() + TOAST_MS;
  }

  // ══════════════════════════════════════════════════════════════════════
  // 登录页
  // ══════════════════════════════════════════════════════════════════════
  function randomGuestName() {
    let suffix = "";
    for (let i = 0; i < 5; i += 1) {
      suffix += GUEST_LETTERS.charAt(Math.floor(Math.random() * GUEST_LETTERS.length));
    }
    return `游客${suffix}`;
  }

  function refreshGuestName() {
    state.guestName = randomGuestName();
    setText("guest-name", state.guestName);
    return state.guestName;
  }

  function setFieldError(inputId, messageId, message) {
    const input = $(inputId);
    const box = $(messageId);
    if (input) input.classList.toggle("is-bad", !!message);
    if (box) {
      box.textContent = message || "";
      box.hidden = !message;
    }
  }

  // 只做**形态**检查：名字 2~20 个字、密码至少 6 位。
  // 这里不比对任何密码（本机也没存过密码可比），所以它拦的是"填错格了"，不是"密码不对"。
  function validateAccount() {
    const name = (($("login-name") || {}).value || "").trim();
    const password = (($("login-pwd") || {}).value || "");
    let nameMessage = "";
    let pwdMessage = "";
    if (name.length < 2) nameMessage = "名字至少 2 个字（最多 20 个）。";
    else if (name.length > 20) nameMessage = "名字最多 20 个字，短一点更好记。";
    if (password.length < 6) pwdMessage = "密码至少 6 位。";
    setFieldError("login-name", "login-name-msg", nameMessage);
    setFieldError("login-pwd", "login-pwd-msg", pwdMessage);
    return { ok: !nameMessage && !pwdMessage, name: name };
  }

  function enter(name, kind) {
    state.who = { name: name, kind: kind };
    writeWho(state.who);
    hide($("login-gate"));
    if (document.body) document.body.classList.remove("is-locked");
    state.lastActive = Date.now();
    setStatus("online", "login");
    renderUserArea();
    renderPaneUser();
    const pending = state.pending;
    state.pending = null;
    if (pending) pending();
  }

  async function submitAccount(event) {
    if (event && event.preventDefault) event.preventDefault();
    const checked = validateAccount();
    if (!checked.ok) return;                        // 形态不对：就地标红，不发任何请求
    const button = $("btn-login");
    const spinner = $("login-spinner");
    if (button) button.disabled = true;
    show(spinner);
    setStatus("busy", "login");
    // 等页面上那次**真实**的数据读取（服务慢的时候它就是真在等），不是设定时器凑时间
    await (factsPromise || Promise.resolve()).catch(() => {});
    const remember = $("login-remember");
    rememberName(remember && remember.checked ? checked.name : "");
    if (button) button.disabled = false;
    hide(spinner);
    enter(checked.name, "account");
  }

  async function submitGuest(event) {
    if (event && event.preventDefault) event.preventDefault();
    const name = refreshGuestName();                // 每次进来现取一个新名字
    const button = $("btn-guest");
    const spinner = $("guest-spinner");
    if (button) button.disabled = true;
    show(spinner);
    setStatus("busy", "login");
    await (factsPromise || Promise.resolve()).catch(() => {});
    if (button) button.disabled = false;
    hide(spinner);
    enter(name, "guest");
    toast(`已以「${name}」进入。这个名字是本机随机取的，只用于展示与记录，`
      + "退出后再进来会换一个新的。", "ok");
  }

  function switchTab(kind) {
    const isAccount = kind !== "guest";
    const accountTab = $("ltab-account");
    const guestTab = $("ltab-guest");
    if (accountTab) accountTab.classList.toggle("is-on", isAccount);
    if (guestTab) guestTab.classList.toggle("is-on", !isAccount);
    if (isAccount) {
      show($("login-form"));
      hide($("guest-pane"));
    } else {
      hide($("login-form"));
      show($("guest-pane"));
    }
    setText("login-title", isAccount ? "欢迎回来 👋" : "游客模式");
    setText("login-sub", isAccount ? "填个名字就能进，随时可以退出" : "不用填任何信息，直接进来看看");
  }

  function togglePassword() {
    const input = $("login-pwd");
    const button = $("btn-pwd-eye");
    if (!input) return;
    const shown = input.type === "text";
    input.type = shown ? "password" : "text";
    if (button) {
      button.textContent = shown ? "显示" : "隐藏";
      button.setAttribute("aria-pressed", shown ? "false" : "true");
    }
  }

  function logout() {
    clearWho();
    state.who = null;
    setStatus("offline", "logout");
    resetForm();
    if (document.body) document.body.classList.add("is-locked");
    show($("login-gate"));
    renderUserArea();
    renderPaneUser();
    refreshGuestName();
    toast("已退出登录，本机那条登录记录也清掉了。想再进来，重新登一次就行。");
  }

  function closeUserMenu() {
    const menu = $("user-menu");
    const button = $("btn-user");
    if (menu) hide(menu);
    if (button) button.setAttribute("aria-expanded", "false");
  }

  function resetForm() {
    const pwd = $("login-pwd");
    if (pwd) {
      pwd.value = "";
      pwd.type = "password";
    }
    const eye = $("btn-pwd-eye");
    if (eye) {
      eye.textContent = "显示";
      eye.setAttribute("aria-pressed", "false");
    }
    setFieldError("login-name", "login-name-msg", "");
    setFieldError("login-pwd", "login-pwd-msg", "");
    const loginButton = $("btn-login");
    if (loginButton) loginButton.disabled = false;
    const guestButton = $("btn-guest");
    if (guestButton) guestButton.disabled = false;
    hide($("login-spinner"));
    hide($("guest-spinner"));
    switchTab("account");
  }

  function loadFacts() {
    if (typeof API === "undefined" || !API.chatCapabilities) return null;
    factsPromise = API.chatCapabilities().then((caps) => {
      const profile = (caps || {}).data_profile || {};
      const rows = fmtCount(profile.rows);
      const customers = fmtCount(profile.customer_count);
      const countries = fmtCount(profile.country_count);
      if (!rows || !customers || !countries) {      // 数字不全就不摆一排"—"充数
        hide($("login-facts"));
        setText("login-facts-note", "数据概况暂时读不到（服务还没就绪时会这样），不影响登录。");
        return;
      }
      show($("login-facts"));
      setText("fact-rows", rows);
      setText("fact-customers", customers);
      setText("fact-countries", countries);
      setText("login-facts-note", profile.first_day && profile.last_day
        ? `数据范围 ${profile.first_day} ~ ${profile.last_day}　·　三个数字都从当前数据源读出来，换数据源会跟着变`
        : "三个数字都从当前数据源读出来，换数据源会跟着变");
    }).catch(() => {
      hide($("login-facts"));
      setText("login-facts-note", "数据概况暂时读不到（服务还没就绪时会这样），不影响登录。");
    });
    return factsPromise;
  }

  // ══════════════════════════════════════════════════════════════════════
  // 事件绑定
  // ══════════════════════════════════════════════════════════════════════
  function bindGate() {
    // 账号登录只有**一条**提交路径：表单 submit（按钮 type=submit，回车也一样走这里）
    const form = $("login-form");
    if (form) form.addEventListener("submit", submitAccount);
    const guestButton = $("btn-guest");
    if (guestButton) guestButton.addEventListener("click", submitGuest);
    const eye = $("btn-pwd-eye");
    if (eye) eye.addEventListener("click", togglePassword);
    const accountTab = $("ltab-account");
    if (accountTab) accountTab.addEventListener("click", () => switchTab("account"));
    const guestTab = $("ltab-guest");
    if (guestTab) guestTab.addEventListener("click", () => switchTab("guest"));

    // 尚未开通的入口：点一下给一句人话，不做"点不动的假按钮"
    const notReady = [
      ["link-forgot", "找回密码尚未开通。本地模式下没有密码可比对，重新填一次就能进。"],
      ["link-register", "注册尚未开通。直接填个名字登录，或用游客身份进来即可。"],
      ["link-help", "帮助文档尚未提供，先看看页面上的示例问题。"],
      ["link-privacy", "隐私说明尚未提供。这台机器上只保存你填的名字与本次状态，不保存密码。"],
    ];
    notReady.forEach(([id, message]) => {
      const button = $(id);
      if (button) button.addEventListener("click", () => toast(message));
    });

    // 输入时清掉上一次的红字（红字不跟着人走）
    ["login-name", "login-pwd"].forEach((id) => {
      const input = $(id);
      if (input) input.addEventListener("input", () => {
        setFieldError(id, `${id}-msg`, "");
      });
    });
  }

  function bindUserMenu() {
    const button = $("btn-user");
    const menu = $("user-menu");
    if (button && menu) {
      button.addEventListener("click", (event) => {
        if (event && event.stopPropagation) event.stopPropagation();
        const willOpen = menu.hidden;
        menu.hidden = !willOpen;
        button.setAttribute("aria-expanded", willOpen ? "true" : "false");
      });
      // 菜单内的点击不再冒泡到 document（否则刚点开就被"点外面关掉"收走）
      menu.addEventListener("click", (event) => {
        if (event && event.stopPropagation) event.stopPropagation();
      });
      document.addEventListener("click", () => closeUserMenu());
    }
    STATUS_ORDER.forEach((name) => {
      const item = $(`um-status-${name}`);
      if (!item) return;
      item.addEventListener("click", () => {
        closeUserMenu();
        if (!state.who) return;              // 没登录就没有"我的状态"可谈，别让菜单把状态点亮
        setStatus(name, "manual");
        toast(`状态已改成「${STATUS_TEXT[name]}」。这是本机显示的状态，`
          + "下一次登录、提问或长时间没操作时会自动更新。");
      });
    });
    const settings = $("um-settings");
    if (settings) settings.addEventListener("click", () => {
      closeUserMenu();
      toast(NOT_READY);
    });
    const logoutItem = $("um-logout");
    if (logoutItem) logoutItem.addEventListener("click", () => {
      closeUserMenu();
      logout();
    });
  }

  function bindActivity() {
    // "有操作" = 点了 / 按了键（这两种才算真的在动；鼠标划过不算，免得手一抖就打断）
    ["click", "keydown"].forEach((type) => {
      document.addEventListener(type, () => {
        if (!state.who) return;
        state.manual = false;            // 又开始操作了：手动选的状态让位给自动规则
        markActive();
      });
    });
    // 鼠标划过只刷新"最后活动时间"，不改状态
    ["mousemove", "wheel", "touchstart"].forEach((type) => {
      document.addEventListener(type, () => {
        if (!state.who) return;
        state.lastActive = Date.now();
      });
    });
  }

  // ══════════════════════════════════════════════════════════════════════
  // 启动
  // ══════════════════════════════════════════════════════════════════════
  function boot() {
    state.lastActive = Date.now();
    bindGate();
    bindUserMenu();
    bindActivity();

    const who = readWho();
    if (who) {
      state.who = who;
      hide($("login-gate"));
      if (document.body) document.body.classList.remove("is-locked");
      setStatus("online", "restore");           // 刷新时读回本机记录 → 直接在线
    } else {
      state.who = null;
      show($("login-gate"));
      if (document.body) document.body.classList.add("is-locked");
      setStatus("offline", "boot");
    }
    renderUserArea();
    renderPaneUser();
    resetForm();
    refreshGuestName();

    const remembered = rememberedName();
    const nameInput = $("login-name");
    const rememberBox = $("login-remember");
    if (remembered && nameInput) {
      nameInput.value = remembered;
      if (rememberBox) rememberBox.checked = true;
    }
    loadFacts();
    setInterval(tick, 1000);
  }

  // 严格按 DOM 是否就绪决定何时启动：本文件在 app.js 之前执行，
  // 所以"先给不给登录页"这个决定一定发生在 app.js 开始读数据之前。
  if (typeof document !== "undefined" && document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  return {
    // app.js 用这三个：要不要先登录、分析请求交给谁、当前是谁
    requireLogin(callback) {
      if (state.who) callback();
      else state.pending = callback;
    },
    trackRequest,
    name: () => (state.who ? state.who.name : ""),
    // 排查与验收用（不参与界面渲染）
    status: () => state.status,
    loggedIn: () => !!state.who,
    _boot: boot,
  };
})();
