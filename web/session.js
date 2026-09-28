/* session.js · 登录 / 注册 / 用户区 / 在线状态（本机会话层）
 *
 * ════ 这一层做什么、不做什么（写清楚边界）════
 * 做：画登录页、注册页与右上角的用户区；把账号与密码**递给后端**核对（注册、登录各一次请求）；
 *     记住"这次是谁"（一条本机记录，退出即清）；让四种状态跟着**真实动作**走
 *     （登录 / 提问 / 长时间没操作 / 退出）。
 * 不做：**任何密码运算**。本文件里没有摘要、没有加密、没有比对 —— 密码从输入框直接进请求体，
 *       算与比都在后端（`app/accounts.py`）。所以本机记录、页面文本、控制台里都不会有密码。
 * 也不做：令牌 / 多用户数据隔离（本地单机级别，那些留待后续）。
 *       于是这里也不说"已安全登录"这种话 —— 它只是"这台机器记得你刚对过密码"。
 *
 * ════ 角色与游客（本文件新增的两件事）════
 * ① **角色**：只有一条规则 —— 这台机器上第一个注册的账号是管理员。管理员登录后
 *    「系统设置」里会多出「账号管理」（待批准列表 + 已批准列表），别人看不到。
 *    角色是**后端说了算**的（登录响应里带着 role），前端只负责照着显示。
 *    FR-002A：**要不要等人批准也由后端（本机模式）说了算** —— 默认的本机单用户模式
 *    注册即生效，页面看到 `active` 就直接进系统，不再假设"新账号一定待批准"。
 * ② **游客**：不进账号表、没有会话编号，所以后端那几条管理端点天然拒绝它（401）——
 *    这里再做一道**前端拦截**，让用户在点下去的那一刻就看到人话，而不是等一个 401。
 *    受限清单写在 RESTRICTED 里（导入 / 导出 / 改任务 / 账号管理 / 删除），
 *    **只有这些地方**会出现 ⚠ 记号与弹窗；提问、看表格、看周报一概不受限。
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

  // 本机那条"这次是谁"的记录：**只存账号、显示名、入口类型、角色与会话编号**，
  // 密码连碰都不碰（键名沿用当初的写法，老记录里的"记住账号"因此不会丢）。
  // 会话编号不是令牌：它只用来回答"这个管理动作是谁按的"，服务重启即失效（见 app/accounts.py）。
  const WHO_KEY = "sra.who";
  const NAME_KEY = "sra.remembered-name";

  // ── 游客**用不了**的东西（清单只写在这里一处，页面别处不许再列一遍）──────────
  // 每一项：给用户看的功能名 + 点下去时弹窗里补的那半句。
  // 没列在这里的（提问、看表格、看周报、翻历史、看帮助…）游客都能用。
  const RESTRICTED = {
    import: { label: "导入数据（更换数据源）", why: "导入数据需要登录后使用。" },
    export: { label: "导出报表", why: "导出报表需要登录后使用。" },
    task: { label: "保存或修改任务", why: "保存或修改任务需要登录后使用。" },
    accounts: { label: "账号管理", why: "账号管理仅管理员可用。" },
    delete: { label: "删除操作", why: "删除操作需要登录后使用。" },
  };
  const GUEST_BAR_TEXT = "游客模式 · 部分功能受限";
  const GUARD_TITLE = "需要您先登录才能使用完整服务";

  // 三栏：账号登录 / 注册 / 游客登录。标题与副标题跟栏目走 —— 切栏时一起换。
  const TABS = {
    account: { title: "账号登录", sub: "登录后使用完整功能" },
    register: { title: "注册账号", sub: "填写账号信息完成注册" },
    guest: { title: "游客登录", sub: "无需账号，部分功能受限" },
  };
  const PANES = { account: "login-form", register: "register-form", guest: "guest-pane" };
  // 两张**附加**卡（FR-001A）：
  //   reset  —— 忘记密码的四步流程
  //   change —— 用临时密码登录之后"必须先改密"的那一屏
  // 它们不进 PANES：上面那三栏底下有标签按钮，这两张没有 —— 它们是被"推进去"的，
  // 不是与"登录/注册/游客"并列的第三个选项。
  const EXTRA_PANES = { reset: "reset-pane", change: "pwd-change-form" };

  // 两个表单各有**自己的一张**验证码（各换各的，互不影响）：
  // value 是这张图的编号，提交时连同用户填的字符一起发出去。
  const CAPTCHA_SLOTS = {
    account: { input: "login-captcha", img: "login-captcha-img",
               msg: "login-captcha-msg", button: "login-captcha-btn" },
    register: { input: "reg-captcha", img: "reg-captcha-img",
                msg: "reg-captcha-msg", button: "reg-captcha-btn" },
    // 忘记密码第①步自己一张（与登录/注册那两张互不影响：它们各换各的）
    reset: { input: "reset-captcha", img: "reset-captcha-img",
             msg: "reset-captcha-msg", button: "reset-captcha-btn" },
  };

  // 帮助 / 隐私：同一只抽屉，按 kind 换标题与内容
  const INFO_PANES = {
    help: { pane: "info-pane-help", title: "帮助" },
    privacy: { pane: "info-pane-privacy", title: "隐私说明" },
  };

  // 账号字符口径：与后端同一套（中文 / 字母 / 数字 / 下划线 / 点 / 中划线）
  const USERNAME_OK = /^[A-Za-z0-9_.\-一-鿿]+$/

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
  const NOT_READY = "个人设置尚未开通。当前可改的只有登录账号与右上角的状态显示。";

  const state = {
    who: null,          // {name, kind, displayName}；kind: account | guest；name = 账号名
    lastCheckedName: "",  // 已经问过后端"有没有被注册"的那个账号名（避免同一个名字反复问）
    captchas: { account: "", register: "", reset: "" },   // 当前三张验证码图的编号（每个表单一张）
    status: "offline",
    manual: false,      // 用户手动选过状态（先听用户的，等下次自动事件再接管）
    lastActive: 0,
    toastUntil: 0,
    pending: null,      // 未登录时 app.js 交给我们的"登录后要干的事"
    guestName: "",
    // 忘记密码流程里的两张一次性凭证 —— **只在内存里，刷新即丢**（丢了就回第①步重来，
    // 与后端"这两个东西只在内存、重启即失效"是同一条 fail-closed 约定）。
    //   第①步 → resetToken + challengeId（第②步要带同一个 challengeId 才能通过交叉绑定）
    //   第②步 → ticket（第③步只用它 + 新密码，页面无从指定"改谁的密码"）
    reset: { step: 1, resetToken: "", challengeId: "", ticket: "", username: "" },
    // 临时密码登录后，在"改密"这一步之前先把这条会话与本机身份记在这里（改成了才真进去）
    pendingChange: null,
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
      const name = String(parsed.name);
      const kind = parsed.kind === "guest" ? "guest" : "account";
      return {
        name: name,
        kind: kind,
        displayName: String(parsed.display_name || name),   // 老记录没有这一项：退回账号名
        // 老记录（这一版之前存的）没有角色：当作普通账号 —— 宁可不给管理入口，
        // 也不要凭一条旧记录就让人看到"账号管理"。真正的权限在后端（会回 401/403）。
        role: kind === "guest" ? "" : (parsed.role === "admin" ? "admin" : "user"),
        sessionId: kind === "guest" ? "" : String(parsed.session_id || ""),
      };
    } catch (err) {
      return null;                       // 记录坏了就当没登录，不把页面搞挂
    }
  }

  // 只存"这次是谁"：账号、显示名、入口类型、角色、会话编号。
  // **不存密码、不存令牌**（也没有令牌可存）—— 会话编号服务重启即失效。
  function writeWho(who) {
    try {
      localStorage.setItem(WHO_KEY, JSON.stringify({
        name: who.name, kind: who.kind, display_name: who.displayName,
        role: who.role || "", session_id: who.sessionId || "",
      }));
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

  // ══════════════════════════════════════════════════════════════════════
  // 图形验证码（两张：登录一张、注册一张）
  // ══════════════════════════════════════════════════════════════════════
  // 图是**后端现画的一张图**，前端只负责把它显示出来、把用户填的字符原样递回去。
  // 前端不生成、不判断验证码对不对（"对不对"由后端说了算，前端只管把人话显示出来）。
  async function loadCaptcha(which) {
    const slot = CAPTCHA_SLOTS[which];
    if (!slot || typeof API === "undefined" || !API.captcha) return;
    try {
      const data = await API.captcha();
      state.captchas[which] = (data && data.captcha_id) || "";
      const img = $(slot.img);
      if (img) {
        // 当**图片**用（data: 图），不是当 HTML 插进页面 —— 图里的字符是画上去的，不是页面文字
        img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent((data && data.image_svg) || "")}`;
      }
      const input = $(slot.input);
      if (input) input.value = "";           // 换图 = 旧的那 4 个字符作废，清掉免得用户以为还算数
      // 注意：这里**不碰提示文字** —— 换图往往是"因为刚出错"才换的，
      // 顺手把提示清掉会让用户看不到那句人话（"验证码不对"一闪就没）。
      // 该清的地方（用户主动点图、表单复位）自己清。
    } catch (err) {
      state.captchas[which] = "";
      setFieldError(slot.input, slot.msg, "验证码图片加载失败，请点击右侧图片重试。");
    }
  }

  function captchaPayload(which) {
    const slot = CAPTCHA_SLOTS[which];
    return {
      captcha_id: state.captchas[which],
      captcha_text: (($(slot.input) || {}).value || "").trim(),
    };
  }

  // 验证码这条路上后端会回三种人话，前端**照它说的做**：
  //   填错   → 就地提示 + 换一张（旧的那张已经没用了）
  //   过期   → 提示"已帮你换一张" + 换一张
  //   太多次 → 提示要等多久（这是"先等等"，不是"你错了"）
  function handleCaptchaError(which, err) {
    const slot = CAPTCHA_SLOTS[which];
    const code = (err && err.code) || "";
    if (code === "captcha_wrong") {
      setFieldError(slot.input, slot.msg, "验证码不对，请重新输入。");
      loadCaptcha(which);
      return true;
    }
    if (code === "captcha_expired") {
      setFieldError(slot.input, slot.msg, "验证码已过期，已自动更换一张。");
      loadCaptcha(which);
      return true;
    }
    if (code === "captcha_missing") {
      setFieldError(slot.input, slot.msg, "请输入图中的 4 个字符。");
      return true;
    }
    if (code === "too_many_attempts") {
      setFieldError(slot.input, slot.msg,
        (err && err.message) || "尝试次数过多，请稍后再试。");
      loadCaptcha(which);
      return true;
    }
    return false;
  }

  // ══════════════════════════════════════════════════════════════════════
  // 帮助 / 隐私（同一只右侧抽屉）
  // ══════════════════════════════════════════════════════════════════════
  // 内容分两半：
  //   · **写死的那半**是口径与规则（口径不会因为换数据源而变）—— 但每一句都对着真系统核过；
  //   · **读出来的那半**是当前数据的事实（数据源名 / 范围 / 行数 / 客户数 / 国家数 / 币种 /
  //     不能问的维度 / 报告能导成什么格式）—— 换数据源会跟着变，所以每次打开都重新读一次。
  // 读不到就**显示"暂时读不到"** —— 不摆一个看起来像真数字的东西蒙混过去。
  const NOT_AVAILABLE = "暂时无法读取（服务尚未就绪时会出现）";

  async function loadHelpFacts() {
    setText("help-source", "读取中…");
    if (typeof API === "undefined" || !API.chatCapabilities) return;
    let caps = null;
    let health = null;
    try {
      caps = await API.chatCapabilities();
    } catch (err) { /* 读不到就按"读不到"显示，下面统一处理 */ }
    try {
      health = await API.health();
    } catch (err) { /* 同上 */ }

    const profile = (caps || {}).data_profile || {};
    // 数据源名与顶栏**同一处来源**（健康检查里的快照路径），不另起一份"当前数据源"的定义
    const snapshot = (health || {}).data_snapshot || {};
    const sourceName = snapshot.path ? String(snapshot.path).split(/[\\/]/).pop() : "";
    setText("help-source", sourceName || NOT_AVAILABLE);
    setText("help-range", (profile.first_day && profile.last_day)
      ? `${profile.first_day} ~ ${profile.last_day}`
      : NOT_AVAILABLE);
    setText("help-rows", fmtCount(profile.rows) || NOT_AVAILABLE);
    setText("help-customers", fmtCount(profile.customer_count) || NOT_AVAILABLE);
    setText("help-countries", fmtCount(profile.country_count) || NOT_AVAILABLE);
    setText("help-currency", (profile.currency && profile.currency.name)
      ? `人民币「${profile.currency.name}」`
      : NOT_AVAILABLE);
    setText("help-last-day", profile.last_day || NOT_AVAILABLE);

    // "数据里没有什么"照后端给的清单渲染 —— 后端说不能问什么，这里就写什么（同源，不手抄）
    const holder = $("help-unsupported");
    const dimensions = ((caps || {}).unsupported || {}).dimensions || [];
    if (holder) {
      holder.innerHTML = "";
      if (!dimensions.length) {
        const item = document.createElement("li");
        item.textContent = NOT_AVAILABLE;
        holder.appendChild(item);
      }
      dimensions.forEach((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        holder.appendChild(item);
      });
    }
    // 报告能导成什么格式：也读后端的清单（不自己写死扩展名）
    const formats = (((caps || {}).report || {}).export_formats || [])
      .map((item) => item && item.label).filter(Boolean);
    setText("help-report-formats", formats.length ? formats.join(" / ") : NOT_AVAILABLE);
  }

  function openInfo(kind) {
    const wanted = INFO_PANES[kind] ? kind : "help";
    Object.entries(INFO_PANES).forEach(([key, info]) => {
      const pane = $(info.pane);
      if (key === wanted) show(pane); else hide(pane);
    });
    setText("info-title", INFO_PANES[wanted].title);
    const drawer = $("info-drawer");
    show(drawer);
    if (drawer) drawer.setAttribute("data-kind", wanted);
    const close = $("btn-info-close");
    if (close && close.focus) close.focus();
    if (wanted === "help") loadHelpFacts();
  }

  function closeInfo() {
    hide($("info-drawer"));
  }

  function infoOpen() {
    const drawer = $("info-drawer");
    return Boolean(drawer) && drawer.hidden === false;
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

  // 头像取"名字的第一个字"。
  // 用 Array.from 而不是 [0]：中文一个字就是一个字符（"唐宇" → 唐），
  // emoji 这类由两个编码单元组成的字符也不会被劈成半个方块。
  function firstGlyph(text) {
    const trimmed = String(text || "").trim();
    if (!trimmed) return "未";
    return Array.from(trimmed)[0];
  }

  // 顶栏那个名字显示的是**显示名**（注册时可以另填，留空就是账号名本身），
  // 账号名只在用户菜单的一行说明里出现 —— 两个不一样时才两个都提一句。
  function kindNote(who) {
    if (!who) return "未登录";
    if (who.kind === "guest") return "游客（本机随机分配名称，不留身份）";
    const account = who.name;
    return who.displayName && who.displayName !== account
      ? `账号（本机注册的账号：${account}）`
      : "账号（本机注册，登录时已校验密码）";
  }

  function renderUserArea() {
    const who = state.who;
    const name = who ? (who.displayName || who.name) : "未登录";
    setText("avatar", who ? firstGlyph(name) : "未");
    setText("user-name", name);
    setText("um-name", name);
    setText("um-kind", kindNote(who));
    renderStatus();
  }

  function renderPaneUser() {
    const who = state.who;
    setText("rp-user-name", who ? (who.displayName || who.name) : "未登录");
    setText("rp-user-kind", who ? kindNote(who) : "—");
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

  // tone：不传 = 红字（填错了）；"ok" = 绿字（查过了，可用）。
  // 两种都用同一个落点，这样"红字不跟着人走"的清理逻辑只有一处。
  function setFieldError(inputId, messageId, message, tone) {
    const input = $(inputId);
    const box = $(messageId);
    const kind = message ? (tone || "bad") : "";
    if (input) {
      input.classList.toggle("is-bad", kind === "bad");
      input.classList.toggle("is-ok", kind === "ok");
    }
    if (box) {
      box.textContent = message || "";
      box.hidden = !message;
      box.classList.toggle("is-ok", kind === "ok");
    }
  }

  // 账号形态：2~40 个字符，且只用允许的那几种字符。
  // 前后端用同一套口径（这里是**提前拦**，真正的判定在后端）。
  function accountShapeMessage(account) {
    if (account.length < 2) return "账号至少 2 个字符（最多 40 个）。";
    if (account.length > 40) return "账号最多 40 个字符。";
    if (!USERNAME_OK.test(account)) return "账号只能用中文、字母、数字和下划线、点、中划线。";
    return "";
  }

  // 登录表单的**形态**检查（填错格了，不是"密码不对"）。
  // 真正的"对不对"由后端判定 —— 密码在发出去之前，这里只数位数；
  // 验证码也一样：这里只检查"填了没有、是不是 4 个字符"，对错由后端说了算。
  function validateAccount() {
    const account = (($("login-name") || {}).value || "").trim();
    const password = (($("login-pwd") || {}).value || "");
    const captchaText = captchaPayload("account").captcha_text;
    const accountMessage = accountShapeMessage(account);
    const pwdMessage = password.length < 6 ? "密码至少 6 位。" : "";
    const captchaMessage = captchaText.length !== 4 ? "请输入图中的 4 个字符。" : "";
    setFieldError("login-name", "login-name-msg", accountMessage);
    setFieldError("login-pwd", "login-pwd-msg", pwdMessage);
    setFieldError("login-captcha", "login-captcha-msg", captchaMessage);
    return { ok: !accountMessage && !pwdMessage && !captchaMessage, name: account, password: password };
  }

  // 注册表单的形态检查：账号 / 密码 / 确认密码 / 验证码（显示名不填就用账号名）。
  function validateRegister() {
    const account = (($("reg-name") || {}).value || "").trim();
    const password = (($("reg-pwd") || {}).value || "");
    const again = (($("reg-pwd2") || {}).value || "");
    const display = (($("reg-display") || {}).value || "").trim();
    const captchaText = captchaPayload("register").captcha_text;
    const accountMessage = accountShapeMessage(account);
    const pwdMessage = password.length < 6 ? "密码至少 6 位。" : "";
    // 两次不一致的提示只在"密码本身没问题"时说（否则会同时冒两条，反而看不清先改哪个）
    const againMessage = (!pwdMessage && again !== password) ? "两次输入的密码不一致。" : "";
    const displayMessage = display.length > 40 ? "显示名最多 40 个字符。" : "";
    const captchaMessage = captchaText.length !== 4 ? "请输入图中的 4 个字符。" : "";
    setFieldError("reg-name", "reg-name-msg", accountMessage);
    setFieldError("reg-pwd", "reg-pwd-msg", pwdMessage);
    setFieldError("reg-pwd2", "reg-pwd2-msg", againMessage);
    setFieldError("reg-display", "reg-display-msg", displayMessage);
    setFieldError("reg-captcha", "reg-captcha-msg", captchaMessage);
    return {
      ok: !accountMessage && !pwdMessage && !againMessage && !displayMessage && !captchaMessage,
      name: account, password: password, display: display,
    };
  }

  // 密码强度：**纯前端提醒，不拦人**（弱密码也能注册，只是提醒一句）。
  // 口径写在这儿，别处不许再算一遍：
  //   长度 ≥8 记 1 分、≥12 再记 1 分；同时有字母和数字记 1 分；有其它符号再记 1 分。
  //   0~1 分 = 弱，2 分 = 中，3 分及以上 = 强。
  function strengthOf(password) {
    const value = password || "";
    if (value.length < 6) return null;                  // 还没到能注册的长度，先不评
    let score = 0;
    if (value.length >= 8) score += 1;
    if (value.length >= 12) score += 1;
    if (/[A-Za-z]/.test(value) && /[0-9]/.test(value)) score += 1;
    if (/[^A-Za-z0-9]/.test(value)) score += 1;
    if (score <= 1) return { label: "弱", ratio: 1 };
    if (score === 2) return { label: "中", ratio: 2 };
    return { label: "强", ratio: 3 };
  }

  function renderStrength() {
    const password = (($("reg-pwd") || {}).value || "");
    const box = $("reg-strength");
    const fill = $("reg-strength-fill");
    const text = $("reg-strength-text");
    const level = strengthOf(password);
    if (!box) return;
    if (!level) {
      hide(box);
      return;
    }
    show(box);
    box.className = `pwd-strength lv-${level.ratio}`;
    if (fill && fill.style) fill.style.width = `${Math.round(level.ratio / 3 * 100)}%`;
    if (text) text.textContent = `密码强度：${level.label}`
      + (level.label === "弱" ? "（可以注册，建议加长并使用字母与数字混合）" : "");
  }

  // 账号框失焦：问一次后端"这个名字有没有被注册"，就地给结论。
  // 名字没变过就不重复问（切来切去不该反复打请求）；问不到就**不下结论**（不假装可用）。
  async function checkNameAvailable() {
    const input = $("reg-name");
    if (!input) return;
    const account = (input.value || "").trim();
    if (!account) {
      state.lastCheckedName = "";
      setFieldError("reg-name", "reg-name-msg", "");
      return;
    }
    const shape = accountShapeMessage(account);
    if (shape) {
      state.lastCheckedName = "";
      setFieldError("reg-name", "reg-name-msg", shape);
      return;
    }
    if (account === state.lastCheckedName) return;
    if (typeof API === "undefined" || !API.accountExists) return;
    try {
      const answer = await API.accountExists(account);
      state.lastCheckedName = account;
      setFieldError("reg-name", "reg-name-msg",
        (answer && answer.exists) ? "该账号已被注册，请更换。" : "✓ 可用",
        (answer && answer.exists) ? "bad" : "ok");
    } catch (err) {
      state.lastCheckedName = "";
      setFieldError("reg-name", "reg-name-msg", "");
    }
  }

  // 进来（账号登录成功 / 注册第一个账号 / 游客）。
  // `extra` 带上后端给的角色与会话编号 —— **角色以后端为准**，前端不自己算。
  function enter(name, kind, displayName, extra) {
    const data = extra || {};
    state.who = {
      name: name, kind: kind, displayName: displayName || name,
      role: kind === "guest" ? "" : (data.role === "admin" ? "admin" : "user"),
      sessionId: kind === "guest" ? "" : String(data.sessionId || ""),
    };
    writeWho(state.who);
    hide($("login-gate"));
    if (document.body) document.body.classList.remove("is-locked");
    state.lastActive = Date.now();
    setStatus("online", "login");
    renderUserArea();
    renderPaneUser();
    renderGuestMode();
    // 身份变了：受限入口上的 ⚠ 记号跟着重画（游客 → 登录后记号要全部消失）
    applyGuards();
    if (typeof document !== "undefined" && document.dispatchEvent) {
      document.dispatchEvent(new CustomEvent("sra:identity", { detail: identity() }));
    }
    const pending = state.pending;
    state.pending = null;
    if (pending) pending();
  }

  // ══════════════════════════════════════════════════════════════════════
  // 游客：提示条 + 受限拦截 + ⚠ 记号
  // ══════════════════════════════════════════════════════════════════════
  function isGuest() {
    return Boolean(state.who) && state.who.kind === "guest";
  }

  function isAdmin() {
    return Boolean(state.who) && state.who.kind === "account" && state.who.role === "admin";
  }

  function identity() {
    const who = state.who;
    return {
      name: who ? who.name : "",
      kind: who ? who.kind : "",
      role: who ? (who.role || "") : "",
      displayName: who ? (who.displayName || who.name) : "",
      guest: isGuest(),
      admin: isAdmin(),
      loggedIn: Boolean(who),
      sessionId: who ? (who.sessionId || "") : "",
    };
  }

  // 顶部那条轻量提示：只在游客身份下出现，一条，不是满屏遮罩。
  function renderGuestMode() {
    const bar = $("guest-bar");
    if (bar) bar.hidden = !isGuest();
    setText("guest-bar-text", GUEST_BAR_TEXT);
  }

  // ⚠ 记号只画在 `[data-guard]` 元素上 —— 页面里没标的地方一个记号都没有。
  // 账号登录之后记号全部消失（受限是**游客**的限制，不是"所有人"的限制）。
  function paintGuard(el) {
    const action = el.getAttribute("data-guard");
    if (!action || !RESTRICTED[action]) return;
    const needed = isGuest();
    el.classList.toggle("is-guarded", needed);
    const old = el.querySelector(".guard-ico");
    if (needed && !old) {
      const icon = document.createElement("span");
      icon.className = "guard-ico";
      icon.textContent = "⚠";
      icon.title = "游客模式：该功能需登录后使用";
      icon.setAttribute("aria-hidden", "true");
      el.appendChild(icon);
    } else if (!needed && old) {
      old.remove();
    }
  }

  function applyGuards() {
    if (typeof document === "undefined" || !document.querySelectorAll) return;
    const nodes = document.querySelectorAll("[data-guard]");
    Array.prototype.forEach.call(nodes, paintGuard);
  }

  // 给**动态生成的**按钮挂上受限标记（app.js 造按钮时调它，跟页面上的静态按钮同一套）。
  function mark(el, action) {
    if (!el || !el.setAttribute) return el;
    el.setAttribute("data-guard", action);
    paintGuard(el);
    return el;
  }

  // ★ 拦截点：受限动作在执行**之前**问这里一句。
  //   返回 true = 照常执行；返回 false = 已经弹过窗，调用方必须 return（**动作不发生**）。
  function guard(action) {
    const item = RESTRICTED[action];
    if (!item) return true;                 // 不在清单里 = 不受限，任何人可用
    if (!isGuest()) return true;            // 账号登录（含管理员）不受这些限制
    openGuardModal(item);
    return false;
  }

  function openGuardModal(item) {
    setText("guard-title", GUARD_TITLE);
    setText("guard-text", `「${item.label}」${item.why}`);
    show($("guard-modal"));
    const button = $("guard-login");
    if (button && button.focus) button.focus();
  }

  function closeGuardModal() {
    hide($("guard-modal"));
  }

  // 「去登录」：退掉游客身份、回到登录页的账号那一栏（游客不留身份，本来也没什么可保留）。
  function leaveGuestForLogin() {
    closeGuardModal();
    logout("guest");
    switchTab("account");
    const name = $("login-name");
    if (name && name.focus) name.focus();
  }

  // 登录：账号与密码**原样交给后端**核对（前端不比、不算、不存）。
  // 401 → 「账号或密码不对」（后端刻意不区分是账号不存在还是密码错）；
  // 失败时**保留账号、清空密码框**，并把光标放回密码框 —— 重试只需要再敲密码。
  async function submitAccount(event) {
    if (event && event.preventDefault) event.preventDefault();
    const checked = validateAccount();
    if (!checked.ok) return;                        // 形态不对：就地标红，不发请求
    const button = $("btn-login");
    const spinner = $("login-spinner");
    if (button) button.disabled = true;
    show(spinner);
    setStatus("busy", "login");
    let account = null;
    try {
      const answer = await API.authLogin({
        username: checked.name,
        password: checked.password,
        ...captchaPayload("account"),
      });
      account = (answer && answer.account) || null;
    } catch (err) {
      if (button) button.disabled = false;
      hide(spinner);
      setStatus("offline", "login");
      // 验证码这条路上的三种人话先分出去（填错/过期/太多次）——它跟"账号密码不对"不是一回事
      if (handleCaptchaError("account", err)) return;
      const wrongPassword = err && err.status === 401;
      const message = wrongPassword
        ? "账号或密码不对。账号还在框里，密码要重新敲一遍。"
        : ((err && err.message) || "登录没成功，请稍后再试。");
      setFieldError("login-pwd", "login-pwd-msg", message);
      // 密码或账号不对：**换一张验证码**（旧的那张已经交出去了，不能再用第二次）
      loadCaptcha("account");
      const pwd = $("login-pwd");
      if (pwd) {
        pwd.value = "";                            // 清空密码，保留账号
        if (pwd.focus) pwd.focus();
      }
      return;
    }
    const remember = $("login-remember");
    rememberName(remember && remember.checked ? checked.name : "");
    if (button) button.disabled = false;
    hide(spinner);
    const who = (account && account.username) || checked.name;
    // ★ 临时密码登录：**先改密才算真的进来**（后端那条会话此刻只准改密码与退出登录，
    //   这里只是把人引到改密那一屏；页面不做任何"准不准"的判断）。
    if (account && account.must_change_password) {
      setStatus("online", "login");
      beginPasswordChange(account, who);
      toast("这是用临时密码登录的。请先设置新密码，之后才能使用其它功能。");
      return;
    }
    enter(who, "account", (account && account.display_name) || who,
      { role: account && account.role, sessionId: account && account.session_id });
    toast(isAdmin()
      ? `已使用管理员账号「${state.who.displayName}」登录。「系统设置 → 账号管理」中可批准新账号。`
      : `已使用「${state.who.displayName}」登录。`, "ok");
  }

  // 注册：按后端返回的**状态**决定下一步（FR-002A 起有两种"可用"，不再只有第一个账号）：
  //   · active + admin → 这台机器上的第一个账号，自动是管理员 → 照常进来；
  //   · active + user  → 本机单用户模式（默认）：注册即生效 → 也直接进来，不需要谁批准；
  //   · pending        → 只有"设为需要管理员审批"的本机才会这样 → 就地告诉用户等批准，
  //                      **不进系统、不写本机登录记录**。
  // 重复账号（409）就地标在账号框下面 —— 那句话就是后端给的，前端不改写。
  async function submitRegister(event) {
    if (event && event.preventDefault) event.preventDefault();
    const checked = validateRegister();
    if (!checked.ok) return;
    const button = $("btn-register");
    const spinner = $("register-spinner");
    if (button) button.disabled = true;
    show(spinner);
    setStatus("busy", "login");
    let account = null;
    try {
      const answer = await API.authRegister({
        username: checked.name,
        password: checked.password,
        display_name: checked.display,
        ...captchaPayload("register"),
      });
      account = (answer && answer.account) || null;
    } catch (err) {
      if (button) button.disabled = false;
      hide(spinner);
      setStatus("offline", "register");
      if (handleCaptchaError("register", err)) return;
      const taken = err && err.status === 409;
      const message = taken
        ? "该账号已被注册，请更换。"
        : ((err && err.message) || "注册没成功，请稍后再试。");
      setFieldError(taken ? "reg-name" : "reg-pwd", taken ? "reg-name-msg" : "reg-pwd-msg", message);
      // 这一张已经用掉了（一次性的），换一张再让人改别的地方
      loadCaptcha("register");
      if (taken) {
        state.lastCheckedName = checked.name;        // 已经知道被占了，别再问一次
        const nameInput = $("reg-name");
        if (nameInput && nameInput.focus) nameInput.focus();
      }
      return;
    }
    if (button) button.disabled = false;
    hide(spinner);
    setStatus("offline", "register");
    const who = (account && account.username) || checked.name;
    const active = account && account.status === "active";
    // 验证码是一次性的，注册这条路走完了就换一张（不管成没成，旧的那张已经交出去了）
    loadCaptcha("register");
    if (active) {
      // 能直接进来有两种情况（FR-002A）：
      //   · 第一个账号 → 自动是管理员（否则没人能按下"批准"，系统当场死锁）；
      //   · 本机单用户模式（默认）→ 注册即生效，**不需要任何人批准**（自己开的机器）。
      const isAdmin = (account && account.role) === "admin";
      enter(who, "account", (account && account.display_name) || who,
        { role: account && account.role, sessionId: account && account.session_id });
      toast(isAdmin
        ? "这是本机第一个账号，已自动成为管理员，可立即使用。"
          + "其他账号需经你批准后才能登录（在「系统设置 → 账号管理」中）。"
        : "账号已生效，可以立即使用。", "ok");
      return;
    }
    // 等待批准：不进来，只把结果说清楚（三句话：接下来会怎样、谁能批、现在能干什么）
    showRegisterPending((account && account.display_name) || who);
  }

  // 注册完「等待批准」就地提示：注册表单收起来，换成一张结果卡片。
  // 为什么不是弹个 toast：toast 三秒就没了，而"接下来会发生什么、现在能做什么"
  // 是要用户看明白的事（而且**不给**他一个"已经进来了"的错觉）。
  function showRegisterPending(displayName) {
    hide($("register-form"));
    setText("reg-pending-name", displayName);
    // 账号已经建好了：注册表单清干净（密码不该在屏幕上多留），验证码换一张
    // （旧的那张随这次提交已经作废）。等批准的人回来时看到的是一张空表。
    ["reg-name", "reg-display", "reg-pwd", "reg-pwd2", "reg-captcha"].forEach((id) => {
      const input = $(id);
      if (input) input.value = "";
      setFieldError(id, `${id}-msg`, "");
    });
    const strength = $("reg-strength");
    if (strength) hide(strength);
    state.lastCheckedName = "";
    loadCaptcha("register");
    show($("register-pending"));
    const button = $("btn-reg-pending-back");
    if (button && button.focus) button.focus();
  }

  function hideRegisterPending() {
    hide($("register-pending"));
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
    toast(`已以「${name}」进入。该名称由本机随机分配，仅用于展示与记录；`
      + "游客不留身份，退出后再次进入将更换名称。", "ok");
  }

  // ══════════════════════════════════════════════════════════════════════
  // 忘记密码：单页四步（① 账号+验证码 ② 恢复码 ③ 新密码 ④ 完成）
  // ══════════════════════════════════════════════════════════════════════
  // ★ 三层权限在**后端**是分开的（验证码只允许进流程 / 恢复码证明所有权 / 票据只允许改密），
  //   页面这四步只是把这三层如实地摆出来：
  //     · 第①步永远只说同一句话（"如果账户信息有效且满足恢复条件，将继续下一步"）——
  //       页面**不判断**账号在不在、有没有恢复码，也没本事判断（后端不告诉它）；
  //     · 第②步要把第①步的那个 challengeId 一起带上（后端要校验两者是同一张挑战）；
  //     · 第③步只有 ticket 与新密码 —— 页面无从指定改谁的密码。
  const RESET_STEPS = 4;

  // 恢复码的规整：大写 + 去掉空格、连字符等一切分隔符。
  // ★ 服务端**也**会做同样一遍（那才是有效的那一遍，这里是为了让用户当场看懂自己输的像不像）。
  function normalizeRecoveryCode(raw) {
    return String(raw || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
  }

  function resetGoStep(step) {
    const wanted = Math.min(Math.max(Number(step) || 1, 1), RESET_STEPS);
    state.reset.step = wanted;
    for (let index = 1; index <= RESET_STEPS; index += 1) {
      const pane = $(`reset-step-${index}`);
      if (pane) { if (index === wanted) show(pane); else hide(pane); }
      const dot = $(`reset-dot-${index}`);
      if (dot) dot.classList.toggle("is-on", index <= wanted);
    }
    setText("login-title", "找回密码");
    setText("login-sub", wanted === RESET_STEPS ? "完成" : `第 ${wanted} 步 / 共 3 步`);
    // "返回登录"在第④步换成"返回登录"按钮（那张卡里自己有一个），这里只隐藏重复的那个
    const back = $("btn-reset-back");
    if (back) back.hidden = wanted === RESET_STEPS;
    const note = $("reset-note-1");
    if (note) note.hidden = wanted !== 1;
    if (wanted === 1) loadCaptcha("reset");
    if (wanted === 2) {
      const input = $("reset-code");
      if (input && input.focus) input.focus();
    }
    if (wanted === 3) {
      const input = $("reset-pwd");
      if (input && input.focus) input.focus();
    }
  }

  // 「忘记密码？」：进这个流程（不再是弹一句"本机没法找回"）。
  function openReset() {
    state.reset = { step: 1, resetToken: "", challengeId: "", ticket: "", username: "" };
    switchTab("reset");                       // 五张卡里只留这一张（见 PANES/EXTRA_PANES）
    resetGoStep(1);
    const name = $("reset-name");
    if (name && !name.value) name.value = ($("login-name") || {}).value || "";
    if (name && name.focus) name.focus();
  }

  function closeReset() {
    state.reset = { step: 1, resetToken: "", challengeId: "", ticket: "", username: "" };
    switchTab("account");
  }

  // 第①步：账号 + 验证码 → reset_token。
  // 无论账号在不在、有没有恢复码，后端回的**形状与话都一样**，页面也就照着同一句话显示。
  async function submitResetStep1() {
    const name = (($("reset-name") || {}).value || "").trim();
    const captcha = captchaPayload("reset");
    setFieldError("reset-name", "reset-name-msg", "");
    setFieldError("reset-captcha", "reset-captcha-msg", "");
    if (!name) { setFieldError("reset-name", "reset-name-msg", "请填写账号。"); return; }
    if (!captcha.captcha_text) {
      setFieldError("reset-captcha", "reset-captcha-msg", "请输入图中的 4 个字符。");
      return;
    }
    const button = $("btn-reset-step1");
    if (button) button.disabled = true;
    try {
      const data = await API.resetRequest({
        username: name,
        captcha: captcha.captcha_text,
        challenge_id: captcha.captcha_id,
        challenge_proof: captcha.captcha_text,
      });
      state.reset.resetToken = (data && data.reset_token) || "";
      state.reset.challengeId = captcha.captcha_id;
      state.reset.username = name;
      resetGoStep(2);
    } catch (err) {
      if (handleCaptchaError("reset", err)) return;   // 验证码那三种错就地提示 + 换一张
      setFieldError("reset-captcha", "reset-captcha-msg",
        (err && err.message) || "这一步没成功，请稍后再试。");
      loadCaptcha("reset");
    } finally {
      if (button) button.disabled = false;
    }
  }

  // 第②步：恢复码 → ticket。
  async function submitResetStep2() {
    const raw = ($("reset-code") || {}).value || "";
    const code = normalizeRecoveryCode(raw);
    setFieldError("reset-code", "reset-code-msg", "");
    if (!code) { setFieldError("reset-code", "reset-code-msg", "请填写恢复码。"); return; }
    if (code.length !== 20) {
      // 这一条只是**体验**上的提醒（20 位是公开的格式，说了不算泄露什么）：
      // 真正说了算的是服务端 —— 少一位、抄错一位，后端一律回"恢复码不对或已失效"。
      setFieldError("reset-code", "reset-code-msg",
        `恢复码是 20 位，你填了 ${code.length} 位，请检查是否少抄或多抄了字符。`);
      return;
    }
    const button = $("btn-reset-step2");
    if (button) button.disabled = true;
    try {
      const data = await API.resetVerify({
        reset_token: state.reset.resetToken,
        recovery_code: code,
        challenge_id: state.reset.challengeId,       // ★ 必须与第①步那张挑战是同一个
      });
      state.reset.ticket = (data && data.ticket) || "";
      resetGoStep(3);
    } catch (err) {
      const message = (err && err.message) || "这一步没成功，请稍后再试。";
      if (restartableReset(err)) {                   // token/挑战失效：回第①步重来
        setFieldError("reset-captcha", "reset-captcha-msg", message);
        state.reset.resetToken = "";
        resetGoStep(1);
        return;
      }
      setFieldError("reset-code", "reset-code-msg", message);
      const input = $("reset-code");
      if (input && input.focus) input.focus();
    } finally {
      if (button) button.disabled = false;
    }
  }

  // 哪种失败意味着"得从第①步重来"：一次性凭证不在册了（过期 / 用过 / 挑战对不上）。
  function restartableReset(err) {
    const code = (err && err.code) || "";
    return code === "reset_token_invalid" || code === "challenge_invalid";
  }

  // 第③步：票据 + 新密码 → 改密。
  async function submitResetStep3() {
    const first = ($("reset-pwd") || {}).value || "";
    const second = ($("reset-pwd2") || {}).value || "";
    setFieldError("reset-pwd", "reset-pwd-msg", "");
    setFieldError("reset-pwd2", "reset-pwd2-msg", "");
    if (first.length < 6) {
      setFieldError("reset-pwd", "reset-pwd-msg", "新密码至少 6 位。");
      return;
    }
    if (first !== second) {
      setFieldError("reset-pwd2", "reset-pwd2-msg", "两次输入的密码不一样，请重新确认。");
      return;
    }
    const button = $("btn-reset-step3");
    if (button) button.disabled = true;
    try {
      // ★ 请求体里**只有** ticket 与 new_password（后端也只读这两个）。
      //   改的是哪个账号，由票据内部决定 —— 页面根本不知道、也不需要知道。
      const data = await API.resetCommit({ ticket: state.reset.ticket, new_password: first });
      setText("reset-done-title", `「${(data && data.username) || state.reset.username}」的密码已更新`);
      setText("reset-done-note", "请用新密码登录。旧的登录状态已全部失效。");
      resetGoStep(4);
    } catch (err) {
      const message = (err && err.message) || "这一步没成功，请稍后再试。";
      if ((err && err.code) === "ticket_invalid") {
        setFieldError("reset-captcha", "reset-captcha-msg", message);
        state.reset.ticket = "";
        resetGoStep(1);
        return;
      }
      setFieldError("reset-pwd", "reset-pwd-msg", message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  // ══════════════════════════════════════════════════════════════════════
  // 临时密码登录后：先设置新密码，才能进系统
  // ══════════════════════════════════════════════════════════════════════
  // ★ 这一步**不是**页面说了算：后端把这条会话标记成"只准改密码与退出登录"，
  //   其它接口一律 403（见 app/api.py 的 must_change_guard）。
  //   页面做的是"把人引到这里"，不是"决定他能不能做别的"。
  function beginPasswordChange(account, name) {
    state.pendingChange = {
      name: (account && account.username) || name,
      displayName: (account && account.display_name) || name,
      role: (account && account.role) || "user",
      sessionId: (account && account.session_id) || "",
    };
    // 身份收回去：改密成功之前不算"进来了"（进系统那一步在 submitPasswordChange 里）。
    // 这一步同时解决"刷新页面"：本机那条记录被清掉，回来看到的是登录页 + 改密那一屏。
    clearWho();
    state.who = null;
    closeUserMenu();
    if (document.body) document.body.classList.add("is-locked");
    show($("login-gate"));
    renderUserArea();
    renderPaneUser();
    renderGuestMode();
    applyGuards();
    switchTab("change");
    setText("pwd-change-who",
      `「${state.pendingChange.displayName}」是用临时密码登录的。请先设置一个新密码，之后才能使用其它功能。`);
    ["pc-old", "pc-new", "pc-new2"].forEach((id) => {
      const input = $(id);
      if (input) input.value = "";
      setFieldError(id, `${id}-msg`, "");
    });
    const input = $("pc-old");
    if (input && input.focus) input.focus();
    if (typeof document !== "undefined" && document.dispatchEvent) {
      document.dispatchEvent(new CustomEvent("sra:identity", { detail: identity() }));
    }
  }

  // 刷新页面之后问后端一句"我这条会话现在被允许做什么"。
  // ★ 判断始终在**后端**：前端只是看清它的回答 —— 后端说"必须先改密"（403 + 那个错误码），
  //   就把人放回改密那一屏；后端说别的话（没登录 / 不是管理员）都不影响正常使用。
  // 为什么必须问：这条会话的 must_change 状态只在服务端内存里，本机记录里没有它，
  //   刷新一下前端就不知道了 —— 而"不知道"不该变成"看起来一切正常但每个请求都被拒"。
  function checkSessionState() {
    const who = state.who;
    if (!who || who.kind !== "account" || !who.sessionId) return;
    if (typeof API === "undefined" || !API.listAccounts) return;
    API.listAccounts(who.sessionId).catch((err) => {
      if (!err || err.code !== "must_change_password") return;
      beginPasswordChange({ username: who.name, display_name: who.displayName,
                            role: who.role, session_id: who.sessionId }, who.name);
      toast("这个账号是用临时密码登录的。请先设置新密码，之后才能使用其它功能。");
    });
  }

  async function submitPasswordChange() {
    const pending = state.pendingChange;
    if (!pending) { switchTab("account"); return; }
    const oldPwd = ($("pc-old") || {}).value || "";
    const first = ($("pc-new") || {}).value || "";
    const second = ($("pc-new2") || {}).value || "";
    ["pc-old", "pc-new", "pc-new2"].forEach((id) => setFieldError(id, `${id}-msg`, ""));
    if (!oldPwd) { setFieldError("pc-old", "pc-old-msg", "请填写临时密码。"); return; }
    if (first.length < 6) { setFieldError("pc-new", "pc-new-msg", "新密码至少 6 位。"); return; }
    if (first !== second) {
      setFieldError("pc-new2", "pc-new2-msg", "两次输入的密码不一样，请重新确认。");
      return;
    }
    const button = $("btn-pwd-change");
    if (button) button.disabled = true;
    try {
      const answer = await API.changePassword({
        old_password: oldPwd,
        new_password: first,
        session_id: pending.sessionId,
      });
      state.pendingChange = null;
      // ★ 密码变了 = 认证状态重建：旧会话已经作废，这里换上后端刚发的**新**会话
      enter(pending.name, "account", pending.displayName,
        { role: pending.role, sessionId: (answer && answer.session_id) || "" });
      toast("密码已更新，已用新密码重新登录。", "ok");
    } catch (err) {
      const message = (err && err.message) || "改密没成功，请稍后再试。";
      if ((err && err.status) === 401) {             // 会话没了（服务重启过）：回登录页
        state.pendingChange = null;
        switchTab("account");
        setFieldError("login-pwd", "login-pwd-msg", message);
        return;
      }
      setFieldError((err && err.code === "bad_credentials") ? "pc-old" : "pc-new",
        (err && err.code === "bad_credentials") ? "pc-old-msg" : "pc-new-msg", message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  // 三栏切换：账号登录 / 注册 / 游客登录。
  // 「注册」做成第三个标签（而不是只留着底部那个链接）：注册与登录是**并列**的两件事 ——
  // 第一次来的人要先注册，把它藏在底部小字里会让人以为"必须先有账号才能用"；
  // 底部的「注册账号」链接保留，点它等于切到这一栏（老位置、老习惯不掉）。
  function switchTab(kind) {
    const all = { ...PANES, ...EXTRA_PANES };
    const wanted = all[kind] ? kind : "account";
    Object.entries(all).forEach(([key, paneId]) => {
      const tab = $(`ltab-${key}`);
      if (tab) tab.classList.toggle("is-on", key === wanted);
      const pane = $(paneId);
      if (key === wanted) show(pane); else hide(pane);
    });
    // 注册那张"等待批准"的结果卡：切栏时一律收起来（回到填表状态）。
    // 不收的话，切走再回来看到的还是上一次的结论，人会以为刚填的也提交了。
    hideRegisterPending();
    const titles = {
      ...TABS,
      reset: { title: "找回密码", sub: "第 1 步 / 共 3 步" },
      change: { title: "设置新密码", sub: "临时密码登录后必须先改密" },
    };
    setText("login-title", titles[wanted].title);
    setText("login-sub", titles[wanted].sub);
  }

  // 眼睛图标两态：睁眼（明文藏着，点了就显示）↔ 闭眼带斜线（明文显示中，点了藏回去）。
  // 两个图标都是页面里现成的 <svg>，这里只切 `.is-shown` 一个类 + 无障碍标签，不拼字符串、不引图标库。
  function setEyeState(shown) {
    const button = $("btn-pwd-eye");
    if (!button) return;
    const label = shown ? "隐藏密码" : "显示密码";
    button.classList.toggle("is-shown", shown);
    button.setAttribute("aria-pressed", shown ? "true" : "false");
    button.setAttribute("aria-label", label);          // 读屏念的是"当前动作"，不是"眼睛"
    button.setAttribute("title", label);               // 鼠标悬停也看得到同一句话
  }

  function togglePassword() {
    const input = $("login-pwd");
    if (!input) return;
    const shown = input.type === "text";
    input.type = shown ? "password" : "text";
    setEyeState(!shown);
  }

  // 退出（`reason` = "guest" 时是游客点「去登录」触发的：不说"退出登录"，
  // 因为游客本来就没登录过，说错了用户会以为自己刚才登录过）。
  function logout(reason) {
    const wasGuest = isGuest() || reason === "guest";
    const sessionId = state.who ? state.who.sessionId : "";
    clearWho();
    state.who = null;
    closeGuardModal();
    closeUserMenu();
    setStatus("offline", "logout");
    resetForm();
    if (document.body) document.body.classList.add("is-locked");
    show($("login-gate"));
    renderUserArea();
    renderPaneUser();
    renderGuestMode();
    applyGuards();                       // 记号只在游客态出现；退出后一个都不该留
    refreshGuestName();
    if (typeof document !== "undefined" && document.dispatchEvent) {
      document.dispatchEvent(new CustomEvent("sra:identity", { detail: identity() }));
    }
    // 服务端那条本机会话也清掉（清不到不算失败：服务重启过本来就没了）。
    // 只对有会话编号的账号做 —— 游客没有会话，不必发这个请求。
    if (sessionId && typeof API !== "undefined" && API.authLogout) {
      API.authLogout(sessionId).catch(() => {});
    }
    if (!wasGuest) toast("已退出登录，本机登录记录已清除。需要时可重新登录。");
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
    setEyeState(false);                     // 眼睛复位成"睁眼 = 点了显示明文"
    setFieldError("login-name", "login-name-msg", "");
    setFieldError("login-pwd", "login-pwd-msg", "");
    // 注册表单也一并清干净：退出/登录后回来不该看到上一次填了一半的密码框
    ["reg-name", "reg-display", "reg-pwd", "reg-pwd2", "reg-captcha"].forEach((id) => {
      const input = $(id);
      if (input) input.value = "";
      setFieldError(id, `${id}-msg`, "");
    });
    // 验证码一并换新的：上次那张早就作废了（一次性），留着只会让人"填对了还被拒"
    const loginCaptcha = $("login-captcha");
    if (loginCaptcha) loginCaptcha.value = "";
    setFieldError("login-captcha", "login-captcha-msg", "");
    loadCaptcha("account");
    loadCaptcha("register");
    // 忘记密码 / 改密那两张卡也清干净，并把它们手里的**一次性凭证**丢掉
    // （reset_token 与 ticket 都只在内存里，丢了就回第①步重来 —— 与后端同一条 fail-closed 约定）
    state.reset = { step: 1, resetToken: "", challengeId: "", ticket: "", username: "" };
    state.pendingChange = null;
    ["reset-name", "reset-captcha", "reset-code", "reset-pwd", "reset-pwd2",
     "pc-old", "pc-new", "pc-new2"].forEach((id) => {
      const input = $(id);
      if (input) input.value = "";
      setFieldError(id, `${id}-msg`, "");
    });
    hide($("set-recovery-box"));
    hide($("reg-strength"));
    state.lastCheckedName = "";
    const loginButton = $("btn-login");
    if (loginButton) loginButton.disabled = false;
    const guestButton = $("btn-guest");
    if (guestButton) guestButton.disabled = false;
    const registerButton = $("btn-register");
    if (registerButton) registerButton.disabled = false;
    hide($("login-spinner"));
    hide($("guest-spinner"));
    hide($("register-spinner"));
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
        setText("login-facts-note", "数据概况暂时无法读取（服务尚未就绪时会出现），不影响登录。");
        return;
      }
      show($("login-facts"));
      setText("fact-rows", rows);
      setText("fact-customers", customers);
      setText("fact-countries", countries);
      setText("login-facts-note", profile.first_day && profile.last_day
        ? `数据范围 ${profile.first_day} ~ ${profile.last_day}　·　三个数字均从当前数据源读取，更换数据源后会随之变化`
        : "三个数字均从当前数据源读取，更换数据源后会随之变化");
    }).catch(() => {
      hide($("login-facts"));
      setText("login-facts-note", "数据概况暂时无法读取（服务尚未就绪时会出现），不影响登录。");
    });
    return factsPromise;
  }

  // 首次使用引导：问一次"这台机器上有几个账号"，一个都没有就把"先注册一个"那条亮出来。
  // 读不到就**不亮**（宁可少一条提示，也不假装知道系统里有没有账号）。
  function loadAccountHint() {
    if (typeof API === "undefined" || !API.accountExists) return null;
    return API.accountExists().then((answer) => {
      const count = Number((answer || {}).account_count || 0);
      const box = $("login-firstrun");
      if (!box) return;
      if (count > 0) hide(box);
      else show(box);
    }).catch(() => { hide($("login-firstrun")); });
  }

  // ══════════════════════════════════════════════════════════════════════
  // 事件绑定
  // ══════════════════════════════════════════════════════════════════════
  function bindGate() {
    // 两个表单各自只有**一条**提交路径：form 的 submit 事件。
    // 按钮是 type="submit" 且表单里就有它 —— 所以**光标在输入框里按回车**走的是同一条路
    // （浏览器的隐式提交），不用另写 keydown，也就不会出现"回车和点按钮做两件事"。
    const form = $("login-form");
    if (form) form.addEventListener("submit", submitAccount);
    const registerForm = $("register-form");
    if (registerForm) registerForm.addEventListener("submit", submitRegister);
    const guestButton = $("btn-guest");
    if (guestButton) guestButton.addEventListener("click", submitGuest);
    const eye = $("btn-pwd-eye");
    if (eye) eye.addEventListener("click", togglePassword);
    Object.keys(PANES).forEach((key) => {
      const tab = $(`ltab-${key}`);
      if (tab) tab.addEventListener("click", () => switchTab(key));
    });

    // 「注册账号」底部那个链接：切到注册那一栏（老位置还在，习惯不用改）
    const registerLink = $("link-register");
    if (registerLink) registerLink.addEventListener("click", () => {
      switchTab("register");
      const input = $("reg-name");
      if (input && input.focus) input.focus();
    });
    // 首次使用那条引导：点一下同样是切到注册栏
    const firstRun = $("login-firstrun");
    if (firstRun) firstRun.addEventListener("click", () => {
      switchTab("register");
      const input = $("reg-name");
      if (input && input.focus) input.focus();
    });

    // 「忘记密码？」——进四步找回流程（不再是弹一句"本机没法找回"）。
    // 本机仍然**无法还原原密码**（只存不可还原的摘要，也没有邮件服务），
    // 但现在有了确实能走通的一条路：用登录后自己生成的**恢复码**重置一个新密码。
    // 没有恢复码的账号走管理员临时密码那条（见帮助「七、忘记密码怎么办」）。
    const forgot = $("link-forgot");
    if (forgot) forgot.addEventListener("click", () => openReset());
    // 找回流程里的四个按钮 + 一个返回
    const resetBindings = [
      ["btn-reset-step1", submitResetStep1],
      ["btn-reset-step2", submitResetStep2],
      ["btn-reset-step3", submitResetStep3],
      ["btn-reset-done", () => closeReset()],
      ["btn-reset-back", () => closeReset()],
      ["btn-pwd-change", submitPasswordChange],
    ];
    resetBindings.forEach(([id, handler]) => {
      const button = $(id);
      if (button) button.addEventListener("click", handler);
    });
    // 恢复码输入框：失焦时把规整后的样子写回去（用户能当场看出自己抄得对不对）。
    // ★ 服务端还会再规整一遍 —— 那才是有效的那一遍（只在前端做等于没做）。
    const codeInput = $("reset-code");
    if (codeInput) codeInput.addEventListener("blur", () => {
      const normalized = normalizeRecoveryCode(codeInput.value);
      if (normalized && normalized !== codeInput.value) {
        codeInput.value = normalized.replace(/(.{4})(?=.)/g, "$1-");
      }
    });

    // 帮助 / 隐私：打开同一只右侧抽屉（不是"尚未开通"的提示条）。
    // 登录页底部两个入口 + 顶栏 ☰ 菜单里两个入口，打开的是**同一只抽屉**（内容只有一份）。
    const openFrom = (kind, closeTopbarMenu) => () => {
      if (closeTopbarMenu) {
        const menu = $("topbar-menu");
        if (menu) hide(menu);                       // 菜单先收起来，别让抽屉盖着一张菜单
        const toggle = $("btn-menu");
        if (toggle) toggle.setAttribute("aria-expanded", "false");
      }
      openInfo(kind);
    };
    [["link-help", "help", false], ["link-privacy", "privacy", false],
     ["link-note-privacy", "privacy", false],
     ["menu-item-help", "help", true], ["menu-item-privacy", "privacy", true]]
      .forEach(([id, kind, fromMenu]) => {
        const button = $(id);
        if (button) button.addEventListener("click", openFrom(kind, fromMenu));
      });
    const closeButton = $("btn-info-close");
    if (closeButton) closeButton.addEventListener("click", closeInfo);
    const backdrop = $("info-backdrop");
    if (backdrop) backdrop.addEventListener("click", closeInfo);
    document.addEventListener("keydown", (event) => {
      if (event && event.key === "Escape" && infoOpen()) closeInfo();   // Esc 也能关
    });

    // 验证码：点图换一张（这是"看不清"的唯一出路，必须有）
    Object.keys(CAPTCHA_SLOTS).forEach((which) => {
      const slot = CAPTCHA_SLOTS[which];
      const image = $(slot.img);
      const button = $(slot.button);
      // 用户主动换图：先把上一条红字收走，再取新图（新图是"重新开始"，不该带着旧提示）
      const reload = () => {
        setFieldError(slot.input, slot.msg, "");
        loadCaptcha(which);
      };
      if (image) image.addEventListener("click", reload);
      if (button) button.addEventListener("click", reload);
    });

    // 输入时清掉上一次的红字（红字不跟着人走）
    ["login-name", "login-pwd", "reg-display", "reg-pwd2",
     "login-captcha", "reg-captcha"].forEach((id) => {
      const input = $(id);
      if (input) input.addEventListener("input", () => {
        setFieldError(id, `${id}-msg`, "");
      });
    });

    // 注册表单专门的几条：账号失焦去查重、密码框边打边给强度提示
    const regName = $("reg-name");
    if (regName) {
      regName.addEventListener("input", () => {
        state.lastCheckedName = "";           // 名字改过了：上一次的查重结论作废
        setFieldError("reg-name", "reg-name-msg", "");
      });
      // 失焦就问一次"这个账号有没有被注册"：不用等提交才知道重名
      regName.addEventListener("blur", checkNameAvailable);
    }
    const regPwd = $("reg-pwd");
    if (regPwd) {
      regPwd.addEventListener("input", () => {
        renderStrength();
        setFieldError("reg-pwd", "reg-pwd-msg", "");
        // 密码变过之后，确认框里那句"两次不一样"要重新判定
        const again = $("reg-pwd2");
        if (again && again.value) {
          setFieldError("reg-pwd2", "reg-pwd2-msg",
            again.value === regPwd.value ? "" : "两次输入的密码不一致。");
        }
      });
    }
    const regPwd2 = $("reg-pwd2");
    if (regPwd2) regPwd2.addEventListener("input", () => {
      const first = $("reg-pwd");
      setFieldError("reg-pwd2", "reg-pwd2-msg",
        (first && first.value === regPwd2.value) ? "" : "两次输入的密码不一致。");
    });
  }

  // 弹窗与游客提示条上的按钮（「去登录」两处都通向同一条路：leaveGuestForLogin）。
  function bindGuardModal() {
    const login = $("guard-login");
    if (login) login.addEventListener("click", leaveGuestForLogin);
    const cancel = $("guard-cancel");
    if (cancel) cancel.addEventListener("click", closeGuardModal);
    const backdrop = $("guard-backdrop");
    if (backdrop) backdrop.addEventListener("click", closeGuardModal);
    const barLink = $("guest-bar-login");
    if (barLink) barLink.addEventListener("click", leaveGuestForLogin);
    const back = $("btn-reg-pending-back");
    if (back) back.addEventListener("click", () => {
      switchTab("account");
      const input = $("login-name");
      if (input && input.focus) input.focus();
    });
    // Esc 关弹窗（跟帮助抽屉同一个习惯）
    document.addEventListener("keydown", (event) => {
      const modal = $("guard-modal");
      if (event && event.key === "Escape" && modal && !modal.hidden) closeGuardModal();
    });
    // 弹窗里点一下不该把点击漏到底下的页面上（避免"关了窗顺手又点了个按钮"）
    const card = $("guard-card");
    if (card) card.addEventListener("click", (event) => {
      if (event && event.stopPropagation) event.stopPropagation();
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
        toast(`状态已设为「${STATUS_TEXT[name]}」。该状态仅在本机显示，`
          + "将在下次登录、提问或长时间无操作时自动更新。");
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
    bindGuardModal();

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
    renderGuestMode();
    applyGuards();                              // 刷新回来仍是游客 → 受限处的 ⚠ 记号照旧画上
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
    loadAccountHint();                          // 一个账号都没有 → 亮出"先注册一个"
    loadCaptcha("account");                     // 两张验证码图各取一张（登录 / 注册）
    loadCaptcha("register");
    checkSessionState();                        // 刷新回来先问一句"这条会话现在准做什么"
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
    displayName: () => (state.who ? (state.who.displayName || state.who.name) : ""),

    // ── 身份与受限（app.js 用这几个；页面逻辑不自己判断"我是不是游客"）────────
    identity,
    isGuest,
    isAdmin,
    sessionId: () => (state.who ? (state.who.sessionId || "") : ""),
    // ★ 受限动作的**唯一**拦截点：返回 false 时调用方必须立刻 return，动作不许发生
    guard,
    // 动态造出来的按钮挂受限标记（页面上的静态按钮写在 index.html 的 data-guard 里）
    mark,
    applyGuards,
    restrictions: () => JSON.parse(JSON.stringify(RESTRICTED)),

    // 排查与验收用（不参与界面渲染）
    status: () => state.status,
    loggedIn: () => !!state.who,
    _boot: boot,
  };
})();
